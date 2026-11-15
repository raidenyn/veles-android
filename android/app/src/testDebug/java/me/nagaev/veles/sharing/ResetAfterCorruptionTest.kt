package me.nagaev.veles.sharing

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import me.nagaev.veles.debugpairing.PeerActionOutcome
import me.nagaev.veles.debugpairing.debugPeerActions
import me.nagaev.veles.debugprobe.DebugRevocationRuntime
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.PeerRevocationCoordinator
import me.nagaev.veles.trust.PeerSummary
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustStatus
import me.nagaev.veles.trust.TrustStorage
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP-11 design D6 and the debug controls of design D9: after a restart with unreadable trust
 * storage the explicit Reset must still be reachable. The startup identity read fails, but it
 * must not abort the service wiring: the coordinator is created, the debug action drives it, and
 * the next repository use creates a fresh identity. Transport authentication stays unavailable
 * for as long as the store cannot supply an identity.
 *
 * The store is a [TrustStorage] whose durable row cannot be decoded until the reset deletes it
 * without reading it, exactly like `RoomTrustStorage`'s decode-before-the-block behavior.
 */
class ResetAfterCorruptionTest {
    @Test
    fun resetThroughTheDebugActionRecoversFromUnreadableStorage() = runBlocking {
        val storage = CorruptStorage()
        val keys = SoftwareKeys()
        val sink = StoreFailureSink(SharingState::updateTrustStatus)
        val repository = TrustRepository(
            storage,
            keys,
            { CLOCK },
            { BOOT },
            { CLOCK },
            ActivationVerifier { _, _, _ -> false },
            onStoreFailure = sink::invoke,
        )
        SharingState.updateTrustStatus(TrustStatus.OK)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)

        // The service's startup read fails; the wiring must continue instead of aborting the
        // service before a coordinator exists.
        val identities = LocalIdentity(repository)
        identities.warm()
        val trust = ResettingTrust(repository, identities)
        val host = SessionHost(scope, RefusingTransport, engineFactory(), trust, { CLOCK })
        val coordinator = PeerRevocationCoordinator(
            trust,
            host,
            scope,
            onStatus = SharingState::updateTrustStatus,
        )
        sink.attach(coordinator::failClosed)
        DebugRevocationRuntime.attach(coordinator)
        val actions = debugPeerActions()
        try {
            // The failure is reported once, and no handshake may sign with an invented identity.
            assertEquals(TrustStatus.RE_PAIR_REQUIRED, actions.trustStatus())
            assertArrayEquals("no identity is invented", ByteArray(0), identities.currentOrEmpty())
            assertEquals("the store supplied no identity", deletedKey, keys.publicKey())

            // The debug Reset is the decode-free recovery: it deletes the row and the key.
            assertEquals(PeerActionOutcome.DONE, actions.reset(confirmed = true))

            assertTrue("the reset deleted the trust row", storage.resetCalls == 1)
            assertEquals("the reset deleted the installation key", 1, keys.deleteCalls)
            assertEquals("a completed reset publishes the recovery status", TrustStatus.OK, actions.trustStatus())

            // The next repository use creates a fresh identity and an empty peer set.
            val fresh = repository.identity()
            assertNotEquals("a fresh installation id", "01".repeat(16), fresh.installationId)
            assertNotEquals("a fresh installation key", deletedKey, keys.publicKey())
            assertTrue("no peer survives the reset", repository.peers().isEmpty())
            assertArrayEquals(
                "the refreshed cache serves the fresh installation id",
                TrustEncoding.bytes(fresh.installationId),
                identities.currentOrEmpty(),
            )
            assertEquals("the fresh store lists no peers", emptyList<PeerSummary>(), actions.peers())
        } finally {
            DebugRevocationRuntime.detach()
            SharingState.updateTrustStatus(TrustStatus.OK)
            scope.cancel()
        }
    }

    /** The engine factory never runs: no connection is admitted in this test. */
    private fun engineFactory(): (ConnectionGeneration) -> PhoneSessionEngine =
        { error("no generation is admitted in this test") }

    /** A durable row that cannot be decoded until `reset()` deletes it without reading it. */
    private class CorruptStorage : TrustStorage {
        var resetCalls = 0
            private set
        private var corrupt = true
        private var snapshot: TrustSnapshot? = null

        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
            if (corrupt) throw IllegalStateException("undecodable trust payload")
            val (next, result) = block(snapshot)
            snapshot = next
            return result
        }

        override suspend fun reset() {
            resetCalls += 1
            corrupt = false
            snapshot = null
        }
    }

    /** A software installation key; the reset deletes this alias before a fresh identity exists. */
    private class SoftwareKeys : IdentityKeyStore {
        private var publicKey: String? = deletedKey

        var deleteCalls = 0
            private set

        override fun publicKey(): String? = publicKey

        override fun generate(): String {
            // A real P-256 point: a fresh identity must be structurally valid.
            val generated = TrustEncoding.publicKey(
                java.security.KeyPairGenerator.getInstance("EC").apply { initialize(256) }
                    .generateKeyPair().public,
            )
            publicKey = generated
            return generated
        }

        override fun sign(message: ByteArray): ByteArray = ByteArray(0)

        override fun delete() {
            deleteCalls += 1
            publicKey = null
        }
    }

    private object RefusingTransport : SessionTransport {
        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean = false

        override fun close(gen: ConnectionGeneration) = Unit
    }

    private companion object {
        const val CLOCK = 1_000L
        const val BOOT = "boot"
    }
}

/** The installation key that exists on disk while the trust payload is corrupt. */
private val deletedKey: String = TrustEncoding.publicKey(
    java.security.KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair().public,
)
