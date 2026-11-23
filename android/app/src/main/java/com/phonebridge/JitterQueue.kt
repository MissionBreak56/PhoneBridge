package com.phonebridge

import java.util.ArrayDeque
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Fila de áudio limitada por DURAÇÃO (não por bytes disponíveis no socket): se o Wi-Fi atrasar e depois
 * entregar tudo de uma vez, descarta os blocos mais antigos e mantém no máximo [maxMs] de áudio.
 * Sem Android: roda e é testada na JVM.
 */
class JitterQueue(private val maxMs: Int) {
    class Item(val data: ByteArray, val len: Int, val ms: Int)

    private val lock = ReentrantLock()
    private val cond = lock.newCondition()
    private val q = ArrayDeque<Item>()
    private var totalMs = 0

    @Volatile var dropped = 0L
        private set

    fun put(data: ByteArray, len: Int, ms: Int) {
        lock.withLock {
            q.addLast(Item(data, len, ms))
            totalMs += ms
            while (totalMs > maxMs && q.size > 1) {
                totalMs -= q.removeFirst().ms
                dropped++
            }
            cond.signal()
        }
    }

    /** Próximo bloco, ou null se nada chegou em [timeoutMs]. */
    fun take(timeoutMs: Long): Item? = lock.withLock {
        var nanos = TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        while (q.isEmpty()) {
            if (nanos <= 0) return null
            nanos = cond.awaitNanos(nanos)
        }
        val item = q.removeFirst()
        totalMs -= item.ms
        item
    }

    fun queuedMs(): Int = lock.withLock { totalMs }
}
