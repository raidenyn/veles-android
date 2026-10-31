package me.nagaev.veles.sharing

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GattTransportTest {
    private var now = 0L
    private val transport = GattTransport { now }

    @Test fun twoIncompleteTransactionsSurviveAndThirdIsRefused() {
        transport.connect("a")
        transport.setMtu("a", 185)
        for (id in listOf(41, 42)) {
            transport.receive("a", byteArrayOf(0, id.toByte(), 0, 179.toByte()) + ByteArray(178))
        }
        assertNull(transport.receive("a", byteArrayOf(0x80.toByte(), 43, 0, 1, 9)).record)
        for (id in listOf(41, 42)) {
            assertEquals(179, transport.receive("a", byteArrayOf(0x81.toByte(), id.toByte(), 0, 0, 9)).record?.size)
        }
    }

    @Test fun messageCountLimitAndGlobalCapacityAreBounded() {
        repeat(8) { index ->
            val id = "$index"
            assertTrue(transport.connect(id))
            transport.setMtu(id, 185)
            repeat(16) { assertTrue(transport.enqueue(id, byteArrayOf(1))) }
            assertFalse(transport.enqueue(id, byteArrayOf(2)))
        }
        transport.clear()
        assertTrue(transport.connect("fresh"))
        transport.setMtu("fresh", 185)
        assertTrue(transport.enqueue("fresh", ByteArray(4096)))
    }

    @Test fun notificationTimeoutDropsOnlyStalledPeer() {
        for (id in listOf("a", "b")) {
            transport.connect(id)
            transport.setMtu(id, 185)
            transport.enqueue(id, byteArrayOf(1))
        }
        transport.nextFragment("a")
        now = 4999
        assertTrue(transport.expire().isEmpty())
        now = 5000
        assertEquals(listOf("a"), transport.expire())
        assertNotNull(transport.nextFragment("b"))
        assertTrue(transport.connect("a"))
    }

    @Test fun conflictWindowResetsAndOriginalTransactionRemainsUsable() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val first = byteArrayOf(0, 1, 0, 179.toByte()) + ByteArray(178)
        transport.receive("a", first)
        assertFalse(transport.receive("a", first).disconnect)
        val last = byteArrayOf(0x81.toByte(), 1, 0, 0, 9)
        assertEquals(179, transport.receive("a", last).record?.size)
        now = 60000
        transport.receive("a", first)
        assertFalse(transport.receive("a", first).disconnect)
        assertEquals(179, transport.receive("a", last).record?.size)
    }

    @Test fun allRecordSizesRoundTripAtBothSupportedMtuExtremes() {
        for (mtu in listOf(185, 517)) {
            val sender = GattTransport { now }
            val receiver = GattTransport { now }
            sender.connect("a")
            receiver.connect("a")
            sender.setMtu("a", mtu)
            receiver.setMtu("a", mtu)
            for (size in listOf(1, GattTransport.payloadBudget(mtu), GattTransport.payloadBudget(mtu) + 1, 4096)) {
                val expected = ByteArray(size) { (it * 17).toByte() }
                sender.enqueue("a", expected)
                var actual: ByteArray? = null
                while (true) {
                    val frame = sender.nextFragment("a") ?: break
                    actual = receiver.receive("a", frame).record ?: actual
                    sender.notificationComplete("a", true)
                }
                assertArrayEquals(expected, actual)
            }
        }
    }

    @Test fun maximumRecordRoundTripsAtMtuFloor() {
        assertTrue(transport.connect("a"))
        assertTrue(transport.setMtu("a", 185))
        val record = ByteArray(4096) { it.toByte() }
        assertTrue(transport.enqueue("a", record))
        var completed: ByteArray? = null
        var count = 0
        while (true) {
            val fragment = transport.nextFragment("a") ?: break
            completed = transport.receive("a", fragment).record ?: completed
            transport.notificationComplete("a", true)
            count++
        }
        assertEquals(24, count)
        assertArrayEquals(record, completed)
    }

    @Test fun setupCapacityAndMtuAreEnforced() {
        repeat(8) { assertTrue(transport.connect("$it")) }
        assertFalse(transport.connect("ninth"))
        assertFalse(transport.enqueue("0", byteArrayOf(1)))
        assertFalse(transport.setMtu("0", 184))
        assertTrue(transport.connect("replacement"))
    }

    @Test fun stalledClientDoesNotBlockAnotherAndQueuesDropNewest() {
        for (peer in listOf("a", "b")) {
            transport.connect(peer)
            transport.setMtu(peer, 185)
        }
        repeat(4) { assertTrue(transport.enqueue("a", ByteArray(4096))) }
        assertFalse(transport.enqueue("a", byteArrayOf(1)))
        assertNotNull(transport.nextFragment("a"))
        assertNull(transport.nextFragment("a"))
        assertTrue(transport.enqueue("b", byteArrayOf(7)))
        assertNotNull(transport.nextFragment("b"))
        transport.disconnect("a")
        assertTrue(transport.connect("a"))
        transport.setMtu("a", 185)
        assertNull(transport.nextFragment("a"))
    }

    @Test fun timeoutAndMalformedContinuationDiscardPartialRecord() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val first = byteArrayOf(0, 1, 0, 179.toByte()) + ByteArray(178)
        assertNull(transport.receive("a", first).record)
        now = 5000
        transport.expire()
        assertNull(transport.receive("a", byteArrayOf(0x81.toByte(), 1, 0, 0, 9)).record)
        transport.receive("a", first)
        assertNull(transport.receive("a", byteArrayOf(0x81.toByte(), 1, 0, 1, 9)).record)
        assertNull(transport.receive("a", byteArrayOf(0x81.toByte(), 1, 0, 0, 9)).record)
    }

    @Test fun secondConflictingStartDisconnectsOnlyOffender() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val first = byteArrayOf(0, 1, 0, 179.toByte()) + ByteArray(178)
        transport.receive("a", first)
        assertFalse(transport.receive("a", first).disconnect)
        assertTrue(transport.receive("a", first).disconnect)
        assertTrue(transport.connect("a"))
    }

    @Test fun invalidSizesAndSequenceNeverDeliver() {
        transport.connect("a")
        transport.setMtu("a", 185)
        assertFalse(transport.enqueue("a", ByteArray(4097)))
        assertFalse(transport.enqueue("a", byteArrayOf()))
        for (frame in listOf(
            byteArrayOf(),
            byteArrayOf(0x80.toByte(), 0, 0, 1, 1),
            byteArrayOf(0x80.toByte(), 1, 0, 2, 1),
            byteArrayOf(0, 1, 0, 2, 1),
        )) {
            assertNull(transport.receive("a", frame).record)
        }
    }
}
