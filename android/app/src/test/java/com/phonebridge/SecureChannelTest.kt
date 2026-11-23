package com.phonebridge

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
private fun ByteArray.toHex() = joinToString("") { "%02x".format(it) }

/** Os vetores abaixo foram gerados pela implementação em Python (pc/security.py): garantem que os dois lados concordam. */
class SecureChannelTest {
    private val psk = ByteArray(32) { it.toByte() }
    private val pid = ByteArray(8) { (it + 1).toByte() }
    private val cn = ByteArray(16) { it.toByte() }
    private val sn = ByteArray(16) { (it + 16).toByte() }

    @Test
    fun hkdfMatchesRfc5869Case1() {
        val okm = Crypto.hkdf(ByteArray(22) { 0x0B }, ByteArray(13) { it.toByte() }, ByteArray(10) { (0xF0 + it).toByte() }, 42)
        assertEquals("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865", okm.toHex())
    }

    @Test
    fun keysAndProofsMatchPython() {
        val ak = Crypto.authKey(psk)
        assertEquals("20052c274d358328175a08e0be0a2423237900c5ce10ce2b4c0630628a4f9bee", ak.toHex())
        assertEquals("1247437dcc5ad05ea0cc4cf3feb61a3ab51d03f781f909e91abdd93c0e149e3a", Crypto.clientProof(ak, pid, cn, sn).toHex())
        assertEquals("402aa765f16d82946e9ed0ab87fb9a81c40ecdd62f8c44f5b654b7719855b56a", Crypto.serverProof(ak, pid, cn, sn).toHex())
        val (c2s, s2c) = Crypto.sessionKeys(psk, pid, cn, sn)
        assertEquals("9485165d03646237004be6499ee396ce282830e18bd34b58db2d78351636dabf", c2s.toHex())
        assertEquals("5608a7d10c5ac674c4f671c707d41418069923c867877356733bb1aa48bb7751", s2c.toHex())
    }

    @Test
    fun sealedRecordMatchesPython() {
        val (c2s, _) = Crypto.sessionKeys(psk, pid, cn, sn)
        val rec = RecordWriter(c2s).seal(Proto3.T_AUDIO, "PhoneBridge".toByteArray())
        assertEquals("001c24aa38845221843ecc8bbc729224317b442b5c3c799d9a5a2605b16d", rec.toHex())
    }

    @Test
    fun pythonRecordCanBeOpened() {
        val (c2s, _) = Crypto.sessionKeys(psk, pid, cn, sn)
        val rec = hex("001c24aa38845221843ecc8bbc729224317b442b5c3c799d9a5a2605b16d")
        val (type, payload) = RecordReader(c2s).open(rec.copyOfRange(0, 2), rec.copyOfRange(2, rec.size))
        assertEquals(Proto3.T_AUDIO, type)
        assertEquals("PhoneBridge", String(payload))
    }

    @Test
    fun roundTripWithOffsets() {
        val key = Crypto.random(32)
        val w = RecordWriter(key)
        val r = RecordReader(key)
        val data = ByteArray(100) { it.toByte() }
        for (i in 0 until 5) {
            val rec = w.seal(Proto3.T_AUDIO, data, 10, 50)
            val (t, p) = r.open(rec.copyOfRange(0, 2), rec.copyOfRange(2, rec.size))
            assertEquals(Proto3.T_AUDIO, t)
            assertArrayEquals(data.copyOfRange(10, 60), p)
        }
    }

    private fun expectRejected(r: RecordReader, rec: ByteArray) {
        try {
            r.open(rec.copyOfRange(0, 2), rec.copyOfRange(2, rec.size))
            fail("deveria ter sido recusado")
        } catch (_: ProtocolException) {
        }
    }

    @Test
    fun tamperReplayAndReorderAreRejected() {
        val key = Crypto.random(32)
        val w = RecordWriter(key)
        val r = RecordReader(key)
        val a = w.seal(Proto3.T_AUDIO, byteArrayOf(1))
        val b = w.seal(Proto3.T_AUDIO, byteArrayOf(2))
        expectRejected(r, b) // fora de ordem
        r.open(a.copyOfRange(0, 2), a.copyOfRange(2, a.size))
        expectRejected(r, a) // repetido
        val bad = b.copyOf().also { it[it.size - 1] = (it[it.size - 1].toInt() xor 1).toByte() }
        expectRejected(r, bad) // adulterado
        val wrongHeader = b.copyOf().also { it[1] = (it[1].toInt() + 1).toByte() }
        expectRejected(r, wrongHeader) // o tamanho (cabeçalho) também é autenticado
    }

    @Test
    fun oversizeRecordIsRefusedByWriter() {
        try {
            RecordWriter(Crypto.random(32)).seal(Proto3.T_AUDIO, ByteArray(Proto3.MAX_PLAINTEXT))
            fail("deveria recusar")
        } catch (_: ProtocolException) {
        }
    }

    @Test
    fun jitterQueueKeepsOnlyRecentAudio() {
        val q = JitterQueue(60)
        for (i in 0 until 10) q.put(byteArrayOf(i.toByte()), 1, 10) // 100 ms chegaram de uma vez
        assertEquals(60, q.queuedMs())
        assertEquals(4L, q.dropped)
        assertEquals(4, q.take(10)!!.data[0].toInt()) // os mais antigos foram descartados
        assertEquals(50, q.queuedMs())
    }

    @Test
    fun jitterQueueTimesOutWhenEmptyAndNeverDropsTheOnlyItem() {
        val q = JitterQueue(10)
        assertNull(q.take(20))
        q.put(ByteArray(4), 4, 20) // maior que o limite, mas é o único bloco
        assertTrue(q.take(20) != null)
        assertEquals(0L, q.dropped)
    }
}
