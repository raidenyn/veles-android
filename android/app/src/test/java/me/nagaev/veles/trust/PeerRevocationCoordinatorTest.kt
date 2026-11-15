package me.nagaev.veles.trust

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.PeerLookup
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.PreAuthCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.RemovalHandler
import me.nagaev.veles.session.RemovalOperations
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SessionContext
import me.nagaev.veles.session.SessionReject
import me.nagaev.veles.session.SignatureFrame
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.sharing.ConnectionGeneration
import me.nagaev.veles.sharing.RecordAdmission
import me.nagaev.veles.sharing.SessionHost
import me.nagaev.veles.sharing.SessionTransport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.security.KeyPairGenerator

/**
 * The revocation coordinator (design D2/D3/D5/D6/D8) over a real [SessionHost] and a recording
 * trust repository: the ordered local revoke, the detached handler-driven finish, the fence
 * lifetime, fail-closed recovery, reset ordering and the peer summary.
 *
 * Ordering is recorded in one event list shared by the repository and the transport. Nothing
 * sleeps: a suspended step is held on an [CompletableDeferred] gate and released by the test.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PeerRevocationCoordinatorTest {
    private val phone = TestIdentity.generate()
    private val clock = FakeClock()
    private val events = mutableListOf<String>()
    private val trust = FakeTrust(events)
    private val transport = RecordingTransport(events)
    private val statuses = mutableListOf<TrustStatus>()
    private val cleaned = mutableListOf<String>()

    private fun TestScope.host(create: (ConnectionGeneration) -> PhoneSessionEngine): SessionHost {
        val scope = backgroundScope
        return SessionHost(scope, transport, create, trust, clock = { clock.now })
    }

    private fun coordinator(host: SessionHost, scope: CoroutineScope): PeerRevocationCoordinator = PeerRevocationCoordinator(
        trust,
        host,
        scope,
        onStatus = { statuses += it },
        historyCleanup = { cleaned += it },
    ).also { created -> trust.reportStoreFailure = { created.failClosed() } }

    private fun engine(
        client: TestClient,
        handler: ApplicationHandler?,
        state: LocalRecordState = LocalRecordState.ACTIVE,
    ): PhoneSessionEngine = TestPeers.engine(phone.signer, TestPeers.lookup(TestPeers.record(client, state)), clock, handler)

    private fun admission(): RecordAdmission = RecordAdmission { }

    private fun TestScope.connect(host: SessionHost, gen: ConnectionGeneration) {
        host.onConnected(gen)
        runCurrent()
    }

    private fun TestScope.authenticate(
        host: SessionHost,
        gen: ConnectionGeneration,
        client: TestClient,
        peerState: Int = TestClient.PEER_STATE_ACTIVE,
    ) {
        host.onRecord(gen, client.helloFrame.copyOf(), admission())
        runCurrent()
        val handshake = transport.sent.filter { it.gen == gen }
        assertEquals("phone hello and signature", 2, handshake.size)
        val phoneHelloPayload = PreAuthCodec.decode(handshake[0].bytes).second
        val phoneSignature = SignatureFrame.decode(PreAuthCodec.decode(handshake[1].bytes).second, Role.PHONE).signature
        client.acceptPhoneHello(phoneHelloPayload, phoneSignature)
        host.onRecord(gen, client.signatureFrame(), admission())
        runCurrent()
        val token = requireNotNull(transport.sent.last().token) { "the phone reconnect_confirm must be tokened" }
        host.onRecord(gen, client.confirmRecord(peerState), admission())
        runCurrent()
        host.onSendComplete(gen, token, true)
        runCurrent()
    }

    private fun wireType(record: ByteArray): Int? {
        if (record.size < 5) return null
        if (record[0].toInt() and 0xFF != RECORD_PROTOCOL_VERSION) return null
        return ((record[3].toInt() and 0xFF) shl 8) or (record[4].toInt() and 0xFF)
    }

    @Test
    fun revokeOrderIsFenceDeleteTerminateAwaitSweepRelease() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)

        coordinator.revoke(TrustEncoding.hex(client.installationId))

        assertEquals(
            "fence, delete, terminate (close), sweep, release",
            listOf(FENCE, DELETE, CLOSE, SWEEP, RELEASE),
            events,
        )
        assertTrue("every matching generation ended", host.authenticatedSessions().isEmpty())
        assertEquals("the transport was closed for the peer", listOf(gen), transport.closed)
        assertEquals("the fence is released exactly once after the sweep", listOf(0), trust.endDepths)
    }

    @Test
    fun deleteFailureStillTerminatesMatching() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        trust.deleteFailure = TrustStoreException()

        val failure = try {
            coordinator.revoke(TrustEncoding.hex(client.installationId))
            null
        } catch (thrown: TrustException) {
            thrown
        }

        assertTrue("a failed delete is a store failure", failure is TrustStoreException)
        assertEquals("the peer's session is still terminated", listOf(gen), transport.closed)
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
        assertEquals("the failed delete escalates to fail-closed", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertEquals("the fence is still released", listOf(0), trust.endDepths)
    }

    @Test
    fun detachedFinishWaitsBehindTheHandlerStep() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        lateinit var coordinator: PeerRevocationCoordinator
        val host = host { engine(client, RemovalHandler(operations(coordinator))) }
        coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        val gate = CompletableDeferred<Unit>()
        trust.deleteGate = gate

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertTrue("the handler is suspended in the deletion", trust.revokes == 1)
        assertTrue("the detached finish cannot pass the handler's step", transport.closed.isEmpty())
        assertTrue("no sweep ran while the step holds the generation", events.none { it == SWEEP })
        assertFalse("the fence is still held", trust.endCalls > 0)

        gate.complete(Unit)
        runCurrent()

        assertEquals("the detachment finishes after the step", listOf(FENCE, DELETE, CLOSE, SWEEP, RELEASE), events)
        // One ordered send/close log: the reply must be the last record sent, the close must be
        // issued after it, and nothing at all may follow the close (design D4).
        val responseIndex = transport.calls.indexOfFirst {
            it is Sent && it.gen == gen && wireType(it.bytes) == RecordType.REMOVAL_RESPONSE.wire
        }
        val closeIndex = transport.calls.indexOfFirst { it is Closed && it.gen == gen }
        assertTrue("the response record is delivered", responseIndex >= 0)
        assertTrue("the close is issued", closeIndex >= 0)
        assertTrue("the response precedes the close", responseIndex < closeIndex)
        assertTrue("nothing is sent after the response", transport.calls.drop(responseIndex + 1).none { it is Sent })
        assertTrue("nothing is called after the close", closeIndex == transport.calls.lastIndex)
    }

    @Test
    fun deleteFailureInHandlerStillReleasesTheFence() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        lateinit var coordinator: PeerRevocationCoordinator
        val host = host { engine(client, RemovalHandler(operations(coordinator))) }
        coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        trust.deleteFailure = TrustStoreException()

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertTrue("a failed delete sends no response", transport.sent.none { wireType(it.bytes) == RecordType.REMOVAL_RESPONSE.wire })
        assertEquals("the engine's failure path ends the session", listOf(gen), transport.closed)
        assertEquals("the fence is released exactly once", listOf(0), trust.endDepths)
        assertEquals("the store failure escalates", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertTrue("the sweep still ran", events.contains(SWEEP))
    }

    @Test
    fun handlerExceptionStillReleasesTheFence() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        lateinit var coordinator: PeerRevocationCoordinator
        val host = host { engine(client, ThrowingRemoval(operations(coordinator), TrustEncoding.hex(client.installationId))) }
        coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertEquals("the handler deleted before throwing", events.take(2), listOf(FENCE, DELETE))
        assertTrue("the sweep still ran", events.contains(SWEEP))
        assertEquals("the fence is released exactly once", listOf(0), trust.endDepths)
        assertEquals("the generation ended", listOf(gen), transport.closed)
    }

    @Test
    fun sweepFailureReportsStoreFailureAndReleasesTheFence() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        lateinit var coordinator: PeerRevocationCoordinator
        val host = host { engine(client, RemovalHandler(operations(coordinator))) }
        coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        trust.sweepFailure = TrustStoreException()

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertEquals("a failed sweep is reported as a store failure", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertEquals("the fence is released exactly once", listOf(0), trust.endDepths)
        assertEquals("the sweep ran after the termination", listOf(FENCE, DELETE, CLOSE, SWEEP, RELEASE), events)
    }

    @Test
    fun cancellationStillReleasesTheFence() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        val gate = CompletableDeferred<Unit>()
        trust.deleteGate = gate

        val revocation = launch { coordinator.revoke(TrustEncoding.hex(client.installationId)) }
        runCurrent()
        revocation.cancel()
        runCurrent()

        assertTrue("the revocation chain is not interruptible", trust.endCalls == 0)
        assertTrue("the fence is still held while the release chain runs", trust.fenceDepth == 1)

        gate.complete(Unit)
        runCurrent()

        assertEquals("the fence is released exactly once", listOf(0), trust.endDepths)
        assertEquals("the termination and sweep still ran", listOf(FENCE, DELETE, CLOSE, SWEEP, RELEASE), events)
    }

    @Test
    fun fenceHeldAtHandlerReturnReleasedAfterEnded() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        lateinit var coordinator: PeerRevocationCoordinator
        lateinit var host: SessionHost
        host = host { engine(client, RemovalHandler(operations(coordinator))) }
        coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        val sweepGate = CompletableDeferred<Unit>()
        trust.sweepGate = sweepGate
        trust.releaseProbe = { host.authenticatedSessions().size }

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertEquals("the fence is still held after the handler returned", 1, trust.fenceDepth)
        assertEquals("nothing is released early", 0, trust.endCalls)
        assertEquals(
            "the handler returned its response",
            1,
            transport.sent.count { wireType(it.bytes) == RecordType.REMOVAL_RESPONSE.wire },
        )
        assertTrue("the current generation has ended before the sweep", host.authenticatedSessions().isEmpty())

        sweepGate.complete(Unit)
        runCurrent()

        assertEquals("the fence is released after the end", 1, trust.endCalls)
        assertEquals("no session was live at release", listOf(0), trust.probeResults)
    }

    @Test
    fun failClosedIsAsyncNonDestructiveAndReportsRePairRequired() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        trust.records += peerRecord(client)

        coordinator.failClosed()

        assertEquals("the single re-pair status is reported", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertTrue("the termination runs asynchronously", transport.closed.isEmpty())
        assertTrue("fail-closed deletes nothing", trust.revokes == 0 && trust.resetCalls == 0)
        assertTrue("fail-closed never fences", trust.fenceDepth == 0)
        assertEquals("the trust record is untouched", 1, trust.records.size)

        runCurrent()

        assertEquals("every generation is terminated", listOf(gen), transport.closed)
        assertTrue("no session remains", host.authenticatedSessions().isEmpty())
    }

    @Test
    fun handlerOriginatedStoreFailureDoesNotDeadlock() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        lateinit var coordinator: PeerRevocationCoordinator
        val host = host { engine(client, RemovalHandler(operations(coordinator))) }
        coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        // The repository reports the store failure through its callback and then throws, exactly
        // as `TrustRepository.update` does; the callback launches the fail-closed action from
        // inside the handler's step, which must not deadlock on the generation mutex.
        trust.deleteFailure = TrustStoreException()

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertEquals("the store failure is reported once", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertEquals("the fence is released exactly once", listOf(0), trust.endDepths)
        assertTrue("the generation ended", host.authenticatedSessions().isEmpty())
        assertTrue("the transport was closed", transport.closed.contains(gen))
    }

    @Test
    fun peerRejectionTerminatesNothingElse() = runTest {
        val first = TestClient(installationId = ByteArray(16) { (0x10 + it).toByte() })
        val second = TestClient(installationId = ByteArray(16) { (0x20 + it).toByte() })
        val firstGen = ConnectionGeneration("aa", 1)
        val secondGen = ConnectionGeneration("bb", 2)
        val lookup = PeerLookup { id ->
            listOf(first, second)
                .firstOrNull { it.installationId.contentEquals(id) }
                ?.let { TestPeers.record(it) }
        }
        val host = host { gen ->
            val client = if (gen == firstGen) first else second
            TestPeers.engine(phone.signer, lookup, clock, null)
        }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, firstGen)
        connect(host, secondGen)
        authenticate(host, firstGen, first)
        authenticate(host, secondGen, second)
        // A peer-level rejection, not a store failure: the delete names a peer that is gone.
        trust.deleteFailure = TrustException()

        try {
            coordinator.revoke(TrustEncoding.hex(first.installationId))
            fail("a peer rejection is reported to the caller")
        } catch (_: TrustException) { }

        assertFalse("the unrelated peer's transport is untouched", transport.closed.contains(secondGen))
        assertEquals("the unrelated session is still authorized", 1, host.authenticatedSessions().size)
        assertTrue("a peer rejection never escalates to fail-closed", statuses.isEmpty())
        assertEquals("the fence is released", listOf(0), trust.endDepths)
    }

    @Test
    fun resetTerminatesBeforeDeleting() = runTest {
        val first = TestClient(installationId = ByteArray(16) { (0x10 + it).toByte() })
        val second = TestClient(installationId = ByteArray(16) { (0x20 + it).toByte() })
        val firstGen = ConnectionGeneration("aa", 1)
        val secondGen = ConnectionGeneration("bb", 2)
        val lookup = PeerLookup { id ->
            listOf(first, second)
                .firstOrNull { it.installationId.contentEquals(id) }
                ?.let { TestPeers.record(it) }
        }
        val host = host { _ -> TestPeers.engine(phone.signer, lookup, clock, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, firstGen)
        connect(host, secondGen)
        authenticate(host, firstGen, first)
        authenticate(host, secondGen, second)

        coordinator.reset()

        assertEquals("every generation is closed before any deletion", listOf(CLOSE, CLOSE, RESET), events)
        assertEquals("both transports were closed", setOf(firstGen, secondGen), transport.closed.toSet())
        assertEquals("reset delegates the deletions", 1, trust.resetCalls)
        assertTrue("reset never revokes a record", trust.revokes == 0)
        assertTrue("reset never fences", trust.fenceDepth == 0)
        assertEquals(
            "a completed reset publishes the one recovery status",
            listOf(TrustStatus.OK),
            statuses,
        )
    }

    /**
     * OTP-11 D6 recovery: an explicit reset is the only path back from the latched
     * "re-pair required" status. It publishes OK only after the deletions and the identity-cache
     * refresh completed; a failed reset leaves the failure status in place.
     */
    @Test
    fun aCompletedResetPublishesOkAndAFailedResetKeepsTheFailureStatus() = runTest {
        val host = host { engine(TestClient(), null) }
        val coordinator = coordinator(host, backgroundScope)

        coordinator.failClosed()
        assertEquals("the failure latches first", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)

        trust.resetFailure = TrustStoreException()
        try {
            coordinator.reset()
            fail("a failed reset propagates instead of publishing OK")
        } catch (_: TrustException) {
            // The deletes and the cache refresh failed; the status must still say re-pair.
        }
        assertTrue("a failed reset never publishes OK", statuses.none { it == TrustStatus.OK })
        assertEquals(TrustStatus.RE_PAIR_REQUIRED, statuses.last())

        trust.resetFailure = null
        coordinator.reset()
        assertEquals("a later completed reset publishes OK", TrustStatus.OK, statuses.last())
    }

    @Test
    fun summariesHaveNoSecrets() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        val active = peerRecord(client, TrustState.ACTIVE, lastSeen = 5_000L)
        val pending = peerRecord(client, TrustState.PENDING, id = "04".repeat(16))
        val committed = peerRecord(client, TrustState.COMMITTED_WAITING_ACK, id = "05".repeat(16))
        trust.records += listOf(active, pending, committed)
        connect(host, gen)
        authenticate(host, gen, client)

        val summaries = coordinator.peers()

        assertEquals("every record is listed fresh", 3, summaries.size)
        val listed = summaries.single { it.installationId == active.installationId }
        assertEquals(active.label, listed.label)
        assertEquals(active.fingerprint, listed.fingerprint)
        assertEquals(active.deviceType, listed.deviceType)
        assertEquals(TrustState.ACTIVE, listed.state)
        assertEquals(active.createdAtMillis, listed.createdAtMillis)
        assertEquals(5_000L, listed.lastSeenMillis)
        assertTrue("the connected flag follows the live session", listed.connected)
        assertTrue(
            "peers without a session are not connected",
            summaries.filter { it.installationId != active.installationId }.none { it.connected },
        )

        val text = summaries.joinToString("\n")
        assertFalse("no public key in a summary", text.contains(active.publicKey))
        assertFalse(
            "no platform hint or handle in a summary",
            text.contains("publicKey") || text.contains("hint") || text.contains("handle"),
        )
    }

    @Test
    fun historyCleanupFailureNeverFailsRemoval() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val failingCleanup = mutableListOf<String>()
        lateinit var coordinator: PeerRevocationCoordinator
        val host = host { engine(client, RemovalHandler(operations(coordinator))) }
        coordinator = PeerRevocationCoordinator(
            trust,
            host,
            backgroundScope,
            onStatus = { statuses += it },
            historyCleanup = {
                failingCleanup += it
                error("history cleanup unavailable")
            },
        ).also { created -> trust.reportStoreFailure = { created.failClosed() } }
        connect(host, gen)
        authenticate(host, gen, client)

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertEquals("the cleanup runs once for the removed peer", listOf(TrustEncoding.hex(client.installationId)), failingCleanup)
        assertEquals(
            "a cleanup failure never fails the removal",
            1,
            transport.sent.count { wireType(it.bytes) == RecordType.REMOVAL_RESPONSE.wire },
        )
        assertEquals("the fence is released", listOf(0), trust.endDepths)

        events.clear()
        coordinator.revoke(TrustEncoding.hex(client.installationId))

        assertTrue("a local revoke also survives a cleanup failure", events.containsAll(listOf(FENCE, SWEEP, RELEASE)))
        assertEquals("the cleanup ran for the local revoke too", 2, failingCleanup.size)
        assertEquals("the fence is released after the local revoke", listOf(0, 0), trust.endDepths)
    }

    @Test
    fun cleanupCancellationNeverFailsRemoval() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val cleanedByCancellation = mutableListOf<String>()
        lateinit var coordinator: PeerRevocationCoordinator
        val host = host { engine(client, RemovalHandler(operations(coordinator))) }
        coordinator = PeerRevocationCoordinator(
            trust,
            host,
            backgroundScope,
            onStatus = { statuses += it },
            historyCleanup = {
                cleanedByCancellation += it
                throw CancellationException("history cleanup cancelled itself")
            },
        ).also { created -> trust.reportStoreFailure = { created.failClosed() } }
        connect(host, gen)
        authenticate(host, gen, client)

        host.onRecord(gen, client.record(RecordType.REMOVAL_REQUEST.wire, 1uL), admission())
        runCurrent()

        assertEquals("the cleanup ran once", listOf(TrustEncoding.hex(client.installationId)), cleanedByCancellation)
        assertEquals(
            "a cleanup-thrown cancellation never fails the removal response",
            1,
            transport.sent.count { wireType(it.bytes) == RecordType.REMOVAL_RESPONSE.wire },
        )
        assertTrue("the record is deleted", trust.revokes >= 1)
        assertEquals("the fence is released", listOf(0), trust.endDepths)

        events.clear()
        coordinator.revoke(TrustEncoding.hex(client.installationId))

        assertTrue("a local revoke also survives a cleanup-thrown cancellation", events.containsAll(listOf(FENCE, SWEEP, RELEASE)))
        assertEquals("the fence is released after the local revoke", listOf(0, 0), trust.endDepths)
    }

    @Test
    fun overlappingRevocationsOfOnePeerKeepTheFence() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        val gate = CompletableDeferred<Unit>()
        trust.deleteGate = gate

        val first = async { coordinator.revoke(TrustEncoding.hex(client.installationId)) }
        runCurrent()
        val second = async { coordinator.revoke(TrustEncoding.hex(client.installationId)) }
        runCurrent()

        assertEquals("both revocations are fenced", 2, trust.maxFenceDepth)
        gate.complete(Unit)
        first.await()
        second.await()
        runCurrent()

        assertEquals("the fence lifts only with the last release", listOf(1, 0), trust.endDepths)
    }

    @Test
    fun localRevokeReleasesFenceWhenCloseThrows() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        transport.closeFailure = IllegalStateException("transport refuses to close")

        coordinator.revoke(TrustEncoding.hex(client.installationId))

        assertEquals("the close was attempted", listOf(gen), transport.closed)
        assertTrue("the session was logically terminated", host.authenticatedSessions().isEmpty())
        assertEquals("the sweep still ran", listOf(FENCE, DELETE, CLOSE, SWEEP, RELEASE), events)
        assertEquals("the fence is released", listOf(0), trust.endDepths)
    }

    @Test
    fun localRevokeReleasesFenceWhenSweepFails() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        trust.sweepFailure = TrustStoreException()

        coordinator.revoke(TrustEncoding.hex(client.installationId))

        assertEquals("a failed sweep is a store failure", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertEquals("the fence is released after a failed sweep", listOf(0), trust.endDepths)
        assertEquals("the sweep ran", listOf(FENCE, DELETE, CLOSE, SWEEP, RELEASE), events)
    }

    @Test
    fun localRevokeReleasesFenceOnCancellation() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)
        val gate = CompletableDeferred<Unit>()
        trust.deleteGate = gate

        val revocation = launch { coordinator.revoke(TrustEncoding.hex(client.installationId)) }
        runCurrent()
        revocation.cancel()
        runCurrent()
        assertTrue("a cancellation never releases the fence early", trust.fenceDepth == 1)

        gate.complete(Unit)
        runCurrent()

        assertEquals("the fence is released exactly once", listOf(0), trust.endDepths)
        assertTrue("the session was still terminated", host.authenticatedSessions().isEmpty())
    }

    @Test
    fun detachedFinishStartsEvenWhenScopeAlreadyCancelled() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinatorScope = CoroutineScope(StandardTestDispatcher(testScheduler) + Job())
        val coordinator = coordinator(host, coordinatorScope)
        connect(host, gen)
        authenticate(host, gen, client)
        coordinatorScope.cancel()

        coordinator.deleteFenced(TrustEncoding.hex(client.installationId))

        assertEquals("the delete happened", listOf(FENCE, DELETE), events)
        assertEquals("dispatch is paused, so the fence is still held", 0, trust.endCalls)

        runCurrent()

        assertEquals("the detached finish still ran", listOf(FENCE, DELETE, CLOSE, SWEEP, RELEASE), events)
        assertEquals("the fence is released exactly once", listOf(0), trust.endDepths)
        assertNull("no failure was recorded", trust.sweepFailure)
    }

    @Test
    fun cleanupIsCalledWithTheInstallationIdAndNoOtherValue() = runTest {
        val client = TestClient()
        val gen = ConnectionGeneration("aa", 1)
        val host = host { engine(client, null) }
        val coordinator = coordinator(host, backgroundScope)
        connect(host, gen)
        authenticate(host, gen, client)

        coordinator.revoke(TrustEncoding.hex(client.installationId))

        assertEquals("the cleanup receives the revoked installation id", listOf(TrustEncoding.hex(client.installationId)), cleaned)
        assertNotNull("the record is gone", trust.revokes > 1)
    }

    private fun operations(coordinator: PeerRevocationCoordinator): RemovalOperations = object : RemovalOperations {
        override suspend fun deleteFenced(peerId: String) = coordinator.deleteFenced(peerId)
    }

    private fun peerRecord(
        client: TestClient,
        state: TrustState = TrustState.ACTIVE,
        id: String = TrustEncoding.hex(client.installationId),
        lastSeen: Long? = null,
    ): TrustedPeer {
        val publicKey = TrustEncoding.publicKey(client.identity.pair.public)
        return TrustedPeer(
            installationId = id,
            publicKey = publicKey,
            label = "Desktop",
            deviceType = 1,
            enrollmentTranscript = "03".repeat(32),
            fingerprint = peerFingerprint(TrustEncoding.publicKey(phone.pair.public), publicKey),
            state = state,
            createdAtMillis = 1_000L,
            lastSeenMillis = lastSeen,
            createdElapsedMillis = 10L,
            bootToken = "04".repeat(32),
            stateChangedAtMillis = 1_000L,
        )
    }

    private class Sent(val gen: ConnectionGeneration, val bytes: ByteArray, val token: Long?)

    private class Closed(val gen: ConnectionGeneration)

    private class RecordingTransport(private val events: MutableList<String>) : SessionTransport {
        /** Every send and every close in call order, so ordering assertions are real. */
        val calls = mutableListOf<Any>()
        val sent = mutableListOf<Sent>()
        val closed = mutableListOf<ConnectionGeneration>()
        var closeFailure: Exception? = null

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
            val record = Sent(gen, bytes.copyOf(), token)
            calls += record
            sent += record
            return true
        }

        override fun close(gen: ConnectionGeneration) {
            calls += Closed(gen)
            closed += gen
            events += CLOSE
            closeFailure?.let { throw it }
        }
    }

    /** One handler that deletes and then fails, to prove the fence release is handler-independent. */
    private class ThrowingRemoval(private val ops: RemovalOperations, private val peerId: String) : ApplicationHandler {
        override fun handles(type: Int): Boolean = type == RecordType.REMOVAL_REQUEST.wire

        override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
            ops.deleteFenced(peerId)
            throw SessionReject()
        }

        override fun forSession(context: SessionContext): ApplicationHandler = this
    }

    /**
     * A recording trust repository. Its `revoke` is the delete on the first call and the sweep on
     * every later one, so the two stages are separately failable and separately ordered.
     */
    private class FakeTrust(private val events: MutableList<String>) : InstallationTrustRepository {
        val records = mutableListOf<TrustedPeer>()
        private val endDepthsList = mutableListOf<Int>()
        val probeResults = mutableListOf<Int>()
        var fenceDepth = 0
            private set
        var maxFenceDepth = 0
            private set
        var endCalls = 0
            private set
        var revokes = 0
            private set
        var resetCalls = 0
            private set
        var deleteFailure: Exception? = null
        var sweepFailure: Exception? = null

        /** A reset that fails after the deletions; it must never publish the recovery status. */
        var resetFailure: Exception? = null
        var deleteGate: CompletableDeferred<Unit>? = null
        var sweepGate: CompletableDeferred<Unit>? = null

        /** The repository-side store-failure callback the composition root wires to fail-closed. */
        var reportStoreFailure: (() -> Unit)? = null
        var releaseProbe: (() -> Int)? = null
        val endDepths: List<Int> get() = endDepthsList

        override suspend fun identity(): InstallationIdentity = InstallationIdentity(
            "01".repeat(16),
            TrustEncoding.publicKey(KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair().public),
        )

        override suspend fun peers(): List<TrustedPeer> = records.toList()

        override suspend fun activePeers(): List<TrustedPeer> = records.filter { it.state == TrustState.ACTIVE }

        override suspend fun addPending(peer: PendingPeer) = error("not used by the coordinator")

        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("not used by the coordinator")

        override suspend fun revoke(peerId: String) {
            revokes += 1
            if (revokes == 1) {
                events += DELETE
                deleteGate?.await()
                deleteFailure?.let { throw reported(it) }
            } else {
                events += SWEEP
                sweepGate?.await()
                sweepFailure?.let { throw reported(it) }
            }
        }

        /** The repository contract: a store failure is reported at the choke point, then thrown. */
        private fun reported(failure: Exception): Exception {
            if (failure is TrustStoreException) reportStoreFailure?.invoke()
            return failure
        }

        override suspend fun beginRevocation(peerId: String) {
            events += FENCE
            fenceDepth += 1
            if (fenceDepth > maxFenceDepth) maxFenceDepth = fenceDepth
        }

        override suspend fun endRevocation(peerId: String) {
            events += RELEASE
            fenceDepth -= 1
            endCalls += 1
            endDepthsList += fenceDepth
            releaseProbe?.let { probeResults += it() }
        }

        override suspend fun markAuthenticated(peerId: String) = Unit

        override suspend fun resetIdentity() {
            events += RESET
            resetCalls += 1
            resetFailure?.let { throw reported(it) }
        }
    }

    private companion object {
        const val RECORD_PROTOCOL_VERSION = 1
        const val FENCE = "fence"
        const val DELETE = "delete"
        const val CLOSE = "close"
        const val SWEEP = "sweep"
        const val RELEASE = "release"
        const val RESET = "reset"
    }
}
