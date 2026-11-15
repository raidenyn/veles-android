package me.nagaev.veles.session

import kotlinx.coroutines.test.runTest
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustStoreException
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The mutual-removal handler contract (design D4, OTP2-REC-013, mutual-peer-removal):
 *
 * - the responder deletes its record inside the handler, replies `removal_response` as the last
 *   record in that direction, and asks the engine for the reply-then-close outcome; it never
 *   tears the session down itself;
 * - a responder whose delete fails sends no response and ends through the engine's failure path,
 *   which is what lets the store-failure callback escalate;
 * - the requester deletes only after a `removal_response` that the engine delivered while the
 *   session was awaiting one; an unsolicited response ends the session and deletes nothing;
 * - a removal record on a non-full session is ended by the record policy, without any trust
 *   change.
 */
class RemovalHandlerTest {
    private val clock = FakeClock()
    private val phone = TestIdentity.generate()
    private val ops = RecordingRemovalOperations()

    private fun engine(
        client: TestClient,
        state: LocalRecordState = LocalRecordState.ACTIVE,
        operations: RemovalOperations = ops,
    ): PhoneSessionEngine = TestPeers.engine(
        phone.signer,
        TestPeers.lookup(TestPeers.record(client, state)),
        clock,
        RemovalHandler(operations),
    )

    private suspend fun open(
        engine: PhoneSessionEngine,
        client: TestClient,
        expected: SessionClass = SessionClass.FULL,
    ) {
        val effects = engine.completeHandshake(client)
        assertEquals(expected, (effects.single() as SessionEffect.Authenticated).sessionClass)
        assertTrue(engine.isOpen)
    }

    @Test
    fun responderDeletesRepliesAndCloses() = runTest {
        val client = TestClient()
        val engine = engine(client)
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.REMOVAL_REQUEST.wire, 1uL))

        assertEquals("the responder deletes its record first", listOf(TrustEncoding.hex(client.installationId)), ops.deleted)
        assertEquals("the response and the terminal pair", 3, effects.size)
        val response = effects[0] as SessionEffect.Send
        val opened = client.openPhoneRecord(response.bytes, 1uL)
        assertEquals("the reply is removal_response", RecordType.REMOVAL_RESPONSE.wire, opened.type)
        assertArrayEquals("removal_response is empty", ByteArray(0), opened.body)
        assertEquals(
            "a removal ends the session through its own stage",
            PhoneSessionEngine.STAGE_REMOVAL,
            (effects[1] as SessionEffect.Terminated).reason,
        )
        assertEquals(SessionEffect.Close, effects[2])
        assertTrue("the response is the last record in that direction", effects.drop(1).none { it is SessionEffect.Send })
        assertTrue("no application record is delivered on a terminal removal", effects.none { it is SessionEffect.Deliver })

        assertFalse("the engine performs the terminal teardown", engine.isOpen)
        assertTrue("the session keys are erased", engine.keysErasedForTest)
        assertTrue(
            "nothing is accepted after the close",
            engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 2uL)).isEmpty(),
        )
    }

    @Test
    fun responderDeleteFailureEndsSessionAndReportsStoreFailure() = runTest {
        val client = TestClient()
        val storeFailures = mutableListOf<String>()
        // The repository reports a store failure through its own callback and raises
        // TrustStoreException to the caller: this fake stands in for that pair.
        val failing = RecordingRemovalOperations(
            failure = true,
            onStoreFailure = { storeFailures += "re-pair required" },
        )
        val engine = engine(client, operations = failing)
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.REMOVAL_REQUEST.wire, 1uL))

        assertEquals("the store failure is reported once", 1, storeFailures.size)
        assertTerminated(effects)
        assertTrue("a failed delete sends no removal_response", effects.none { it is SessionEffect.Send })
        assertTrue("nothing was deleted", failing.deleted.isEmpty())
        assertFalse("the engine's failure path ends the session", engine.isOpen)
    }

    @Test
    fun pendingRequesterRejectsOtherInboundRecords() = runTest {
        val client = TestClient()
        val engine = engine(client)
        open(engine, client)
        engine.setRemovalPending(true)

        val effects = engine.onInbound(client.record(RecordType.EVENT_ACK.wire, 1uL, ByteArray(32) { 0x11 }))

        assertTerminated(effects)
        assertEquals(
            "the removal guard fails the session, not the unhandled-record path",
            "authorization",
            (effects[0] as SessionEffect.Terminated).reason,
        )
        assertTrue("the record is not delivered", effects.none { it is SessionEffect.Deliver })
        assertTrue("no trust is deleted", ops.deleted.isEmpty())
        assertFalse(engine.isOpen)
    }

    @Test
    fun pendingRequesterRejectsOutboundApplicationSend() = runTest {
        val client = TestClient()
        val engine = engine(client)
        open(engine, client)
        engine.setRemovalPending(true)

        val effects = engine.sendApplication(RecordType.PUSH_EVENT.wire, ByteArray(0))

        assertTerminated(effects)
        assertTrue("no record is sent on an awaiting session", effects.none { it is SessionEffect.Send })
        assertFalse("an application send on an awaiting session fails it", engine.isOpen)
    }

    @Test
    fun heartbeatAndAppErrorStillAllowedWhilePending() = runTest {
        val client = TestClient()
        val engine = engine(client)
        open(engine, client)
        engine.setRemovalPending(true)

        assertTrue(
            "a heartbeat is one of the allowed records while pending",
            engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 1uL)).isEmpty(),
        )
        assertTrue(engine.isOpen)

        val effects = engine.onInbound(
            client.record(RecordType.APP_ERROR.wire, 2uL, appErrorBody(AppErrorCode.SESSION_EXPIRED)),
        )

        assertEquals(
            "an app_error is allowed and stays terminal",
            "app_error",
            (effects[0] as SessionEffect.Terminated).reason,
        )
        assertTrue("an allowed record deletes nothing", ops.deleted.isEmpty())
        assertFalse(engine.isOpen)
    }

    @Test
    fun lostResponseKeepsRecord() = runTest {
        val client = TestClient()
        val engine = engine(client)
        open(engine, client)
        engine.setRemovalPending(true)

        // The peer never answers: the session ends on its own idle deadline, not a removal.
        clock.advance(SessionLimits.IDLE_TIMEOUT_MS)
        val effects = engine.onTick()

        assertEquals(
            "the session ends by its idle deadline while awaiting the response",
            "idle",
            (effects[0] as SessionEffect.Terminated).reason,
        )
        assertTrue("a lost response keeps the local record", ops.deleted.isEmpty())
        assertFalse(engine.isOpen)
        assertTrue("nothing is sent after a lost request", engine.onTick().isEmpty())
    }

    @Test
    fun unsolicitedResponseIsViolationAndDeletesNothing() = runTest {
        val client = TestClient()
        val engine = engine(client)
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.REMOVAL_RESPONSE.wire, 1uL))

        assertTerminated(effects)
        assertEquals(
            "a response without a pending request is a protocol violation",
            "authorization",
            (effects[0] as SessionEffect.Terminated).reason,
        )
        assertTrue("an unsolicited response deletes nothing", ops.deleted.isEmpty())
        assertFalse(engine.isOpen)
    }

    @Test
    fun removalOnNonFullSessionEnds() = runTest {
        val client = TestClient()
        val engine = engine(client, LocalRecordState.PENDING)
        open(engine, client, SessionClass.ACTIVATION_RECOVERY)

        val effects = engine.onInbound(client.record(RecordType.REMOVAL_REQUEST.wire, 1uL))

        assertTerminated(effects)
        assertEquals(
            "the record policy ends a removal on a non-full session",
            "authorization",
            (effects[0] as SessionEffect.Terminated).reason,
        )
        assertTrue("a non-full session never changes trust", ops.deleted.isEmpty())
        assertFalse(engine.isOpen)
    }
}

/**
 * A fake [RemovalOperations] (the coordinator in Task 5 implements the real contract). A
 * configured failure raises [TrustStoreException] after reporting the store failure, exactly as
 * the repository's `onStoreFailure` episode does.
 */
internal class RecordingRemovalOperations(
    private val failure: Boolean = false,
    private val onStoreFailure: () -> Unit = {},
) : RemovalOperations {
    val deleted = mutableListOf<String>()

    override suspend fun deleteFenced(peerId: String) {
        if (failure) {
            onStoreFailure()
            throw TrustStoreException()
        }
        deleted += peerId
    }
}
