package me.nagaev.veles.bluetoothspike

internal class SpikeDeliveryQueues {
    private val queues = mutableMapOf<String, ArrayDeque<ByteArray>>()

    @Synchronized
    fun enqueue(clientId: String, frames: List<ByteArray>): Boolean {
        val queue = queues.getOrPut(clientId) { ArrayDeque() }
        val wasEmpty = queue.isEmpty()
        frames.forEach { frame -> queue.addLast(frame.copyOf()) }
        return wasEmpty
    }

    @Synchronized
    fun peek(clientId: String): ByteArray? = queues[clientId]?.firstOrNull()?.copyOf()

    @Synchronized
    fun complete(clientId: String): ByteArray? {
        val queue = queues[clientId] ?: return null
        if (queue.isNotEmpty()) queue.removeFirst()
        return queue.firstOrNull()?.copyOf()
    }

    @Synchronized
    fun remove(clientId: String) {
        queues.remove(clientId)
    }

    @Synchronized
    fun clear() {
        queues.clear()
    }
}
