package me.nagaev.veles.sharing

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.PreAuthCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.RecordingRemovalOperations
import me.nagaev.veles.session.RemovalHandler
import me.nagaev.veles.session.RemovalOperations
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SignatureFrame
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.session.openPhoneRecord
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The requester's owner API on the session host (design D4, OTP2-REC-013):
 * [SessionHost.requestRemoval] runs on the peer's FULL generation, arms the engine's
 * removal-pending guard and a [REMOVAL_RESPONSE_TIMEOUT_MS] deadline on the injected clock,
 * deletes nothing itself, and settles only a validated response as [RemovalResult.REMOVED].
 * The deadline rides the existing 1 000 ms tick: no test sleeps or reads the wall clock.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SessionHostRemovalTest {
    private val phone = TestIdentity.generate()
    private val clock = FakeClock()

    private fun TestScope.host(
        transport: SessionTransport,
        factory: (ConnectionGeneration) -> PhoneSessionEngine,
    ): SessionHost = SessionHost(backgroundScope, transport, factory, RecordingTrust(), clock = { clock.now })

    private fun engine(
        client: TestClient,
        operations: RemovalOperations,
        state: LocalRecordState = LocalRecordState.ACTIVE,
    ): PhoneSessionEngine = TestPeers.engine(
        phone.signer,
        TestPeers.lookup(TestPeers.record(client, state)),
        clock,
        RemovalHandler(operations),
    )

    private fun TestScope.connect(host: SessionHost, gen: ConnectionGeneration) {
        host.onConnected(gen)
        runCurrent()
    }

    private fun TestScope.authenticate(
        host: SessionHost,
        gen: ConnectionGeneration,
        client: TestClient,
        transport: RecordingTransport,
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

    private fun admission(): RecordAdmission = RecordAdmission { }

    @Test
    fun requesterDeletesOnlyAfterValidResponse() = runTest {
        val client = TestClient()
        val wire = RecordingTransport()
        val ops = RecordingRemovalOperations()
        lateinit var created: PhoneSessionEngine
        val host = host(wire) { engine(client, ops).also { created = it } }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client, wire)
        val peerId = TrustEncoding.hex(client.installationId)

        val removal = async { host.requestRemoval(peerId) }
        runCurrent()

        val request = client.openPhoneRecord(wire.sent.last().bytes, 1uL)
        assertEquals("the requester sends removal_request", RecordType.REMOVAL_REQUEST.wire, request.type)
        assertArrayEquals("removal_request is empty", ByteArray(0), request.body)
        assertTrue("nothing is deleted before the response", ops.deleted.isEmpty())
        assertFalse("the exchange waits for the response", removal.isCompleted)
        assertTrue("the session stays open while awaiting", created.isOpen)

        host.onRecord(gen, client.record(RecordType.REMOVAL_RESPONSE.wire, 1uL), admission())
        runCurrent()

        assertEquals("a valid response removes the local record", RemovalResult.REMOVED, removal.await())
        assertEquals("the record is deleted once", listOf(peerId), ops.deleted)
        assertFalse("the requester closes the session", created.isOpen)
        assertTrue("the transport is closed", wire.calls.contains(Wire.Closed(gen)))
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
        assertEquals(
            "the request is the last record the requester sends",
            1,
            wire.sent.count { wireTypeOf(it.bytes) == RecordType.REMOVAL_REQUEST.wire },
        )
    }

    @Test
    fun removalSettlesRemovedWhenADisconnectRacesTheDeletion() = runTest {
        val client = TestClient()
        val wire = RecordingTransport()
        val ops = SuspendedRemovalOperations()
        lateinit var created: PhoneSessionEngine
        val host = host(wire) { engine(client, ops).also { created = it } }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client, wire)
        val peerId = TrustEncoding.hex(client.installationId)

        val removal = async { host.requestRemoval(peerId) }
        runCurrent()
        assertEquals(1, wire.sent.count { wireTypeOf(it.bytes) == RecordType.REMOVAL_REQUEST.wire })

        host.onRecord(gen, client.record(RecordType.REMOVAL_RESPONSE.wire, 1uL), admission())
        runCurrent()
        assertTrue("the handler is suspended in the deletion", ops.entered.isCompleted)
        assertTrue("nothing is deleted yet", ops.deleted.isEmpty())

        host.onDisconnected(gen)
        runCurrent()
        val callsAtDisconnect = wire.calls.size

        ops.allow()
        runCurrent()

        assertEquals(
            "the deletion that already happened still settles the exchange as removed",
            RemovalResult.REMOVED,
            removal.await(),
        )
        assertEquals("the local record is deleted", listOf(peerId), ops.deleted)
        assertEquals("nothing is sent after the disconnect", callsAtDisconnect, wire.calls.size)
        assertFalse("no session remains authorized", host.authenticatedSessions().isNotEmpty())
        assertFalse("the generation is torn down", created.isOpen)
    }

    @Test
    fun requestRemovalWithoutFullSessionIsNotConnected() = runTest {
        val client = TestClient()
        val wire = RecordingTransport()
        val ops = RecordingRemovalOperations()
        val host = host(wire) { engine(client, ops, LocalRecordState.PENDING) }
        val peerId = TrustEncoding.hex(client.installationId)

        assertEquals(
            "with no live session there is nothing to ask",
            RemovalResult.KEPT_NOT_CONNECTED,
            host.requestRemoval(peerId),
        )

        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client, wire, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)
        assertTrue("the recovery session is not full", host.openFullSessions().isEmpty())
        assertEquals(
            "a non-full session is not a removal destination",
            RemovalResult.KEPT_NOT_CONNECTED,
            host.requestRemoval(peerId),
        )

        assertTrue(
            "no removal_request is sent on a non-full session",
            wire.sent.none { wireTypeOf(it.bytes) == RecordType.REMOVAL_REQUEST.wire },
        )
        assertTrue("nothing is deleted", ops.deleted.isEmpty())
    }

    @Test
    fun disconnectKeepsRecord() = runTest {
        val client = TestClient()
        val wire = RecordingTransport()
        val ops = RecordingRemovalOperations()
        lateinit var created: PhoneSessionEngine
        val host = host(wire) { engine(client, ops).also { created = it } }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client, wire)

        val removal = async { host.requestRemoval(TrustEncoding.hex(client.installationId)) }
        runCurrent()
        assertEquals(1, wire.sent.count { wireTypeOf(it.bytes) == RecordType.REMOVAL_REQUEST.wire })

        host.onDisconnected(gen)
        runCurrent()

        assertEquals("a lost response keeps the record", RemovalResult.KEPT_NO_RESPONSE, removal.await())
        assertTrue("the local record is still present", ops.deleted.isEmpty())
        assertFalse("the generation ended", created.isOpen)
    }

    @Test
    fun deadlineAtTenThousandMillisKeepsRecord() = runTest {
        val client = TestClient()
        val wire = RecordingTransport()
        val ops = RecordingRemovalOperations()
        lateinit var created: PhoneSessionEngine
        val host = host(wire) { engine(client, ops).also { created = it } }
        val gen = ConnectionGeneration("aa", 1)
        connect(host, gen)
        authenticate(host, gen, client, wire)

        val removal = async { host.requestRemoval(TrustEncoding.hex(client.installationId)) }
        runCurrent()
        assertEquals(1, wire.sent.count { wireTypeOf(it.bytes) == RecordType.REMOVAL_REQUEST.wire })

        clock.advance(REMOVAL_RESPONSE_TIMEOUT_MS - 1)
        advanceTimeBy(TICK_MILLIS)
        runCurrent()
        assertFalse("the exchange waits until the deadline", removal.isCompleted)
        assertTrue("the session is still open one millisecond early", created.isOpen)

        clock.advance(1)
        advanceTimeBy(TICK_MILLIS)
        runCurrent()

        assertEquals("the deadline keeps the record", RemovalResult.KEPT_NO_RESPONSE, removal.await())
        assertTrue("the local record is still present", ops.deleted.isEmpty())
        assertFalse("the session fails at the deadline", created.isOpen)
        assertTrue("the transport is closed", wire.calls.contains(Wire.Closed(gen)))
    }

    @Test
    fun timerCancelledOnResponseDisconnectAndTeardown() = runTest {
        // A response settles the exchange and the deadline never fires afterwards.
        val answered = TestClient()
        val answeredWire = RecordingTransport()
        val answeredOps = RecordingRemovalOperations()
        val answeredHost = host(answeredWire) { engine(answered, answeredOps) }
        val answeredGen = ConnectionGeneration("aa", 1)
        connect(answeredHost, answeredGen)
        authenticate(answeredHost, answeredGen, answered, answeredWire)
        val answeredRemoval = async { answeredHost.requestRemoval(TrustEncoding.hex(answered.installationId)) }
        runCurrent()
        answeredHost.onRecord(answeredGen, answered.record(RecordType.REMOVAL_RESPONSE.wire, 1uL), admission())
        runCurrent()
        assertEquals(RemovalResult.REMOVED, answeredRemoval.await())
        val answeredCalls = answeredWire.calls.size

        clock.advance(REMOVAL_RESPONSE_TIMEOUT_MS * 2)
        advanceTimeBy(TICK_MILLIS)
        runCurrent()
        assertEquals("the deadline is cancelled by the response", answeredCalls, answeredWire.calls.size)
        assertEquals("the record is deleted exactly once", 1, answeredOps.deleted.size)

        // A disconnect settles the exchange and the deadline never fires afterwards.
        val dropped = TestClient()
        val droppedWire = RecordingTransport()
        val droppedOps = RecordingRemovalOperations()
        val droppedHost = host(droppedWire) { engine(dropped, droppedOps) }
        val droppedGen = ConnectionGeneration("bb", 2)
        connect(droppedHost, droppedGen)
        authenticate(droppedHost, droppedGen, dropped, droppedWire)
        val droppedRemoval = async { droppedHost.requestRemoval(TrustEncoding.hex(dropped.installationId)) }
        runCurrent()
        droppedHost.onDisconnected(droppedGen)
        runCurrent()
        assertEquals(RemovalResult.KEPT_NO_RESPONSE, droppedRemoval.await())
        val droppedCalls = droppedWire.calls.size

        clock.advance(REMOVAL_RESPONSE_TIMEOUT_MS * 2)
        advanceTimeBy(TICK_MILLIS)
        runCurrent()
        assertEquals("the deadline is cancelled by the disconnect", droppedCalls, droppedWire.calls.size)
        assertTrue(droppedOps.deleted.isEmpty())

        // A teardown settles the exchange and the deadline never fires afterwards.
        val closed = TestClient()
        val closedWire = RecordingTransport()
        val closedOps = RecordingRemovalOperations()
        val closedHost = host(closedWire) { engine(closed, closedOps) }
        val closedGen = ConnectionGeneration("cc", 3)
        connect(closedHost, closedGen)
        authenticate(closedHost, closedGen, closed, closedWire)
        val closedRemoval = async { closedHost.requestRemoval(TrustEncoding.hex(closed.installationId)) }
        runCurrent()
        closedHost.closeAll()
        runCurrent()
        assertEquals(RemovalResult.KEPT_NO_RESPONSE, closedRemoval.await())
        val closedCalls = closedWire.calls.size

        clock.advance(REMOVAL_RESPONSE_TIMEOUT_MS * 2)
        advanceTimeBy(TICK_MILLIS)
        runCurrent()
        assertEquals("the deadline is cancelled by the teardown", closedCalls, closedWire.calls.size)
        assertTrue(closedOps.deleted.isEmpty())

        // A fresh generation after all three still runs a normal exchange, so no state leaked.
        val next = TestClient()
        val nextWire = RecordingTransport()
        val nextOps = RecordingRemovalOperations()
        val nextHost = host(nextWire) { engine(next, nextOps) }
        val nextGen = ConnectionGeneration("dd", 4)
        connect(nextHost, nextGen)
        authenticate(nextHost, nextGen, next, nextWire)
        val nextRemoval = async { nextHost.requestRemoval(TrustEncoding.hex(next.installationId)) }
        runCurrent()
        nextHost.onRecord(nextGen, next.record(RecordType.REMOVAL_RESPONSE.wire, 1uL), admission())
        runCurrent()
        assertEquals(RemovalResult.REMOVED, nextRemoval.await())
        assertEquals(listOf(TrustEncoding.hex(next.installationId)), nextOps.deleted)
    }

    private sealed interface Wire {
        data class Sent(val gen: ConnectionGeneration, val bytes: ByteArray, val token: Long?) : Wire

        data class Closed(val gen: ConnectionGeneration) : Wire
    }

    private class RecordingTransport : SessionTransport {
        val calls = mutableListOf<Wire>()
        val sent get() = calls.filterIsInstance<Wire.Sent>()

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
            calls += Wire.Sent(gen, bytes.copyOf(), token)
            return true
        }

        override fun close(gen: ConnectionGeneration) {
            calls += Wire.Closed(gen)
        }
    }

    /**
     * A fake [RemovalOperations] whose deletion suspends until [allow], so a test can race a
     * disconnect into the handler's deletion.
     */
    private class SuspendedRemovalOperations : RemovalOperations {
        val entered = CompletableDeferred<Unit>()
        val deleted = mutableListOf<String>()
        private val gate = CompletableDeferred<Unit>()

        fun allow() = gate.complete(Unit)

        override suspend fun deleteFenced(peerId: String) {
            entered.complete(Unit)
            gate.await()
            deleted += peerId
        }
    }

    /** Only [markAuthenticated] matters here: the host writes it at the authentication gate. */
    private class RecordingTrust : InstallationTrustRepository {
        override suspend fun identity(): InstallationIdentity = error("not used by the removal host")
        override suspend fun peers(): List<TrustedPeer> = emptyList()
        override suspend fun activePeers(): List<TrustedPeer> = emptyList()
        override suspend fun addPending(peer: PendingPeer) = error("not used by the removal host")
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("not used by the removal host")
        override suspend fun revoke(peerId: String) = error("not used by the removal host")
        override suspend fun beginRevocation(peerId: String) = error("not used by the removal host")
        override suspend fun endRevocation(peerId: String) = error("not used by the removal host")
        override suspend fun markAuthenticated(peerId: String) = Unit
        override suspend fun resetIdentity() = error("not used by the removal host")
    }

    private companion object {
        const val TICK_MILLIS = 1_000L

        /** The record type of a protected record; null for a pre-auth frame. */
        fun wireTypeOf(bytes: ByteArray): Int? {
            if (bytes.size < 5) return null
            if (bytes[0].toInt() and 0xFF != RECORD_PROTOCOL_VERSION) return null
            return ((bytes[3].toInt() and 0xFF) shl 8) or (bytes[4].toInt() and 0xFF)
        }

        const val RECORD_PROTOCOL_VERSION = 1
    }
}
