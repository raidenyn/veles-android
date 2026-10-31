package me.nagaev.veles.sharing

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP2-GAT-010 profile revision 2: `attribute = min(mtu - 3, 512)`, `payload = attribute - 4`,
 * exact non-final/final rules in both directions. Expectations are written out literally (not
 * derived from the production formula) so a wrong formula cannot validate itself.
 */
class GattTransportBudgetTest {
    private var now = 0L

    private fun literalPayload(mtu: Int) = if (mtu <= 515) mtu - 7 else 508

    private fun link(mtu: Int): GattTransport = GattTransport { now }.also {
        assertTrue(it.connect("a"))
        assertTrue(it.setMtu("a", mtu))
    }

    private fun frame(index: Int, final: Boolean, tid: Int, total: Int, payload: ByteArray) = byteArrayOf(
        (index or if (final) 128 else 0).toByte(),
        tid.toByte(),
        (total shr 8).toByte(),
        total.toByte(),
    ) + payload

    private fun record(size: Int, seed: Int = 1) = ByteArray(size) { (it * 131 + seed * 17 + (it shr 8) * 29 + 7).toByte() }

    private fun drain(sender: GattTransport): List<ByteArray> {
        val frames = mutableListOf<ByteArray>()
        while (true) {
            val frame = sender.nextFragment("a") ?: break
            frames.add(frame)
            sender.notificationComplete("a", true)
        }
        return frames
    }

    private fun feed(receiver: GattTransport, frames: List<ByteArray>): ByteArray? {
        var out: ByteArray? = null
        frames.forEach { out = receiver.receive("a", it).record ?: out }
        return out
    }

    @Test fun budgetFormulaMatchesLiteralTableForEveryMtu() {
        for (mtu in 185..517) {
            assertEquals("mtu $mtu", literalPayload(mtu), GattTransport.payloadBudget(mtu))
            assertTrue(GattTransport.attributeBudget(mtu) <= 512)
        }
        assertEquals(182, GattTransport.attributeBudget(185))
        assertEquals(511, GattTransport.attributeBudget(514))
        assertEquals(512, GattTransport.attributeBudget(515))
        assertEquals(512, GattTransport.attributeBudget(517))
    }

    @Test fun senderAndReceiverRoundTripEveryBoundaryAtEveryMtu() {
        for (mtu in 185..517) {
            val p = literalPayload(mtu)
            for (size in setOf(1, 2, p - 1, p, p + 1, 2 * p - 1, 2 * p, 2 * p + 1, 4095, 4096)) {
                if (size !in 1..4096) continue
                val sender = link(mtu)
                val receiver = link(mtu)
                val expected = record(size, mtu)
                assertTrue(sender.enqueue("a", expected))
                val frames = drain(sender)
                assertEquals("count mtu $mtu size $size", (size + p - 1) / p, frames.size)
                frames.forEachIndexed { i, f ->
                    assertTrue("mtu $mtu size $size frame $i", f.size <= minOf(mtu - 3, 512))
                    val final = i == frames.lastIndex
                    assertEquals(final, f[0].toInt() and 128 != 0)
                    // exact: every non-final carries the full budget; an exact multiple ends in a FULL final frame
                    if (!final || size % p == 0) assertEquals(p, f.size - 4) else assertEquals(size % p, f.size - 4)
                }
                assertArrayEquals("mtu $mtu size $size", expected, feed(receiver, frames))
            }
        }
    }

    @Test fun lengthsAround507508509AtMtu517() {
        val expectedFrames = mapOf(
            507 to listOf(511),
            508 to listOf(512),
            509 to listOf(512, 5),
            1015 to listOf(512, 511),
            1016 to listOf(512, 512),
            1017 to listOf(512, 512, 5),
            4095 to List(8) { 512 } + 35,
            4096 to List(8) { 512 } + 36,
        )
        for ((size, sizes) in expectedFrames) {
            val sender = link(517)
            val receiver = link(517)
            assertTrue(sender.enqueue("a", record(size)))
            val frames = drain(sender)
            assertEquals("size $size", sizes, frames.map { it.size })
            assertArrayEquals(record(size), feed(receiver, frames))
        }
    }

    @Test fun receiverAcceptsTheNonCanonicalEmptyFinalAfterAnExactMultiple() {
        for (mtu in listOf(185, 514, 517)) {
            val p = literalPayload(mtu)
            val data = record(2 * p, 3)
            val receiver = link(mtu)
            val frames = listOf(
                frame(0, false, 9, 2 * p, data.copyOfRange(0, p)),
                frame(1, false, 9, 0, data.copyOfRange(p, 2 * p)),
                frame(2, true, 9, 0, ByteArray(0)),
            )
            assertArrayEquals("mtu $mtu", data, feed(receiver, frames))
        }
    }

    @Test fun overBudgetAttributesAreRejectedAndClearPartials() {
        // 513 bytes (non-final payload 509) and the legacy 514-byte frame at MTU 517; 512 at MTU 514 (limit 511)
        val cases = listOf(517 to 509, 517 to 510, 516 to 509, 514 to 508)
        for ((mtu, payload) in cases) {
            val receiver = link(mtu)
            assertNull("mtu $mtu payload $payload", receiver.receive("a", frame(0, false, 7, 1100, ByteArray(payload))).record)
            // the oversize frame cleared the transaction, so even a well-formed continuation cannot complete it
            assertNull(receiver.receive("a", frame(1, true, 7, 0, ByteArray(1100 - payload))).record)
        }
        // a FINAL frame over the budget is rejected too, not merely a non-final one
        val receiver = link(517)
        assertNull(receiver.receive("a", frame(0, true, 7, 509, ByteArray(509))).record)
    }

    @Test fun exactNonFinalAndFinalRulesRejectEveryOffByOne() {
        val p = 508
        fun fresh() = link(517)
        // non-final one byte short / one byte long (long = over attribute budget)
        assertNull(fresh().let { feed(it, listOf(frame(0, false, 5, 1016, ByteArray(p - 1)), frame(1, true, 5, 0, ByteArray(509)))) })
        // non-final carrying more than remains
        assertNull(fresh().receive("a", frame(0, false, 5, 507, ByteArray(p))).record)
        // final short of / beyond the declared total
        assertNull(fresh().let { feed(it, listOf(frame(0, false, 5, 1000, ByteArray(p)), frame(1, true, 5, 0, ByteArray(491)))) })
        assertNull(fresh().let { feed(it, listOf(frame(0, false, 5, 1000, ByteArray(p)), frame(1, true, 5, 0, ByteArray(493)))) })
        // empty final when bytes still remain
        assertNull(fresh().let { feed(it, listOf(frame(0, false, 5, 1000, ByteArray(p)), frame(1, true, 5, 0, ByteArray(0)))) })
        // totals 0 and 4097
        assertNull(fresh().receive("a", frame(0, true, 5, 0, ByteArray(0))).record)
        assertNull(fresh().receive("a", frame(0, false, 5, 4097, ByteArray(p))).record)
        // nothing may follow a final fragment
        val receiver = fresh()
        assertEquals(p, receiver.receive("a", frame(0, true, 5, p, ByteArray(p))).record?.size)
        assertNull(receiver.receive("a", frame(1, true, 5, 0, ByteArray(0))).record)
        // gap and duplicate index
        assertNull(fresh().let { feed(it, listOf(frame(0, false, 5, 1017, ByteArray(p)), frame(2, true, 5, 0, ByteArray(509)))) })
        assertNull(fresh().let { feed(it, listOf(frame(0, false, 5, 1017, ByteArray(p)), frame(0, false, 5, 0, ByteArray(p)))) })
    }

    @Test fun mtuOutsideTheSupportedRangeIsRefusedAndNeverRetained() {
        for (mtu in listOf(23, 184, 518, 1000)) {
            val transport = GattTransport { now }
            assertTrue(transport.connect("a"))
            assertFalse("mtu $mtu", transport.setMtu("a", mtu))
            assertFalse(transport.enqueue("a", byteArrayOf(1)))
            assertTrue("peer slot is released", transport.connect("a"))
        }
        val transport = GattTransport { now }
        transport.connect("a")
        assertTrue(transport.setMtu("a", 247))
        assertFalse("changing the MTU mid-link is refused", transport.setMtu("a", 517))
    }

    @Test fun cleanupZeroesAndReleasesPartialAndQueuedState() {
        val transport = link(517)
        transport.receive("a", frame(0, false, 5, 1016, ByteArray(508)))
        assertTrue(transport.enqueue("a", record(4096)))
        transport.nextFragment("a")
        transport.disconnect("a")
        assertNull(transport.nextFragment("a"))
        assertTrue(transport.connect("a"))
        assertTrue(transport.setMtu("a", 517))
        assertNull("no state survives a reconnect", transport.receive("a", frame(1, true, 5, 0, ByteArray(508))).record)
        assertNull(transport.nextFragment("a"))
    }

    @Test fun twoLinksWithDifferentMtusKeepIndependentBudgets() {
        val transport = GattTransport { now }
        for ((id, mtu) in listOf("narrow" to 185, "wide" to 517)) {
            assertTrue(transport.connect(id))
            assertTrue(transport.setMtu(id, mtu))
            assertTrue(transport.enqueue(id, record(1016)))
        }
        assertEquals(182, transport.nextFragment("narrow")?.size)
        assertEquals(512, transport.nextFragment("wide")?.size)
    }
}
