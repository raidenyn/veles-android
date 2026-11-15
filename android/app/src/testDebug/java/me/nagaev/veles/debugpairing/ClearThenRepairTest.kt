package me.nagaev.veles.debugpairing

import kotlinx.coroutines.runBlocking
import me.nagaev.veles.debugprobe.DebugTrustInjector
import me.nagaev.veles.debugprobe.PeerManagement
import me.nagaev.veles.debugprobe.ProbeCommands
import me.nagaev.veles.debugprobe.ProbeEventSource
import me.nagaev.veles.debugprobe.ProbeSession
import me.nagaev.veles.debugprobe.ProbeSessions
import me.nagaev.veles.debugprobe.SyntheticEvent
import me.nagaev.veles.pairing.Handle
import me.nagaev.veles.pairing.Outgoing
import me.nagaev.veles.pairing.PairingAdmission
import me.nagaev.veles.pairing.PairingCore
import me.nagaev.veles.pairing.PairingFrameType
import me.nagaev.veles.pairing.PairingFrames
import me.nagaev.veles.pairing.PairingLimits
import me.nagaev.veles.pairing.PairingWindow
import me.nagaev.veles.pairing.PhonePairingMachine
import me.nagaev.veles.pairing.ProofRegistry
import me.nagaev.veles.pairing.Started
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.sharing.ConnectionGeneration
import me.nagaev.veles.sharing.RemovalResult
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.PeerSummary
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustStorage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The debug Clear control (OTP-11 "Clear incomplete record") deletes through the revocation
 * coordinator, so the shared `REVOKE_PEER` entry point no longer refuses while a session of
 * that peer is open: the coordinator deletes the record and terminates the session.
 *
 * A stranded or injected pending record blocks a fresh pairing (OTP2-STA-012 duplicate
 * refusal); clearing it must delete the record so the same client can re-pair.
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

        override fun delete() {
            publicKey = null
        }
    }
    private val repository = TrustRepository(
        storage = storage,
        keys = keyStore,
        elapsedMillis = { 1_000L },
        bootToken = { "boot" },
        wallMillis = { 1_000L },
        activationVerifier = ActivationVerifier { _, _, _ -> true },
    )
    private val peer = PendingPeer(
        installationId = "02".repeat(16),
        publicKey = TrustEncoding.publicKey(P256.generate().public),
        label = "Desktop",
        deviceType = 1,
        enrollmentTranscript = "03".repeat(32),
    )

    @Test
    fun clear_then_repair_succeeds() = runBlocking {
        repository.identity()
        repository.addPending(peer)
        assertDuplicateRefused()

        val logs = mutableListOf<String>()
        val management = RecordingManagement()
        val commands = commands(management, logs)
        assertTrue(
            "the screen Clear helper reports success",
            clearIncompleteRecord(peer.installationId) { commands },
        )
        assertEquals(
            "the clear goes through the coordinator, never the repository refusal path",
            listOf(peer.installationId),
            management.revoked,
        )
        assertTrue("the stranded record is gone", repository.peers().isEmpty())
        assertTrue(
            "the shared REVOKE_PEER log line is preserved",
            logs.any { it == "revoked peer=${peer.installationId.take(8)}" },
        )

        // A fresh enrollment request is admitted and gets a real pairing offer, not the
        // generic preauth rejection that a stranded duplicate record would force.
        val window = PairingWindow({ 1_000L })
        window.open()
        val machine = machine(window)
        try {
            val outgoing = machine.onFrame(requestFrame())
            assertEquals("the window answers with one pairing offer", 1, outgoing.size)
            val offer = outgoing.single() as Outgoing.Frame
            assertEquals(
                "the request is routed to pairing, not generic rejection",
                PairingFrameType.PA.code,
                offer.bytes[0].toInt(),
            )
        } finally {
            machine.onDisconnect()
        }
        assertEquals("no pairing handle survives", 0, core.openHandles())
    }

    @Test
    fun clear_without_a_coordinator_reports_unavailable_and_deletes_nothing() = runBlocking {
        repository.identity()
        repository.addPending(peer)
        val logs = mutableListOf<String>()
        val commands = commands(null, logs)

        assertFalse(
            "a clear without a running coordinator is refused, never a direct trust-store write",
            clearIncompleteRecord(peer.installationId) { commands },
        )
        assertEquals(
            "the stranded record is untouched",
            listOf(peer.installationId),
            repository.peers().map { it.installationId },
        )
        assertTrue(
            "the unavailable path is logged",
            logs.any { it == "revoke unavailable peer=${peer.installationId.take(8)}" },
        )
    }

    @Test
    fun clear_with_an_open_session_deletes_instead_of_refusing() = runBlocking {
        repository.identity()
        repository.addPending(peer)
        val logs = mutableListOf<String>()
        val management = RecordingManagement()
        val commands = commands(management, logs)

        assertTrue(
            "the coordinator-backed clear succeeds while a session is open",
            clearIncompleteRecord(peer.installationId) { commands },
        )
        assertEquals("the record is deleted", emptyList<String>(), repository.peers().map { it.installationId })
        assertEquals("the delete went through the coordinator", listOf(peer.installationId), management.revoked)
        assertTrue("no refusal is logged", logs.none { it.contains("session_open=true") })
    }

    /** Records the coordinator call and performs the coordinator's local delete. */
    private inner class RecordingManagement : PeerManagement {
        val revoked = mutableListOf<String>()

        override suspend fun peers(): List<PeerSummary> = repository.peers().map {
            PeerSummary(
                installationId = it.installationId,
                label = it.label,
                fingerprint = it.fingerprint,
                deviceType = it.deviceType,
                state = it.state,
                createdAtMillis = it.createdAtMillis,
                lastSeenMillis = it.lastSeenMillis,
                connected = false,
            )
        }

        override suspend fun revoke(peerId: String) {
            revoked += peerId
            repository.revoke(peerId)
        }

        override suspend fun removeMutually(peerId: String): RemovalResult = RemovalResult.KEPT_NOT_CONNECTED

        override suspend fun reset() = error("reset is not part of the clear fixture")
    }

    private fun commands(
        management: PeerManagement?,
        logs: MutableList<String>,
    ): ProbeCommands = ProbeCommands(
        sessions = NoSessions,
        repository = repository,
        injector = DebugTrustInjector(storage, elapsedMillis = { 1_000L }, wallMillis = { 1_000L }),
        handler = ProbeEventSource { SyntheticEvent("", ByteArray(0), ByteArray(0)) },
        log = { logs += it },
        revocation = management,
    )

    private suspend fun assertDuplicateRefused() {
        try {
            repository.addPending(peer)
            fail("the duplicate installation id must block re-pairing before clear")
        } catch (_: IllegalStateException) {
            // TrustException extends IllegalStateException.
        }
    }

    private fun machine(window: PairingWindow): PhonePairingMachine = PhonePairingMachine(
        window = window,
        admission = PairingAdmission(PairingLimits(), { 1_000L }),
        conn = 1L,
        trust = repository,
        coreProvider = { core },
        clock = { 1_000L },
        limits = PairingLimits(),
        nonce = { ByteArray(16) { 0x21 } },
        label = { "Phone" },
        identityNonce = { ByteArray(16) { 0x22 } },
        proofs = ProofRegistry(),
    )

    private fun requestFrame(): ByteArray {
        val nonce = ByteArray(16) { 0x31 }
        val caps = byteArrayOf(1, 3)
        val payload = ByteArray(REQUEST_FIXED + caps.size)
        nonce.copyInto(payload, 0)
        payload[VERSION_OFFSET] = 0
        payload[VERSION_OFFSET + 1] = 1
        payload[CAPS_LEN_OFFSET] = 0
        payload[CAPS_LEN_OFFSET + 1] = caps.size.toByte()
        caps.copyInto(payload, REQUEST_FIXED)
        return PairingFrames.encode(PairingFrameType.REQUEST, payload)
    }

    private object NoSessions : ProbeSessions {
        override fun openFullSessions(): List<ProbeSession> = emptyList()

        override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean = false
    }

    private class MemoryStorage : TrustStorage {
        private var value: TrustSnapshot? = null

        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
            val (next, result) = block(value)
            value = next
            return result
        }

        override suspend fun reset() {
            value = null
        }
    }

    private class FakeCore : PairingCore {
        private val open = mutableSetOf<Long>()
        private var next = 0L

        override fun start(role: Role, code: ByteArray, context: ByteArray): Started {
            require(code.size == CODE_BYTES) { "the machine passes the six-digit code" }
            val handle = Handle(++next)
            open += handle.bits
            return Started(handle, ByteArray(POINT_BYTES).also { it[0] = 4 })
        }

        override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray = ByteArray(CONFIRMATION_BYTES) { 0x0a }

        override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray = ByteArray(0)

        override fun finish(handle: Handle): ByteArray {
            open -= handle.bits
            return ByteArray(KEY_BYTES) { 0x11 }
        }

        override fun close(handle: Handle) {
            open -= handle.bits
        }

        override fun openHandles(): Int = open.size
    }

    private companion object {
        const val REQUEST_FIXED = 16 + 2 + 2
        const val VERSION_OFFSET = 16
        const val CAPS_LEN_OFFSET = VERSION_OFFSET + 2
        const val CODE_BYTES = 6
        const val POINT_BYTES = 65
        const val CONFIRMATION_BYTES = 32
        const val KEY_BYTES = 16
        val core = FakeCore()
    }
}
