package com.phonebridge

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Um PC pareado. [pid] identifica este celular no PC; [secret] é a chave de pareamento de 256 bits. */
class PairedPc(
    val pcid: String,
    val name: String,
    val host: String,
    val port: Int,
    val pid: String,
    val secret: ByteArray,
    val lastUsed: Long,
)

/**
 * Guarda os PCs pareados. As chaves de pareamento ficam cifradas com uma chave AES-GCM do Android Keystore
 * (que não sai do aparelho); o restante (nome, IP) fica em texto no armazenamento privado do app.
 */
class PairingStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("pairings", Context.MODE_PRIVATE)

    companion object {
        private const val ALIAS = "phonebridge_pairing_key"
        private const val KEY = "list"
    }

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }

    private fun encrypt(plain: ByteArray): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        return Base64.encodeToString(c.iv + c.doFinal(plain), Base64.NO_WRAP)
    }

    private fun decrypt(text: String): ByteArray? = try {
        val raw = Base64.decode(text, Base64.NO_WRAP)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, 12))
        c.doFinal(raw, 12, raw.size - 12)
    } catch (_: Exception) {
        null // chave do Keystore perdida (restauração de backup, troca de aparelho): o pareamento precisa ser refeito
    }

    @Synchronized
    fun all(): List<PairedPc> {
        val arr = try { JSONArray(prefs.getString(KEY, "[]")) } catch (_: Exception) { JSONArray() }
        val list = ArrayList<PairedPc>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val secret = decrypt(o.optString("secret")) ?: continue
            if (secret.size != 32 || o.optString("pid").length != 16) continue
            list.add(
                PairedPc(
                    o.optString("pcid"), o.optString("name"), o.optString("host"), o.optInt("port", 5800),
                    o.optString("pid"), secret, o.optLong("last", 0)
                )
            )
        }
        return list.sortedByDescending { it.lastUsed }
    }

    @Synchronized
    private fun write(list: List<PairedPc>) {
        val arr = JSONArray()
        for (p in list) {
            arr.put(
                JSONObject().put("pcid", p.pcid).put("name", p.name).put("host", p.host).put("port", p.port)
                    .put("pid", p.pid).put("secret", encrypt(p.secret)).put("last", p.lastUsed)
            )
        }
        prefs.edit().putString(KEY, arr.toString()).apply()
    }

    /** Salva (ou substitui, se o mesmo PC for pareado de novo). */
    fun save(pc: PairedPc) = write(all().filter { it.pcid != pc.pcid } + pc)

    fun remove(pcid: String) = write(all().filter { it.pcid != pcid })

    fun findByPcid(pcid: String) = all().firstOrNull { it.pcid == pcid }

    fun findByHost(host: String) = all().firstOrNull { it.host == host }

    /** Atualiza o IP/porta (o roteador pode ter dado outro endereço ao PC) e marca como usado agora. */
    fun touch(pcid: String, host: String, port: Int) {
        val pc = findByPcid(pcid) ?: return
        save(PairedPc(pc.pcid, pc.name, host, port, pc.pid, pc.secret, System.currentTimeMillis()))
    }
}

/** Dados do QR code de pareamento: phonebridge://IP:PORTA?v=3&id=<pcid>&pid=<id>&k=<chave>&n=<nome> */
class PairingLink(val host: String, val port: Int, val pcid: String, val pid: String, val secret: ByteArray, val name: String) {
    companion object {
        /** Retorna null se não for um link v3 válido. */
        fun parse(text: String): PairingLink? {
            return try {
                val uri = java.net.URI(text.trim())
                if (uri.scheme != "phonebridge" || uri.host.isNullOrEmpty()) return null
                val params = (uri.rawQuery ?: "").split("&").mapNotNull {
                    val i = it.indexOf('=')
                    if (i <= 0) null else it.substring(0, i) to java.net.URLDecoder.decode(it.substring(i + 1), "UTF-8")
                }.toMap()
                if (params["v"] != "3") return null
                val pid = params["pid"] ?: return null
                val pcid = params["id"] ?: return null
                val k = Base64.decode(params["k"] ?: return null, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
                if (pid.length != 16 || pcid.length != 16 || k.size != 32) return null
                PairingLink(uri.host, if (uri.port > 0) uri.port else 5800, pcid, pid, k, params["n"] ?: uri.host)
            } catch (_: Exception) {
                null
            }
        }
    }
}
