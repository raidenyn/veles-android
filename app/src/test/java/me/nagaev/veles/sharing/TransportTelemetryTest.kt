package me.nagaev.veles.sharing

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TransportTelemetryTest {
    private var clock = 1000L
    private fun telemetry() = TransportTelemetry("run-1", { clock++ }, { clock++ })

    @After fun reset() {
        SyntheticTransportFixture.telemetry = null
    }

    @Test fun disabledByDefaultRetainsNothing() {
        assertNull(SyntheticTransportFixture.telemetry)
    }

    @Test fun nonZeroApiCodeAndCallbackStatusArePreservedAndCountedSeparately() {
        val t = telemetry()
        t.notifyResult("AA:BB", 100, 0)
        t.notifyResult("AA:BB", 100, 200)
        t.callback("AA:BB", 0)
        t.callback("AA:BB", 257)
        val p = t.snapshot().peers.single()
        assertEquals(1, p.apiSuccess)
        assertEquals(1, p.apiNonZero)
        assertEquals(1, p.callbackSuccess)
        assertEquals(1, p.callbackNonZero)
        assertEquals(listOf<Int?>(0, 200, 0, 257), p.events.map { it.code })
        // only one accepted fragment exists, so the second callback is honestly unbound
        assertEquals(1, p.unboundCallbacks)
        assertEquals(listOf(1L, null), p.events.filter { it.kind == "notify_callback" }.map { it.boundOp })
        assertEquals("GATT_FAILURE", p.events.last().label)
    }

    @Test fun peersAreIndependentAndHashedWithoutRawAddress() {
        val t = telemetry()
        t.notifyResult("AA:BB", 10, 0)
        t.notifyResult("CC:DD", 20, 200)
        t.callback("AA:BB", 0)
        val (a, b) = t.snapshot().peers
        assertEquals(1, a.callbackSuccess)
        assertEquals(0, b.callbacks)
        assertEquals(1, b.apiNonZero)
        assertEquals(20L, b.attemptedBytes)
        assertFalse(a.peer == b.peer)
        assertEquals(TransportTelemetry.PEER_KEY_CHARS, a.peer.length)
        assertFalse(t.snapshot().toString().contains("AA:BB"))
    }

    @Test fun disconnectDropsPendingBindingsAndRecordsLifecycle() {
        val t = telemetry()
        t.connected("AA")
        t.mtu("AA", 185)
        t.subscribed("AA")
        t.notifyResult("AA", 5, 0)
        t.disconnected("AA")
        t.callback("AA", 0)
        val p = t.snapshot().peers.single()
        assertEquals(185, p.mtu)
        assertEquals(1, p.disconnects)
        assertEquals(1, p.unboundCallbacks)
    }

    @Test fun eventsAreBoundedCountersStayExactAndTruncationIsFlagged() {
        val t = telemetry()
        repeat(TransportTelemetry.MAX_EVENTS_PER_PEER + 10) { t.notifyResult("AA", 1, 200) }
        val s = t.snapshot()
        assertTrue(s.truncated)
        assertEquals(TransportTelemetry.MAX_EVENTS_PER_PEER, s.peers.single().events.size)
        assertEquals(TransportTelemetry.MAX_EVENTS_PER_PEER + 10, s.peers.single().apiNonZero)
    }

    @Test fun standardM6RunFitsWithoutTruncation() {
        val t = telemetry()
        t.connected("AA")
        t.mtu("AA", 185)
        t.subscribed("AA")
        repeat(20 * 9) {
            t.notifyResult("AA", 180, 0)
            t.callback("AA", 0)
        }
        val s = t.snapshot()
        assertFalse(s.truncated)
        assertEquals(20 * 9 * 2 + 3, s.peers.single().events.size)
    }

    @Test fun acceptedFragmentsWithoutCallbackAreNeverClean() {
        val t = telemetry()
        t.notifyResult("AA", 5, 0)
        t.notifyResult("AA", 5, 0)
        t.callbackSeen("AA", 0, false)
        t.callback("AA", 0)
        val open = t.snapshot()
        assertEquals(1, open.peers.single().pendingUnresolved)
        assertFalse(open.clean)
        t.disconnected("AA")
        val lost = t.snapshot().peers.single()
        assertEquals(0, lost.pendingUnresolved)
        assertEquals(1, lost.pendingDropped)
        assertFalse(t.snapshot().clean)
    }

    @Test fun fullyCalledBackRunIsClean() {
        val t = telemetry()
        repeat(3) {
            t.notifyResult("AA", 5, 0)
            t.callbackSeen("AA", 0, false)
            t.callback("AA", 0)
        }
        assertTrue(t.snapshot().clean)
    }

    @Test fun lateFailureAfterCloseIsCountedAndNeverClean() {
        val t = telemetry()
        t.notifyResult("AA", 5, 0)
        t.callbackSeen("AA", 257, true) // arrives after close(): the main-looper hop would have dropped it
        val p = t.snapshot().peers.single()
        assertEquals(1, p.callbacksAfterClose)
        assertEquals(1, p.callbacksSeenNonZero)
        assertEquals(0, p.callbacks)
        assertEquals(257, p.events.last().code)
        assertEquals("callback_after_close", p.events.last().kind)
        assertFalse(t.snapshot().clean)
    }

    @Test fun seenButUnprocessedCallbackIsNotClean() {
        val t = telemetry()
        t.notifyResult("AA", 5, 0)
        t.callbackSeen("AA", 0, false) // flushed from the looper queue before processing
        assertFalse(t.snapshot().clean)
    }

    @Test fun peerCountIsBoundedAndOverflowIsFlagged() {
        val t = telemetry()
        repeat(TransportTelemetry.MAX_PEERS + 2) { t.connected("peer-$it") }
        val s = t.snapshot()
        assertEquals(TransportTelemetry.MAX_PEERS, s.peers.size)
        assertEquals(2, s.peersDropped)
        assertTrue(s.truncated)
    }

    @Test fun pendingBindingsAreBoundedAndOverflowIsFlagged() {
        val t = telemetry()
        repeat(TransportTelemetry.MAX_PENDING + 1) { t.notifyResult("AA", 1, 0) }
        assertTrue(t.snapshot().peers.single().truncated)
    }

    @Test fun cleanupResetDiscardsAllState() {
        SyntheticTransportFixture.telemetry = telemetry().also { it.notifyResult("AA", 1, 0) }
        SyntheticTransportFixture.telemetry = null
        assertNull(SyntheticTransportFixture.telemetry)
        assertTrue(telemetry().snapshot().peers.isEmpty())
    }
}
