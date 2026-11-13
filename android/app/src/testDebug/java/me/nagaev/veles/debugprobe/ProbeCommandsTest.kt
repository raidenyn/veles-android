package me.nagaev.veles.debugprobe

import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.sharing.ConnectionGeneration
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
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
    }

    private fun commands(
        logs: MutableList<String>,
        results: MutableList<String>,
        storage: TrustStorage = MemoryStorage(snapshot()),
    ): ProbeCommands = ProbeCommands(
        sessions = NoSessions,
        repository = IdentityRepository(localIdentity),
        injector = DebugTrustInjector(storage = storage, elapsedMillis = { 0 }, wallMillis = { 0 }),
        handler = ProbeEventSource { SyntheticEvent("VAAAAA", ByteArray(16), ByteArray(0)) },
        log = { logs += it },
        result = { results += it },
    )

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
        val commands = commands(logs, results)

        commands.injectPeer(peerId, peerPublicKey)
        commands.revokePeer(peerId)

        assertTrue("the commands still report success", logs.any { it.startsWith("injected peer=") })
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

    private object NoSessions : ProbeSessions {
        override fun openFullSessions(): List<ProbeSession> = emptyList()

        override fun hasOpenSession(peerId: String): Boolean = false

        override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
            check(type != RecordType.PUSH_EVENT.wire)
            return false
        }
    }

    private class IdentityRepository(private val identity: InstallationIdentity) : InstallationTrustRepository {
        override suspend fun identity(): InstallationIdentity = identity
        override suspend fun peers(): List<TrustedPeer> = emptyList()
        override suspend fun activePeers(): List<TrustedPeer> = emptyList()
        override suspend fun addPending(peer: PendingPeer) = Unit
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = Unit
        override suspend fun revoke(peerId: String) = Unit
        override suspend fun markAuthenticated(peerId: String) = Unit
    }
}
