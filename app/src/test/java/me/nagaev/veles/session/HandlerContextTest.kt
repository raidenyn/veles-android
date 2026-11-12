package me.nagaev.veles.session

import kotlinx.coroutines.test.runTest
import me.nagaev.veles.trust.TrustEncoding
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP-10 plan 9b: the phone engine hands an application or activation handler the fixed session
 * context at the `Authenticated` gate, and a registered handler can send an unprompted record
 * without a prior inbound record. An activation record with no handler keeps the OTP-09
 * fail-closed rule (`app_error` `protocol_violation`).
 */
class HandlerContextTest {
    private val clock = FakeClock()
    private val inputs = SessionFixtures.input(SessionFixtures.RECONNECT)
    private val outputs = SessionFixtures.outputs(SessionFixtures.RECONNECT)

    private fun recoveryRecord(client: TestClient): PeerRecord = PeerRecord(
        client.installationId,
        client.identity.publicKey,
        LocalRecordState.PENDING,
        SessionFixtures.bytes(inputs, "et"),
    )

    private fun engine(record: PeerRecord, handler: ApplicationHandler?): PhoneSessionEngine = TestPeers.engine(
        TestIdentity.generate().signer,
        TestPeers.lookup(record),
        clock,
        handler,
    )

    @Test
    fun handler_receives_context_on_authenticated() = runTest {
        val client = TestClient()
        val handler = ContextCapturingHandler()
        val engine = engine(recoveryRecord(client), handler)

        val effects = engine.completeHandshake(client, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)
        val authenticated = effects.single() as SessionEffect.Authenticated
        assertEquals(SessionClass.ACTIVATION_RECOVERY, authenticated.sessionClass)

        val context = handler.context ?: error("the registered handler must receive the session context")
        assertEquals(SessionClass.ACTIVATION_RECOVERY, context.sessionClass)
        assertEquals(TrustEncoding.hex(client.installationId), context.peerId)
        assertArrayEquals(SessionFixtures.bytes(inputs, "et"), context.et)
        assertEquals(TestClient.PEER_STATE_BELOW_ACTIVE, context.peerState)
        assertArrayEquals(client.keys.sessionId.copyOfRange(0, 8), context.sessionIdPrefix)

        // OTP2-CRY-019f: a non-full class keeps the live recovery holder derived from ET.
        val recovery = context.recovery ?: error("a recovery class must keep the derived keys")
        assertArrayEquals(SessionFixtures.bytes(outputs, "k_rec_client"), recovery.kRecClient)
        assertArrayEquals(SessionFixtures.bytes(outputs, "k_rec_phone"), recovery.kRecPhone)
    }

    @Test
    fun handler_can_send_unprompted_record() = runTest {
        val client = TestClient()
        val handler = ContextCapturingHandler()
        val engine = engine(recoveryRecord(client), handler)
        engine.completeHandshake(client, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)
        val context = handler.context ?: error("the registered handler must receive the session context")

        // OTP2-STA-010b step 0: the client reported peer_state 0x02, so the recovered phone
        // owes the prepare. Nothing inbound was processed; the handler sends it from its context.
        val body = ByteArray(32) { (it + 1).toByte() }
        val effects = engine.sendApplication(RecordType.ENROLLMENT_PREPARE.wire, body)

        val send = effects.single() as SessionEffect.Send
        val opened = client.openPhoneRecord(send.bytes, 1uL)
        assertEquals(RecordType.ENROLLMENT_PREPARE.wire, opened.type)
        assertArrayEquals(body, opened.body)
        assertEquals(TestClient.PEER_STATE_BELOW_ACTIVE, context.peerState)
        assertTrue("the unprompted send leaves the session open", engine.isOpen)
    }

    @Test
    fun activation_record_without_handler_protocol_violation() = runTest {
        val client = TestClient()
        val engine = engine(recoveryRecord(client), handler = null)
        engine.completeHandshake(client, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)

        // No activation handler is registered: the authorized activation record must keep the
        // OTP-09 fail-closed rule and change no trust state.
        val effects = engine.onInbound(
            client.record(RecordType.ACTIVATION_COMMIT.wire, 1uL, ByteArray(32)),
        )

        assertEquals(3, effects.size)
        val error = client.openPhoneRecord((effects[0] as SessionEffect.Send).bytes, 1uL)
        assertEquals(RecordType.APP_ERROR.wire, error.type)
        assertEquals(AppErrorCode.PROTOCOL_VIOLATION.wire, error.body[0].toInt() and 0xFF)
        val terminated = effects[1] as SessionEffect.Terminated
        assertEquals("the no-handler branch fired, not authorization", "unhandled", terminated.reason)
        assertEquals(SessionEffect.Close, effects[2])
        assertTrue("nothing is delivered", effects.none { it is SessionEffect.Deliver })
        assertFalse("the session ends", engine.isOpen)
    }
}
