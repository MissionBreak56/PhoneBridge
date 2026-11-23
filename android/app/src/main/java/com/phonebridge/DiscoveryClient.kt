package com.phonebridge

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.NetworkInterface

/** Procura PCs com o PhoneBridge na rede local (UDP broadcast, porta 5801). */
object DiscoveryClient {
    private const val PORT = 5801
    private val REQUEST = "PBFIND1".toByteArray(Charsets.US_ASCII)

    data class Host(val name: String, val ip: String, val port: Int, val opus: Boolean, val pcid: String = "")

    fun scan(timeoutMs: Int = 1500): List<Host> {
        val found = LinkedHashMap<String, Host>()
        val socket = DatagramSocket()
        try {
            socket.broadcast = true
            socket.soTimeout = 250
            for (addr in broadcastAddresses()) {
                try {
                    socket.send(DatagramPacket(REQUEST, REQUEST.size, addr, PORT))
                } catch (_: Exception) {
                }
            }
            val deadline = System.currentTimeMillis() + timeoutMs
            val buf = ByteArray(512)
            while (System.currentTimeMillis() < deadline) {
                val pkt = DatagramPacket(buf, buf.size)
                try {
                    socket.receive(pkt)
                } catch (_: java.net.SocketTimeoutException) {
                    continue
                }
                parse(String(pkt.data, 0, pkt.length, Charsets.UTF_8), pkt.address.hostAddress ?: continue)
                    ?.let { found[it.ip] = it }
            }
        } finally {
            socket.close()
        }
        return found.values.toList()
    }

    /** "PBHERE1|nome|porta|opus|proto|pcid" (os dois últimos só existem a partir da versão 3 do PC) */
    internal fun parse(text: String, ip: String): Host? {
        val p = text.split("|")
        if (p.size < 4 || p[0] != "PBHERE1") return null
        val port = p[2].toIntOrNull() ?: return null
        return Host(p[1], ip, port, p[3] == "1", if (p.size >= 6) p[5] else "")
    }

    private fun broadcastAddresses(): List<InetAddress> {
        val list = ArrayList<InetAddress>()
        list.add(InetAddress.getByName("255.255.255.255"))
        try {
            for (nif in NetworkInterface.getNetworkInterfaces()) {
                if (!nif.isUp || nif.isLoopback) continue
                for (ia in nif.interfaceAddresses) {
                    ia.broadcast?.let { list.add(it) }
                }
            }
        } catch (_: Exception) {
        }
        return list.distinct()
    }
}
