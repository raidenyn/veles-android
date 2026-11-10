package me.nagaev.veles.session

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Heartbeat, idle and handshake-deadline rules on an injected monotonic clock (tasks.md 3.5). */
class PhoneSessionEngineTimingTest {
    private val clock = FakeClock()
    private val phone = TestIdentity.generate()

    private fun engine(
        lookup: PeerLookup,
        handler: ApplicationHandler? = null,
    ): PhoneSessionEngine = TestPeers.engine(phone.signer, lookup, clock, handler)

    private fun lookupFor(client: TestClient): PeerLookup = TestPeers.lookup(TestPeers.record(client))

    private suspend fun open(engine: PhoneSessionEngine, client: TestClient) {
        val effects = engine.completeHandshake(client)
        assertEquals(SessionClass.FULL, (effects.single() as SessionEffect.Authenticated).sessionClass)
        assertTrue(engine.isOpen)
    }

    private fun assertClosed(engine: PhoneSessionEngine) {
        assertFalse(engine.isOpen)
        assertNull(engine.sessionClass)
        assertNull(engine.peerId)
    }

    @Test
    fun earlyHeartbeatIgnored() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 1uL)).isEmpty())
        clock.advance(10_000L)
        assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 2uL)).isEmpty())
        assertTrue(engine.isOpen)
    }

    @Test
    fun fifthHeartbeatIn60sTerminates() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        for (sequence in 1uL..4uL) {
            assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, sequence)).isEmpty())
            clock.advance(10_000L)
        }

        assertTerminated(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 5uL)))
        assertClosed(engine)
    }

    @Test
    fun directionsCountedSeparately() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        clock.advance(40_000L)
        for (sequence in 1uL..4uL) {
            assertTrue(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, sequence)).isEmpty())
            if (sequence < 4uL) clock.advance(1_000L)
        }

        clock.advance(27_000L)
        val outbound = engine.onTick().single() as SessionEffect.Send
        assertEquals(
            RecordType.APP_HEARTBEAT.wire,
            client.openPhoneRecord(outbound.bytes, 1uL).type,
        )
        assertTrue(engine.isOpen)

        clock.advance(1_000L)
        assertTerminated(engine.onInbound(client.record(RecordType.APP_HEARTBEAT.wire, 5uL)))
        assertClosed(engine)
    }

    @Test
    fun applicationHeartbeatSpacingEnforced() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        val first = engine.sendApplication(RecordType.APP_HEARTBEAT.wire, ByteArray(0)).single()
            as SessionEffect.Send
        assertEquals(RecordType.APP_HEARTBEAT.wire, client.openPhoneRecord(first.bytes, 1uL).type)

        clock.advance(SessionLimits.HEARTBEAT_MIN_INTERVAL_MS - 1_000L)
        assertTrue(engine.sendApplication(RecordType.APP_HEARTBEAT.wire, ByteArray(0)).isEmpty())

        clock.advance(1_000L)
        val second = engine.sendApplication(RecordType.APP_HEARTBEAT.wire, ByteArray(0)).single()
            as SessionEffect.Send
        assertEquals(RecordType.APP_HEARTBEAT.wire, client.openPhoneRecord(second.bytes, 2uL).type)
        assertTrue(engine.isOpen)
    }

    @Test
    fun outboundOnlyActivityPreventsIdle() = runTest {        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        repeat(4) {
            clock.advance(SessionLimits.IDLE_TIMEOUT_MS - 10_000L)
            assertEquals(
                1,
                engine.sendApplication(RecordType.PULL_EVENTS.wire, ByteArray(0)).size,
            )
            assertTrue(engine.isOpen)
        }
    }

    @Test
    fun idleTimeoutCountsInjectedSleepTime() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        open(engine, client)

        clock.advance(SessionLimits.IDLE_TIMEOUT_MS)
        assertTerminated(engine.onTick())
        assertClosed(engine)

        val inboundClock = FakeClock()
        val inboundClient = TestClient()
        val inbound = TestPeers.engine(phone.signer, lookupFor(inboundClient), inboundClock)
        open(inbound, inboundClient)
        inboundClock.advance(SessionLimits.IDLE_TIMEOUT_MS)

        assertTerminated(inbound.onInbound(inboundClient.record(RecordType.APP_HEARTBEAT.wire, 1uL)))
        assertClosed(inbound)
    }

    @Test
    fun clientSignatureTimeout30s() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        val helloEffects = engine.onInbound(client.helloFrame)
        assertEquals(2, helloEffects.size)
        val phoneHello = PreAuthCodec.decode((helloEffects[0] as SessionEffect.Send).bytes).second
        val phoneSignature = SignatureFrame.decode(
            PreAuthCodec.decode((helloEffects[1] as SessionEffect.Send).bytes).second,
            Role.PHONE,
        ).signature
        client.acceptPhoneHello(phoneHello, phoneSignature)

        clock.advance(SessionLimits.HANDSHAKE_TIMEOUT_MS)
        val effects = engine.onInbound(client.signatureFrame())

        assertTerminated(effects)
        assertClosed(engine)
    }

    @Test
    fun helloWaitExpires30s() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))

        clock.advance(SessionLimits.HANDSHAKE_TIMEOUT_MS)
        val effects = engine.onInbound(client.helloFrame)

        assertTerminated(effects)
        assertTrue(effects.none { it is SessionEffect.Send })
        assertClosed(engine)
    }

    @Test
    fun lateSignerCompletionDiscarded() = runTest {
        val client = TestClient()
        val engine = engine(lookupFor(client))
        val progress = engine.driveToPeerConfirm(client)
        assertTrue(engine.onInbound(progress.clientConfirmRecord).isEmpty())
        assertFalse(engine.isOpen)

        clock.advance(SessionLimits.HANDSHAKE_TIMEOUT_MS)
        val effects = engine.onSendComplete(progress.token, true)

        assertTerminated(effects)
        assertTrue(effects.none { it is SessionEffect.Authenticated })
        assertClosed(engine)
    }

    @Test
    fun trustSurvivesTeardown() = runTest {
        val client = TestClient()
        val record = TestPeers.record(client)
        val lookup = MutablePeerLookup(record)
        val engine = engine(lookup)
        open(engine, client)

        engine.teardown()

        assertClosed(engine)
        assertNotNull(lookup.record)
        val surviving = checkNotNull(lookup.record)
        assertArrayEquals(record.installationId, surviving.installationId)
        assertArrayEquals(record.identityPublicKey, surviving.identityPublicKey)
        assertEquals(record.state, surviving.state)
    }
}
