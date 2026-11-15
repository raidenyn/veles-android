package me.nagaev.veles.debugpairing

import kotlinx.coroutines.test.runTest
import me.nagaev.veles.debugprobe.PeerManagement
import me.nagaev.veles.sharing.RemovalResult
import me.nagaev.veles.trust.PeerSummary
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP-11 D9: the debug screen's peer-management logic. Every control is backed by the
 * revocation coordinator surface ([PeerManagement]); the screen only maps an outcome to copy.
 *
 * The mutual-removal scenario is the spec's "Mutual remove with no connection": with no open
 * full session the control reports that only local removal is possible and deletes nothing.
 */
class PeerManagementTest {
    private val peer = PeerSummary(
        installationId = "02".repeat(16),
        label = "Desktop",
        fingerprint = "ABCD EF01 2345",
        deviceType = 1,
        state = TrustState.PENDING,
        createdAtMillis = 1_000L,
        lastSeenMillis = null,
        connected = false,
    )

    private class Recorder(var records: List<PeerSummary> = emptyList()) : PeerManagement {
        val revoked = mutableListOf<String>()
        val mutual = mutableListOf<String>()
        var removal: RemovalResult = RemovalResult.REMOVED
        var resets = 0
        var failure: TrustException? = null

        private fun fail() {
            failure?.let { throw it }
        }

        override suspend fun peers(): List<PeerSummary> {
            fail()
            return records
        }

        override suspend fun revoke(peerId: String) {
            fail()
            revoked += peerId
        }

        override suspend fun removeMutually(peerId: String): RemovalResult {
            fail()
            mutual += peerId
            return removal
        }

        override suspend fun reset() {
            fail()
            resets += 1
        }
    }

    private fun actions(recorder: Recorder?) = DebugPeerActions(
        management = { recorder },
        status = { TrustStatus.OK },
    )

    @Test
    fun peer_list_carries_label_state_fingerprint_and_connected_flag() = runTest {
        val recorder = Recorder(listOf(peer.copy(connected = true)))

        val listed = actions(recorder).peers()

        assertEquals(listOf(peer.copy(connected = true)), listed)
        assertTrue("the connected flag is what the screen shows", listed!!.single().connected)
    }

    @Test
    fun revoke_deletes_the_record_and_closes_its_sessions() = runTest {
        val recorder = Recorder(listOf(peer))
        val debug = actions(recorder)

        assertEquals(PeerActionOutcome.DONE, debug.revoke(peer.installationId))
        assertEquals("the control goes through the coordinator", listOf(peer.installationId), recorder.revoked)

        recorder.records = emptyList()
        assertEquals("the deleted peer is no longer listed", emptyList<PeerSummary>(), debug.peers())
    }

    @Test
    fun mutual_remove_without_a_full_session_reports_local_only_and_deletes_nothing() = runTest {
        val recorder = Recorder(listOf(peer)).apply { removal = RemovalResult.KEPT_NOT_CONNECTED }
        val debug = actions(recorder)

        assertEquals(PeerActionOutcome.LOCAL_ONLY, debug.removeMutually(peer.installationId))

        assertTrue("mutual removal never deletes locally", recorder.revoked.isEmpty())
        assertEquals("the record is still listed", listOf(peer), debug.peers())
    }

    @Test
    fun mutual_remove_reports_removed_and_missing_response_separately() = runTest {
        val recorder = Recorder(listOf(peer))

        assertEquals(PeerActionOutcome.DONE, actions(recorder).removeMutually(peer.installationId))

        recorder.removal = RemovalResult.KEPT_NO_RESPONSE
        assertEquals(PeerActionOutcome.NO_RESPONSE, actions(recorder).removeMutually(peer.installationId))
        assertTrue("a lost response keeps the local record", recorder.revoked.isEmpty())
    }

    @Test
    fun reset_without_confirmation_deletes_nothing() = runTest {
        val recorder = Recorder(listOf(peer))

        assertEquals(PeerActionOutcome.CONFIRMATION_REQUIRED, actions(recorder).reset(confirmed = false))

        assertEquals("nothing was reset", 0, recorder.resets)
        assertEquals("nothing was deleted", listOf(peer), actions(recorder).peers())
    }

    @Test
    fun confirmed_reset_runs_the_endpoint_wide_reset() = runTest {
        val recorder = Recorder(listOf(peer))

        assertEquals(PeerActionOutcome.DONE, actions(recorder).reset(confirmed = true))

        assertEquals("the endpoint-wide reset ran once", 1, recorder.resets)
    }

    @Test
    fun an_unreadable_store_is_reported_instead_of_crashing() = runTest {
        val recorder = Recorder(listOf(peer)).apply { failure = TrustException() }
        val debug = actions(recorder)

        assertNull("the list is unavailable", debug.peers())
        assertEquals(PeerActionOutcome.FAILED, debug.revoke(peer.installationId))
        assertEquals(PeerActionOutcome.FAILED, debug.reset(confirmed = true))
    }

    @Test
    fun no_running_service_reports_unavailable() = runTest {
        val debug = actions(null)

        assertNull(debug.peers())
        assertEquals(PeerActionOutcome.UNAVAILABLE, debug.revoke(peer.installationId))
        assertEquals(PeerActionOutcome.UNAVAILABLE, debug.removeMutually(peer.installationId))
        assertEquals(PeerActionOutcome.UNAVAILABLE, debug.reset(confirmed = true))
    }

    @Test
    fun status_is_read_from_the_coordinator_sink() {
        val debug = DebugPeerActions(
            management = { Recorder() },
            status = { TrustStatus.RE_PAIR_REQUIRED },
        )

        assertEquals(TrustStatus.RE_PAIR_REQUIRED, debug.trustStatus())
    }

    @Test
    fun status_label_distinguishes_the_two_values() {
        assertNotEquals(trustStatusLabel(TrustStatus.OK), trustStatusLabel(TrustStatus.RE_PAIR_REQUIRED))
    }
}
