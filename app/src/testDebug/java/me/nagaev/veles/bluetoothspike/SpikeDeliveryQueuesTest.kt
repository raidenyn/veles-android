package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class SpikeDeliveryQueuesTest {
    @Test
    fun `delivery completion advances only the matching client queue`() {
        val queues = SpikeDeliveryQueues()
        queues.enqueue("desktop-a", listOf(byteArrayOf(1), byteArrayOf(2)))
        queues.enqueue("desktop-b", listOf(byteArrayOf(9)))

        assertContentEquals(byteArrayOf(1), queues.peek("desktop-a"))
        assertNull(queues.complete("desktop-b"))
        assertContentEquals(byteArrayOf(2), queues.complete("desktop-a"))
    }

    @Test
    fun `enqueue returns true only when the client queue was empty before insertion`() {
        val queues = SpikeDeliveryQueues()

        assertTrue(queues.enqueue("desktop-a", listOf(byteArrayOf(1), byteArrayOf(2))))
        // Second enqueue on a non-empty queue does not signal a fresh send.
        assertFalse(queues.enqueue("desktop-a", listOf(byteArrayOf(3))))
        // A different client's first enqueue still signals.
        assertTrue(queues.enqueue("desktop-b", listOf(byteArrayOf(9))))
    }

    @Test
    fun `peek returns the current frame without removing it and null when empty`() {
        val queues = SpikeDeliveryQueues()

        assertNull(queues.peek("desktop-a"))
        queues.enqueue("desktop-a", listOf(byteArrayOf(1), byteArrayOf(2)))
        assertContentEquals(byteArrayOf(1), queues.peek("desktop-a"))
        // Repeated peeks return the same frame.
        assertContentEquals(byteArrayOf(1), queues.peek("desktop-a"))
    }

    @Test
    fun `complete returns null when the client queue is exhausted`() {
        val queues = SpikeDeliveryQueues()
        queues.enqueue("desktop-a", listOf(byteArrayOf(1)))

        // The only frame is dropped and no next frame remains.
        assertNull(queues.complete("desktop-a"))
        assertNull(queues.peek("desktop-a"))
        // Subsequent complete calls on an empty queue stay null.
        assertNull(queues.complete("desktop-a"))
    }

    @Test
    fun `queued frames cannot be mutated by callers`() {
        val queues = SpikeDeliveryQueues()
        val frame = byteArrayOf(1)
        queues.enqueue("desktop-a", listOf(frame))

        // Mutate the caller-side reference; the peeked frame must be unaffected.
        frame[0] = 99
        assertContentEquals(byteArrayOf(1), queues.peek("desktop-a"))
    }

    @Test
    fun `remove drops a single client queue and clear resets everything`() {
        val queues = SpikeDeliveryQueues()
        queues.enqueue("desktop-a", listOf(byteArrayOf(1)))
        queues.enqueue("desktop-b", listOf(byteArrayOf(2)))

        queues.remove("desktop-a")
        assertNull(queues.peek("desktop-a"))
        assertContentEquals(byteArrayOf(2), queues.peek("desktop-b"))

        queues.clear()
        assertNull(queues.peek("desktop-b"))
    }

    @Test
    fun `enqueue returns true again after a queue is fully drained`() {
        val queues = SpikeDeliveryQueues()
        assertTrue(queues.enqueue("desktop-a", listOf(byteArrayOf(1))))
        queues.complete("desktop-a")
        // After draining the queue is empty again, so the next enqueue signals.
        assertTrue(queues.enqueue("desktop-a", listOf(byteArrayOf(2))))
        assertContentEquals(byteArrayOf(2), queues.peek("desktop-a"))
    }
}
