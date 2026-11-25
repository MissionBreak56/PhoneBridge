package com.phonebridge

import java.io.DataInputStream
import java.io.EOFException
import java.io.IOException
import java.io.OutputStream
import java.net.Socket
import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec
import javax.crypto.KeyAgreement
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/*
 * Protocolo PhoneBridge v3 (lado do celular). Especificação: PROTOCOL.md.
 * Este arquivo não depende de Android: roda e é testado na JVM.
 */

object Proto3 {
    const val VERSION = 3
    val MAGIC = "PAB3".toByteArray(Charsets.US_ASCII)
    val MAGIC_SERVER = "PBS3".toByteArray(Charsets.US_ASCII)
    val MAGIC_ERROR = "PBE3".toByteArray(Charsets.US_ASCII)

    const val T_REQUEST = 0x10
    const val T_ACCEPT = 0x11
    const val T_REJECT = 0x12
    const val T_AUDIO = 0x20
    const val T_HEARTBEAT = 0x30
    const val T_CLOSE = 0x40
    const val T_PAIR_GRANT = 0x50
    const val T_PAIR_DENY = 0x51

    val MAGIC_PAIR = "PAP3".toByteArray(Charsets.US_ASCII)
    val MAGIC_PAIR_SERVER = "PBP3".toByteArray(Charsets.US_ASCII)
    const val PAIR_PUB_LEN = 65
    const val PAIR_WAIT_MS = 70_000

    const val REJECT_BUSY = 1
    const val REJECT_NO_OUTPUT = 2
    const val REJECT_UNSUPPORTED = 3
    const val REJECT_INTERNAL = 4

    const val PID_LEN = 8
    const val NONCE_LEN = 16
    const val TAG_LEN = 16
    const val MAX_PLAINTEXT = 16384
    const val MAX_NAME = 32
    const val HEARTBEAT_INTERVAL_MS = 2000L
    const val HEARTBEAT_TIMEOUT_MS = 6000L
    const val WRITE_STUCK_MS = 4000L
}

class ProtocolException(message: String) : IOException(message)

/** O PC fechou a conexão durante a autenticação: o celular não está pareado ou a chave não confere. */
class AuthRejectedException : IOException("autenticação recusada pelo PC")

/** O PC não fala esta versão do protocolo. */
class UnsupportedVersionException(val serverMax: Int) : IOException("versão do protocolo não suportada pelo PC")

class RejectedException(val code: Int, message: String) : IOException(message)

object Crypto {
    fun hmac(key: ByteArray, vararg parts: ByteArray): ByteArray {
        val m = Mac.getInstance("HmacSHA256")
        m.init(SecretKeySpec(key, "HmacSHA256"))
        for (p in parts) m.update(p)
        return m.doFinal()
    }

    fun hkdf(ikm: ByteArray, salt: ByteArray, info: ByteArray, length: Int): ByteArray {
        val prk = hmac(if (salt.isEmpty()) ByteArray(32) else salt, ikm)
        var okm = ByteArray(0)
        var t = ByteArray(0)
        var i = 1
        while (okm.size < length) {
            t = hmac(prk, t, info, byteArrayOf(i.toByte()))
            okm += t
            i++
        }
        return okm.copyOf(length)
    }

    fun authKey(psk: ByteArray) = hkdf(psk, "PBv3".toByteArray(), "PBv3 auth".toByteArray(), 32)

    fun clientProof(authKey: ByteArray, pid: ByteArray, cn: ByteArray, sn: ByteArray) =
        hmac(authKey, "PBv3 client".toByteArray(), pid, cn, sn)

    fun serverProof(authKey: ByteArray, pid: ByteArray, cn: ByteArray, sn: ByteArray) =
        hmac(authKey, "PBv3 server".toByteArray(), pid, cn, sn)

    /** (chave celular->PC, chave PC->celular) */
    fun sessionKeys(psk: ByteArray, pid: ByteArray, cn: ByteArray, sn: ByteArray): Pair<ByteArray, ByteArray> {
        val okm = hkdf(psk, cn + sn, "PBv3 session".toByteArray() + pid, 64)
        return okm.copyOfRange(0, 32) to okm.copyOfRange(32, 64)
    }

    fun constantTimeEquals(a: ByteArray, b: ByteArray) = MessageDigest.isEqual(a, b)

    fun random(n: Int) = ByteArray(n).also { SecureRandom().nextBytes(it) }
}

private fun nonce(counter: Long): ByteArray {
    val n = ByteArray(12)
    for (i in 0 until 8) n[11 - i] = (counter ushr (8 * i)).toByte()
    return n
}

class RecordWriter(key: ByteArray) {
    private val keySpec = SecretKeySpec(key, "AES")
    private val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    private var counter = 0L

    /** Retorna cabeçalho (2 bytes, tamanho) + texto cifrado + tag. */
    @Synchronized
    fun seal(type: Int, payload: ByteArray, off: Int = 0, len: Int = payload.size - off): ByteArray {
        if (1 + len > Proto3.MAX_PLAINTEXT) throw ProtocolException("registro grande demais")
        val total = 1 + len + Proto3.TAG_LEN
        val header = byteArrayOf((total shr 8).toByte(), (total and 0xFF).toByte())
        val plain = ByteArray(1 + len)
        plain[0] = type.toByte()
        System.arraycopy(payload, off, plain, 1, len)
        cipher.init(Cipher.ENCRYPT_MODE, keySpec, GCMParameterSpec(128, nonce(counter)))
        cipher.updateAAD(header)
        val ct = cipher.doFinal(plain)
        counter++
        return header + ct
    }
}

class RecordReader(key: ByteArray) {
    private val keySpec = SecretKeySpec(key, "AES")
    private val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    private var counter = 0L

    @Synchronized
    fun open(header: ByteArray, ciphertext: ByteArray): Pair<Int, ByteArray> {
        val plain = try {
            cipher.init(Cipher.DECRYPT_MODE, keySpec, GCMParameterSpec(128, nonce(counter)))
            cipher.updateAAD(header)
            cipher.doFinal(ciphertext)
        } catch (e: AEADBadTagException) {
            throw ProtocolException("registro inválido (adulterado, repetido ou fora de ordem)")
        } catch (e: javax.crypto.BadPaddingException) {
            throw ProtocolException("registro inválido")
        }
        counter++
        if (plain.isEmpty()) throw ProtocolException("registro vazio")
        return (plain[0].toInt() and 0xFF) to plain.copyOfRange(1, plain.size)
    }
}

data class Accept(val rate: Int, val channels: Int, val codec: Int, val session: ByteArray, val heartbeatMs: Int)

/** Canal de áudio autenticado e criptografado. */
class V3Channel private constructor(
    private val socket: Socket,
    private val reader: RecordReader,
    private val writer: RecordWriter,
    private val log: (String) -> Unit,
) {
    private val inp = DataInputStream(socket.getInputStream())
    private val out: OutputStream = socket.getOutputStream()
    private val sendLock = Any()
    @Volatile var lastRx = System.currentTimeMillis()
    @Volatile private var writeStart = 0L
    @Volatile var closed = false
        private set
    private val started = System.currentTimeMillis()

    companion object {
        /** Faz o desafio-resposta e devolve o canal cifrado. Dados: pid (8 bytes) e chave de pareamento (32 bytes). */
        fun connect(socket: Socket, pid: ByteArray, psk: ByteArray, log: (String) -> Unit = {}): V3Channel {
            val inp = DataInputStream(socket.getInputStream())
            val out = socket.getOutputStream()
            val cn = Crypto.random(Proto3.NONCE_LEN)
            out.write(Proto3.MAGIC + byteArrayOf(Proto3.VERSION.toByte()) + pid + cn)
            out.flush()
            val head = ByteArray(4)
            try {
                inp.readFully(head)
            } catch (e: EOFException) {
                throw AuthRejectedException()
            }
            if (head.contentEquals(Proto3.MAGIC_ERROR)) {
                val e = ByteArray(2)
                inp.readFully(e)
                throw UnsupportedVersionException(e[1].toInt() and 0xFF)
            }
            if (!head.contentEquals(Proto3.MAGIC_SERVER)) throw ProtocolException("resposta inválida do PC")
            inp.readUnsignedByte() // versão máxima do servidor
            val sn = ByteArray(Proto3.NONCE_LEN)
            inp.readFully(sn)
            val ak = Crypto.authKey(psk)
            out.write(Crypto.clientProof(ak, pid, cn, sn))
            out.flush()
            val sp = ByteArray(32)
            try {
                inp.readFully(sp)
            } catch (e: EOFException) {
                throw AuthRejectedException() // o PC fecha sem responder quando a prova não confere
            }
            // o PC também precisa provar que conhece a chave (evita um impostor na rede)
            if (!Crypto.constantTimeEquals(sp, Crypto.serverProof(ak, pid, cn, sn))) {
                throw ProtocolException("o PC não provou conhecer a chave de pareamento")
            }
            val (kc2s, ks2c) = Crypto.sessionKeys(psk, pid, cn, sn)
            return V3Channel(socket, RecordReader(ks2c), RecordWriter(kc2s), log)
        }
    }

    fun send(type: Int, payload: ByteArray = ByteArray(0), off: Int = 0, len: Int = payload.size - off) {
        synchronized(sendLock) {
            writeStart = System.currentTimeMillis()
            try {
                out.write(writer.seal(type, payload, off, len))
            } finally {
                writeStart = 0
            }
        }
    }

    fun sendAudio(buf: ByteArray, off: Int, len: Int) = send(Proto3.T_AUDIO, buf, off, len)

    private fun readRecord(): Pair<Int, ByteArray> {
        val header = ByteArray(2)
        inp.readFully(header)
        val n = ((header[0].toInt() and 0xFF) shl 8) or (header[1].toInt() and 0xFF)
        if (n < Proto3.TAG_LEN + 1 || n > Proto3.MAX_PLAINTEXT + Proto3.TAG_LEN) {
            throw ProtocolException("tamanho de registro inválido: $n")
        }
        val ct = ByteArray(n)
        inp.readFully(ct)
        val r = reader.open(header, ct)
        lastRx = System.currentTimeMillis()
        return r
    }

    /** Envia o pedido e lê a resposta (aceite ou recusa). */
    fun request(mode: Int, codecMask: Int, deviceName: String): Accept {
        val nm = deviceName.toByteArray(Charsets.UTF_8).let { if (it.size > Proto3.MAX_NAME) it.copyOf(Proto3.MAX_NAME) else it }
        send(Proto3.T_REQUEST, byteArrayOf(mode.toByte(), codecMask.toByte(), nm.size.toByte()) + nm)
        val (type, p) = readRecord()
        when (type) {
            Proto3.T_ACCEPT -> {
                if (p.size < 16) throw ProtocolException("resposta de aceite malformada")
                val rate = ((p[0].toInt() and 0xFF) shl 24) or ((p[1].toInt() and 0xFF) shl 16) or
                    ((p[2].toInt() and 0xFF) shl 8) or (p[3].toInt() and 0xFF)
                val hb = ((p[14].toInt() and 0xFF) shl 8) or (p[15].toInt() and 0xFF)
                return Accept(rate, p[4].toInt() and 0xFF, p[5].toInt() and 0xFF, p.copyOfRange(6, 14), hb)
            }
            Proto3.T_REJECT -> throw RejectedException(
                if (p.isNotEmpty()) p[0].toInt() and 0xFF else 0,
                if (p.size > 1) String(p, 1, p.size - 1, Charsets.UTF_8) else ""
            )
            else -> throw ProtocolException("resposta inesperada do PC: $type")
        }
    }

    /** Próximo bloco de áudio; consome heartbeats e levanta exceção se o PC encerrar. */
    fun readAudio(): ByteArray {
        while (true) {
            val (type, p) = readRecord()
            when (type) {
                Proto3.T_AUDIO -> return p
                Proto3.T_HEARTBEAT -> Unit
                Proto3.T_CLOSE -> throw IOException("o PC encerrou a sessão: " + String(p, Charsets.UTF_8))
                else -> throw ProtocolException("registro inesperado: $type")
            }
        }
    }

    /** Heartbeat para o PC e vigia: sem sinal do PC (ou escrita presa) = conexão morta. */
    fun startKeepalive() {
        lastRx = System.currentTimeMillis()
        Thread {
            var next = System.currentTimeMillis()
            while (!closed) {
                try {
                    Thread.sleep(250)
                } catch (_: InterruptedException) {
                    return@Thread
                }
                val now = System.currentTimeMillis()
                val w = writeStart
                if (now - lastRx > Proto3.HEARTBEAT_TIMEOUT_MS || (w != 0L && now - w > Proto3.WRITE_STUCK_MS)) {
                    log("sem sinal do PC")
                    close()
                    return@Thread
                }
                if (now >= next) {
                    next = now + Proto3.HEARTBEAT_INTERVAL_MS
                    try {
                        send(Proto3.T_HEARTBEAT)
                    } catch (_: Exception) {
                        close()
                        return@Thread
                    }
                }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    /** Modo microfone: o celular só envia áudio, mas precisa ler os heartbeats do PC. */
    fun startDrain() {
        Thread {
            try {
                while (!closed) {
                    val (type, _) = readRecord()
                    if (type == Proto3.T_CLOSE) break
                }
            } catch (_: Exception) {
            }
            close()
        }.also { it.isDaemon = true; it.start() }
    }

    fun close() {
        if (closed) return
        closed = true
        try {
            socket.shutdownInput()
        } catch (_: Exception) {
        }
        try {
            socket.close()
        } catch (_: Exception) {
        }
    }
}


/** O PC recusou (ou deixou expirar) o pedido de pareamento. */
class PairingDeniedException(val reason: String) : IOException(reason)

/** O PC fechou a conexão ao receber o pedido: versão antiga ou pedidos de pareamento desativados. */
class PairingUnsupportedException : IOException("o PC não aceita pedidos de pareamento")

class PairingResult(val pid: ByteArray, val secret: ByteArray, val pcid: String, val pcName: String)

/**
 * Pareamento com confirmação no PC (sem QR code): ECDH P-256 efêmero + código de 6 dígitos que o PC e o celular
 * mostram. O usuário confere os códigos e aprova no PC; se alguém estiver no meio da conversa, os códigos diferem.
 */
object PairingClient {
    private fun fixed32(v: BigInteger): ByteArray {
        val raw = v.toByteArray()
        val out = ByteArray(32)
        val n = minOf(raw.size, 32)
        System.arraycopy(raw, raw.size - n, out, 32 - n, n)
        return out
    }

    private fun encodePoint(p: ECPoint) = byteArrayOf(4) + fixed32(p.affineX) + fixed32(p.affineY)

    /** [onCode] recebe o código de 6 dígitos para mostrar na tela enquanto espera a aprovação no PC. */
    fun request(socket: Socket, deviceName: String, onCode: (String) -> Unit): PairingResult {
        val inp = DataInputStream(socket.getInputStream())
        val out = socket.getOutputStream()
        val gen = KeyPairGenerator.getInstance("EC")
        gen.initialize(ECGenParameterSpec("secp256r1"))
        val kp = gen.generateKeyPair()
        val pub = kp.public as ECPublicKey
        val cpub = encodePoint(pub.w)
        val nm = deviceName.toByteArray(Charsets.UTF_8).let { if (it.size > Proto3.MAX_NAME) it.copyOf(Proto3.MAX_NAME) else it }
        out.write(Proto3.MAGIC_PAIR + byteArrayOf(Proto3.VERSION.toByte()) + cpub + byteArrayOf(nm.size.toByte()) + nm)
        out.flush()

        val head = ByteArray(4)
        try {
            inp.readFully(head)
        } catch (e: EOFException) {
            throw PairingUnsupportedException()
        }
        if (!head.contentEquals(Proto3.MAGIC_PAIR_SERVER)) throw ProtocolException("resposta inválida do PC")
        val spub = ByteArray(Proto3.PAIR_PUB_LEN)
        inp.readFully(spub)
        if (spub[0].toInt() != 4) throw ProtocolException("chave pública inválida")

        val peerPoint = ECPoint(BigInteger(1, spub.copyOfRange(1, 33)), BigInteger(1, spub.copyOfRange(33, 65)))
        val peer = try {
            KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(peerPoint, pub.params))
        } catch (e: Exception) {
            throw ProtocolException("chave pública inválida")
        }
        val ka = KeyAgreement.getInstance("ECDH")
        ka.init(kp.private)
        ka.doPhase(peer, true)
        val shared = ka.generateSecret()
        val salt = cpub + spub
        val okm = Crypto.hkdf(shared, salt, "PBv3 pair".toByteArray(), 64)
        val sas = (ByteBuffer.wrap(Crypto.hkdf(shared, salt, "PBv3 sas".toByteArray(), 4)).int.toLong() and 0xFFFFFFFFL) % 1_000_000L
        onCode("%06d".format(sas))

        val reader = RecordReader(okm.copyOfRange(32, 64)) // PC -> celular
        socket.soTimeout = Proto3.PAIR_WAIT_MS
        val header = ByteArray(2)
        try {
            inp.readFully(header)
        } catch (e: EOFException) {
            throw PairingDeniedException("conexão encerrada pelo PC")
        }
        val n = ((header[0].toInt() and 0xFF) shl 8) or (header[1].toInt() and 0xFF)
        if (n < Proto3.TAG_LEN + 1 || n > Proto3.MAX_PLAINTEXT + Proto3.TAG_LEN) throw ProtocolException("tamanho inválido: $n")
        val ct = ByteArray(n)
        inp.readFully(ct)
        val (type, p) = reader.open(header, ct)
        when (type) {
            Proto3.T_PAIR_GRANT -> {
                if (p.size < 8 + 32 + 8) throw ProtocolException("resposta de pareamento malformada")
                val pcid = p.copyOfRange(40, 48).joinToString("") { "%02x".format(it) }
                return PairingResult(
                    p.copyOfRange(0, 8), p.copyOfRange(8, 40), pcid, String(p, 48, p.size - 48, Charsets.UTF_8)
                )
            }
            Proto3.T_PAIR_DENY -> throw PairingDeniedException(String(p, Charsets.UTF_8))
            else -> throw ProtocolException("resposta inesperada: $type")
        }
    }
}
