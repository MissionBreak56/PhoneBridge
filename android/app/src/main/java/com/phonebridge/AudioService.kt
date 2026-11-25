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
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Serviço em primeiro plano que fala com o servidor de PC (veja PROTOCOL.md):
 *  v3 (padrão): PC pareado por QR code; autenticação mútua + AES-256-GCM + heartbeat. Ver SecureChannel.kt.
 *  v1/v2 (legado, só se o usuário permitir): sem proteção; PCM cru ou pacotes Opus.
 * Se a conexão cair, reconecta sozinho (configurável) até o usuário parar.
 */
/** Canal de áudio, independente do protocolo. */
internal interface Link {
    val rate: Int
    val channels: Int
    val codec: Int
    val socket: Socket
    fun readAudio(): ByteArray
    fun sendAudio(buf: ByteArray, off: Int, len: Int)
    fun start(drain: Boolean)
    fun close()
}

internal class SecureLink(
    override val socket: Socket, private val ch: V3Channel,
    override val rate: Int, override val channels: Int, override val codec: Int,
) : Link {
    override fun readAudio() = ch.readAudio()
    override fun sendAudio(buf: ByteArray, off: Int, len: Int) = ch.sendAudio(buf, off, len)
    override fun start(drain: Boolean) {
        ch.startKeepalive()
        if (drain) ch.startDrain()
    }
    override fun close() = ch.close()
}

/** Protocolos v1/v2 (sem proteção). */
internal class LegacyLink(
    override val socket: Socket, override val rate: Int, override val channels: Int, override val codec: Int,
) : Link {
    private val inp = DataInputStream(socket.getInputStream())
    private val out = socket.getOutputStream()
    private val chunk = rate / 100 * channels * 2
    @Volatile private var writeStart = 0L

    override fun readAudio(): ByteArray {
        if (codec == 1) {
            val n = inp.readUnsignedShort()
            if (n > 1500) throw ProtocolException("pacote Opus inválido")
            return ByteArray(n).also { inp.readFully(it) }
        }
        return ByteArray(chunk).also { inp.readFully(it) }
    }

    override fun sendAudio(buf: ByteArray, off: Int, len: Int) {
        writeStart = System.currentTimeMillis()
        try {
            if (codec == 1) {
                val framed = ByteArray(len + 2)
                framed[0] = (len shr 8).toByte()
                framed[1] = len.toByte()
                System.arraycopy(buf, off, framed, 2, len)
                out.write(framed)
            } else {
                out.write(buf, off, len)
            }
        } finally {
            writeStart = 0
        }
    }

    override fun start(drain: Boolean) {
        // se uma escrita ficar presa (Wi-Fi caiu), fecha o socket para o serviço reconectar
        Thread {
            while (!socket.isClosed) {
                val w = writeStart
                if (w != 0L && System.currentTimeMillis() - w > Proto3.WRITE_STUCK_MS) {
                    close()
                    break
                }
                try { Thread.sleep(500) } catch (_: InterruptedException) { break }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    override fun close() {
        try { socket.close() } catch (_: Exception) {}
    }
}

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
        private const val MAX_BUFFER_MS = 60 // áudio máximo guardado no celular (modo alto-falante)

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

    /** O PC recusou a chave de pareamento guardada: ela não vale mais. */
    private class StalePairingException : RuntimeException()

    /** Erro sem sentido tentar de novo (ex.: não abriu o microfone). */
    private class FatalError(msg: String) : RuntimeException(msg)

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
        val pcid = intent.getStringExtra("pcid")
        val allowLegacy = intent.getBooleanExtra("allowLegacy", false)
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
                        if (mode == 1) runMic(ip, port, pcid, allowLegacy, source, wantOpus, aec, ns) { attempt = 0 }
                        else runSpeaker(ip, port, pcid, allowLegacy, wantOpus) { attempt = 0 }
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

    private fun newSocket(ip: String, port: Int): Socket {
        val s = Socket()
        s.tcpNoDelay = true
        s.keepAlive = true
        s.receiveBufferSize = 16384 // buffers pequenos = menos atraso acumulado
        s.sendBufferSize = 16384
        s.connect(InetSocketAddress(ip, port), 5000)
        s.soTimeout = 8000 // o PC manda áudio ou heartbeat a cada poucos ms: 8 s sem nada = conexão morta
        socket = s
        return s
    }

    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun connect(ip: String, port: Int, mode: Int, wantOpus: Boolean, pcid: String?, allowLegacy: Boolean): Link {
        val store = PairingStore(this)
        var pc = if (pcid != null) store.findByPcid(pcid) else null
        if (pc != null) {
            try {
                return connectSecure(ip, port, mode, wantOpus, pc)
            } catch (e: StalePairingException) {
                // o PC não reconhece mais este celular (removido no PC, PC reinstalado, QR expirado): esquece o pareamento
                // antigo e pede um novo, com confirmação no PC
                store.remove(pc.pcid)
                pc = null
            }
        }
        try {
            return connectSecure(ip, port, mode, wantOpus, requestPairing(ip, port, store))
        } catch (e: PairingUnsupportedException) {
            // PC que não aceita pedidos de pareamento: PC antigo (v2) ou opção desligada no PC
            if (!allowLegacy) throw FatalError(getString(R.string.pair_unsupported))
        }
        return connectLegacy(ip, port, mode, wantOpus)
    }

    /** Pede o pareamento ao PC e espera a aprovação dele; mostra no celular o código que o usuário deve conferir. */
    private fun requestPairing(ip: String, port: Int, store: PairingStore): PairedPc {
        val s = newSocket(ip, port)
        try {
            val res = PairingClient.request(s, Build.MODEL ?: "Android") { code ->
                val shown = code.substring(0, 3) + " " + code.substring(3)
                val text = getString(R.string.pairing_waiting, shown)
                setState(STATE_STARTING, null, text)
                updateNotification(text)
            }
            val pc = PairedPc(
                res.pcid, res.pcName, ip, port,
                res.pid.joinToString("") { "%02x".format(it) }, res.secret, System.currentTimeMillis()
            )
            store.save(pc)
            return pc
        } catch (e: PairingDeniedException) {
            throw FatalError(getString(R.string.pairing_denied, e.reason))
        } catch (e: java.net.SocketTimeoutException) {
            throw FatalError(getString(R.string.pairing_timeout))
        } finally {
            try { s.close() } catch (_: Exception) {}
        }
    }

    private fun connectSecure(ip: String, port: Int, mode: Int, wantOpus: Boolean, pc: PairedPc): Link {
        val s = newSocket(ip, port)
        try {
            val ch = V3Channel.connect(s, hex(pc.pid), pc.secret)
            val acc = ch.request(mode, if (wantOpus) 3 else 1, Build.MODEL ?: "Android")
            val link = SecureLink(s, ch, acc.rate, acc.channels, acc.codec)
            link.start(drain = mode == 1)
            return link
        } catch (e: AuthRejectedException) {
            try { s.close() } catch (_: Exception) {}
            throw StalePairingException()
        } catch (e: UnsupportedVersionException) {
            throw FatalError(getString(R.string.pc_version_unsupported))
        } catch (e: RejectedException) {
            when (e.code) {
                Proto3.REJECT_BUSY -> throw RuntimeException(getString(R.string.pc_busy))
                else -> throw FatalError(e.message?.takeIf { it.isNotBlank() } ?: getString(R.string.pc_refused))
            }
        }
    }

    /** Protocolos v1/v2: só com a opção "PCs antigos sem proteção" ligada. */
    private fun connectLegacy(ip: String, port: Int, mode: Int, wantOpus: Boolean): Link {
        val s = newSocket(ip, port)
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
        val inp = DataInputStream(s.getInputStream())
        try {
            val status = inp.readUnsignedByte()
            val rate = inp.readInt()
            val channels = inp.readUnsignedByte()
            val codec = if (v2) inp.readUnsignedByte() else 0
            if (status == 3) throw FatalError(getString(R.string.pc_requires_pairing))
            if (status != 0) throw RuntimeException(getString(R.string.pc_refused))
            val link = LegacyLink(s, rate, channels, codec)
            link.start(drain = false)
            if (mode == 2) s.soTimeout = 5000
            else s.soTimeout = 0
            return link
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
        ip: String, port: Int, pcid: String?, allowLegacy: Boolean, source: Int, wantOpus: Boolean,
        aec: Boolean, ns: Boolean, onConnected: () -> Unit
    ) {
        val link = connect(ip, port, 1, wantOpus, pcid, allowLegacy)
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
            link.close()
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

        setState(STATE_CONNECTED)
        updateNotification(getString(R.string.notif_streaming))
        onConnected()
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
                if (encoder != null) {
                    val len = encoder.encode(shorts, 0, frame, pkt, 0, pkt.size)
                    link.sendAudio(pkt, 0, len)
                } else {
                    var j = 0
                    for (v in shorts) {
                        bytes[j++] = (v.toInt() and 0xFF).toByte()
                        bytes[j++] = ((v.toInt() shr 8) and 0xFF).toByte()
                    }
                    link.sendAudio(bytes, 0, frame * 2)
                }
            }
        } finally {
            try { aecFx?.release() } catch (_: Exception) {}
            try { nsFx?.release() } catch (_: Exception) {}
            try { rec.stop() } catch (_: Exception) {}
            rec.release()
            link.close()
        }
    }

    // PC -> celular
    private fun runSpeaker(
        ip: String, port: Int, pcid: String?, allowLegacy: Boolean, wantOpus: Boolean, onConnected: () -> Unit
    ) {
        val link = connect(ip, port, 2, wantOpus, pcid, allowLegacy)
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

        // Jitter buffer limitado por DURAÇÃO: uma thread só lê da rede e a outra toca. O atraso acumulado é
        // medido pelos blocos já recebidos (não por InputStream.available(), que não indica latência).
        val queue = JitterQueue(MAX_BUFFER_MS)
        val decoder = if (opus) OpusDecoder(OPUS_RATE, channels) else null
        val readerError = java.util.concurrent.atomic.AtomicReference<Throwable?>(null)
        val reader = Thread {
            try {
                val pcm = ShortArray(OPUS_FRAME * channels)
                while (running) {
                    val data = link.readAudio()
                    if (decoder != null) {
                        val got = decoder.decode(data, 0, data.size, pcm, 0, OPUS_FRAME, false)
                        val out = ByteArray(got * channels * 2)
                        var j = 0
                        for (i in 0 until got * channels) {
                            out[j++] = (pcm[i].toInt() and 0xFF).toByte()
                            out[j++] = ((pcm[i].toInt() shr 8) and 0xFF).toByte()
                        }
                        queue.put(out, out.size, got * 1000 / rate)
                    } else {
                        if (data.isEmpty() || data.size % frame != 0) throw ProtocolException("bloco de áudio inválido")
                        queue.put(data, data.size, data.size / frame * 10)
                    }
                }
            } catch (e: Throwable) {
                readerError.set(e)
            }
        }.also { it.isDaemon = true; it.start() }

        setState(STATE_CONNECTED)
        updateNotification(getString(R.string.notif_streaming))
        onConnected()
        try {
            track.play()
            while (running) {
                track.setVolume(if (isMuted()) 0f else (level + 1) / 10f)
                val item = queue.take(200)
                if (item == null) {
                    readerError.get()?.let { throw it }
                    if (!reader.isAlive) throw IOException(getString(R.string.pc_closed))
                    continue
                }
                track.write(item.data, 0, item.len)
            }
        } finally {
            link.close() // acorda a thread de leitura
            try { track.stop() } catch (_: Exception) {}
            track.release()
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
