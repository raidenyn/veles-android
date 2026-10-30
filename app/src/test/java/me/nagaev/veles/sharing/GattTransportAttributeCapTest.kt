package me.nagaev.veles.sharing

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** OTP-23 PoC: experimental attribute-cap wire behavior, only active via the injected constructor. */
class GattTransportAttributeCapTest {
    private var now = 0L

    private fun pair(attributeLimit: Int): Pair<GattTransport, GattTransport> {
        val sender = GattTransport(attributeLimit) { now }
        val receiver = GattTransport(attributeLimit) { now }
        for (transport in listOf(sender, receiver)) {
            assertTrue(transport.connect("a"))
            assertTrue(transport.setMtu("a", 517))
        }
        return sender to receiver
    }

    private fun roundTrip(sender: GattTransport, receiver: GattTransport, record: ByteArray, sizes: List<Int>) {
        assertTrue(sender.enqueue("a", record))
        var actual: ByteArray? = null
        var count = 0
        while (true) {
            val frame = sender.nextFragment("a") ?: break
            assertEquals(sizes[count], frame.size)
            count++
            actual = receiver.receive("a", frame).record ?: actual
            sender.notificationComplete("a", true)
        }
        assertEquals(sizes.size, count)
        assertArrayEquals(record, actual)
    }

    @Test fun pocMtu517ChunksToExactly508BytePayloads() {
        val (sender, receiver) = pair(512)
        roundTrip(
            sender,
            receiver,
            ByteArray(4096) { (it * 17).toByte() },
            listOf(512, 512, 512, 512, 512, 512, 512, 512, 36),
        )
    }

    @Test fun pocPayloadBoundary508FitsAnd509NeedsTwoFragments() {
        val (sender, receiver) = pair(512)
        roundTrip(sender, receiver, ByteArray(508) { (it * 3).toByte() }, listOf(512))
        val (sender2, receiver2) = pair(512)
        roundTrip(sender2, receiver2, ByteArray(509) { (it * 3).toByte() }, listOf(512, 5))
    }

    @Test fun pocOverCapInboundFramesAreRejectedAndClearPartialsAtMtu517() {
        for (payload in listOf(509, 510, 511)) {
            val receiver = GattTransport(512) { now }
            assertTrue(receiver.connect("a"))
            assertTrue(receiver.setMtu("a", 517))
            val start = byteArrayOf(0, 7, 0x01, 0xFE.toByte()) + ByteArray(payload)
            assertNull("payload $payload", receiver.receive("a", start).record)
            val lateContinuation = byteArrayOf(0x81.toByte(), 7, 0, 0, 9)
            assertNull("payload $payload", receiver.receive("a", lateContinuation).record)
        }
        val receiver = GattTransport(512) { now }
        assertTrue(receiver.connect("a"))
        assertTrue(receiver.setMtu("a", 517))
        val exactCap = byteArrayOf(0, 7, 0x03, 0xF8.toByte()) + ByteArray(508)
        assertNull(exactCap.let { receiver.receive("a", it).record })
        assertEquals(
            1016,
            receiver.receive("a", byteArrayOf(0x81.toByte(), 7, 0, 0) + ByteArray(508)).record?.size,
        )
    }

    @Test fun pocCap512LeavesMtu185FrozenBehaviorIntact() {
        val sender = GattTransport(512) { now }
        val receiver = GattTransport(512) { now }
        for (transport in listOf(sender, receiver)) {
            assertTrue(transport.connect("a"))
            assertTrue(transport.setMtu("a", 185))
        }
        roundTrip(
            sender,
            receiver,
            ByteArray(4096) { (it * 17).toByte() },
            List(23) { 182 } + listOf(6),
        )
    }

    @Test fun pocDefaultAttributeLimitKeepsFrozenFullMtuBehavior() {
        val sender = GattTransport { now }
        val receiver = GattTransport { now }
        for (transport in listOf(sender, receiver)) {
            assertTrue(transport.connect("a"))
            assertTrue(transport.setMtu("a", 517))
        }
        roundTrip(
            sender,
            receiver,
            ByteArray(4096) { (it * 17).toByte() },
            listOf(514, 514, 514, 514, 514, 514, 514, 514, 20),
        )
    }

    @Test fun pocOverCapDiscriminator514DeliversAnd512Rejects() {
        for ((limit, expected) in listOf(514 to 511, 512 to null)) {
            val receiver = GattTransport(limit) { now }
            assertTrue(receiver.connect("a"))
            assertTrue(receiver.setMtu("a", 517))
            // frame of 514 bytes: declared total 511, non-final payload 510 + final payload 1.
            val start = byteArrayOf(0, 7, 0x01, 0xFF.toByte()) + ByteArray(510)
            assertNull(receiver.receive("a", start).record)
            val record = receiver.receive("a", byteArrayOf(0x81.toByte(), 7, 0, 0, 9)).record
            assertEquals("limit $limit", expected, record?.size)
            // A fresh transaction, sized for this limit's own chunk budget, still completes.
            val budget = minOf(514, limit) - 4
            val fresh = byteArrayOf(0, 9, 0x01, budget.toByte()) + ByteArray(budget)
            assertNull(receiver.receive("a", fresh).record)
            assertEquals(
                budget,
                receiver.receive("a", byteArrayOf(0x81.toByte(), 9, 0, 0)).record?.size,
            )
        }
    }
}
