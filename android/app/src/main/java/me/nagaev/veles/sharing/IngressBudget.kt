package me.nagaev.veles.sharing

/** Bounds copied callback data before it reaches the main looper (<= 64 KiB). */
internal class IngressBudget {
    private val pending = mutableMapOf<String, Int>()
    private var closed = false

    @Synchronized fun acquire(id: String): Boolean {
        val count = pending[id] ?: 0
        if (closed || count >= PER_PEER || (count == 0 && pending.size >= PEERS)) return false
        pending[id] = count + 1
        return true
    }

    @Synchronized fun release(id: String) {
        val count = pending[id] ?: return
        if (count <= 1) pending.remove(id) else pending[id] = count - 1
    }

    @Synchronized fun close() {
        closed = true
        pending.clear()
    }
    companion object {
        private const val PER_PEER = 16
        private const val PEERS = 8
    }
}
