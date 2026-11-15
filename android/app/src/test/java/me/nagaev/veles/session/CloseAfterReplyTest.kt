package me.nagaev.veles.session

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The explicit reply-then-close handler outcome (design D4, mutual-peer-removal "Close after
 * reply"): the handler's sealed replies are delivered, the engine tears itself down, the
 * terminal pair follows, and nothing is accepted afterwards. Production composition forwards
 * the outcome from the child that handled the record type.
 */
class CloseAfterReplyTest {
    private val clock = FakeClock()
    private val phone = TestIdentity.generate()

    private fun engine(client: TestClient, handler: ApplicationHandler?): PhoneSessionEngine = TestPeers.engine(
        phone.signer,
        TestPeers.lookup(TestPeers.record(client)),
        clock,
        handler,
    )

    private suspend fun open(engine: PhoneSessionEngine, client: TestClient) {
        val effects = engine.completeHandshake(client)
        assertEquals(SessionClass.FULL, (effects.single() as SessionEffect.Authenticated).sessionClass)
        assertTrue(engine.isOpen)
    }

    @Test
    fun closeAfterReplySendsReplyThenCloses() = runTest {
        val client = TestClient()
        val engine = engine(client, ReplyThenCloseHandler(closeAfterReply = true))
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.REMOVAL_REQUEST.wire, 1uL))

        assertEquals("the sealed reply comes first", 3, effects.size)
        val reply = effects[0] as SessionEffect.Send
        val opened = client.openPhoneRecord(reply.bytes, 1uL)
        assertEquals("the reply is removal_response", RecordType.REMOVAL_RESPONSE.wire, opened.type)
        assertArrayEquals("removal_response is empty", ByteArray(0), opened.body)
        assertTrue("the session is terminated after the reply", effects[1] is SessionEffect.Terminated)
        assertEquals(SessionEffect.Close, effects[2])
        assertTrue("no application record is delivered on a terminal reply", effects.none { it is SessionEffect.Deliver })
        assertTrue("the reply is the last record in that direction", effects.drop(1).none { it is SessionEffect.Send })

        assertFalse("the engine is torn down", engine.isOpen)
        assertNull("the session class is erased", engine.sessionClass)
        assertTrue("the session keys are erased", engine.keysErasedForTest)
        assertTrue(
            "the engine accepts nothing after the close",
            engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 2uL)).isEmpty(),
        )
    }

    @Test
    fun defaultCloseAfterReplyKeepsTheSessionOpen() = runTest {
        val client = TestClient()
        val engine = engine(client, ReplyThenCloseHandler(closeAfterReply = false))
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.REMOVAL_REQUEST.wire, 1uL))

        assertEquals("the record is delivered and answered", 2, effects.size)
        assertTrue(effects[0] is SessionEffect.Deliver)
        assertTrue(effects[1] is SessionEffect.Send)
        assertTrue("the default outcome never closes the session", engine.isOpen)
    }

    @Test
    fun compositeForwardsCloseAfterReply() {
        val composite = requireNotNull(
            composeApplicationHandlers(ReplyThenCloseHandler(closeAfterReply = true), SiblingHandler(RecordType.ACTIVATION_COMMIT.wire)),
        )
        assertTrue(
            "the child that wants the close is forwarded",
            composite.closeAfterReply(RecordType.REMOVAL_REQUEST.wire),
        )
        assertFalse(
            "a child that does not ask for a close stays false",
            composite.closeAfterReply(RecordType.ACTIVATION_COMMIT.wire),
        )
        assertFalse(
            "an unhandled type asks for no close",
            composite.closeAfterReply(RecordType.PULL_REQUEST.wire),
        )

        val context = SessionContext(
            sessionIdPrefix = ByteArray(8) { 0x01 },
            sessionClass = SessionClass.FULL,
            peerId = "00112233445566778899aabbccddeeff",
            et = null,
            peerState = 1,
            recovery = null,
        )
        assertTrue(
            "the session-scoped composition forwards the outcome too",
            composite.forSession(context).closeAfterReply(RecordType.REMOVAL_REQUEST.wire),
        )
    }

    @Test
    fun composedHandlerCloseAfterReplyTerminates() = runTest {
        val client = TestClient()
        val composed = requireNotNull(
            composeApplicationHandlers(ReplyThenCloseHandler(closeAfterReply = true), SiblingHandler(RecordType.ACTIVATION_COMMIT.wire)),
        )
        val engine = engine(client, composed)
        open(engine, client)

        val effects = engine.onInbound(client.record(RecordType.REMOVAL_REQUEST.wire, 1uL))

        assertEquals("the composed handler's close reaches the engine", SessionEffect.Close, effects.last())
        assertTrue(effects.dropLast(1).last() is SessionEffect.Terminated)
        assertFalse("a missed forward would silently keep the session open", engine.isOpen)
    }

    /** Answers `removal_request` with the empty `removal_response`, as the removal handler does. */
    private class ReplyThenCloseHandler(private val closeAfterReply: Boolean) : ApplicationHandler {
        override fun handles(type: Int): Boolean = type == RecordType.REMOVAL_REQUEST.wire

        override suspend fun handle(
            type: Int,
            body: ByteArray,
            reply: suspend (type: Int, body: ByteArray) -> Unit,
        ) {
            reply(RecordType.REMOVAL_RESPONSE.wire, ByteArray(0))
        }

        override fun closeAfterReply(type: Int): Boolean = closeAfterReply && type == RecordType.REMOVAL_REQUEST.wire
    }

    /** A sibling child that handles one other type and never asks to close. */
    private class SiblingHandler(private val handled: Int) : ApplicationHandler {
        override fun handles(type: Int): Boolean = type == handled

        override suspend fun handle(
            type: Int,
            body: ByteArray,
            reply: suspend (type: Int, body: ByteArray) -> Unit,
        ) = Unit
    }
}
