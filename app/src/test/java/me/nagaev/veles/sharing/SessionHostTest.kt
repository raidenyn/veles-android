package me.nagaev.veles.sharing

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.IdentitySigner
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.PreAuthCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SessionLimits
import me.nagaev.veles.session.SignatureFrame
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.session.openPhoneRecord
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustedPeer
import me.nagaev.veles.trust.TrustEncoding
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Android transport adapter (design D4): one engine and one mutex per connection generation,
 * serialized engine work, generation-scoped async results, and a lastSeen write only on the
 * current, open generation's [me.nagaev.veles.session.SessionEffect.Authenticated] effect.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SessionHostTest {
    private val phone = TestIdentity.generate()
    private val clock = FakeClock()
    private val wire = RecordingTransport()
    private val trust = RecordingTrust()

    private fun TestScope.host(
        transport: SessionTransport = wire,
        repository: InstallationTrustRepository = trust,
        factory: (ConnectionGeneration) -> PhoneSessionEngine,
    ): SessionHost = SessionHost(backgroundScope, transport, factory, repository) { clock.now }

    private fun engine(
        client: TestClient,
        state: LocalRecordState = LocalRecordState.ACTIVE,
        signer: IdentitySigner = phone.signer,
        handler: ApplicationHandler? = null,
    ): PhoneSessionEngine = TestPeers.engine(signer, TestPeers.lookup(TestPeers.record(client, state)), clock, handler)

    private fun clientLike(client: TestClient) =
        TestClient(identity = client.identity, installationId = client.installationId, ephemeral = P256.generate())

    private suspend fun TestScope.connect(host: SessionHost, gen: ConnectionGeneration) {
        host.onConnected(gen)
        runCurrent()
    }

    private suspend fun TestScope.authenticate(
        host: SessionHost,
        gen: ConnectionGeneration,
        client: TestClient,
        transport: RecordingTransport = wire,
    ) {
        host.onRecord(gen, client.helloFrame.copyOf())
        runCurrent()
        val handshake = transport.sent.filter { it.gen == gen }
        assertEquals("phone hello and signature", 2, handshake.size)
        val phoneHelloPayload = PreAuthCodec.decode(handshake[0].bytes).second
        val phoneSignature = SignatureFrame.decode(
            PreAuthCodec.decode(handshake[1].bytes).second,
            Role.PHONE,
        ).signature
        client.acceptPhoneHello(phoneHelloPayload, phoneSignature)
        host.onRecord(gen, client.signatureFrame())
        runCurrent()
        val token = requireNotNull(transport.sent.last().token) { "the phone reconnect_confirm must be tokened" }
        host.onRecord(gen, client.confirmRecord())
        runCurrent()
        host.onSendComplete(gen, token, true)
        runCurrent()
    }

    @Test
    fun linkLossDuringSigningDiscardsResult() = runTest {
        val client = TestClient()
        val release = CompletableDeferred<Unit>()
        val signer = IdentitySigner { message ->
            release.await()
            phone.signer.signDer(message)
        }
        lateinit var created: PhoneSessionEngine
        val host = host { created = engine(client, signer = signer); created }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        host.onRecord(gen, client.helloFrame.copyOf())
        runCurrent()
        assertTrue("the signer is still pending", wire.sent.isEmpty())

        host.onDisconnected(gen)
        release.complete(Unit)
        runCurrent()

        assertTrue("a stale signing result is never sent", wire.calls.none { it is Wire.Sent })
        assertFalse("the abandoned engine is torn down", created.isOpen)
    }

    @Test
    fun sameAddressNewGenerationStartsFresh() = runTest {
        val first = TestClient()
        val second = clientLike(first)
        val engines = mutableListOf<PhoneSessionEngine>()
        val host = host { gen -> (if (gen.serial == 1L) engine(first) else engine(second)).also { engines += it } }

        val old = ConnectionGeneration("aa", 1)
        connect(host, old)
        host.onRecord(old, first.helloFrame.copyOf())
        runCurrent()
        host.onDisconnected(old)
        runCurrent()

        val fresh = ConnectionGeneration("aa", 2)
        connect(host, fresh)
        authenticate(host, fresh, second)

        assertTrue("the reconnected generation opens its own session", engines[1].isOpen)
        assertFalse("the dropped generation is closed", engines[0].isOpen)
        assertEquals(2, engines.size)
    }

    @Test
    fun twoGenerationsSameInstallationAreIndependent() = runTest {
        val first = TestClient()
        val second = clientLike(first)
        val clients = mapOf("aa" to first, "bb" to second)
        val engines = mutableMapOf<String, PhoneSessionEngine>()
        val host = host { gen -> engine(clients.getValue(gen.address)).also { engines[gen.address] = it } }
        val a = ConnectionGeneration("aa", 1)
        val b = ConnectionGeneration("bb", 2)
        connect(host, a)
        connect(host, b)
        authenticate(host, a, first)
        authenticate(host, b, second)

        assertTrue(engines.getValue("aa").isOpen)
        assertTrue(engines.getValue("bb").isOpen)
        assertEquals(
            "both generations stamp the same installation exactly once",
            listOf(TrustEncoding.hex(first.installationId), TrustEncoding.hex(first.installationId)),
            trust.marked,
        )

        host.onRecord(b, byteArrayOf(1, 2, 3))
        runCurrent()
        assertFalse("the failed generation ends", engines.getValue("bb").isOpen)
        assertTrue("the other generation is unaffected", engines.getValue("aa").isOpen)
    }

    @Test
    fun terminalFrameQueuedBeforeClose() = runTest {
        val unknown = TestClient()
        val host = host { TestPeers.engine(phone.signer, TestPeers.lookup(null), clock) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        host.onRecord(gen, unknown.helloFrame.copyOf())
        runCurrent()

        assertEquals(2, wire.calls.size)
        val reject = wire.calls[0] as Wire.Sent
        assertEquals(gen, reject.gen)
        assertEquals(SessionEffectBytes.PREAUTH_REJECT, reject.bytes.toList())
        assertNull(reject.token)
        assertEquals(Wire.Closed(gen), wire.calls[1])
        assertTrue("nothing is sent after the terminal frame", wire.calls.drop(2).isEmpty())
    }

    @Test
    fun sendFailureTearsDown() = runTest {
        val client = TestClient()
        lateinit var created: PhoneSessionEngine
        val failing = RecordingTransport(accept = false)
        val host = host(transport = failing) { created = engine(client); created }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        host.onRecord(gen, client.helloFrame.copyOf())
        runCurrent()

        assertEquals("the first refused send stops the handshake", 1, failing.sent.size)
        assertEquals(Wire.Closed(gen), failing.calls.last())
        assertFalse(created.isOpen)
    }

    @Test
    fun maxSizeRecordThroughAdapterAccepted() = runTest {
        val client = TestClient()
        var delivered: ByteArray? = null
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                delivered = body.copyOf()
            }
        }
        val host = host { engine(client, handler = handler) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)

        val body = ByteArray(4057) { (it and 0xff).toByte() }
        val record = client.record(RecordType.PULL_REQUEST.wire, 1uL, body)
        assertEquals(4096, record.size)
        host.onRecord(gen, record.copyOf())
        runCurrent()

        assertTrue("the maximum-size record is delivered intact", delivered?.contentEquals(body) == true)
        assertTrue("no teardown for a valid record", wire.calls.none { it is Wire.Closed })
    }

    @Test
    fun confirmCompletionPendingThenPeerConfirm() = runTest {
        val client = TestClient()
        lateinit var created: PhoneSessionEngine
        val host = host { created = engine(client); created }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        host.onRecord(gen, client.helloFrame.copyOf())
        runCurrent()
        val handshake = wire.sent.filter { it.gen == gen }
        client.acceptPhoneHello(
            PreAuthCodec.decode(handshake[0].bytes).second,
            SignatureFrame.decode(PreAuthCodec.decode(handshake[1].bytes).second, Role.PHONE).signature,
        )
        host.onRecord(gen, client.signatureFrame())
        runCurrent()
        val token = requireNotNull(wire.sent.last().token)
        host.onRecord(gen, client.confirmRecord())
        runCurrent()
        assertTrue("the peer confirm alone must not authenticate", trust.marked.isEmpty())

        host.onSendComplete(gen, token, true)
        runCurrent()
        assertEquals(listOf(TrustEncoding.hex(client.installationId)), trust.marked)
        assertTrue(created.isOpen)

        val failingClient = TestClient()
        val failingTransport = RecordingTransport()
        val failingTrust = RecordingTrust()
        val failingHost = host(transport = failingTransport, repository = failingTrust) { engine(failingClient) }
        val failingGen = ConnectionGeneration("bb", 2)
        connect(failingHost, failingGen)
        failingHost.onRecord(failingGen, failingClient.helloFrame.copyOf())
        runCurrent()
        val failingHandshake = failingTransport.sent.filter { it.gen == failingGen }
        failingClient.acceptPhoneHello(
            PreAuthCodec.decode(failingHandshake[0].bytes).second,
            SignatureFrame.decode(PreAuthCodec.decode(failingHandshake[1].bytes).second, Role.PHONE).signature,
        )
        failingHost.onRecord(failingGen, failingClient.signatureFrame())
        runCurrent()
        val failingToken = requireNotNull(failingTransport.sent.last().token)
        failingHost.onRecord(failingGen, failingClient.confirmRecord())
        runCurrent()
        failingHost.onSendComplete(failingGen, failingToken, false)
        runCurrent()
        assertTrue("a failed completion never stamps lastSeen", failingTrust.marked.isEmpty())
    }

    @Test
    fun recordBufferCopiedBeforeCallerZeroes() = runTest {
        val client = TestClient()
        val host = host { engine(client) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        // The GATT adapter copies the reassembled record and zeroes its own buffer before the
        // callback; the engine must still see intact bytes once its serialized work runs.
        val transportBuffer = client.helloFrame.copyOf()
        val owned = transportBuffer.copyOf()
        transportBuffer.fill(0)
        host.onRecord(gen, owned)
        assertTrue("engine execution waits for the callback to return", wire.sent.isEmpty())

        runCurrent()
        assertEquals(2, wire.sent.size)
        assertTrue("the host zeroes its owned copy after processing", owned.all { it == 0.toByte() })
    }

    @Test
    fun tickFiresIdleTeardownWithoutInbound() = runTest {
        val client = TestClient()
        lateinit var created: PhoneSessionEngine
        val host = host { created = engine(client); created }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)

        clock.advance(SessionLimits.IDLE_TIMEOUT_MS)
        advanceTimeBy(1_000L)
        runCurrent()

        assertFalse(created.isOpen)
        assertEquals(Wire.Closed(gen), wire.calls.last())
    }

    @Test
    fun tickSendsHeartbeatWithoutInbound() = runTest {
        val client = TestClient()
        val host = host { engine(client) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)

        clock.advance(60_000L)
        advanceTimeBy(1_000L)
        runCurrent()

        val heartbeat = wire.sent.last()
        assertNull(heartbeat.token)
        assertEquals(RecordType.APP_HEARTBEAT.wire, client.openPhoneRecord(heartbeat.bytes, 1uL).type)
    }

    @Test
    fun wakeAfterExpiryTearsDownOnFirstTick() = runTest {
        val client = TestClient()
        lateinit var created: PhoneSessionEngine
        val host = host { created = engine(client); created }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        // The device slept through the whole handshake deadline: a single tick after wake ends it.
        clock.advance(SessionLimits.HANDSHAKE_TIMEOUT_MS)
        advanceTimeBy(1_000L)
        runCurrent()

        assertFalse(created.isOpen)
        assertEquals(Wire.Closed(gen), wire.calls.last())
    }

    @Test
    fun staleGenerationDoesNotMarkAuthenticated() = runTest {
        val client = TestClient()
        val host = host { engine(client) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        host.onRecord(gen, client.helloFrame.copyOf())
        runCurrent()
        val handshake = wire.sent.filter { it.gen == gen }
        client.acceptPhoneHello(
            PreAuthCodec.decode(handshake[0].bytes).second,
            SignatureFrame.decode(PreAuthCodec.decode(handshake[1].bytes).second, Role.PHONE).signature,
        )
        host.onRecord(gen, client.signatureFrame())
        runCurrent()
        val token = requireNotNull(wire.sent.last().token)
        host.onRecord(gen, client.confirmRecord())
        runCurrent()

        host.onSendComplete(gen, token, true)
        host.onDisconnected(gen)
        runCurrent()

        assertTrue("a stale completion never stamps lastSeen", trust.marked.isEmpty())
        assertFalse(trust.marked.contains(TrustEncoding.hex(client.installationId)))
    }

    @Test
    fun openFullSessionsExcludesRecoveryClass() = runTest {
        val full = TestClient()
        val recovery = TestClient()
        val host = host { gen ->
            if (gen.address == "full") {
                engine(full, LocalRecordState.ACTIVE)
            } else {
                engine(recovery, LocalRecordState.PENDING)
            }
        }
        val fullGen = ConnectionGeneration("full", 1)
        val recoveryGen = ConnectionGeneration("recovery", 2)
        connect(host, fullGen)
        connect(host, recoveryGen)
        authenticate(host, fullGen, full)
        authenticate(host, recoveryGen, recovery)

        assertEquals(listOf(fullGen), host.openFullSessions())
        assertTrue(host.hasOpenSession(TrustEncoding.hex(full.installationId)))
        assertTrue(host.hasOpenSession(TrustEncoding.hex(recovery.installationId)))
        assertFalse(host.hasOpenSession("00".repeat(16)))
    }

    private object SessionEffectBytes {
        val PREAUTH_REJECT = listOf(0x0F.toByte(), 0, 0)
    }

    private sealed interface Wire {
        data class Sent(val gen: ConnectionGeneration, val bytes: ByteArray, val token: Long?) : Wire
        data class Closed(val gen: ConnectionGeneration) : Wire
    }

    private class RecordingTransport(var accept: Boolean = true) : SessionTransport {
        val calls = mutableListOf<Wire>()
        val sent get() = calls.filterIsInstance<Wire.Sent>()

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
            calls += Wire.Sent(gen, bytes.copyOf(), token)
            return accept
        }

        override fun close(gen: ConnectionGeneration) {
            calls += Wire.Closed(gen)
        }
    }

    private class RecordingTrust : InstallationTrustRepository {
        val marked = mutableListOf<String>()

        override suspend fun identity(): InstallationIdentity = error("not used by the adapter")
        override suspend fun peers(): List<TrustedPeer> = emptyList()
        override suspend fun activePeers(): List<TrustedPeer> = emptyList()
        override suspend fun addPending(peer: PendingPeer) = error("not used by the adapter")
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("not used by the adapter")
        override suspend fun revoke(peerId: String) = error("not used by the adapter")
        override suspend fun markAuthenticated(peerId: String) {
            marked += peerId
        }
    }
}
