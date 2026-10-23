package com.phonebridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import java.io.DataInputStream
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Serviço em primeiro plano que fala o protocolo do servidor de PC:
 *  -> "PAB1" + modo (1 = microfone, 2 = alto-falante)
 *  <- status(1) + taxa(4, big endian) + canais(1)
 * depois PCM 16 bits little-endian cru.
 */
class AudioService : Service() {

    companion object {
        const val ACTION_START = "start"
        const val ACTION_STOP = "stop"
        private const val CHANNEL = "pb"
        private const val NOTIF_ID = 1

        @Volatile var running = false
        @Volatile var lastStatus = "Desconectado."
        @Volatile var listener: ((String, Boolean) -> Unit)? = null

        fun report(msg: String, isRunning: Boolean = running) {
            lastStatus = msg
            listener?.invoke(msg, isRunning)
        }
    }

    private var worker: Thread? = null
    @Volatile private var socket: Socket? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopAll()
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY

        val ip = intent?.getStringExtra("ip") ?: return START_NOT_STICKY
        val port = intent.getIntExtra("port", 5800)
        val mode = intent.getIntExtra("mode", 1)

        startForegroundCompat(mode)
        running = true
        acquireLocks()
        report("Conectando a $ip:$port ...", true)

        worker = Thread {
            try {
                if (mode == 1) runMic(ip, port) else runSpeaker(ip, port)
            } catch (e: Exception) {
                if (running) report("Erro: ${e.message}", false)
            } finally {
                running = false
                try { socket?.close() } catch (_: Exception) {}
                releaseLocks()
                report(if (lastStatus.startsWith("Erro")) lastStatus else "Desconectado.", false)
                stopForegroundCompat()
                stopSelf()
            }
        }.also { it.start() }
        return START_NOT_STICKY
    }

    private fun connect(ip: String, port: Int, mode: Int): Triple<Socket, Int, Int> {
        val s = Socket()
        s.tcpNoDelay = true
        s.connect(InetSocketAddress(ip, port), 5000)
        socket = s
        val out = s.getOutputStream()
        out.write("PAB1".toByteArray(Charsets.US_ASCII))
        out.write(mode)
        out.flush()
        val inp = DataInputStream(s.getInputStream())
        val status = inp.readUnsignedByte()
        val rate = inp.readInt()
        val channels = inp.readUnsignedByte()
        if (status != 0) throw RuntimeException("o PC recusou a conexão (confira o dispositivo de saída)")
        return Triple(s, rate, channels)
    }

    // celular -> PC
    private fun runMic(ip: String, port: Int) {
        val (s, rate, _) = connect(ip, port, 1)
        val minBuf = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val frame = rate / 100 * 2 // 10 ms em bytes
        val rec = AudioRecord(
            MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuf, frame * 4)
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) throw RuntimeException("não foi possível abrir o microfone")
        report("Microfone ativo (${rate} Hz).", true)
        val out = s.getOutputStream()
        val buf = ByteArray(frame)
        try {
            rec.startRecording()
            while (running) {
                var off = 0
                while (off < frame && running) {
                    val n = rec.read(buf, off, frame - off)
                    if (n < 0) throw RuntimeException("falha na captura")
                    off += n
                }
                out.write(buf, 0, frame)
            }
        } finally {
            rec.stop(); rec.release()
        }
    }

    // PC -> celular
    private fun runSpeaker(ip: String, port: Int) {
        val (s, rate, channels) = connect(ip, port, 2)
        val chMask = if (channels == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
        val minBuf = AudioTrack.getMinBufferSize(rate, chMask, AudioFormat.ENCODING_PCM_16BIT)
        val frame = rate / 100 * channels * 2
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build()
            )
            .setAudioFormat(
                AudioFormat.Builder().setSampleRate(rate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(chMask).build()
            )
            .setBufferSizeInBytes(maxOf(minBuf, frame * 6))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        report("Alto-falante ativo (${rate} Hz).", true)
        val inp: InputStream = s.getInputStream()
        val buf = ByteArray(frame)
        try {
            track.play()
            while (running) {
                var off = 0
                while (off < frame) {
                    val n = inp.read(buf, off, frame - off)
                    if (n < 0) throw RuntimeException("PC encerrou a conexão")
                    off += n
                }
                track.write(buf, 0, frame)
            }
        } finally {
            track.stop(); track.release()
        }
    }

    private fun stopAll() {
        running = false
        try { socket?.close() } catch (_: Exception) {}
    }

    private fun buildNotification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "PhoneBridge", NotificationManager.IMPORTANCE_LOW))
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
        return b.setContentTitle("PhoneBridge")
            .setContentText("Transmitindo áudio com o PC")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    private fun startForegroundCompat(mode: Int) {
        val n = buildNotification()
        if (Build.VERSION.SDK_INT >= 29) {
            val type = if (mode == 1) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            else ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
            startForeground(NOTIF_ID, n, type)
        } else {
            startForeground(NOTIF_ID, n)
        }
    }

    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE) else @Suppress("DEPRECATION") stopForeground(true)
    }

    private fun acquireLocks() {
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "pb:audio").apply { acquire() }
        val wm = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "pb:wifi").apply { acquire() }
    }

    private fun releaseLocks() {
        try { wakeLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { wifiLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
    }

    override fun onDestroy() {
        stopAll()
        super.onDestroy()
    }
}
