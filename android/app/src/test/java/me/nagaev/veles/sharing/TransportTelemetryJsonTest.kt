package me.nagaev.veles.sharing

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Final evidence serialization: exact fields, explicit loss markers, and no raw addresses or payloads. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TransportTelemetryJsonTest {
    private var clock = 1000L
    private fun telemetry() = TransportTelemetry("run-1", { clock++ }, { clock++ })

    @Test fun eventsCarryMetadataNullsAndHashedPeersOnly() {
        val telemetry = telemetry()
        telemetry.inbound("AA:BB", "fragment_received", "index=0;final=false;transaction=9", 7, "frame-hash")
        telemetry.notifyResult("AA:BB", 5, 0)
        telemetry.callbackSeen("AA:BB", 0, true)
        val snapshot = telemetry.snapshot()

        val events = TransportTelemetryJson.events(snapshot)
        assertEquals(3, events.length())
        val received = events.getJSONObject(0)
        assertEquals("fragment_received", received.getString("kind"))
        assertEquals("index=0;final=false;transaction=9", received.getString("label"))
        assertEquals(7, received.getInt("bytes"))
        assertEquals("frame-hash", received.getString("sha256"))
        assertTrue("no API code belongs to an inbound event", received.isNull("code"))
        val peer = received.getString("peer")
        assertEquals(TransportTelemetry.PEER_KEY_CHARS, peer.length)
        assertEquals(snapshot.peers.single().peer, peer)

        val api = events.getJSONObject(1)
        assertEquals("notify_api", api.getString("kind"))
        assertEquals(0, api.getInt("code"))
        assertEquals("SUCCESS", api.getString("label"))
        assertTrue(api.isNull("sha256"))

        val late = events.getJSONObject(2)
        assertEquals("callback_after_close", late.getString("kind"))
        assertEquals(0, late.getInt("code"))
        assertEquals("GATT_SUCCESS", late.getString("label"))

        val text = events.toString()
        assertFalse("raw address must never be serialized", text.contains("AA:BB"))
        assertFalse("no payload key is serialized", text.contains("\"payload\""))
    }

    @Test fun peerSummaryAndFinalStateExposeLossInsteadOfHidingIt() {
        val telemetry = telemetry()
        telemetry.notifyResult("AA", 5, 0)
        telemetry.callbackSeen("AA", 257, true)
        val snapshot = telemetry.snapshot()

        val peers = TransportTelemetryJson.peers(snapshot)
        val peer = peers.getJSONObject(0)
        assertEquals(1, peer.getInt("pending_unresolved"))
        assertEquals(0, peer.getInt("pending_dropped"))
        assertEquals(1, peer.getInt("callbacks_seen"))
        assertEquals(0, peer.getInt("callbacks"))
        assertEquals(1, peer.getInt("callbacks_after_close"))
        assertFalse(peer.getBoolean("truncated"))

        val state = JSONObject().put("epoch_ms", 1L)
        TransportTelemetryJson.attachFinalState(state, snapshot)
        assertEquals(2, state.getJSONArray("transport_events").length())
        assertFalse(state.getBoolean("telemetry_truncated"))
        assertEquals(0, state.getInt("telemetry_peers_dropped"))
        assertFalse("an unprocessed accepted fragment is incomplete evidence", snapshot.clean)
    }

    @Test fun truncationAndPeerOverflowAreSerializedAsExplicitLoss() {
        val telemetry = telemetry()
        repeat(TransportTelemetry.MAX_EVENTS_PER_PEER + 1) {
            telemetry.inbound("AA", "fragment_received", null, 4, null)
        }
        repeat(TransportTelemetry.MAX_PEERS + 2) { telemetry.connected("peer-$it") }
        val snapshot = telemetry.snapshot()

        assertEquals(
            TransportTelemetry.MAX_EVENTS_PER_PEER + TransportTelemetry.MAX_PEERS - 1,
            TransportTelemetryJson.events(snapshot).length(),
        )
        assertEquals(TransportTelemetry.MAX_PEERS, TransportTelemetryJson.peers(snapshot).length())
        assertTrue(snapshot.truncated)
        assertEquals(TransportTelemetry.MAX_EVENTS_PER_PEER, snapshot.peers.first().events.size)
        assertTrue(snapshot.peers.first().truncated)
        val state = JSONObject()
        TransportTelemetryJson.attachFinalState(state, snapshot)
        assertTrue(state.getBoolean("telemetry_truncated"))
        assertTrue(state.getInt("telemetry_peers_dropped") > 0)
    }

    @Test fun finalOutputSerializesFinalTelemetryWithoutAnyLiveStateSample() {
        val telemetry = telemetry()
        telemetry.notifyResult("AA", 5, 0)
        val snapshot = telemetry.snapshot()

        val output = TransportTelemetryJson.finalOutput(
            runId = "run-1",
            status = "ERROR",
            error = "IllegalStateException",
            states = JSONArray(),
            records = JSONArray(),
            injections = JSONArray(),
            snapshot = snapshot,
        )

        assertEquals(0, output.getJSONArray("states").length())
        assertEquals(1, output.getJSONArray("transport_events").length())
        assertFalse(output.getBoolean("telemetry_truncated"))
        assertEquals(0, output.getInt("telemetry_peers_dropped"))
        assertFalse("an unresolved fragment is incomplete evidence", output.getBoolean("telemetry_clean"))
        assertEquals(1, output.getJSONArray("telemetry_peers").length())
        assertEquals("ERROR", output.getString("status"))
        assertEquals("IllegalStateException", output.getString("error"))
    }

    @Test fun finalOutputStillAttachesFinalTelemetryToALiveState() {
        val telemetry = telemetry()
        telemetry.inbound("AA", "fragment_received", null, 4, null)
        val snapshot = telemetry.snapshot()
        val states = JSONArray().put(JSONObject().put("epoch_ms", 7L))

        val output = TransportTelemetryJson.finalOutput(
            runId = "run-1",
            status = "CAPTURED",
            error = null,
            states = states,
            records = JSONArray(),
            injections = JSONArray(),
            snapshot = snapshot,
        )

        val state = output.getJSONArray("states").getJSONObject(0)
        assertEquals(1, state.getJSONArray("transport_events").length())
        assertEquals(1, output.getJSONArray("transport_events").length())
        assertTrue(output.isNull("error"))
    }

    @Test fun earlierSnapshotIsAnImmutableCopyOfLaterActivity() {
        val telemetry = telemetry()
        val empty = telemetry.snapshot()
        telemetry.notifyResult("AA", 5, 0)
        assertEquals(0, empty.peers.size)
        assertEquals(0, TransportTelemetryJson.events(empty).length())
        assertEquals(1, TransportTelemetryJson.events(telemetry.snapshot()).length())
    }
}
