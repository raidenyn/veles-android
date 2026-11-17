package me.nagaev.veles.debugprobe

import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.sharing.ConnectionGeneration
import me.nagaev.veles.sharing.RemovalResult
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PeerSummary
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The adb command surface must never write an installation id or identity key to logcat: the
 * `EXPORT_IDENTITY` values travel through the ordered broadcast's result data, which
 * `adb shell am broadcast` prints, and the log keeps bounded prefixes only.
 */
class ProbeCommandsTest {
    private val localIdentity = InstallationIdentity(
        installationId = "00112233445566778899aabbccddeeff",
        publicKey = "04" + "ab".repeat(64),
    )

    private class MemoryStorage(initial: TrustSnapshot) : TrustStorage {
        private var snapshot: TrustSnapshot? = initial
        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
            val (next, result) = block(snapshot)
            snapshot = next
            return result
        }

        override suspend fun reset() {
            snapshot = null
        }
    }

    private fun commands(
        logs: MutableList<String>,
        results: MutableList<String>,
        storage: TrustStorage = MemoryStorage(snapshot()),
        revocation: PeerManagement? = null,
    ): ProbeCommands = ProbeCommands(
        sessions = NoSessions,
        repository = IdentityRepository(localIdentity),
        injector = DebugTrustInjector(storage = storage, elapsedMillis = { 0 }, wallMillis = { 0 }),
        handler = ProbeEventSource { SyntheticEvent("VAAAAA", ByteArray(16), ByteArray(0)) },
        log = { logs += it },
        result = { results += it },
        revocation = revocation,
    )

    @Test
    fun push_while_armed_sends_nothing_and_logs() = runTest {
        val logs = mutableListOf<String>()
        val sessions = RecordingSessions(ProbeSession(ConnectionGeneration("aa", 1), ByteArray(8)))
        var issued = 0
        val commands = ProbeCommands(
            sessions = sessions,
            repository = IdentityRepository(localIdentity),
            injector = DebugTrustInjector(
                storage = MemoryStorage(snapshot()),
                elapsedMillis = { 0 },
                wallMillis = { 0 },
            ),
            handler = ProbeEventSource {
                issued += 1
                SyntheticEvent("VAAAAA", ByteArray(16), ByteArray(0))
            },
            log = { logs += it },
            gateBArmed = { true },
            elapsed = ElapsedClock { 42L },
        )

        commands.push()

        assertTrue("the fixture owns pushes while armed", sessions.sends.isEmpty())
        assertEquals("no synthetic event is issued", 0, issued)
        assertEquals("the refusal is logged with an elapsed timestamp", listOf("elapsed=42 fixture owns pushes"), logs)
    }

    private fun snapshot(): TrustSnapshot = TrustSnapshot(
        identity = localIdentity,
        bootMarker = "bb".repeat(32),
        continuityToken = "cc".repeat(32),
    )

    @Test
    fun exportIdentitySendsFullValuesThroughTheShellResultOnly() = runTest {
        val logs = mutableListOf<String>()
        val results = mutableListOf<String>()

        commands(logs, results).exportIdentity()

        assertEquals(
            "identity=${localIdentity.installationId} pub=${localIdentity.publicKey}",
            results.single(),
        )
        val log = logs.single()
        assertEquals(
            "identity=${localIdentity.installationId.take(8)} pub=${localIdentity.publicKey.take(8)}",
            log,
        )
        assertFalse("the full id never reaches the log", log.contains(localIdentity.installationId))
        assertFalse("the full key never reaches the log", log.contains(localIdentity.publicKey))
    }

    @Test
    fun peerCommandsLogOnlyBoundedPrefixes() = runTest {
        val logs = mutableListOf<String>()
        val results = mutableListOf<String>()
        val peerId = "ffeeddccbbaa99887766554433221100"
        val peerPublicKey = TrustEncoding.hex(TestIdentity.generate().publicKey)
        val management = RecordingManagement()
        val commands = commands(logs, results, revocation = management)

        commands.injectPeer(peerId, peerPublicKey)
        commands.revokePeer(peerId)

        assertTrue("the commands still report success", logs.any { it.startsWith("injected peer=") })
        assertEquals("the Clear goes through the coordinator", listOf(peerId), management.revoked)
        assertTrue("the commands still report revocation", logs.any { it.startsWith("revoked peer=") })
        for (line in logs) {
            assertFalse("no full installation id in $line", line.contains(peerId))
            assertFalse("no full key in $line", line.contains(peerPublicKey))
        }
        assertTrue(
            "the prefix correlates the two lines",
            logs.any { it == "injected peer=${peerId.take(8)}" } &&
                logs.any { it == "revoked peer=${peerId.take(8)}" },
        )
        assertTrue("no structured result for peer commands", results.isEmpty())
    }

    /** The coordinator the Clear goes through; it never falls back to the repository. */
    private class RecordingManagement : PeerManagement {
        val revoked = mutableListOf<String>()

        override suspend fun peers(): List<PeerSummary> = emptyList()

        override suspend fun revoke(peerId: String) {
            revoked += peerId
        }

        override suspend fun removeMutually(peerId: String): RemovalResult = RemovalResult.KEPT_NOT_CONNECTED

        override suspend fun reset() = Unit
    }

    private object NoSessions : ProbeSessions {
        override fun openFullSessions(): List<ProbeSession> = emptyList()

        override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
            check(type != RecordType.PUSH_EVENT.wire)
            return false
        }
    }

    /** One open full session that records every send a push would make. */
    private class RecordingSessions(private val session: ProbeSession) : ProbeSessions {
        val sends = mutableListOf<Pair<Int, ByteArray>>()

        override fun openFullSessions(): List<ProbeSession> = listOf(session)

        override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
            sends += type to body
            return true
        }
    }

    private class IdentityRepository(private val identity: InstallationIdentity) : InstallationTrustRepository {
        override suspend fun identity(): InstallationIdentity = identity
        override suspend fun peers(): List<TrustedPeer> = emptyList()
        override suspend fun activePeers(): List<TrustedPeer> = emptyList()
        override suspend fun addPending(peer: PendingPeer) = Unit
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = Unit
        override suspend fun revoke(peerId: String) = Unit
        override suspend fun beginRevocation(peerId: String) = Unit
        override suspend fun endRevocation(peerId: String) = Unit
        override suspend fun markAuthenticated(peerId: String) = Unit
        override suspend fun resetIdentity() = error("reset is not part of the probe fixture")
    }
}
