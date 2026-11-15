package me.nagaev.veles.sharing

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import me.nagaev.veles.pairing.Outgoing
import me.nagaev.veles.pairing.PairingConnection
import me.nagaev.veles.pairing.RejectingPairingConnection
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.IdentitySigner
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.PreAuthCodec
import me.nagaev.veles.session.RecordCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SessionLimits
import me.nagaev.veles.session.SignatureFrame
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.session.UnpromptedApplication
import me.nagaev.veles.session.openPhoneRecord
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

/**
 * Android transport adapter (design D4): one engine and one mutex per connection generation,
 * serialized engine work, generation-scoped async results, and a lastSeen write only on the
 * current, open generation's [me.nagaev.veles.session.SessionEffect.Authenticated] effect.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@Suppress("LargeClass") // One class per adapter surface keeps the generation tests together.
class SessionHostTest {
    private val phone = TestIdentity.generate()
    private val clock = FakeClock()
    private val wire = RecordingTransport()
    private val trust = RecordingTrust()

    private fun TestScope.host(
        transport: SessionTransport = wire,
        repository: InstallationTrustRepository = trust,
        now: () -> Long = { clock.now },
        onSessionClosed: (String) -> Unit = {},
        pairingFactory: (ConnectionGeneration) -> PairingConnection = { RejectingPairingConnection() },
        factory: (ConnectionGeneration) -> PhoneSessionEngine,
    ): SessionHost = SessionHost(
        backgroundScope,
        transport,
        factory,
        repository,
        now,
        pairingFactory,
        onSessionClosed = onSessionClosed,
    )

    private fun engine(
        client: TestClient,
        state: LocalRecordState = LocalRecordState.ACTIVE,
        signer: IdentitySigner = phone.signer,
        handler: ApplicationHandler? = null,
    ): PhoneSessionEngine = TestPeers.engine(signer, TestPeers.lookup(TestPeers.record(client, state)), clock, handler)

    /** Engine whose clock is the test scheduler, for tests that advance virtual time. */
    private fun engineAt(client: TestClient, now: () -> Long, handler: ApplicationHandler? = null): PhoneSessionEngine = PhoneSessionEngine(
        localInstallationId = TestPeers.phoneInstallationId,
        signer = phone.signer,
        lookup = TestPeers.lookup(TestPeers.record(client)),
        clock = now,
        ephemeral = { TestPeers.phoneEphemeral },
        nonce = { TestPeers.phoneNonce },
        handler = handler,
    )

    @Suppress("MaxLineLength") // ktlint keeps this expression body on one line.
    private fun clientLike(client: TestClient) = TestClient(identity = client.identity, installationId = client.installationId, ephemeral = P256.generate())

    private fun admission(): RecordAdmission = RecordAdmission { }

    /** A valid fixed-width `event_ack` body whose first byte carries an ordering marker. */
    private fun ackBody(marker: Int): ByteArray = ByteArray(32).also { it[0] = marker.toByte() }

    private class RecordingAdmission : RecordAdmission {
        var releases = 0
            private set

        override fun release() {
            releases++
        }
    }

    private suspend fun TestScope.connect(host: SessionHost, gen: ConnectionGeneration) {
        host.onConnected(gen)
        runCurrent()
    }

    private suspend fun TestScope.authenticate(
        host: SessionHost,
        gen: ConnectionGeneration,
        client: TestClient,
        transport: RecordingTransport = wire,
        peerState: Int = TestClient.PEER_STATE_ACTIVE,
    ) {
        host.onRecord(gen, client.helloFrame.copyOf(), admission())
        runCurrent()
        val handshake = transport.sent.filter { it.gen == gen }
        assertEquals("phone hello and signature", 2, handshake.size)
        val phoneHelloPayload = PreAuthCodec.decode(handshake[0].bytes).second
        val phoneSignature = SignatureFrame.decode(
            PreAuthCodec.decode(handshake[1].bytes).second,
            Role.PHONE,
        ).signature
        client.acceptPhoneHello(phoneHelloPayload, phoneSignature)
        host.onRecord(gen, client.signatureFrame(), admission())
        runCurrent()
        val token = requireNotNull(transport.sent.last().token) { "the phone reconnect_confirm must be tokened" }
        host.onRecord(gen, client.confirmRecord(peerState), admission())
        runCurrent()
        host.onSendComplete(gen, token, true)
        runCurrent()
    }

    @Test
    fun unpromptedRecordIsSentOnlyAfterMarkAuthenticated() = runTest {
        val client = TestClient()
        val body = ByteArray(32) { 0x11 }
        val events = mutableListOf<String>()
        val transport = RecordingTransport(events = events)
        val trust = RecordingTrust(events = events)
        val host = host(transport = transport, repository = trust) {
            engine(client, state = LocalRecordState.PENDING, handler = FixedUnpromptedHandler(body))
        }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        authenticate(host, gen, client, transport, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)

        val prepareSends = transport.sent.filter { wireTypeOf(it.bytes) == RecordType.ENROLLMENT_PREPARE.wire }
        assertEquals("the owed prepare reached the transport", 1, prepareSends.size)
        val mark = events.indexOf("mark")
        val send = events.indexOf("send:260")
        assertTrue("the authentication mark happened", mark >= 0)
        assertTrue("the prepare send happened", send >= 0)
        assertTrue("the prepare is sent only after markAuthenticated", mark < send)
        assertTrue("the handler's body is erased after the send", body.all { it == 0.toByte() })
    }

    @Test
    fun unpromptedRecordIsNotSentWhenMarkAuthenticatedFails() = runTest {
        val client = TestClient()
        val body = ByteArray(32) { 0x22 }
        val transport = RecordingTransport()
        val trust = RecordingTrust(failMark = true)
        val host = host(transport = transport, repository = trust) {
            engine(client, state = LocalRecordState.PENDING, handler = FixedUnpromptedHandler(body))
        }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        authenticate(host, gen, client, transport, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)

        assertTrue(
            "a failed authentication mark sends nothing",
            transport.sent.none { wireTypeOf(it.bytes) == RecordType.ENROLLMENT_PREPARE.wire },
        )
        assertArrayEquals("the unsent body is zeroed", ByteArray(32), body)
        assertTrue("the failed generation is torn down", transport.calls.any { it is Wire.Closed })
    }

    private class FixedUnpromptedHandler(private val body: ByteArray) : ApplicationHandler {
        override fun handles(type: Int): Boolean = false

        override suspend fun handle(
            type: Int,
            body: ByteArray,
            reply: suspend (type: Int, body: ByteArray) -> Unit,
        ) = Unit

        override fun unprompted(): List<UnpromptedApplication> = listOf(UnpromptedApplication(RecordType.ENROLLMENT_PREPARE.wire, body))
    }

    @Test
    fun sessionTeardownRevokesItsProofs() = runTest {
        val client = TestClient()
        val revoked = mutableListOf<String>()
        val host = host(onSessionClosed = { revoked += it }) { engine(client) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)

        host.onDisconnected(gen)
        runCurrent()

        assertEquals("the session's proofs are revoked once on teardown", 1, revoked.size)
        assertEquals("the revocation is keyed by the 8-byte session prefix", 16, revoked.single().length)
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
        val host = host {
            created = engine(client, signer = signer)
            created
        }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        host.onRecord(gen, client.helloFrame.copyOf(), admission())
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
        host.onRecord(old, first.helloFrame.copyOf(), admission())
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

        host.onRecord(b, byteArrayOf(1, 2, 3), admission())
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

        host.onRecord(gen, unknown.helloFrame.copyOf(), admission())
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
        val host = host(transport = failing) {
            created = engine(client)
            created
        }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        host.onRecord(gen, client.helloFrame.copyOf(), admission())
        runCurrent()

        assertEquals("the first refused send stops the handshake", 1, failing.sent.size)
        assertEquals(Wire.Closed(gen), failing.calls.last())
        assertFalse(created.isOpen)
    }

    @Test
    fun maximumSizeBodyIsACodecBoundNotAnApplicationBody() = runTest {
        val client = TestClient()
        var delivered: ByteArray? = null
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.EVENT_ACK.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                delivered = body.copyOf()
            }
        }
        val host = host { engine(client, handler = handler) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)

        // Codec/transport size acceptance is a separate layer from application-body validity:
        // the largest record opens at 4096 bytes for a handler-owned event payload.
        val largest = ByteArray(RecordCodec.MAX_CIPHERTEXT) { (it and 0xff).toByte() }
        val sealed = RecordCodec.seal(
            client.keys.p2cKey,
            client.keys.p2cPrefix,
            client.keys.sessionId.copyOfRange(0, 8),
            Direction.PHONE_TO_CLIENT,
            RecordType.PUSH_EVENT.wire,
            0uL,
            largest,
        )
        assertEquals(4096, sealed.size)
        assertArrayEquals(
            largest,
            RecordCodec.open(
                client.keys.p2cKey,
                client.keys.p2cPrefix,
                client.keys.sessionId.copyOfRange(0, 8),
                Direction.PHONE_TO_CLIENT,
                0uL,
                sealed,
            ).body,
        )

        // A valid application body travels through the adapter intact and without teardown.
        val ack = ByteArray(32) { (it + 1).toByte() }
        host.onRecord(gen, client.record(RecordType.EVENT_ACK.wire, 1uL, ack), admission())
        runCurrent()
        assertTrue("the valid ack is delivered intact", delivered?.contentEquals(ack) == true)
        assertTrue("no teardown for a valid record", wire.calls.none { it is Wire.Closed })
    }

    @Test
    fun maximumSizePullRequestBodyIsNotAnApplicationBody() = runTest {
        val client = TestClient()
        var delivered = false
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                delivered = true
            }
        }
        val host = host { engine(client, handler = handler) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)

        // OTP2-REC-031: `pull_request` is empty. A 4057-byte body fits the record format but is
        // not a valid application body, so it must never reach the handler.
        val body = ByteArray(RecordCodec.MAX_CIPHERTEXT) { (it and 0xff).toByte() }
        val record = client.uncheckedRecord(RecordType.PULL_REQUEST.wire, 1uL, body)
        assertEquals(4096, record.size)
        host.onRecord(gen, record.copyOf(), admission())
        runCurrent()

        assertFalse("a semantically invalid pull body is never delivered", delivered)
        assertTrue("the session is torn down", wire.calls.any { it is Wire.Closed })
    }

    @Test
    fun confirmCompletionPendingThenPeerConfirm() = runTest {
        val client = TestClient()
        lateinit var created: PhoneSessionEngine
        val host = host {
            created = engine(client)
            created
        }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        host.onRecord(gen, client.helloFrame.copyOf(), admission())
        runCurrent()
        val handshake = wire.sent.filter { it.gen == gen }
        client.acceptPhoneHello(
            PreAuthCodec.decode(handshake[0].bytes).second,
            SignatureFrame.decode(PreAuthCodec.decode(handshake[1].bytes).second, Role.PHONE).signature,
        )
        host.onRecord(gen, client.signatureFrame(), admission())
        runCurrent()
        val token = requireNotNull(wire.sent.last().token)
        host.onRecord(gen, client.confirmRecord(), admission())
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
        failingHost.onRecord(failingGen, failingClient.helloFrame.copyOf(), admission())
        runCurrent()
        val failingHandshake = failingTransport.sent.filter { it.gen == failingGen }
        failingClient.acceptPhoneHello(
            PreAuthCodec.decode(failingHandshake[0].bytes).second,
            SignatureFrame.decode(PreAuthCodec.decode(failingHandshake[1].bytes).second, Role.PHONE).signature,
        )
        failingHost.onRecord(failingGen, failingClient.signatureFrame(), admission())
        runCurrent()
        val failingToken = requireNotNull(failingTransport.sent.last().token)
        failingHost.onRecord(failingGen, failingClient.confirmRecord(), admission())
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
        val admission = RecordingAdmission()
        host.onRecord(gen, owned, admission)
        assertTrue("engine execution waits for the callback to return", wire.sent.isEmpty())

        runCurrent()
        assertEquals(2, wire.sent.size)
        assertTrue("the host zeroes its owned copy after processing", owned.all { it == 0.toByte() })
        assertEquals("the ingress admission is released after the record is erased", 1, admission.releases)
    }

    @Test
    fun inboundAdmissionIsHeldUntilTheEngineErasesTheRecord() = runTest {
        val client = TestClient()
        val release = CompletableDeferred<Unit>()
        val signer = IdentitySigner { message ->
            release.await()
            phone.signer.signDer(message)
        }
        val host = host { engine(client, signer = signer) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        val bytes = client.helloFrame.copyOf()
        val admission = RecordingAdmission()
        host.onRecord(gen, bytes, admission)
        runCurrent()
        assertEquals("a suspended engine still owns the admission", 0, admission.releases)
        assertTrue("the record is intact while the engine processes it", bytes.any { it != 0.toByte() })

        release.complete(Unit)
        runCurrent()
        assertEquals("the admission is released exactly once after processing", 1, admission.releases)
        assertTrue(bytes.all { it == 0.toByte() })
    }

    @Test
    fun shutdownErasesHeldRecordsEvenWithASuspendedEngine() = runTest {
        val client = TestClient()
        val release = CompletableDeferred<Unit>()
        val signer = IdentitySigner { message ->
            release.await()
            phone.signer.signDer(message)
        }
        val host = host { engine(client, signer = signer) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        val first = client.helloFrame.copyOf()
        val second = client.helloFrame.copyOf()
        val firstAdmission = RecordingAdmission()
        val secondAdmission = RecordingAdmission()
        host.onRecord(gen, first, firstAdmission)
        runCurrent()
        host.onRecord(gen, second, secondAdmission)
        runCurrent()
        assertEquals("the in-flight engine step holds both", 0, firstAdmission.releases)

        host.shutdown()

        assertEquals("the in-flight record is erased despite the suspended engine", 1, firstAdmission.releases)
        assertEquals("the queued record is erased too", 1, secondAdmission.releases)
        assertTrue(first.all { it == 0.toByte() })
        assertTrue(second.all { it == 0.toByte() })

        release.complete(Unit)
        runCurrent()
        assertEquals("erasure is idempotent", 1, firstAdmission.releases)
    }

    @Test
    fun tickProducerKeepsRunningWhileTheEngineIsSuspended() = runTest {
        val client = TestClient()
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.EVENT_ACK.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                entered.complete(Unit)
                gate.await()
            }
        }
        val host = host(now = { testScheduler.currentTime }) { engineAt(client, { testScheduler.currentTime }, handler) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)
        val beforeTicks = wire.sent.size

        host.onRecord(gen, client.record(RecordType.EVENT_ACK.wire, 1uL, ackBody(9)), admission())
        runCurrent()
        assertTrue("the handler holds the worker", entered.isCompleted)

        // A slow handler must not let coalesced ticks fill and stop the producer.
        advanceTimeBy(60_000L)
        runCurrent()
        gate.complete(Unit)
        runCurrent()
        val afterResume = wire.sent.size
        assertTrue("the queued tick still emits a heartbeat", afterResume > beforeTicks)

        advanceTimeBy(60_000L)
        runCurrent()
        assertTrue("the tick producer survived saturation", wire.sent.size > afterResume)
    }

    @Test
    fun shutdownFailsWaitingApplicationSenders() = runTest {
        val client = TestClient()
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.EVENT_ACK.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                entered.complete(Unit)
                gate.await()
            }
        }
        val host = host { engine(client, handler = handler) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)
        host.onRecord(gen, client.record(RecordType.EVENT_ACK.wire, 1uL, ackBody(9)), admission())
        runCurrent()
        assertTrue(entered.isCompleted)

        var result: Boolean? = null
        val caller = launch { result = host.sendApplication(gen, RecordType.PUSH_EVENT.wire, ByteArray(0)) }
        runCurrent()
        assertNull("the worker is still suspended", result)

        host.shutdown()
        runCurrent()

        assertTrue("shutdown completes the queued caller", caller.isCompleted)
        assertEquals(false, result)
        gate.complete(Unit)
        runCurrent()
    }

    @Test
    fun saturatedQueueFailsAdditionalApplicationSendersWithoutStranding() = runTest {
        val client = TestClient()
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.EVENT_ACK.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                entered.complete(Unit)
                gate.await()
            }
        }
        val host = host { engine(client, handler = handler) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)
        // Occupy the worker so nothing drains the queue.
        host.onRecord(gen, client.record(RecordType.EVENT_ACK.wire, 1uL, ackBody(9)), admission())
        runCurrent()
        assertTrue(entered.isCompleted)

        // The queue holds 32 steps: the first 32 callers are accepted and suspended on their own
        // deferred, every further caller must fail immediately instead of suspending on admission.
        val results = mutableListOf<Boolean>()
        val callers = List(SATURATION_ATTEMPTS) {
            launch { results += host.sendApplication(gen, RecordType.PUSH_EVENT.wire, ByteArray(0)) }
        }
        runCurrent()
        assertEquals("only the refused caller returned before shutdown", 1, results.size)
        assertEquals(false, results.single())

        host.shutdown()
        runCurrent()

        assertTrue("no accepted caller is stranded", callers.all { it.isCompleted })
        assertEquals(SATURATION_ATTEMPTS, results.size)
        assertTrue("every caller fails deterministically", results.all { !it })
        gate.complete(Unit)
        runCurrent()
    }

    @Test
    fun concurrentSubmissionKeepsRecordOrderWhileTheEngineIsSuspended() = runBlocking {
        val client = TestClient()
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val seen = mutableListOf<Int>()
        val active = AtomicInteger()
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.EVENT_ACK.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                check(active.incrementAndGet() == 1) { "engine calls must never overlap" }
                val marker = body[0].toInt()
                synchronized(seen) { seen += marker }
                if (marker == 0) {
                    entered.complete(Unit)
                    gate.await()
                }
                active.decrementAndGet()
            }
        }
        val transport = RecordingTransport()
        val repository = RecordingTrust()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val host = SessionHost(scope, transport, { engine(client, handler = handler) }, repository, clock = { clock.now })
        val gen = ConnectionGeneration("aa", 1)
        try {
            host.onConnected(gen)
            host.onRecord(gen, client.helloFrame.copyOf(), admission())
            awaitSends(transport, 2)
            val handshake = transport.sent.filter { it.gen == gen }
            client.acceptPhoneHello(
                PreAuthCodec.decode(handshake[0].bytes).second,
                SignatureFrame.decode(PreAuthCodec.decode(handshake[1].bytes).second, Role.PHONE).signature,
            )
            host.onRecord(gen, client.signatureFrame(), admission())
            awaitSends(transport, 3)
            val token = requireNotNull(transport.sent.last().token)
            host.onRecord(gen, client.confirmRecord(), admission())
            host.onSendComplete(gen, token, true)
            awaitCondition { repository.marked.isNotEmpty() }

            // Record 0 reaches the engine and suspends there; the rest arrive while it is in
            // flight, from a real multi-threaded dispatcher.
            host.onRecord(gen, client.record(RecordType.EVENT_ACK.wire, 1uL, ackBody(0)), admission())
            withTimeout(5_000) { entered.await() }
            for (marker in 1..8) {
                host.onRecord(
                    gen,
                    client.record(RecordType.EVENT_ACK.wire, (marker + 1).toULong(), ackBody(marker)),
                    admission(),
                )
            }
            gate.complete(Unit)
            awaitCondition { synchronized(seen) { seen.size } == 9 }
            assertEquals((0..8).toList(), synchronized(seen) { seen.toList() })
        } finally {
            scope.cancel()
        }
    }

    private suspend fun awaitSends(transport: RecordingTransport, count: Int) = awaitCondition { transport.sent.size >= count }

    private suspend fun awaitCondition(condition: () -> Boolean) {
        kotlinx.coroutines.withTimeout(5_000) {
            while (!condition()) delay(5)
        }
    }

    @Test
    fun tickFiresIdleTeardownWithoutInbound() = runTest {
        val client = TestClient()
        lateinit var created: PhoneSessionEngine
        val host = host {
            created = engine(client)
            created
        }
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
        val host = host {
            created = engine(client)
            created
        }
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
        host.onRecord(gen, client.helloFrame.copyOf(), admission())
        runCurrent()
        val handshake = wire.sent.filter { it.gen == gen }
        client.acceptPhoneHello(
            PreAuthCodec.decode(handshake[0].bytes).second,
            SignatureFrame.decode(PreAuthCodec.decode(handshake[1].bytes).second, Role.PHONE).signature,
        )
        host.onRecord(gen, client.signatureFrame(), admission())
        runCurrent()
        val token = requireNotNull(wire.sent.last().token)
        host.onRecord(gen, client.confirmRecord(), admission())
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

    @Test
    fun cancelPairingsCancelsOnceSendsDisconnectAndClosesTheTransport() = runTest {
        val client = TestClient()
        val pairing = FakePairingConnection()
        val host = SessionHost(
            backgroundScope,
            wire,
            { engine(client) },
            trust,
            { clock.now },
            pairingFactory = { pairing },
        )
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        host.onRecord(gen, byteArrayOf(PAIRING_FIRST_FRAME), admission())
        runCurrent()
        assertEquals("the generation selected the pairing path", 1, pairing.frames)

        host.cancelPairings()
        runCurrent()

        assertEquals("the machine cancel runs exactly once", 1, pairing.cancels)
        assertEquals("the host sends the machine's disconnect by tearing down", 1, pairing.disconnects)
        assertEquals("the transport is closed", Wire.Closed(gen), wire.calls.last())

        val next = ConnectionGeneration("bb", 2)
        connect(host, next)
        authenticate(host, next, client)
        assertTrue(
            "the host stays usable after cancel",
            host.authenticatedSessions().any { it.generation == next },
        )
        host.shutdown()
    }

    @Test
    fun cancelPairingsIsSafeWithoutAPairingConnection() = runTest {
        val client = TestClient()
        val host = host { engine(client) }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)

        host.cancelPairings()
        runCurrent()

        assertTrue("a reconnect generation is untouched", host.authenticatedSessions().isEmpty())
        assertTrue("no transport close for a non-pairing generation", wire.calls.none { it is Wire.Closed })
        host.shutdown()
    }

    @Test
    fun closePeerClosesOnlyMatchingGenerations() = runTest {
        val peerA = peer(0x11)
        val peerB = peer(0x22)
        val hexA = TrustEncoding.hex(peerA.installationId)
        val hexB = TrustEncoding.hex(peerB.installationId)
        // Two distinct peers that share one label: a label never selects a generation.
        val repository = RecordingTrust(
            known = listOf(trustedPeer(hexA, SAME_LABEL), trustedPeer(hexB, SAME_LABEL)),
        )
        val bound = FakePairingConnection(bound = hexA)
        val unbound = FakePairingConnection()
        val engines = mutableMapOf<String, PhoneSessionEngine>()
        val host = host(
            repository = repository,
            pairingFactory = { gen -> if (gen.address == "enroll-bound") bound else unbound },
        ) { gen ->
            (if (gen.address == "b") engine(peerB) else engine(peerA)).also { engines[gen.address] = it }
        }
        val a = ConnectionGeneration("a", 1)
        val b = ConnectionGeneration("b", 2)
        val enrollBound = ConnectionGeneration("enroll-bound", 3)
        val enrollUnbound = ConnectionGeneration("enroll-unbound", 4)
        val handshaking = ConnectionGeneration("handshaking", 5)
        connect(host, a)
        connect(host, b)
        authenticate(host, a, peerA)
        authenticate(host, b, peerB)
        connect(host, enrollBound)
        host.onRecord(enrollBound, byteArrayOf(PAIRING_FIRST_FRAME), admission())
        runCurrent()
        connect(host, enrollUnbound)
        host.onRecord(enrollUnbound, byteArrayOf(PAIRING_FIRST_FRAME), admission())
        runCurrent()
        connect(host, handshaking)
        assertEquals("both trusted peers carry the same label", 1, repository.peers().map { it.label }.distinct().size)

        host.closePeer(hexA)
        runCurrent()

        assertFalse("the matching authenticated session ended", engines.getValue("a").isOpen)
        assertTrue("its transport was closed", wire.calls.contains(Wire.Closed(a)))
        assertTrue("the other peer's session is untouched", engines.getValue("b").isOpen)
        assertTrue("the other peer's transport stays open", wire.calls.none { it == Wire.Closed(b) })
        assertEquals("the enrollment bound to the peer is torn down", 1, bound.disconnects)
        assertEquals("a bound enrollment is never cancelled destructively", 0, bound.cancels)
        assertTrue("the bound enrollment's transport was closed", wire.calls.contains(Wire.Closed(enrollBound)))
        assertEquals("an unbound enrollment is untouched", 0, unbound.disconnects)
        assertTrue("an unbound enrollment stays connected", wire.calls.none { it == Wire.Closed(enrollUnbound) })
        assertTrue(
            "a generation that authenticated no peer stays untouched",
            wire.calls.none { it == Wire.Closed(handshaking) },
        )
        assertEquals(
            "only the other peer's session remains listed",
            listOf(b),
            host.authenticatedSessions().map { it.generation },
        )
        host.shutdown()
    }

    @Test
    fun closePeerReturnsAfterEnded() = runTest {
        val client = TestClient()
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.EVENT_ACK.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                entered.complete(Unit)
                gate.await()
            }
        }
        lateinit var created: PhoneSessionEngine
        val host = host { engine(client, handler = handler).also { created = it } }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)
        host.onRecord(gen, client.record(RecordType.EVENT_ACK.wire, 1uL, ackBody(1)), admission())
        runCurrent()
        assertTrue("the handler holds the generation", entered.isCompleted)

        val closing = async { host.closePeer(TrustEncoding.hex(client.installationId)) }
        runCurrent()
        assertFalse("closePeer waits behind the in-flight step", closing.isCompleted)
        assertTrue("the session is live while the handler runs", created.isOpen)

        gate.complete(Unit)
        runCurrent()

        assertTrue("closePeer returned", closing.isCompleted)
        assertFalse("the generation ended before closePeer returned", created.isOpen)
        assertNull("the peer id is erased at teardown", created.peerId)
        assertTrue("the session keys are erased", created.keysErasedForTest)
        assertTrue("the transport was closed", wire.calls.contains(Wire.Closed(gen)))
        host.shutdown()
    }

    @Test
    fun closePeerWaitsBehindInFlightHandlerStep() = runTest {
        val client = TestClient()
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.REMOVAL_REQUEST.wire

            override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
                reply(RecordType.REMOVAL_RESPONSE.wire, ByteArray(0))
                entered.complete(Unit)
                gate.await()
            }
        }
        lateinit var created: PhoneSessionEngine
        val host = host { engine(client, handler = handler).also { created = it } }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client)
        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()
        assertTrue("the handler holds the generation", entered.isCompleted)

        val closing = async { host.closePeer(TrustEncoding.hex(client.installationId)) }
        runCurrent()
        assertFalse("the close waits behind the handler step", closing.isCompleted)
        assertTrue(
            "the reply has not reached the transport before the handler returns",
            wire.sent.none { wireTypeOf(it.bytes) == RecordType.REMOVAL_RESPONSE.wire },
        )
        assertTrue("nothing is closed while the handler is in flight", wire.calls.none { it is Wire.Closed })

        gate.complete(Unit)
        runCurrent()

        val replyIndex = wire.calls.indexOfFirst { it is Wire.Sent && wireTypeOf(it.bytes) == RecordType.REMOVAL_RESPONSE.wire }
        val closeIndex = wire.calls.indexOfFirst { it is Wire.Closed && it.gen == gen }
        assertTrue("the handler's reply reached the transport", replyIndex >= 0)
        assertTrue("the transport closed only after the reply", closeIndex > replyIndex)
        assertTrue("closePeer returned after the close", closing.isCompleted)
        assertFalse(created.isOpen)
        host.shutdown()
    }

    @Test
    fun closeAllKeepsDurablePendingRecords() = runTest {
        val client = TestClient()
        val durable = mutableListOf("pending-record")
        val pairing = FakePairingConnection(durable = durable)
        val host = host(pairingFactory = { pairing }) { engine(client) }
        val session = ConnectionGeneration("aa", 1)
        val enrollment = ConnectionGeneration("bb", 2)
        connect(host, session)
        authenticate(host, session, client)
        connect(host, enrollment)
        host.onRecord(enrollment, byteArrayOf(PAIRING_FIRST_FRAME), admission())
        runCurrent()

        host.closeAll()
        runCurrent()

        assertEquals("the durable pending record is kept", listOf("pending-record"), durable)
        assertEquals("no enrollment is destructively cancelled", 0, pairing.cancels)
        assertEquals("the enrollment is disconnected non-destructively", 1, pairing.disconnects)
        assertTrue("the session transport closed", wire.calls.contains(Wire.Closed(session)))
        assertTrue("the enrollment transport closed", wire.calls.contains(Wire.Closed(enrollment)))
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())

        val next = ConnectionGeneration("cc", 3)
        connect(host, next)
        authenticate(host, next, client)
        assertTrue(
            "the host stays usable after a non-destructive closeAll",
            host.authenticatedSessions().any { it.generation == next },
        )
        host.shutdown()
    }

    @Test
    fun transportCloseFailureStillErasesKeys() = runTest {
        val client = TestClient()
        lateinit var created: PhoneSessionEngine
        val failing = RecordingTransport(closeFailsFor = { true })
        val host = host(transport = failing) { engine(client).also { created = it } }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client, transport = failing)

        val failure = runCatching { host.closePeer(TrustEncoding.hex(client.installationId)) }

        assertTrue("the best-effort disconnect failure is reported", failure.isFailure)
        assertFalse("logical termination does not depend on the transport", created.isOpen)
        assertTrue("the keys were erased before the close", created.keysErasedForTest)
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
        assertFalse("the ended generation is never selected again", host.hasOpenSession(TrustEncoding.hex(client.installationId)))
    }

    @Test
    fun closePeerCloseFailureStillEndsEveryMatchingGeneration() = runTest {
        val peer = TestClient()
        // The same authenticated peer on two generations; the first disconnect fails.
        val second = clientLike(peer)
        val clients = mapOf("aa" to peer, "bb" to second)
        val engines = mutableMapOf<String, PhoneSessionEngine>()
        val first = ConnectionGeneration("aa", 1)
        val secondGen = ConnectionGeneration("bb", 2)
        val failing = RecordingTransport(closeFailsFor = { it == first })
        val host = host(transport = failing) { gen ->
            engine(clients.getValue(gen.address)).also { engines[gen.address] = it }
        }
        connect(host, first)
        connect(host, secondGen)
        authenticate(host, first, peer, transport = failing)
        authenticate(host, secondGen, second, transport = failing)

        val failure = runCatching { host.closePeer(TrustEncoding.hex(peer.installationId)) }

        assertTrue("the first disconnect failure is reported", failure.isFailure)
        assertTrue("the failing generation's close was attempted", failing.calls.contains(Wire.Closed(first)))
        assertTrue("the later matching generation was closed too", failing.calls.contains(Wire.Closed(secondGen)))
        assertFalse("the failing session was torn down", engines.getValue("aa").isOpen)
        assertFalse("the later session ended despite the first failure", engines.getValue("bb").isOpen)
        assertTrue("the first session's keys were erased", engines.getValue("aa").keysErasedForTest)
        assertTrue("the later session's keys were erased too", engines.getValue("bb").keysErasedForTest)
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
        assertFalse(host.hasOpenSession(TrustEncoding.hex(peer.installationId)))
    }

    @Test
    fun closeAllCloseFailureStillEndsEveryGeneration() = runTest {
        val client = TestClient()
        val pairing = FakePairingConnection(bound = TrustEncoding.hex(client.installationId))
        val engines = mutableMapOf<String, PhoneSessionEngine>()
        val session = ConnectionGeneration("aa", 1)
        val enrollment = ConnectionGeneration("bb", 2)
        val failing = RecordingTransport(closeFailsFor = { it == session })
        val host = host(transport = failing, pairingFactory = { pairing }) { gen ->
            engine(client).also { engines[gen.address] = it }
        }
        connect(host, session)
        connect(host, enrollment)
        authenticate(host, session, client, transport = failing)
        host.onRecord(enrollment, byteArrayOf(PAIRING_FIRST_FRAME), admission())
        runCurrent()

        val failure = runCatching { host.closeAll() }

        assertTrue("the failing disconnect is reported", failure.isFailure)
        assertTrue("the failing generation's close was attempted", failing.calls.contains(Wire.Closed(session)))
        assertTrue("the enrollment generation was closed too", failing.calls.contains(Wire.Closed(enrollment)))
        assertFalse("the session was torn down despite its failing disconnect", engines.getValue("aa").isOpen)
        assertTrue("the session's keys were erased", engines.getValue("aa").keysErasedForTest)
        assertEquals("the enrollment was disconnected non-destructively", 1, pairing.disconnects)
        assertEquals("no enrollment was cancelled", 0, pairing.cancels)
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
    }

    /** A distinct peer: its own identity, installation id and ephemeral key. */
    private fun peer(seed: Int): TestClient = TestClient(
        identity = TestIdentity.generate(),
        installationId = ByteArray(16) { seed.toByte() },
        ephemeral = P256.generate(),
    )

    private fun trustedPeer(installationId: String, label: String): TrustedPeer = TrustedPeer(
        installationId = installationId,
        publicKey = "04" + "00".repeat(64),
        label = label,
        deviceType = 1,
        enrollmentTranscript = "",
        fingerprint = "",
        state = TrustState.ACTIVE,
        createdAtMillis = 0L,
        lastSeenMillis = null,
        createdElapsedMillis = 0L,
        bootToken = "",
        stateChangedAtMillis = 0L,
    )

    private class FakePairingConnection(
        private val bound: String? = null,
        private val durable: MutableList<String>? = null,
    ) : PairingConnection {
        var frames = 0
            private set
        var cancels = 0
            private set
        var disconnects = 0
            private set

        override fun boundPeerId(): String? = bound

        override suspend fun onFrame(bytes: ByteArray): List<Outgoing> {
            frames++
            return emptyList()
        }

        override suspend fun onTick(): List<Outgoing> = emptyList()

        override suspend fun onDisconnect() {
            disconnects++
        }

        override suspend fun cancel(): List<Outgoing> {
            cancels++
            durable?.clear()
            return listOf(Outgoing.Disconnect)
        }
    }

    private companion object {
        /** One more than the worker queue capacity: the last attempt must be refused at admission. */
        const val SATURATION_ATTEMPTS = 33

        /** Any `0x02`-`0x05` first frame selects the pairing path. */
        const val PAIRING_FIRST_FRAME: Byte = 0x03

        /** The version byte every protected record starts with (pre-auth frames never carry it). */
        const val RECORD_PROTOCOL_VERSION = 1

        /** Both trusted peers in the selection test carry this one label. */
        const val SAME_LABEL = "Desktop"

        /**
         * The record type of a protected record, or null for a pre-auth frame. A pre-auth frame
         * starts with its frame type (`0x06`/`0x07`/`0x0F`); its payload bytes 0-1 would
         * otherwise read as a record type and a signature's first byte randomly matched one.
         */
        fun wireTypeOf(bytes: ByteArray): Int? {
            if (bytes.size < 5) return null
            if (bytes[0].toInt() and 0xFF != RECORD_PROTOCOL_VERSION) return null
            return ((bytes[3].toInt() and 0xFF) shl 8) or (bytes[4].toInt() and 0xFF)
        }
    }

    private object SessionEffectBytes {
        val PREAUTH_REJECT = listOf(0x0F.toByte(), 0, 0)
    }

    private sealed interface Wire {
        data class Sent(val gen: ConnectionGeneration, val bytes: ByteArray, val token: Long?) : Wire
        data class Closed(val gen: ConnectionGeneration) : Wire
    }

    private class RecordingTransport(
        var accept: Boolean = true,
        private val events: MutableList<String>? = null,
        private val closeFailsFor: (ConnectionGeneration) -> Boolean = { false },
    ) : SessionTransport {
        val calls = mutableListOf<Wire>()
        val sent get() = calls.filterIsInstance<Wire.Sent>()

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
            calls += Wire.Sent(gen, bytes.copyOf(), token)
            events?.add("send:${wireTypeOf(bytes)}")
            return accept
        }

        override fun close(gen: ConnectionGeneration) {
            calls += Wire.Closed(gen)
            if (closeFailsFor(gen)) error("the transport disconnect failed")
        }
    }

    private class RecordingTrust(
        private val events: MutableList<String>? = null,
        private val failMark: Boolean = false,
        private val known: List<TrustedPeer> = emptyList(),
    ) : InstallationTrustRepository {
        val marked = mutableListOf<String>()

        override suspend fun identity(): InstallationIdentity = error("not used by the adapter")
        override suspend fun peers(): List<TrustedPeer> = known
        override suspend fun activePeers(): List<TrustedPeer> = known
        override suspend fun addPending(peer: PendingPeer) = error("not used by the adapter")
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("not used by the adapter")
        override suspend fun revoke(peerId: String) = error("not used by the adapter")
        override suspend fun beginRevocation(peerId: String) = error("not used by the adapter")
        override suspend fun endRevocation(peerId: String) = error("not used by the adapter")
        override suspend fun markAuthenticated(peerId: String) {
            if (failMark) throw TrustException()
            events?.add("mark")
            marked += peerId
        }

        override suspend fun resetIdentity() = error("not used by the adapter")
    }
}
