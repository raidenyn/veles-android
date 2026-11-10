package me.nagaev.veles.session

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** §7.3/§11.4 protected-record rules, sequences and fail-closed paths (tasks.md 3.4, 3.7). */
class PhoneSessionEngineRecordsTest {
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

    private suspend fun open(
        engine: PhoneSessionEngine,
        client: TestClient,
        expected: SessionClass = SessionClass.FULL,
    ) {
        val effects = engine.completeHandshake(client)
        assertEquals(expected, (effects.single() as SessionEffect.Authenticated).sessionClass)
        assertTrue(engine.isOpen)
    }

    private fun assertClosed(engine: PhoneSessionEngine) {
        assertFalse(engine.isOpen)
        assertNull(engine.sessionClass)
        assertNull(engine.peerId)
    }

    @Test
    fun replayDuplicateTerminates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val heartbeat = client.record(RecordType.APP_HEARTBEAT.wire, 1uL)
        assertTrue(engine.onInbound(heartbeat).isEmpty())
        assertTrue(engine.isOpen)

        assertTerminated(engine.onInbound(heartbeat))
        assertClosed(engine)
    }

    @Test
    fun reorderTerminates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        assertTerminated(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 2uL)))
        assertClosed(engine)
    }

    @Test
    fun skipTerminates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 1uL)).isEmpty())
        assertTerminated(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 3uL)))
        assertClosed(engine)
    }

    @Test
    fun priorSessionRecordRejected() = runTest {
        val identity = TestIdentity.generate()
        val first = TestClient(identity = identity)
        val firstEngine = engine(lookupFor(first))
        open(firstEngine, first)
        val captured = first.record(RecordType.APP_HEARTBEAT.wire, 1uL)

        val second = TestClient(
            identity = identity,
            ephemeral = P256.generate(),
            nonce = ByteArray(16) { 0x5A },
        )
        val secondEngine = engine(lookupFor(second))
        open(secondEngine, second)

        assertTerminated(secondEngine.onInbound(captured))
        assertClosed(secondEngine)
    }

    @Test
    fun sequenceMaxAcceptedOnceThenTerminates() = runTest {
        val inboundClient = TestClient()
        val inbound = engine(lookupFor(inboundClient))
        open(inbound, inboundClient)
        inbound.seekSequences(ULong.MAX_VALUE, 1uL)

        val atMax = inboundClient.record(RecordType.APP_HEARTBEAT.wire, ULong.MAX_VALUE)
        assertTrue(inbound.onInbound(atMax).isEmpty())
        assertTrue(inbound.isOpen)

        assertTerminated(inbound.onInbound(atMax))
        assertClosed(inbound)

        val outboundClient = TestClient()
        val outbound = engine(lookupFor(outboundClient))
        open(outbound, outboundClient)
        outbound.seekSequences(1uL, ULong.MAX_VALUE)

        val sent = outbound.sendApplication(RecordType.PULL_EVENTS.wire, ByteArray(0)).single()
            as SessionEffect.Send
        assertEquals(
            ULong.MAX_VALUE,
            outboundClient.openPhoneRecord(sent.bytes, ULong.MAX_VALUE).sequence,
        )
        assertTerminated(outbound.onTick())
        assertClosed(outbound)
    }

    @Test
    fun unknownType0000And0201Terminate() = runTest {
        for (type in listOf(0x0000, 0x0201)) {
            val client = TestClient()
            val engine = engine(lookupFor(client))
            open(engine, client)

            val effects = engine.onInbound(client.uncheckedRecord(type, 1uL))
            assertTerminated(effects)
            assertClosed(engine)
        }
    }

    @Test
    fun wrongDirectionTerminates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val phoneOnly = client.phoneRecord(RecordType.PULL_EVENTS.wire, 1uL)
        assertTerminated(engine.onInbound(phoneOnly))
        assertClosed(engine)
    }

    @Test
    fun pullInActivationRecoveryTerminatesNoDelivery() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client, LocalRecordState.PENDING))
        open(engine, client, SessionClass.ACTIVATION_RECOVERY)

        val effects = engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, 1uL))
        assertTerminated(effects)
        assertClosed(engine)
    }

    @Test
    fun unhandledAuthorizedRecordSendsProtocolViolation() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, 1uL))

        assertEquals(3, effects.size)
        val error = client.openPhoneRecord((effects[0] as SessionEffect.Send).bytes, 1uL)
        assertEquals(RecordType.APP_ERROR.wire, error.type)
        assertEquals(AppErrorCode.PROTOCOL_VIOLATION.wire, error.body[0].toInt() and 0xFF)
        assertEquals(0, error.body[1].toInt())
        assertEquals(0, error.body[2].toInt())
        assertTrue(effects[1] is SessionEffect.Terminated)
        assertEquals(SessionEffect.Close, effects[2])
        assertTrue(effects.none { it is SessionEffect.Deliver })
        assertClosed(engine)
    }

    @Test
    fun unhandledActivationRecordFailsClosed() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client, LocalRecordState.COMMITTED_WAITING_ACK))
        open(engine, client, SessionClass.ACTIVATION_RECOVERY)

        val effects = engine.onInbound(
            client.record(RecordType.ACTIVATION_COMMIT.wire, 1uL, ByteArray(32)),
        )

        assertEquals(3, effects.size)
        val error = client.openPhoneRecord((effects[0] as SessionEffect.Send).bytes, 1uL)
        assertEquals(RecordType.APP_ERROR.wire, error.type)
        assertEquals(AppErrorCode.PROTOCOL_VIOLATION.wire, error.body[0].toInt() and 0xFF)
        assertTrue(effects[1] is SessionEffect.Terminated)
        assertEquals(SessionEffect.Close, effects[2])
        assertTrue(effects.none { it is SessionEffect.Deliver })
        assertClosed(engine)
    }

    @Test
    fun appErrorReceivedIsTerminal() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val error = client.record(
            RecordType.APP_ERROR.wire,
            1uL,
            appErrorBody(AppErrorCode.SESSION_EXPIRED),
        )
        assertTerminated(engine.onInbound(error))
        assertClosed(engine)
        assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 2uL)).isEmpty())
    }

    @Test
    fun unknownAppErrorCodeTerminates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val unknownCode = byteArrayOf(0x09, 0x00, 0x00)
        val effects = engine.onInbound(client.record(RecordType.APP_ERROR.wire, 1uL, unknownCode))
        assertTerminated(effects)
        assertClosed(engine)
    }

    @Test
    fun inputAfterTeardownIgnored() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        engine.teardown()
        assertClosed(engine)
        assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 1uL)).isEmpty())
        assertTrue(engine.onTick().isEmpty())
        assertTrue(engine.onSendComplete(0L, true).isEmpty())
        assertTrue(engine.onInbound(client.helloFrame).isEmpty())
        try {
            engine.sendApplication(RecordType.PULL_EVENTS.wire, ByteArray(0))
            fail("a closed session must reject application sends")
        } catch (_: SessionReject) {
            // expected
        }
    }

    @Test
    fun repeatedHelloOnSameGenerationRejects() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val effects = engine.onInbound(client.helloFrame)
        assertPreAuthReject(effects)
        assertClosed(engine)

        val waitingClient = TestClient()
        val waiting = engine(lookupFor(waitingClient))
        val helloEffects = waiting.onInbound(waitingClient.helloFrame)
        assertEquals(2, helloEffects.size)
        val repeated = waiting.onInbound(waitingClient.helloFrame)
        assertPreAuthReject(repeated)
        assertFalse(waiting.isOpen)
    }

    @Test
    fun recordDeletedMidHandshakeYieldsNoProtectedData() = runTest {
        val client = TestClient()
        val lookup = MutablePeerLookup(TestPeers.record(client))
        val engine = engine(lookup)
        val helloEffects = engine.onInbound(client.helloFrame)
        assertEquals(2, helloEffects.size)
        val phoneHello = PreAuthCodec.decode((helloEffects[0] as SessionEffect.Send).bytes).second
        val phoneSignature = SignatureFrame.decode(
            PreAuthCodec.decode((helloEffects[1] as SessionEffect.Send).bytes).second,
            Role.PHONE,
        ).signature
        client.acceptPhoneHello(phoneHello, phoneSignature)

        lookup.record = null
        val effects = engine.onInbound(client.signatureFrame())

        assertPreAuthReject(effects)
        assertTrue(effects.none { it is SessionEffect.Send && it.bytes[0] != 0x0F.toByte() })
        assertClosed(engine)
    }

    @Test
    fun throwingLookupFailsClosed() = runTest {
        val engine = engine(PeerLookup { throw IllegalStateException("lookup down") })

        assertTerminated(engine.onInbound(TestClient().helloFrame))
        assertClosed(engine)
    }

    @Test
    fun throwingSignerFailsClosed() = runTest {
        val client = TestClient()
        val failing = TestPeers.engine(
            IdentitySigner { throw IllegalStateException("keystore unavailable") },
            lookupFor(client),
            clock,
        )

        val effects = failing.onInbound(client.helloFrame)
        assertTerminated(effects)
        assertTrue(effects.none { it is SessionEffect.Send })
        assertFalse(failing.isOpen)
    }

    @Test
    fun throwingHandlerFailsClosed() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client), ThrowingHandler(IllegalStateException("handler down")))
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, 1uL))
        assertTerminated(effects)
        assertTrue(effects.none { it is SessionEffect.Send })
        assertClosed(engine)
    }

    @Test
    fun handlerCancellationPropagates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client), ThrowingHandler(CancellationException("cancelled")))
        open(engine, client)

        val caught = try {
            engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, 1uL))
            null
        } catch (cancellation: CancellationException) {
            cancellation
        }
        assertNotNull("CancellationException must not be swallowed", caught)
    }

    @Test
    fun applicationErrorSendIsTerminal() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val effects = engine.sendApplication(
            RecordType.APP_ERROR.wire,
            appErrorBody(AppErrorCode.SESSION_EXPIRED),
        )

        assertEquals(3, effects.size)
        val error = client.openPhoneRecord((effects[0] as SessionEffect.Send).bytes, 1uL)
        assertEquals(RecordType.APP_ERROR.wire, error.type)
        assertEquals(AppErrorCode.SESSION_EXPIRED.wire, error.body[0].toInt() and 0xFF)
        assertTrue(effects[1] is SessionEffect.Terminated)
        assertEquals(SessionEffect.Close, effects[2])
        assertClosed(engine)
        assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 1uL)).isEmpty())
    }

    @Test
    fun handlerErrorReplyIsTerminalAndStopsFurtherReplies() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client), ErrorThenHeartbeatHandler())
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, 1uL))

        assertEquals(3, effects.size)
        val error = client.openPhoneRecord((effects[0] as SessionEffect.Send).bytes, 1uL)
        assertEquals(RecordType.APP_ERROR.wire, error.type)
        assertEquals(AppErrorCode.PROTOCOL_VIOLATION.wire, error.body[0].toInt() and 0xFF)
        assertTrue(effects[1] is SessionEffect.Terminated)
        assertEquals(SessionEffect.Close, effects[2])
        assertTrue(effects.none { it is SessionEffect.Deliver })
        assertClosed(engine)
        assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 2uL)).isEmpty())
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun bufferedErrorReplyAfterTeardownIsNotEmitted() = runTest {
        val client = TestClient()
        val gate = CompletableDeferred<Unit>()
        val handler = object : ApplicationHandler {
            override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire

            override suspend fun handle(
                type: Int,
                body: ByteArray,
                reply: suspend (type: Int, body: ByteArray) -> Unit,
            ) {
                reply(RecordType.APP_ERROR.wire, appErrorBody(AppErrorCode.PROTOCOL_VIOLATION))
                gate.await()
                error("handler failed after teardown")
            }
        }
        val engine = engine(lookupFor(client), handler)
        open(engine, client)

        val pending = async { engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, 1uL)) }
        runCurrent()
        engine.teardown()
        gate.complete(Unit)

        val effects = pending.await()
        assertTrue(effects.isEmpty())
        assertFalse(engine.isOpen)
        assertNull(engine.sessionClass)
    }

    private class ErrorThenHeartbeatHandler : ApplicationHandler {
        override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire

        override suspend fun handle(
            type: Int,
            body: ByteArray,
            reply: suspend (type: Int, body: ByteArray) -> Unit,
        ) {
            reply(RecordType.APP_ERROR.wire, appErrorBody(AppErrorCode.PROTOCOL_VIOLATION))
            reply(RecordType.APP_HEARTBEAT.wire, ByteArray(0))
            reply(RecordType.PULL_EVENTS.wire, ByteArray(0))
        }
    }

    private class ThrowingHandler(private val error: Exception) : ApplicationHandler {
        override fun handles(type: Int): Boolean = true

        override suspend fun handle(
            type: Int,
            body: ByteArray,
            reply: suspend (type: Int, body: ByteArray) -> Unit,
        ) {
            throw error
        }
    }
}
