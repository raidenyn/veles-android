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

    @Test fun receiveReportsDistinctDiagnosticOutcomes() {
        fun outcome(result: GattTransport.Received): String = result.diagnostic

        assertEquals("unknown_peer", outcome(transport.receive("a", byteArrayOf())))
        transport.connect("a")
        assertEquals("missing_mtu", outcome(transport.receive("a", byteArrayOf())))
        transport.setMtu("a", 185)
        assertEquals("invalid_frame_size", outcome(transport.receive("a", byteArrayOf())))
        assertEquals("missing_partial", outcome(transport.receive("a", byteArrayOf(1, 1, 0, 0, 1))))
        val start = byteArrayOf(0, 1, 0, 179.toByte()) + ByteArray(178)
        assertEquals("partial", outcome(transport.receive("a", start)))
        assertEquals("conflict", outcome(transport.receive("a", start)))
        assertEquals("partial", outcome(transport.receive("a", start)))
        assertEquals("invalid_order", outcome(transport.receive("a", byteArrayOf(0x82.toByte(), 1, 0, 0, 9))))
        assertEquals("partial", outcome(transport.receive("a", start)))
        val complete = transport.receive("a", byteArrayOf(0x81.toByte(), 1, 0, 0, 9))
        assertEquals("complete", outcome(complete))
        assertEquals(179, complete.record?.size)
    }

    @Test fun twoIncompleteTransactionsSurviveAndThirdIsRefused() {
        transport.connect("a")
        transport.setMtu("a", 185)
        for (id in listOf(41, 42)) {
            transport.receive("a", byteArrayOf(0, id.toByte(), 0, 179.toByte()) + ByteArray(178))
        }
        val refused = transport.receive("a", byteArrayOf(0x80.toByte(), 43, 0, 1, 9))
        assertNull(refused.record)
        assertEquals("allocation_limit", refused.diagnostic)
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

    @Test fun conflictingStartErasesLivePartialAndOldContinuationIsIgnored() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val first = byteArrayOf(0, 1, 0, 179.toByte()) + ByteArray(178)
        transport.receive("a", first)
        val conflict = transport.receive("a", first)
        assertFalse(conflict.disconnect)
        assertNull(conflict.record)
        val last = byteArrayOf(0x81.toByte(), 1, 0, 0, 9)
        assertNull("no live partial remains to continue", transport.receive("a", last).record)
    }

    @Test fun conflictWindowIsForgottenAfterSixtySecondsAndFreshStartWorks() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val first = byteArrayOf(0, 1, 0, 179.toByte()) + ByteArray(178)
        val last = byteArrayOf(0x81.toByte(), 1, 0, 0, 9)
        transport.receive("a", first)
        assertFalse(transport.receive("a", first).disconnect) // conflict 1 erases the partial
        now = 60000
        transport.receive("a", first) // fresh start, live again
        assertFalse("conflict at t=0 no longer counts", transport.receive("a", first).disconnect)
        transport.receive("a", first) // fresh start after the second erase
        assertEquals(179, transport.receive("a", last).record?.size)
    }

    @Test fun sameIdPrefixPlusForeignSuffixNeverCompletes() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val prefixA = byteArrayOf(0, 7, 0x00, 200.toByte()) + ByteArray(178) { 0xA1.toByte() }
        val startB = byteArrayOf(0, 7, 0x00, 200.toByte()) + ByteArray(178) { 0xB2.toByte() }
        val suffixB = byteArrayOf(0x81.toByte(), 7, 0, 0) + ByteArray(22) { 0xB2.toByte() }
        assertNull(transport.receive("a", prefixA).record)
        assertNull(transport.receive("a", startB).record)
        assertNull("prefix A must not be completed by B's suffix", transport.receive("a", suffixB).record)
    }

    @Test fun freshStartAfterConflictDiscardCompletesWithOnlyItsOwnBytes() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val start = { fill: Int -> byteArrayOf(0, 7, 0x00, 200.toByte()) + ByteArray(178) { fill.toByte() } }
        val suffix = { fill: Int -> byteArrayOf(0x81.toByte(), 7, 0, 0) + ByteArray(22) { fill.toByte() } }
        transport.receive("a", start(0xA1))
        transport.receive("a", start(0xB2)) // conflict: erases A, rejects B
        assertNull(transport.receive("a", start(0xC3)).record) // fresh start
        val record = transport.receive("a", suffix(0xC3)).record
        assertArrayEquals(ByteArray(200) { 0xC3.toByte() }, record)
    }

    // Global ceilings (16 transactions / 64 KiB) equal 8 peers x the per-peer ceilings here, and both
    // are derived from the same live partials, so they cannot bind independently of the per-peer
    // ones on this class; the Rust host tests exercise the shared global budget with explicit usage.
    @Test fun conflictDiscardReleasesPeerCountAndByteBudget() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val big = { id: Int -> byteArrayOf(0, id.toByte(), 0x10, 0) + ByteArray(178) } // total 4096
        transport.receive("a", big(1))
        transport.receive("a", big(2))
        // Peer is at its 2-transaction / 8 KiB ceiling: a third start is refused.
        transport.receive("a", big(3))
        val thirdSuffix = byteArrayOf(0x81.toByte(), 3, 0, 0, 1)
        assertNull(transport.receive("a", thirdSuffix).record)
        // Conflicting start of id 1 erases it and frees both the slot and its 4096 bytes.
        assertFalse(transport.receive("a", big(1)).disconnect)
        // Now id 3 can start and live: a short record using id 3 completes.
        val small = byteArrayOf(0, 3, 0, 179.toByte()) + ByteArray(178)
        assertNull(transport.receive("a", small).record)
        assertEquals(179, transport.receive("a", byteArrayOf(0x81.toByte(), 3, 0, 0, 9)).record?.size)
    }

    @Test fun malformedDeclaredLengthOnLiveIdIsStillAConflictThatErasesThePartial() {
        for (badTotal in listOf(0, 4097)) {
            val local = GattTransport { now }
            local.connect("a")
            local.setMtu("a", 185)
            val live = byteArrayOf(0, 7, 0x00, 200.toByte()) + ByteArray(178) { 0xA1.toByte() }
            val dup = byteArrayOf(0, 7, (badTotal shr 8).toByte(), badTotal.toByte()) +
                ByteArray(178) { 0xEE.toByte() }
            val suffix = byteArrayOf(0x81.toByte(), 7, 0, 0) + ByteArray(22) { 0xA1.toByte() }
            assertNull(local.receive("a", live).record)
            val conflict = local.receive("a", dup)
            assertFalse("first conflict does not disconnect (total=$badTotal)", conflict.disconnect)
            assertNull(conflict.record)
            assertNull("erased prefix cannot be completed (total=$badTotal)", local.receive("a", suffix).record)
            // Fresh start works afterwards and completes with only its own bytes.
            assertNull(local.receive("a", live).record)
            assertArrayEquals(
                ByteArray(200) { 0xA1.toByte() },
                local.receive("a", suffix).record,
            )
        }
    }

    @Test fun malformedDuplicateCountsTowardTheRollingWindowAndSecondConflictDisconnects() {
        for (badTotal in listOf(0, 4097)) {
            val local = GattTransport { now }
            local.connect("a")
            local.setMtu("a", 185)
            val live = byteArrayOf(0, 7, 0x00, 200.toByte()) + ByteArray(178)
            val dup = byteArrayOf(0, 7, (badTotal shr 8).toByte(), badTotal.toByte()) + ByteArray(178)
            local.receive("a", live)
            assertFalse(local.receive("a", dup).disconnect) // conflict 1 (erases)
            local.receive("a", live) // fresh live start
            assertTrue("conflict 2 inside 60 s (total=$badTotal)", local.receive("a", dup).disconnect)
        }
    }

    @Test fun overBudgetDuplicateStartIsAFrameDiscardNotAConflict() {
        transport.connect("a")
        transport.setMtu("a", 185)
        val live = { id: Int -> byteArrayOf(0, id.toByte(), 0x00, 200.toByte()) + ByteArray(178) { 0xA1.toByte() } }
        val over = byteArrayOf(0, 52, 0x00, 200.toByte()) + ByteArray(179) { 0xEE.toByte() } // 183 > 182
        val tail = { id: Int -> byteArrayOf(0x81.toByte(), id.toByte(), 0, 0) + ByteArray(22) { 0xA1.toByte() } }
        transport.receive("a", live(52))
        transport.receive("a", live(53))
        val rejected = transport.receive("a", over)
        assertFalse(rejected.disconnect)
        assertNull(rejected.record)
        // Every partial of the peer was discarded (not only id 52): tails cannot complete.
        assertNull(transport.receive("a", tail(52)).record)
        assertNull(transport.receive("a", tail(53)).record)
        // 53 is no longer live, so a fresh start completes (it would be a conflict otherwise).
        assertNull(transport.receive("a", live(53)).record)
        assertArrayEquals(ByteArray(200) { 0xA1.toByte() }, transport.receive("a", tail(53)).record)
        // No conflict was fabricated by the over-budget frame: the next conflict is the FIRST.
        transport.receive("a", live(52))
        assertFalse(transport.receive("a", live(52)).disconnect) // conflict 1
        transport.receive("a", live(52)) // fresh live start
        assertTrue(transport.receive("a", live(52)).disconnect) // conflict 2 inside 60 s
    }

    @Test fun secondConflictAcrossFirstDiscardAndFreshStartDisconnectsOnlyOffender() {
        transport.connect("a")
        transport.connect("b")
        transport.setMtu("a", 185)
        transport.setMtu("b", 185)
        val first = byteArrayOf(0, 1, 0, 179.toByte()) + ByteArray(178)
        transport.receive("a", first)
        assertFalse(transport.receive("a", first).disconnect) // conflict 1: discard
        transport.receive("a", first) // fresh live start: the id is live again
        assertTrue("conflict 2 inside 60 s disconnects", transport.receive("a", first).disconnect)
        assertTrue(transport.connect("a"))
        assertFalse(transport.connect("b"))
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

    @Test fun enqueueRefusalReasonsNameTheFirstEnforcedLimit() {
        assertEquals("unknown_peer", transport.enqueueRefusalReason("missing", 1))
        assertTrue(transport.connect("a"))
        assertEquals("missing_mtu", transport.enqueueRefusalReason("a", 1))
        assertTrue(transport.setMtu("a", 185))
        for (size in listOf(0, 4097)) {
            assertEquals("invalid_record_size", transport.enqueueRefusalReason("a", size))
        }
        repeat(4) { assertTrue(transport.enqueue("a", ByteArray(4096))) }
        assertEquals("peer_byte_limit", transport.enqueueRefusalReason("a", 1))

        val counted = GattTransport { now }
        assertTrue(counted.connect("b"))
        assertTrue(counted.setMtu("b", 185))
        repeat(16) { assertTrue(counted.enqueue("b", ByteArray(1))) }
        assertEquals("peer_count_limit", counted.enqueueRefusalReason("b", 1))
    }
}
