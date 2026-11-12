package me.nagaev.veles.debugpairing

import kotlinx.coroutines.runBlocking
import me.nagaev.veles.debugprobe.DebugTrustInjector
import me.nagaev.veles.debugprobe.ProbeCommands
import me.nagaev.veles.debugprobe.ProbeEventSource
import me.nagaev.veles.debugprobe.ProbeSession
import me.nagaev.veles.debugprobe.ProbeSessions
import me.nagaev.veles.debugprobe.SyntheticEvent
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.sharing.ConnectionGeneration
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The debug Clear control reuses the existing `REVOKE_PEER` implementation. A stranded or
 * injected pending record blocks a fresh pairing (OTP2-STA-012 duplicate refusal); clearing it
 * must delete the record so the same client can re-pair without the OTP-11 revocation protocol.
 */
class ClearThenRepairTest {
    private val identity = TestIdentity.generate()
    private val storage = MemoryStorage()
    private val keyStore = object : IdentityKeyStore {
        private var publicKey: String? = null

        override fun publicKey(): String? = publicKey

        override fun generate(): String {
            val value = TrustEncoding.publicKey(identity.pair.public)
            publicKey = value
            return value
        }

        override fun sign(message: ByteArray): ByteArray = runBlocking { identity.signer.signDer(message) }
    }
    private val repository = TrustRepository(
        storage = storage,
        keys = keyStore,
        elapsedMillis = { 1_000L },
        bootToken = { "boot" },
        wallMillis = { 1_000L },
        activationVerifier = ActivationVerifier { _, _, _ -> true },
    )

    @Test
    fun clear_then_repair_succeeds() = runBlocking {
        repository.identity()
        val peer = PendingPeer(
            installationId = "02".repeat(16),
            publicKey = TrustEncoding.publicKey(P256.generate().public),
            label = "Desktop",
            deviceType = 1,
            enrollmentTranscript = "03".repeat(32),
        )
        repository.addPending(peer)
        assertDuplicateRefused(peer)

        val commands = ProbeCommands(
            sessions = ClosedSessions,
            repository = repository,
            injector = DebugTrustInjector(storage, elapsedMillis = { 1_000L }, wallMillis = { 1_000L }),
            handler = ProbeEventSource { SyntheticEvent("", ByteArray(0), ByteArray(0)) },
            log = {},
        )

        assertTrue("clear reports success", commands.revokePeer(peer.installationId))
        assertTrue("the stranded record is gone", repository.peers().isEmpty())

        // A fresh enrollment with the same installation id now persists its own record.
        repository.addPending(peer)
        assertEquals(
            "re-pairing persists a fresh pending record",
            TrustState.PENDING,
            repository.peers().single().state,
        )
        assertEquals(peer.installationId, repository.peers().single().installationId)
    }

    private suspend fun assertDuplicateRefused(peer: PendingPeer) {
        try {
            repository.addPending(peer)
            fail("the duplicate installation id must block re-pairing before clear")
        } catch (_: IllegalStateException) {
            // TrustException extends IllegalStateException.
        }
    }

    private object ClosedSessions : ProbeSessions {
        override fun openFullSessions(): List<ProbeSession> = emptyList()

        override fun hasOpenSession(peerId: String): Boolean = false

        override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean = false
    }

    private class MemoryStorage : TrustStorage {
        private var value: TrustSnapshot? = null

        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
            val (next, result) = block(value)
            value = next
            return result
        }
    }
}
