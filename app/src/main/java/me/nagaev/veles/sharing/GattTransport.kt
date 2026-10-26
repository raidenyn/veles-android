@file:Suppress("MagicNumber") // Frozen wire offsets and resource limits: OTP2-GAT-001..013.

package me.nagaev.veles.sharing

/**
 * Byte transport only. No OTP, identity, authentication or application acknowledgement semantics.
 *
 * Fragment budget (OTP2-GAT-010, profile revision 2), identical in both directions: a frame is a
 * 4-byte header plus payload and may never exceed `min(mtu - 3, 512)` bytes, the ATT maximum attribute
 * value. Every non-final fragment carries exactly `attributeBudget - 4` payload bytes, the final one
 * exactly what remains. [attributeBudget] is the only place that formula is written.
 */
internal class GattTransport(private val clock: () -> Long) {
    data class Received(val record: ByteArray? = null, val disconnect: Boolean = false)
    private class Partial(val id: Int, val total: Int, var touched: Long) {
        val bytes = ByteArray(total)
        var used = 0
        var index = 0
    }
    private class Peer {
        var mtu = 0
        val partials = mutableMapOf<Int, Partial>()
        val conflicts = ArrayDeque<Long>()
        val queue = ArrayDeque<ByteArray>()
        var offset = 0
        var index = 0
        var transaction = 1
        var awaiting = false
        var sentAt = 0L
        var sentBytes = 0
    }
    private val peers = mutableMapOf<String, Peer>()

    companion object {
        const val MIN_MTU = 185
        const val MAX_MTU = 517
        const val ATT_OVERHEAD = 3
        const val ATTRIBUTE_VALUE_MAX = 512
        const val HEADER = 4

        /** Largest fragment frame (header + payload) allowed on a link negotiating [mtu]. */
        fun attributeBudget(mtu: Int): Int = minOf(mtu - ATT_OVERHEAD, ATTRIBUTE_VALUE_MAX)

        /** Payload carried by every non-final fragment on a link negotiating [mtu]. */
        fun payloadBudget(mtu: Int): Int = attributeBudget(mtu) - HEADER
    }

    @Synchronized fun connect(id: String): Boolean {
        if (id in peers) return false
        if (peers.size >= 8) return false
        peers[id] = Peer()
        return true
    }

    @Synchronized fun setMtu(id: String, mtu: Int): Boolean {
        if (mtu !in MIN_MTU..MAX_MTU) {
            disconnect(id)
            return false
        }
        val peer = peers[id] ?: return false
        if (peer.mtu != 0 && peer.mtu != mtu) {
            disconnect(id)
            return false
        }
        peer.mtu = mtu
        return true
    }

    @Synchronized fun disconnect(id: String) {
        peers.remove(id)?.let { peer ->
            peer.partials.values.forEach { it.bytes.fill(0) }
            peer.queue.forEach { it.fill(0) }
        }
    }

    @Synchronized fun clear() = peers.keys.toList().forEach(::disconnect)

    @Synchronized fun expire(): List<String> {
        val stalled = mutableListOf<String>()
        peers.forEach { (id, peer) ->
            expirePartials(peer)
            if (peer.awaiting && clock() - peer.sentAt >= 5000) stalled.add(id)
        }
        stalled.forEach(::disconnect)
        return stalled
    }

    @Suppress("ReturnCount") // Parser rejects invalid input before allocation/copy.
    @Synchronized
    fun receive(id: String, frame: ByteArray): Received {
        val peer = peers[id] ?: return Received()
        if (peer.mtu == 0) return Received()
        if (frame.size !in HEADER..attributeBudget(peer.mtu)) return reject(peer)
        val index = frame[0].toInt() and 127
        val final = frame[0].toInt() and 128 != 0
        val transaction = frame[1].toInt() and 255
        val length = ((frame[2].toInt() and 255) shl 8) or (frame[3].toInt() and 255)
        if (transaction == 0) return reject(peer)
        peers.values.forEach(::expirePartials)
        if (index == 0 && transaction in peer.partials) return conflict(id, peer)
        if (index == 0) {
            if (length !in 1..4096) return reject(peer)
            if (!canAllocate(peer, length)) return Received()
            peer.partials[transaction] = Partial(transaction, length, clock())
        }
        val partial = peer.partials[transaction] ?: return Received()
        val size = frame.size - HEADER
        val ordered = partial.id == transaction && partial.index == index && (index == 0 || length == 0)
        val remaining = partial.total - partial.used
        val validSize = validPayload(final, size, remaining, payloadBudget(peer.mtu), index)
        if (!ordered || !validSize) {
            return reject(peer)
        }
        frame.copyInto(partial.bytes, partial.used, HEADER)
        partial.used += size
        partial.index++
        partial.touched = clock()
        if (!final) return Received()
        peer.partials.remove(transaction)
        return Received(record = partial.bytes)
    }

    private fun validPayload(
        final: Boolean,
        size: Int,
        remaining: Int,
        budget: Int,
        index: Int,
    ): Boolean = if (final) size == remaining else size == budget && index < 127 && size <= remaining

    private fun canAllocate(peer: Peer, length: Int): Boolean = peer.partials.size < 2 &&
        peer.partials.values.sumOf { it.total } + length <= 8192 &&
        peers.values.sumOf { it.partials.size } < 16 &&
        peers.values.sumOf { p -> p.partials.values.sumOf { it.total } } + length <= 65536

    private fun expirePartials(peer: Peer) {
        val expired = peer.partials.filterValues { clock() - it.touched >= 5000 }.keys
        expired.toList().forEach { peer.partials.remove(it)?.bytes?.fill(0) }
    }

    private fun conflict(id: String, peer: Peer): Received {
        while (peer.conflicts.firstOrNull()?.let { clock() - it >= 60000 } == true) {
            peer.conflicts.removeFirst()
        }
        peer.conflicts.addLast(clock())
        val disconnect = peer.conflicts.size >= 2
        if (disconnect) disconnect(id)
        return Received(disconnect = disconnect)
    }

    @Suppress("ReturnCount") // Independent per-peer and global ceilings.
    @Synchronized
    fun enqueue(id: String, record: ByteArray): Boolean {
        val peer = peers[id] ?: return false
        if (peer.mtu == 0 || record.size !in 1..4096) return false
        if (peer.queue.size >= 16 || peer.queue.sumOf { it.size } + record.size > 16384) return false
        if (peers.values.sumOf { it.queue.size } >= 128 ||
            peers.values.sumOf { p -> p.queue.sumOf { it.size } } + record.size > 131072
        ) {
            return false
        }
        peer.queue.addLast(record.copyOf())
        return true
    }

    @Synchronized fun nextFragment(id: String): ByteArray? {
        val peer = peers[id] ?: return null
        val record = peer.queue.firstOrNull() ?: return null
        if (peer.awaiting) return null
        val count = minOf(payloadBudget(peer.mtu), record.size - peer.offset)
        val final = peer.offset + count == record.size
        val total = if (peer.index == 0) record.size else 0
        val header = byteArrayOf(
            (peer.index or if (final) 128 else 0).toByte(),
            peer.transaction.toByte(),
            (total shr 8).toByte(),
            total.toByte(),
        )
        peer.awaiting = true
        peer.sentAt = clock()
        peer.sentBytes = count
        return header + record.copyOfRange(peer.offset, peer.offset + count)
    }

    @Synchronized fun notificationComplete(id: String, success: Boolean) {
        val peer = peers[id] ?: return
        if (!success) {
            disconnect(id)
            return
        }
        if (!peer.awaiting) return
        peer.awaiting = false
        peer.offset += peer.sentBytes
        peer.index++
        if (peer.offset == peer.queue.first().size) {
            peer.queue.removeFirst().fill(0)
            peer.offset = 0
            peer.index = 0
            peer.transaction = peer.transaction % 255 + 1
        }
    }

    private fun reject(peer: Peer): Received {
        peer.partials.values.forEach { it.bytes.fill(0) }
        peer.partials.clear()
        return Received()
    }
}
