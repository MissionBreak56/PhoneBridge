package com.phonebridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.Process
import org.concentus.OpusApplication
import org.concentus.OpusDecoder
import org.concentus.OpusEncoder
import java.io.DataInputStream
import java.io.EOFException
import java.io.InputStream
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Serviço em primeiro plano que fala o protocolo do servidor de PC (veja PROTOCOL.md):
 *  v1:  -> "PAB1" + modo (1 = microfone, 2 = alto-falante)
 *       <- status(1) + taxa(4, big endian) + canais(1)             depois PCM 16 bits little-endian cru
 *  v2:  -> "PAB2" + modo + máscara de codecs (1 = PCM, 2 = Opus)
 *       <- status(1) + taxa(4) + canais(1) + codec(1)              Opus: pacotes de 20 ms com tamanho (2 bytes)
 * Se a conexão cair, reconecta sozinho (configurável) até o usuário parar.
 */
class AudioService : Service() {

    companion object {
        const val ACTION_START = "start"
        const val ACTION_STOP = "stop"
        private const val CHANNEL = "pb"
        private const val NOTIF_ID = 1

        const val STATE_STOPPED = 0
        const val STATE_STARTING = 1
        const val STATE_CONNECTED = 2

        private const val OPUS_RATE = 48000
        private const val OPUS_FRAME = 960 // 20 ms
        private const val CODEC_OPUS = 1

        @Volatile var running = false
        @Volatile var state = STATE_STOPPED
        @Volatile var errorMessage: String? = null
        @Volatile var info: String? = null
        @Volatile var muted = false
        @Volatile var level = 4
        @Volatile var listener: (() -> Unit)? = null
        @Volatile private var forceV1 = false // PC antigo (só fala v1): cai para PCM

        fun setState(s: Int, error: String? = null, information: String? = null) {
            state = s
            errorMessage = error
            info = information
            listener?.invoke()
        }

        fun setError(msg: String) = setState(STATE_STOPPED, msg)
    }

    /** Erro sem sentido tentar de novo (ex.: não abriu o microfone). */
    private class FatalError(msg: String) : RuntimeException(msg)

    private class Link(val socket: Socket, val rate: Int, val channels: Int, val codec: Int)

    private var worker: Thread? = null
    @Volatile private var socket: Socket? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var autoMute = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            val wasRunning = running
            stopAll()
            if (!wasRunning) stopSelf()
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY

        val ip = intent?.getStringExtra("ip") ?: return START_NOT_STICKY
        val port = intent.getIntExtra("port", 5800)
        val mode = intent.getIntExtra("mode", 1)
        val source = intent.getIntExtra("source", 0)
        val wantOpus = intent.getBooleanExtra("opus", false)
        val reconnect = intent.getBooleanExtra("reconnect", true)
        val aec = intent.getBooleanExtra("aec", false)
        val ns = intent.getBooleanExtra("ns", false)
        autoMute = intent.getBooleanExtra("autoMute", false)
        errorMessage = null
        info = null

        startForegroundCompat(mode)
        running = true
        acquireLocks()
        setState(STATE_STARTING)

        worker = Thread {
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
            var attempt = 0
            try {
                while (running) {
                    try {
                        if (mode == 1) runMic(ip, port, source, wantOpus, aec, ns) { attempt = 0 }
                        else runSpeaker(ip, port, wantOpus) { attempt = 0 }
                    } catch (e: FatalError) {
                        errorMessage = e.message
                        break
                    } catch (e: Exception) {
                        if (!running) break
                        if (!reconnect) {
                            errorMessage = e.message ?: e.javaClass.simpleName
                            break
                        }
                    } finally {
                        try { socket?.close() } catch (_: Exception) {}
                    }
                    if (!running) break
                    attempt++
                    setState(STATE_STARTING, null, getString(R.string.state_reconnecting, attempt))
                    updateNotification(getString(R.string.state_reconnecting, attempt))
                    var waited = 0
                    val wait = minOf(attempt, 5) * 1000
                    while (running && waited < wait) { Thread.sleep(100); waited += 100 }
                }
            } finally {
                running = false
                try { socket?.close() } catch (_: Exception) {}
                releaseLocks()
                setState(STATE_STOPPED, errorMessage)
                stopForegroundCompat()
                stopSelf()
            }
        }.also { it.start() }
        return START_NOT_STICKY
    }

    private fun connect(ip: String, port: Int, mode: Int, wantOpus: Boolean): Link {
        val s = Socket()
        s.tcpNoDelay = true
        s.keepAlive = true
        s.receiveBufferSize = 16384 // buffers pequenos = menos atraso acumulado
        s.sendBufferSize = 16384
        s.connect(InetSocketAddress(ip, port), 5000)
        socket = s
        val v2 = wantOpus && !forceV1
        val out = s.getOutputStream()
        if (v2) {
            out.write("PAB2".toByteArray(Charsets.US_ASCII))
            out.write(mode)
            out.write(3) // aceita PCM e Opus
        } else {
            out.write("PAB1".toByteArray(Charsets.US_ASCII))
            out.write(mode)
        }
        out.flush()
        s.soTimeout = 8000
        val inp = DataInputStream(s.getInputStream())
        try {
            val status = inp.readUnsignedByte()
            val rate = inp.readInt()
            val channels = inp.readUnsignedByte()
            val codec = if (v2) inp.readUnsignedByte() else 0
            if (status != 0) throw RuntimeException(getString(R.string.pc_refused))
            s.soTimeout = 0
            return Link(s, rate, channels, codec)
        } catch (e: EOFException) {
            if (v2) {
                forceV1 = true // o PC fechou ao ver "PAB2": é uma versão antiga; usa PCM na próxima
                throw RuntimeException(getString(R.string.pc_old_version))
            }
            throw e
        }
    }

    // celular -> PC
    private fun runMic(
        ip: String, port: Int, source: Int, wantOpus: Boolean, aec: Boolean, ns: Boolean, onConnected: () -> Unit
    ) {
        val link = connect(ip, port, 1, wantOpus)
        val s = link.socket
        val rate = link.rate
        val opus = link.codec == CODEC_OPUS
        val frame = if (opus) OPUS_FRAME else rate / 100 // amostras: 20 ms (Opus) ou 10 ms (PCM)
        val minBuf = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val src = if ((aec || ns) && source == 0) MediaRecorder.AudioSource.VOICE_COMMUNICATION else source
        val rec = AudioRecord(
            src, rate, AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT, maxOf(minBuf, frame * 2 * 4)
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            throw FatalError(getString(R.string.mic_open_failed))
        }
        var aecFx: AcousticEchoCanceler? = null
        var nsFx: NoiseSuppressor? = null
        if (aec && AcousticEchoCanceler.isAvailable()) {
            aecFx = AcousticEchoCanceler.create(rec.audioSessionId)?.also { it.enabled = true }
        }
        if (ns && NoiseSuppressor.isAvailable()) {
            nsFx = NoiseSuppressor.create(rec.audioSessionId)?.also { it.enabled = true }
        }
        val encoder = if (opus) {
            OpusEncoder(OPUS_RATE, 1, OpusApplication.OPUS_APPLICATION_VOIP).also {
                it.setBitrate(32000)
                it.setComplexity(5)
            }
        } else null

        // vigia: se uma escrita ficar presa (Wi-Fi caiu), fecha o socket para reconectar
        val writeStart = java.util.concurrent.atomic.AtomicLong(0L)
        val watchdog = Thread {
            while (running && !s.isClosed) {
                val w = writeStart.get()
                if (w != 0L && System.currentTimeMillis() - w > 4000) {
                    try { s.close() } catch (_: Exception) {}
                    break
                }
                try { Thread.sleep(500) } catch (_: InterruptedException) { break }
            }
        }.also { it.isDaemon = true; it.start() }

        setState(STATE_CONNECTED)
        updateNotification(getString(R.string.notif_streaming))
        onConnected()
        val out = s.getOutputStream()
        val shorts = ShortArray(frame)
        val bytes = ByteArray(frame * 2)
        val pkt = ByteArray(1500)
        try {
            rec.startRecording()
            while (running) {
                var off = 0
                while (off < frame && running) {
                    val n = rec.read(shorts, off, frame - off)
                    if (n < 0) throw RuntimeException(getString(R.string.capture_failed))
                    off += n
                }
                if (!running) break
                if (isMuted()) java.util.Arrays.fill(shorts, 0.toShort()) else applyGain(shorts, (level + 1) / 5f)
                writeStart.set(System.currentTimeMillis())
                if (encoder != null) {
                    val len = encoder.encode(shorts, 0, frame, pkt, 2, pkt.size - 2)
                    pkt[0] = (len shr 8).toByte()
                    pkt[1] = len.toByte()
                    out.write(pkt, 0, len + 2)
                } else {
                    var j = 0
                    for (v in shorts) {
                        bytes[j++] = (v.toInt() and 0xFF).toByte()
                        bytes[j++] = ((v.toInt() shr 8) and 0xFF).toByte()
                    }
                    out.write(bytes, 0, frame * 2)
                }
                writeStart.set(0L)
            }
        } finally {
            writeStart.set(0L)
            watchdog.interrupt()
            try { aecFx?.release() } catch (_: Exception) {}
            try { nsFx?.release() } catch (_: Exception) {}
            try { rec.stop() } catch (_: Exception) {}
            rec.release()
        }
    }

    // PC -> celular
    private fun runSpeaker(ip: String, port: Int, wantOpus: Boolean, onConnected: () -> Unit) {
        val link = connect(ip, port, 2, wantOpus)
        val s = link.socket
        val rate = link.rate
        val channels = link.channels
        val opus = link.codec == CODEC_OPUS
        val chMask = if (channels == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
        val minBuf = AudioTrack.getMinBufferSize(rate, chMask, AudioFormat.ENCODING_PCM_16BIT)
        val frame = rate / 100 * channels * 2 // 10 ms em bytes
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
            .setBufferSizeInBytes(maxOf(minBuf, frame * 4))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .apply { if (Build.VERSION.SDK_INT >= 26) setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY) }
            .build()
        // o PC manda áudio sem parar: ficar 5 s sem nada significa que a conexão morreu
        s.soTimeout = 5000
        setState(STATE_CONNECTED)
        updateNotification(getString(R.string.notif_streaming))
        onConnected()
        val inp: InputStream = s.getInputStream()
        try {
            track.play()
            if (opus) {
                val dis = DataInputStream(inp)
                val decoder = OpusDecoder(OPUS_RATE, channels)
                val pkt = ByteArray(1500)
                val pcm = ShortArray(OPUS_FRAME * channels)
                fun next(): Int {
                    val n = dis.readUnsignedShort()
                    if (n > pkt.size) throw RuntimeException("pacote Opus inválido")
                    dis.readFully(pkt, 0, n)
                    return decoder.decode(pkt, 0, n, pcm, 0, OPUS_FRAME, false)
                }
                while (running) {
                    track.setVolume(if (isMuted()) 0f else (level + 1) / 10f)
                    // atraso acumulado: decodifica e descarta o excesso (mantém o decodificador em dia)
                    while (dis.available() > 2000) next()
                    val got = next()
                    track.write(pcm, 0, got * channels)
                }
            } else {
                val buf = ByteArray(frame)
                while (running) {
                    track.setVolume(if (isMuted()) 0f else (level + 1) / 10f)
                    // se acumulou mais de ~60 ms, descarta o atraso e fica com ~20 ms
                    val backlog = inp.available() / frame
                    if (backlog > 6) repeat(backlog - 2) { readFrame(inp, buf, frame) }
                    readFrame(inp, buf, frame)
                    track.write(buf, 0, frame)
                }
            }
        } finally {
            try { track.stop() } catch (_: Exception) {}
            track.release()
        }
    }

    private fun readFrame(inp: InputStream, buf: ByteArray, frame: Int) {
        var off = 0
        while (off < frame) {
            val n = inp.read(buf, off, frame - off)
            if (n < 0) throw RuntimeException(getString(R.string.pc_closed))
            off += n
        }
    }

    private fun isMuted(): Boolean {
        if (muted) return true
        if (!autoMute) return false
        val am = getSystemService(AUDIO_SERVICE) as android.media.AudioManager
        return am.mode == android.media.AudioManager.MODE_IN_CALL ||
            am.mode == android.media.AudioManager.MODE_RINGTONE
    }

    private fun applyGain(buf: ShortArray, gain: Float) {
        if (gain == 1f) return
        for (i in buf.indices) {
            buf[i] = (buf[i] * gain).toInt().coerceIn(-32768, 32767).toShort()
        }
    }

    private fun stopAll() {
        running = false
        try { socket?.close() } catch (_: Exception) {}
    }

    private fun buildNotification(text: String): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "PhoneBridge", NotificationManager.IMPORTANCE_LOW))
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, AudioService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE
        )
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
        return b.setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(
                    Icon.createWithResource(this, R.drawable.ic_notification), getString(R.string.stop), stop
                ).build()
            )
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        try {
            getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(text))
        } catch (_: Exception) {
        }
    }

    private fun startForegroundCompat(mode: Int) {
        val n = buildNotification(getString(R.string.notif_starting))
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
        wifiLock = wm.createWifiLock(
            if (Build.VERSION.SDK_INT >= 29) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF,
            "pb:wifi"
        ).apply { acquire() }
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
