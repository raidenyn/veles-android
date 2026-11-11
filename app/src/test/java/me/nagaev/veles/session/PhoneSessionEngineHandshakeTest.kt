package me.nagaev.veles.session

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class PhoneSessionEngineHandshakeTest {
    private val clock = FakeClock()
    private val phone = TestIdentity.generate()

    private fun engine(
        lookup: PeerLookup,
        handler: ApplicationHandler? = null,
    ): PhoneSessionEngine = TestPeers.engine(phone.signer, lookup, clock, handler)

    private fun lookupFor(
        client: TestClient,
        state: LocalRecordState = LocalRecordState.ACTIVE,
    ): PeerLookup = TestPeers.lookup(TestPeers.record(client, state))

    private fun phoneConfirm(progress: HandshakeProgress, client: TestClient): OpenedRecord = RecordCodec.open(
        client.keys.p2cKey,
        client.keys.p2cPrefix,
        client.keys.sessionId.copyOfRange(0, TestPeers.SESSION_PREFIX_BYTES),
        Direction.PHONE_TO_CLIENT,
        0uL,
        progress.phoneConfirmRecord,
    )

    private fun assertClosed(engine: PhoneSessionEngine) {
        assertFalse(engine.isOpen)
        assertNull(engine.sessionClass)
        assertNull(engine.peerId)
    }

    @Test
    fun fullHandshakeOpensFullClass() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        val progress = engine.driveToPeerConfirm(client)

        val confirm = phoneConfirm(progress, client)
        assertEquals(RecordType.RECONNECT_CONFIRM.wire, confirm.type)
        assertArrayEquals(
            ReconnectKeySchedule.confirmation(client.keys.phoneConfirmKey, client.confirmInput),
            confirm.body.copyOfRange(0, TestPeers.HASH_BYTES),
        )
        assertEquals(TestClient.PEER_STATE_ACTIVE, confirm.body[TestPeers.HASH_BYTES].toInt())

        assertTrue(engine.onInbound(progress.clientConfirmRecord).isEmpty())
        val authenticated = engine.onSendComplete(progress.token, true)
            .single() as SessionEffect.Authenticated
        assertEquals(SessionClass.FULL, authenticated.sessionClass)
        assertArrayEquals(client.installationId, authenticated.peerId)
        assertArrayEquals(client.keys.sessionId.copyOfRange(0, TestPeers.SESSION_PREFIX_BYTES), authenticated.sessionIdPrefix)
        assertTrue(engine.isOpen)
        assertEquals(SessionClass.FULL, engine.sessionClass)
        assertArrayEquals(client.installationId, engine.peerId)
    }

    @Test
    fun unknownClientGetsByteIdenticalReject() = runTest {
        val client = TestClient()
        val unknown = engine(TestPeers.lookup(null))
        val unknownEffects = unknown.onInbound(client.helloFrame)

        val deletedLookup = MutablePeerLookup(TestPeers.record(client))
        val deleted = engine(deletedLookup)
        deletedLookup.record = null
        val deletedEffects = deleted.onInbound(client.helloFrame)

        assertEquals(2, unknownEffects.size)
        assertEquals(2, deletedEffects.size)
        assertArrayEquals(sendBytes(unknownEffects), sendBytes(deletedEffects))
        assertArrayEquals(byteArrayOf(0x0F.toByte(), 0, 0), sendBytes(unknownEffects))
        assertNull((unknownEffects[0] as SessionEffect.Send).completionToken)
        assertEquals(SessionEffect.Close, unknownEffects[1])
        assertEquals(SessionEffect.Close, deletedEffects[1])
        assertClosed(unknown)
        assertClosed(deleted)
    }

    @Test
    fun phoneSignsFirst() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))

        val effects = engine.onInbound(client.helloFrame)

        assertEquals(2, effects.size)
        val helloFrame = (effects[0] as SessionEffect.Send).bytes
        val signatureFrame = (effects[1] as SessionEffect.Send).bytes
        assertEquals(FrameType.RECONNECT_HELLO.wire, PreAuthCodec.decode(helloFrame).first)
        assertEquals(FrameType.RECONNECT_SIGNATURE.wire, PreAuthCodec.decode(signatureFrame).first)
        assertNull((effects[0] as SessionEffect.Send).completionToken)
        val signature = SignatureFrame.decode(PreAuthCodec.decode(signatureFrame).second, Role.PHONE)
        EcdsaP1363.requireValid(
            phone.publicKey,
            ReconnectKeySchedule.rbase(PreAuthCodec.decode(helloFrame).second, client.helloPayload),
            signature.signature,
        )
        assertFalse(engine.isOpen)
        assertNull(engine.sessionClass)
    }

    @Test
    fun peerConfirmBeforeOwnConfirmSentDoesNotAuthenticate() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        val progress = engine.driveToPeerConfirm(client)
        assertTrue(engine.onInbound(progress.clientConfirmRecord).isEmpty())
        assertFalse(engine.isOpen)

        val authenticated = engine.onSendComplete(progress.token, true)
        assertEquals(SessionClass.FULL, (authenticated.single() as SessionEffect.Authenticated).sessionClass)

        val failedClient = TestClient()
        val failed = engine(lookupFor(failedClient))
        val failedProgress = failed.driveToPeerConfirm(failedClient)
        assertTrue(failed.onInbound(failedProgress.clientConfirmRecord).isEmpty())
        val failedEffects = failed.onSendComplete(failedProgress.token, false)
        assertEquals(2, failedEffects.size)
        assertTrue((failedEffects[0] as SessionEffect.Terminated).reason.isNotEmpty())
        assertEquals(SessionEffect.Close, failedEffects[1])
        assertTrue(failedEffects.none { it is SessionEffect.Authenticated })
        assertClosed(failed)
    }

    @Test
    fun wrongHmacConfirmTerminatesAndErases() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        engine.driveToPeerConfirm(client)

        val effects = engine.onInbound(client.confirmRecord(confirmation = ByteArray(TestPeers.HASH_BYTES)))

        assertEquals(2, effects.size)
        assertTrue((effects[0] as SessionEffect.Terminated).reason.isNotEmpty())
        assertEquals(SessionEffect.Close, effects[1])
        assertClosed(engine)
        assertTrue(engine.onInbound(client.confirmRecord()).isEmpty())
        try {
            engine.sendApplication(RecordType.PULL_EVENTS.wire, ByteArray(0))
            fail("a closed session must reject application sends")
        } catch (_: SessionReject) {
            // expected
        }
    }

    @Test
    fun recordBeforeConfirmTerminates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        engine.driveToPeerConfirm(client)

        val effects = engine.onInbound(client.heartbeatRecord())

        assertEquals(2, effects.size)
        assertTrue((effects[0] as SessionEffect.Terminated).reason.isNotEmpty())
        assertEquals(SessionEffect.Close, effects[1])
        assertTrue(effects.none { it is SessionEffect.Deliver })
        assertClosed(engine)
    }

    @Test
    fun recordRemovedBeforeConfirmSendsOnlyPreauthReject() = runTest {
        val client = TestClient()
        val lookup = MutablePeerLookup(TestPeers.record(client))
        val engine = engine(lookup)
        val helloEffects = engine.onInbound(client.helloFrame)
        assertEquals(2, helloEffects.size)
        val phoneHelloPayload = PreAuthCodec.decode((helloEffects[0] as SessionEffect.Send).bytes).second
        val phoneSignature = SignatureFrame.decode(
            PreAuthCodec.decode((helloEffects[1] as SessionEffect.Send).bytes).second,
            Role.PHONE,
        ).signature
        client.acceptPhoneHello(phoneHelloPayload, phoneSignature)

        lookup.record = null
        val effects = engine.onInbound(client.signatureFrame())

        assertEquals(2, effects.size)
        assertArrayEquals(byteArrayOf(0x0F.toByte(), 0, 0), (effects[0] as SessionEffect.Send).bytes)
        assertEquals(SessionEffect.Close, effects[1])
        assertClosed(engine)
    }

    @Test
    fun tickEmitsHeartbeatAfter60sOutboundSilence() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        assertEquals(
            SessionClass.FULL,
            engine.completeHandshake(client).single().let { it as SessionEffect.Authenticated }.sessionClass,
        )

        clock.advance(HEARTBEAT_SILENCE_MS - MILLIS_PER_SECOND)
        assertTrue(engine.onTick().isEmpty())

        clock.advance(MILLIS_PER_SECOND)
        val first = engine.onTick().single() as SessionEffect.Send
        assertTrue(engine.isOpen)
        assertEquals(RecordType.APP_HEARTBEAT.wire, openedHeartbeat(client, first.bytes, 1uL).type)

        clock.advance(HEARTBEAT_SILENCE_MS)
        val second = engine.onTick().single() as SessionEffect.Send
        assertEquals(RecordType.APP_HEARTBEAT.wire, openedHeartbeat(client, second.bytes, 2uL).type)
        assertTrue(engine.isOpen)
    }

    @Test
    fun teardownDuringSuspendedSignerIsFinal() = runBlocking {
        val client = TestClient()
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val signer = IdentitySigner { message ->
            entered.complete(Unit)
            gate.await()
            phone.signer.signDer(message)
        }
        val engine = TestPeers.engine(signer, TestPeers.lookup(TestPeers.record(client)), clock)

        val hello = async(Dispatchers.Default) { engine.onInbound(client.helloFrame) }
        withTimeout(5_000) { entered.await() }
        engine.teardown()
        gate.complete(Unit)

        assertTrue(
            "a signing result that resumes after teardown installs nothing",
            withTimeout(5_000) { hello.await() }.isEmpty(),
        )
        assertFalse(engine.isOpen)
        assertNull(engine.sessionClass)
        assertNull(engine.peerId)
        assertTrue("later input is refused", engine.onInbound(client.helloFrame).isEmpty())
    }

    @Test
    fun teardownDuringSuspendedLookupRefusesLateKeyInstall() = runBlocking {
        val client = TestClient()
        val record = TestPeers.record(client)
        var calls = 0
        val entered = CompletableDeferred<Unit>()
        val gate = CompletableDeferred<Unit>()
        val lookup = PeerLookup {
            if (calls++ == 0) {
                record
            } else {
                entered.complete(Unit)
                gate.await()
                record
            }
        }
        val engine = TestPeers.engine(phone.signer, lookup, clock)
        val helloEffects = engine.onInbound(client.helloFrame)
        clientAccept(helloEffects, client)

        val late = async(Dispatchers.Default) { engine.onInbound(client.signatureFrame()) }
        withTimeout(5_000) { entered.await() }
        engine.teardown()
        gate.complete(Unit)

        assertTrue(
            "a signature result that resumes after teardown installs no keys",
            withTimeout(5_000) { late.await() }.isEmpty(),
        )
        assertFalse(engine.isOpen)
        assertNull(engine.sessionClass)
        assertNull(engine.peerId)
    }

    private fun clientAccept(helloEffects: List<SessionEffect>, client: TestClient) {
        val phoneHelloPayload = PreAuthCodec.decode((helloEffects[0] as SessionEffect.Send).bytes).second
        val phoneSignature = SignatureFrame.decode(
            PreAuthCodec.decode((helloEffects[1] as SessionEffect.Send).bytes).second,
            Role.PHONE,
        ).signature
        client.acceptPhoneHello(phoneHelloPayload, phoneSignature)
    }

    private fun openedHeartbeat(client: TestClient, record: ByteArray, sequence: ULong): OpenedRecord = RecordCodec.open(
        client.keys.p2cKey,
        client.keys.p2cPrefix,
        client.keys.sessionId.copyOfRange(0, TestPeers.SESSION_PREFIX_BYTES),
        Direction.PHONE_TO_CLIENT,
        sequence,
        record,
    )

    private fun sendBytes(effects: List<SessionEffect>): ByteArray = (effects[0] as SessionEffect.Send).bytes

    private companion object {
        const val HEARTBEAT_SILENCE_MS = 60_000L
        const val MILLIS_PER_SECOND = 1_000L
    }
}
