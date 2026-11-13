package me.nagaev.veles.debugpairing

import kotlinx.coroutines.runBlocking
import me.nagaev.veles.debugprobe.DebugTrustInjector
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
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
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
 * The debug Clear control reuses the existing `REVOKE_PEER` implementation through the
 * screen helper. A stranded or injected pending record blocks a fresh pairing
 * (OTP2-STA-012 duplicate refusal); clearing it must delete the record so the same client
 * can re-pair without the OTP-11 revocation protocol.
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
        val commands = commands(ClosedSessions, logs)
        assertTrue(
            "the screen Clear helper reports success",
            clearIncompleteRecord(peer.installationId) { commands },
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
    fun clear_refused_while_authenticated_session_open() = runBlocking {
        repository.identity()
        repository.addPending(peer)
        val logs = mutableListOf<String>()
        val commands = commands(OpenSessions, logs)

        assertFalse(
            "the REVOKE_PEER refusal is visible to the screen",
            clearIncompleteRecord(peer.installationId) { commands },
        )
        assertEquals("the open session keeps its record", 1, repository.peers().size)
        assertTrue(
            "the refusal is logged like the broadcast path",
            logs.any { it.contains("session_open=true") },
        )
    }

    private fun commands(sessions: ProbeSessions, logs: MutableList<String>): ProbeCommands = ProbeCommands(
        sessions = sessions,
        repository = repository,
        injector = DebugTrustInjector(storage, elapsedMillis = { 1_000L }, wallMillis = { 1_000L }),
        handler = ProbeEventSource { SyntheticEvent("", ByteArray(0), ByteArray(0)) },
        log = { logs += it },
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

    private object ClosedSessions : ProbeSessions {
        override fun openFullSessions(): List<ProbeSession> = emptyList()

        override fun hasOpenSession(peerId: String): Boolean = false

        override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean = false
    }

    private object OpenSessions : ProbeSessions {
        override fun openFullSessions(): List<ProbeSession> = emptyList()

        override fun hasOpenSession(peerId: String): Boolean = true

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
