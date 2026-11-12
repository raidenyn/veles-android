package me.nagaev.veles.sharing

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.pairing.PairingConnection
import me.nagaev.veles.pairing.PairingFrameType
import me.nagaev.veles.pairing.PairingFrames
import me.nagaev.veles.pairing.ProofRegistry
import me.nagaev.veles.pairing.RejectingPairingConnection
import me.nagaev.veles.pairing.VelesPairingCore
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `SessionHost`'s per-connection dispatcher: the first frame selects the pairing machine
 * or the reconnect engine, the reconnect frame set is unchanged, malformed reconnect
 * traffic never spends the pairing budgets, and shutdown releases the pairing execution
 * and the window.
 *
 * Plain JUnit: the pairing path here drives the real host core, which a Robolectric
 * sandbox class loader must never load.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SessionHostRoutingTest {
    private val phone = TestIdentity.generate()
    private val client = TestClient()
    private val clock = FakeClock()
    private val wire = RecordingTransport()
    private val core = VelesPairingCore()
    private var pairingConnections = 0

    private fun TestScope.host(
        pairing: ((ConnectionGeneration) -> PairingConnection)? = null,
        engine: (ConnectionGeneration) -> PhoneSessionEngine = { knownPeerEngine() },
    ): SessionHost {
        val factory: (ConnectionGeneration) -> PairingConnection = { gen ->
            pairingConnections++
            pairing?.invoke(gen) ?: RejectingPairingConnection()
        }
        return SessionHost(backgroundScope, wire, engine, NoTrust, { clock.now }, factory)
    }

    private fun knownPeerEngine(): PhoneSessionEngine = TestPeers.engine(
        phone.signer,
        TestPeers.lookup(TestPeers.record(client)),
        clock,
    )

    private suspend fun TestScope.deliver(host: SessionHost, gen: ConnectionGeneration, frame: ByteArray) {
        host.onConnected(gen)
        runCurrent()
        host.onRecord(gen, frame.copyOf(), RecordAdmission { })
        runCurrent()
    }

    @Test
    fun reconnect_frame_set_unchanged() = runTest {
        val host = host()
        val gen = ConnectionGeneration("aa", 1)

        deliver(host, gen, client.helloFrame)

        val types = wire.sent.filter { it.gen == gen }.map { it.bytes[0].toInt() and U8_MASK }
        assertEquals("the phone hello and signature frames are unchanged", listOf(HELLO, SIGNATURE), types)
        assertEquals("reconnect traffic never builds a pairing machine", 0, pairingConnections)
        assertTrue(wire.closed.isEmpty())

        host.shutdown()
    }

    @Test
    fun default_rejection_answers_every_generation() = runTest {
        // No pairing factory: every generation gets its own default rejection.
        val host = host()

        repeat(DEFAULT_GENERATIONS) { index ->
            val gen = ConnectionGeneration("d$index", index.toLong() + 1)
            deliver(host, gen, requestFrame())
            assertEquals(
                "generation ${index + 1} must get the generic frame and a close",
                listOf("0f0000"),
                wire.sent.filter { it.gen == gen }.map { hex(it.bytes) },
            )
            assertEquals(listOf(gen), wire.closed.filter { it == gen })
        }

        assertEquals("the default path handled every generation", DEFAULT_GENERATIONS, pairingConnections)
        host.shutdown()
    }

    @Test
    fun malformed_pairing_first_frames_spend_the_pairing_budget() = runTest {
        val endpoint = PairingEndpoint({ clock.now }, NoTrust, ProofRegistry(), { core })
        val host = host(pairing = { gen -> endpoint.connection(gen.serial) })

        // `0x02`-`0x05` first frames are unambiguous pairing traffic and count as malformed.
        repeat(MALFORMED_PAIRING_FRAMES) { index ->
            val gen = ConnectionGeneration("bad$index", index.toLong() + 1)
            deliver(host, gen, byteArrayOf(((index % MALFORMED_TYPES) + FIRST_MALFORMED_TYPE).toByte(), 0, 0))
            assertEquals(
                "malformed generation ${index + 1} is rejected generically",
                listOf("0f0000"),
                wire.sent.filter { it.gen == gen }.map { hex(it.bytes) },
            )
        }

        endpoint.window.open()
        val gen = ConnectionGeneration("pairing", PAIRING_GENERATION)
        deliver(host, gen, requestFrame())

        assertEquals(
            "twenty malformed pairing connections block the next request",
            listOf("0f0000"),
            wire.sent.filter { it.gen == gen }.map { hex(it.bytes) },
        )
        assertTrue("no offer is emitted", wire.sent.filter { it.gen == gen }.none { it.bytes[0].toInt() == OFFER })
        assertEquals("every malformed generation used the pairing path", MALFORMED_PAIRING_FRAMES + 1, pairingConnections)

        host.shutdown()
        endpoint.shutdown()
    }

    @Test
    fun one_byte_0x03_first_frame_counts_as_a_malformed_pairing_connection() = runTest {
        val endpoint = PairingEndpoint({ clock.now }, NoTrust, ProofRegistry(), { core })
        val host = host(pairing = { gen -> endpoint.connection(gen.serial) })

        // A single-byte `pairing_client_point`: no envelope, but still `0x03` pairing traffic.
        repeat(MALFORMED_PAIRING_FRAMES) { index ->
            val gen = ConnectionGeneration("short$index", index.toLong() + 1)
            deliver(host, gen, byteArrayOf(0x03))
            assertEquals(
                "short generation ${index + 1} is rejected generically",
                listOf("0f0000"),
                wire.sent.filter { it.gen == gen }.map { hex(it.bytes) },
            )
        }

        endpoint.window.open()
        val gen = ConnectionGeneration("pairing", PAIRING_GENERATION)
        deliver(host, gen, requestFrame())

        assertEquals(
            "twenty one-byte 0x03 frames block the next request",
            listOf("0f0000"),
            wire.sent.filter { it.gen == gen }.map { hex(it.bytes) },
        )
        assertTrue("no offer is emitted", wire.sent.filter { it.gen == gen }.none { it.bytes[0].toInt() == OFFER })

        host.shutdown()
        endpoint.shutdown()
    }

    @Test
    fun no_window_pairing_request_rejected_generically() = runTest {
        val endpoint = PairingEndpoint({ clock.now }, NoTrust, ProofRegistry(), { core })
        val host = host(pairing = { gen -> endpoint.connection(gen.serial) })
        val gen = ConnectionGeneration("aa", 1)

        deliver(host, gen, requestFrame())

        assertEquals(
            "no open window is the generic empty preauth_reject",
            listOf("0f0000"),
            wire.sent.filter { it.gen == gen }.map { hex(it.bytes) },
        )
        assertEquals("the pairing path handled the first frame", 1, pairingConnections)
        assertEquals("the connection is closed", listOf(gen), wire.closed)
        assertEquals("no core execution exists", 0, core.openHandles())
        assertFalse("the window stays closed", endpoint.window.isOpen())

        host.shutdown()
    }

    @Test
    fun reconnect_malformed_does_not_use_pairing_budget() = runTest {
        val endpoint = PairingEndpoint({ clock.now }, NoTrust, ProofRegistry(), { core })
        val host = host(pairing = { gen -> endpoint.connection(gen.serial) })

        // Malformed *reconnect* traffic: a truncated reconnect_hello on each generation.
        repeat(MALFORMED_RECONNECT_CONNECTIONS) { index ->
            val gen = ConnectionGeneration("m$index", index.toLong() + 1)
            deliver(host, gen, TRUNCATED_HELLO.copyOf())
        }

        endpoint.window.open()
        val gen = ConnectionGeneration("pairing", PAIRING_GENERATION)
        deliver(host, gen, requestFrame())

        assertEquals(
            "the pairing malformed-connection budget is untouched by reconnect traffic",
            OFFER,
            wire.sent.filter { it.gen == gen }.single().bytes[0].toInt() and U8_MASK,
        )

        host.shutdown()
        endpoint.shutdown()
    }

    @Test
    fun shutdown_erases_window_and_closes_handle() = runTest {
        val endpoint = PairingEndpoint({ clock.now }, NoTrust, ProofRegistry(), { core })
        val host = host(pairing = { gen -> endpoint.connection(gen.serial) })
        endpoint.window.open()
        val gen = ConnectionGeneration("aa", 1)

        deliver(host, gen, requestFrame())
        assertEquals("the offer holds one core handle", 1, core.openHandles())
        assertTrue(endpoint.window.isOpen())

        host.shutdown()
        endpoint.shutdown()
        runCurrent()

        assertEquals("service shutdown closes the execution", 0, core.openHandles())
        assertFalse("service shutdown closes the window", endpoint.window.isOpen())
        assertTrue("the pairing id is erased", endpoint.window.pairingId.isEmpty())
    }

    private fun requestFrame(): ByteArray {
        val caps = byteArrayOf(0x01)
        val payload = ByteArray(REQUEST_FIXED_BYTES + caps.size)
        payload[CAPS_LEN_OFFSET + 1] = caps.size.toByte()
        caps.copyInto(payload, REQUEST_FIXED_BYTES)
        payload[VERSION_OFFSET + 1] = 1
        return PairingFrames.encode(PairingFrameType.REQUEST, payload)
    }

    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { (it.toInt() and U8_MASK).toString(16).padStart(2, '0') }

    private class RecordingTransport : SessionTransport {
        val sent = mutableListOf<Wire.Sent>()
        val closed = mutableListOf<ConnectionGeneration>()

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
            sent += Wire.Sent(gen, bytes.copyOf(), token)
            return true
        }

        override fun close(gen: ConnectionGeneration) {
            closed += gen
        }
    }

    private sealed interface Wire {
        data class Sent(val gen: ConnectionGeneration, val bytes: ByteArray, val token: Long?)
    }

    private object NoTrust : InstallationTrustRepository {
        override suspend fun identity(): InstallationIdentity = error("not used")
        override suspend fun peers(): List<TrustedPeer> = emptyList()
        override suspend fun activePeers(): List<TrustedPeer> = emptyList()
        override suspend fun addPending(peer: PendingPeer) = error("not used")
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("not used")
        override suspend fun revoke(peerId: String) = error("not used")
        override suspend fun markAuthenticated(peerId: String) = error("not used")
    }

    private companion object {
        const val U8_MASK = 0xFF
        const val HELLO = 0x06
        const val SIGNATURE = 0x07
        const val OFFER = 0x02
        const val REQUEST_FIXED_BYTES = 20
        const val VERSION_OFFSET = 16
        const val CAPS_LEN_OFFSET = 18
        const val MALFORMED_RECONNECT_CONNECTIONS = 21
        const val DEFAULT_GENERATIONS = 2
        const val MALFORMED_PAIRING_FRAMES = 20
        const val MALFORMED_TYPES = 4
        const val FIRST_MALFORMED_TYPE = 0x02
        const val PAIRING_GENERATION = 100L
        val TRUNCATED_HELLO = byteArrayOf(0x06, 0, 5, 1)
    }
}
