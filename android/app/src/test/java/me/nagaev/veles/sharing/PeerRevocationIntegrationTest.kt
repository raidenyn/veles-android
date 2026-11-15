package me.nagaev.veles.sharing

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.pairing.ActivationEngine
import me.nagaev.veles.pairing.ActivationProof
import me.nagaev.veles.pairing.DeviceType
import me.nagaev.veles.pairing.EnrollmentKeys
import me.nagaev.veles.pairing.EnrollmentRecords
import me.nagaev.veles.pairing.EnrollmentSchedule
import me.nagaev.veles.pairing.Handle
import me.nagaev.veles.pairing.IdentityRole
import me.nagaev.veles.pairing.PairingConnection
import me.nagaev.veles.pairing.PairingCore
import me.nagaev.veles.pairing.PairingFrameType
import me.nagaev.veles.pairing.PairingFrames
import me.nagaev.veles.pairing.ProofRegistry
import me.nagaev.veles.pairing.Started
import me.nagaev.veles.session.AppErrorCode
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.IdentitySigner
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.PeerLookup
import me.nagaev.veles.session.PeerRecord
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.PreAuthCodec
import me.nagaev.veles.session.RecordCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.RemovalHandler
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SignatureFrame
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.session.composeApplicationHandlers
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PeerRevocationCoordinator
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStatus
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustStoreException
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.security.KeyPair
import java.security.interfaces.ECPublicKey
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The OTP-11 revocation flows end to end on the production-shaped Android stack (design
 * D2/D3/D6/D7/D8): the real [TrustRepository] over in-memory storage, the real [SessionHost],
 * the real reconnect engine and the real pairing machine, a fake transport, a deterministic
 * pairing core and the real coordinator.
 *
 * Every deadline runs on the injected [FakeClock]; nothing sleeps.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PeerRevocationIntegrationTest {
    private val clock = FakeClock()
    private val events = mutableListOf<String>()
    private val statuses = mutableListOf<TrustStatus>()
    private val cleaned = mutableListOf<String>()
    private val storage = MemoryStorage(events)
    private val keys = SoftwareKeys(events)
    private val proofs = ProofRegistry()
    private val transport = RecordingTransport(events)
    private val core = FakePairingCore()
    private val fingerprints = mutableListOf<String>()
    private val pairings = mutableMapOf<Long, PairingConnection>()

    /** The production boot-token supplier's failure: `BOOT_COUNT` unavailable or invalid. */
    private var bootFailure: TrustException? = null

    private lateinit var repository: TrustRepository
    private lateinit var trust: GatedTrust
    private lateinit var identities: LocalIdentity
    private lateinit var host: SessionHost
    private lateinit var pairing: PairingEndpoint
    private lateinit var coordinator: PeerRevocationCoordinator
    private lateinit var handler: ApplicationHandler

    private suspend fun TestScope.start() {
        lateinit var created: PeerRevocationCoordinator
        repository = TrustRepository(
            storage,
            keys,
            { clock.now },
            { bootFailure?.let { throw it } ?: BOOT },
            { clock.now },
            ActivationVerifier { peer, step, proof -> proofs.consume(peer, step, proof) },
        ) { created.failClosed() }
        // The production identity cache and reset decorator, wired exactly as the service does.
        identities = LocalIdentity(repository)
        trust = GatedTrust(ResettingTrust(repository, identities))
        pairing = PairingEndpoint(
            clock = { clock.now },
            trust = trust,
            proofs = proofs,
            coreFactory = { core },
            onPairFingerprint = { fingerprints += it },
        )
        host = SessionHost(
            scope = backgroundScope,
            transport = transport,
            engineFactory = { engine() },
            trust = trust,
            clock = { clock.now },
            pairingFactory = { gen -> pairing.connection(gen.serial).also { pairings[gen.serial] = it } },
            onSessionClosed = { sessionId -> proofs.revoke(sessionId) },
        )
        created = PeerRevocationCoordinator(
            trust,
            host,
            backgroundScope,
            onStatus = { statuses += it },
            historyCleanup = { cleaned += it },
        )
        coordinator = created
        handler = checkNotNull(composeApplicationHandlers(RemovalHandler(coordinator), ActivationEngine(trust, proofs)))
        // The service warms the identity cache before the first link is admitted.
        identities.warm()
    }

    private fun engine(): PhoneSessionEngine = PhoneSessionEngine(
        localInstallationId = identities.currentOrEmpty(),
        signer = IdentitySigner { message -> keys.sign(message) },
        lookup = PeerLookup { installationId -> lookup(installationId) },
        clock = { clock.now },
        nonce = { ByteArray(16) { index -> (index + 7).toByte() } },
        handler = handler,
    )

    private suspend fun lookup(installationId: ByteArray): PeerRecord? = trust.peers()
        .firstOrNull { TrustEncoding.bytes(it.installationId).contentEquals(installationId) }
        ?.let { peer ->
            PeerRecord(
                installationId = TrustEncoding.bytes(peer.installationId),
                identityPublicKey = TrustEncoding.bytes(peer.publicKey),
                state = when (peer.state) {
                    TrustState.ACTIVE -> LocalRecordState.ACTIVE
                    TrustState.PENDING -> LocalRecordState.PENDING
                    TrustState.COMMITTED_WAITING_ACK -> LocalRecordState.COMMITTED_WAITING_ACK
                },
                et = TrustEncoding.bytes(peer.enrollmentTranscript),
            )
        }

    private fun TestScope.connect(gen: ConnectionGeneration) {
        host.onConnected(gen)
        runCurrent()
    }

    private fun TestScope.send(gen: ConnectionGeneration, bytes: ByteArray) {
        host.onRecord(gen, bytes.copyOf(), RecordAdmission { })
        runCurrent()
    }

    private fun Sent.isOn(gen: ConnectionGeneration): Boolean = this.gen == gen

    private fun lastFrame(gen: ConnectionGeneration): ByteArray {
        val sent = transport.calls.filterIsInstance<Sent>().lastOrNull { it.isOn(gen) }
        return checkNotNull(sent) { "no frame sent on $gen" }.bytes
    }

    private fun TestScope.reconnect(
        gen: ConnectionGeneration,
        client: TestClient,
        et: ByteArray? = null,
        peerState: Int = TestClient.PEER_STATE_ACTIVE,
    ) {
        connect(gen)
        send(gen, client.helloFrame.copyOf())
        val handshake = transport.calls.filterIsInstance<Sent>().filter { it.isOn(gen) }
        assertEquals("phone hello and signature", 2, handshake.size)
        val phoneHelloPayload = PreAuthCodec.decode(handshake[0].bytes).second
        val phoneSignature = SignatureFrame.decode(PreAuthCodec.decode(handshake[1].bytes).second, Role.PHONE).signature
        client.acceptPhoneHello(phoneHelloPayload, phoneSignature, et)
        send(gen, client.signatureFrame())
        val token = requireNotNull(transport.calls.filterIsInstance<Sent>().last().token) { "the phone confirm must be tokened" }
        send(gen, client.confirmRecord(peerState))
        host.onSendComplete(gen, token, true)
        runCurrent()
    }

    /** Pair to the verified `cB`: the enrollment connection is protected and bound. */
    private fun TestScope.pairToProtected(gen: ConnectionGeneration, client: PairingClient) {
        connect(gen)
        pairing.window.open()
        send(gen, client.requestFrame())
        client.acceptOffer(lastFrame(gen))
        send(gen, client.pointFrame())
        client.acceptConfirmation(lastFrame(gen))
        send(gen, client.confirmFrame())
        client.acceptPhoneIdentity(lastFrame(gen))
    }

    /** Pair to `awaiting_commit`: the pending record is durable and `enrollment_prepare` was sent. */
    private fun TestScope.pairToPending(gen: ConnectionGeneration, client: PairingClient) {
        pairToProtected(gen, client)
        send(gen, client.clientIdentityFrame())
    }

    /** Pair to `active`: pending, then the verified commit and ack, as production does. */
    private fun TestScope.pairToActive(gen: ConnectionGeneration, client: PairingClient) {
        pairToPending(gen, client)
        send(gen, client.commitFrame())
        send(gen, client.ackFrame())
    }

    private suspend fun seedPending(client: PairingClient, et: ByteArray) {
        repository.addPending(
            PendingPeer(
                installationId = client.installationHex,
                publicKey = client.publicKeyHex,
                label = "Desktop",
                deviceType = 1,
                enrollmentTranscript = TrustEncoding.hex(et),
            ),
        )
    }

    private suspend fun seedActive(client: PairingClient, et: ByteArray = ByteArray(32) { 0x44 }) {
        seedPending(client, et)
        confirm(client.installationHex, ActivationStep.COMMIT)
        confirm(client.installationHex, ActivationStep.ACK)
    }

    private suspend fun confirm(peerId: String, step: ActivationStep) {
        val proof = ByteArray(32) { 0x5a }
        proofs.register("seed", listOf(ActivationProof(peerId, step, proof)))
        repository.confirm(peerId, step, proof)
    }

    @Test
    fun corruptStateSeenByReconnectLookupClosesUnrelatedSessions() = runTest {
        start()
        val first = PairingClient(ByteArray(16) { (0x10 + it).toByte() })
        val second = PairingClient(ByteArray(16) { (0x20 + it).toByte() })
        seedActive(first)
        seedActive(second)
        val firstGen = ConnectionGeneration("aa", 1)
        val secondGen = ConnectionGeneration("bb", 2)
        reconnect(firstGen, first.reconnectClient())
        reconnect(secondGen, second.reconnectClient())
        assertEquals("two live sessions", 2, host.authenticatedSessions().size)
        val storedBefore = storage.snapshot

        // The stored payload becomes undecodable: the next reconnect lookup is a store failure.
        storage.fault = Fault.READ
        val third = PairingClient(ByteArray(16) { (0x30 + it).toByte() })
        val thirdGen = ConnectionGeneration("cc", 3)
        connect(thirdGen)
        send(thirdGen, third.reconnectClient().helloFrame.copyOf())

        assertEquals("the single re-pair status", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertTrue("the unrelated first session is closed", transport.closed.contains(firstGen))
        assertTrue("the unrelated second session is closed", transport.closed.contains(secondGen))
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
        assertEquals("a store failure deletes nothing", storedBefore, storage.snapshot)
    }

    @Test
    fun failingDurableWriteClosesUnrelatedSessions() = runTest {
        start()
        val first = PairingClient(ByteArray(16) { (0x10 + it).toByte() })
        val second = PairingClient(ByteArray(16) { (0x20 + it).toByte() })
        val third = PairingClient(ByteArray(16) { (0x30 + it).toByte() })
        seedActive(first)
        seedActive(second)
        seedActive(third)
        val firstGen = ConnectionGeneration("aa", 1)
        val secondGen = ConnectionGeneration("bb", 2)
        reconnect(firstGen, first.reconnectClient())
        reconnect(secondGen, second.reconnectClient())

        // The handshake reads succeed and only the authentication metadata write fails: it is the
        // third transaction of the reconnect (hello lookup, signature lookup, markAuthenticated).
        storage.writeFaultAt = storage.nextTransaction() + 2
        val thirdGen = ConnectionGeneration("cc", 3)
        reconnect(thirdGen, third.reconnectClient())

        assertEquals("the single re-pair status", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertTrue("the unrelated first session is closed", transport.closed.contains(firstGen))
        assertTrue("the unrelated second session is closed", transport.closed.contains(secondGen))
        assertFalse("the failing session never authenticated", host.authenticatedSessions().any { it.generation == thirdGen })
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
    }

    @Test
    fun unavailableBootTokenClosesUnrelatedSessions() = runTest {
        start()
        val first = PairingClient(ByteArray(16) { (0x10 + it).toByte() })
        val second = PairingClient(ByteArray(16) { (0x20 + it).toByte() })
        seedActive(first)
        seedActive(second)
        val firstGen = ConnectionGeneration("aa", 1)
        val secondGen = ConnectionGeneration("bb", 2)
        reconnect(firstGen, first.reconnectClient())
        reconnect(secondGen, second.reconnectClient())
        assertEquals("two live sessions", 2, host.authenticatedSessions().size)
        val storedBefore = storage.snapshot

        // The production supplier cannot read `BOOT_COUNT`; continuity validation is
        // unavailable, so the plain TrustException must be classified as a store failure.
        bootFailure = TrustException()
        val failure = try {
            repository.peers()
            null
        } catch (thrown: TrustException) {
            thrown
        }
        runCurrent()

        assertTrue("a boot-token failure is a store failure", failure is TrustStoreException)
        assertEquals("the single re-pair status", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertTrue("the unrelated first session is closed", transport.closed.contains(firstGen))
        assertTrue("the unrelated second session is closed", transport.closed.contains(secondGen))
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
        assertEquals("fail-closed deletes nothing", storedBefore, storage.snapshot)
        assertTrue("fail-closed never resets", events.none { it == STORAGE_RESET || it == KEY_DELETED })
    }

    @Test
    fun revokeBeforeAddPendingLeavesNoRecordOrConnection() = runTest {
        start()
        val client = PairingClient()
        val gen = ConnectionGeneration("aa", 1)
        pairToProtected(gen, client)
        // The duplicate check is suspended before `addPending`, so this peer identity is
        // authenticated and bound while nothing is durable yet (design D3/D8).
        trust.peersGate = CompletableDeferred()
        trust.peersEntered = CompletableDeferred()
        val persist = async { send(gen, client.clientIdentityFrame()) }
        trust.peersEntered!!.await()
        assertEquals(
            "the connection is bound to the authenticated id before the durable write",
            client.installationHex,
            pairings.getValue(gen.serial).boundPeerId(),
        )

        val revocation = async { coordinator.revoke(client.installationHex) }
        runCurrent()
        assertTrue("nothing is closed while the step holds the generation", transport.closed.isEmpty())
        assertFalse("the fence is still held", trust.fenceReleased)

        trust.peersGate!!.complete(Unit)
        persist.await()
        revocation.await()
        runCurrent()

        assertTrue("no record was ever created", repository.peers().isEmpty())
        assertTrue("the enrollment connection is closed", transport.closed.contains(gen))
        assertTrue(
            "the persist was refused, not prepared",
            transport.calls.none { it is Sent && recordTypeOf(it.bytes) == RecordType.ENROLLMENT_PREPARE.wire },
        )
        assertTrue("a peer rejection never escalates to fail-closed", statuses.isEmpty())
        assertTrue("the fence is released", trust.fenceReleased)
    }

    @Test
    fun revokeBetweenAddPendingAndCommitFailsActivation() = runTest {
        start()
        val client = PairingClient()
        val gen = ConnectionGeneration("aa", 1)
        pairToPending(gen, client)
        assertEquals("the pending record is durable", TrustState.PENDING, repository.peers().single().state)

        coordinator.revoke(client.installationHex)

        assertTrue("the pending record is deleted", repository.peers().isEmpty())
        assertTrue("the enrollment connection is closed", transport.closed.contains(gen))
        assertTrue("a healthy revocation escalates to nothing", statuses.isEmpty())

        // A later activation step on the closed generation changes nothing.
        val mark = transport.calls.size
        send(gen, client.commitFrame())
        assertEquals("no frame is sent on a closed generation", 0, transport.calls.drop(mark).size)
        assertTrue("no record is recreated", repository.peers().isEmpty())
    }

    @Test
    fun revokeBetweenCommitAndAckFailsAck() = runTest {
        start()
        val client = PairingClient()
        val gen = ConnectionGeneration("aa", 1)
        pairToPending(gen, client)
        send(gen, client.commitFrame())
        assertEquals(
            "the commit moved the record",
            TrustState.COMMITTED_WAITING_ACK,
            repository.peers().single().state,
        )

        coordinator.revoke(client.installationHex)

        assertTrue("the committed record is deleted", repository.peers().isEmpty())
        assertTrue("the enrollment connection is closed", transport.closed.contains(gen))
        val mark = transport.calls.size
        send(gen, client.ackFrame())
        assertEquals("the later ack reaches no live connection", 0, transport.calls.drop(mark).size)
        assertTrue("no record is recreated", repository.activePeers().isEmpty())
    }

    @Test
    fun revokeDuringSignedRecoveryClosesConnection() = runTest {
        start()
        val client = PairingClient()
        val et = ByteArray(32) { 0x33 }
        seedPending(client, et)
        val gen = ConnectionGeneration("aa", 1)
        val reconnecting = client.reconnectClient()
        reconnect(gen, reconnecting, et = et, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)
        assertEquals(
            "the pending record recovers through the activation class",
            SessionClass.ACTIVATION_RECOVERY,
            host.authenticatedSessions().single().sessionClass,
        )
        assertTrue(
            "the recovery session owes enrollment_prepare",
            transport.calls.filterIsInstance<Sent>().any { recordTypeOf(it.bytes) == RecordType.ENROLLMENT_PREPARE.wire },
        )
        client.useRecoveryKeys(reconnecting, et)

        coordinator.revoke(client.installationHex)

        assertTrue("the pending record is deleted", repository.peers().isEmpty())
        assertTrue("the recovery connection is closed", transport.closed.contains(gen))
        assertTrue("no session remains authorized", host.authenticatedSessions().isEmpty())
        val mark = transport.calls.size
        send(gen, client.commitFrame())
        assertEquals("the later activation step reaches no live connection", 0, transport.calls.drop(mark).size)
    }

    @Test
    fun revokeDuringDuplicateCommit() = runTest {
        start()
        val client = PairingClient()
        val et = ByteArray(32) { 0x55 }
        seedActive(client, et)
        val gen = ConnectionGeneration("aa", 1)
        val reconnecting = client.reconnectClient()
        reconnect(gen, reconnecting, et = et, peerState = TestClient.PEER_STATE_BELOW_ACTIVE)
        assertEquals(
            "an active record reports the below-active state",
            SessionClass.FULL_PENDING_PEER,
            host.authenticatedSessions().single().sessionClass,
        )
        client.useRecoveryKeys(reconnecting, et)

        // The duplicate commit is suspended inside its store write, so the revocation runs
        // concurrently with it (design D8).
        trust.confirmGate = CompletableDeferred()
        trust.confirmEntered = CompletableDeferred()
        val commit = async { send(gen, client.commitFrame()) }
        trust.confirmEntered!!.await()

        val revocation = async { coordinator.revoke(client.installationHex) }
        runCurrent()
        trust.confirmGate!!.complete(Unit)
        commit.await()
        revocation.await()
        runCurrent()

        assertTrue("no record remains after the sweep", repository.peers().isEmpty())
        assertTrue("the connection is closed", transport.closed.contains(gen))
        assertTrue("a peer rejection in the race terminates nothing else", statuses.isEmpty())
        assertTrue("the fence is released", trust.fenceReleased)
    }

    @Test
    fun revokedPeerReconnectFailsGenerically() = runTest {
        start()
        val client = PairingClient()
        seedActive(client)
        val gen = ConnectionGeneration("aa", 1)
        reconnect(gen, client.reconnectClient())
        assertEquals("the peer authenticated first", 1, host.authenticatedSessions().size)

        coordinator.revoke(client.installationHex)

        val retry = ConnectionGeneration("bb", 2)
        connect(retry)
        send(retry, client.reconnectClient().helloFrame.copyOf())

        val frames = transport.calls.filterIsInstance<Sent>().filter { it.isOn(retry) }
        assertEquals("one generic refusal, no handshake", 1, frames.size)
        assertArrayEquals("the refusal is the generic empty preauth_reject", PairingFrames.preauthReject(), frames.single().bytes)
        assertTrue("the connection is closed", transport.closed.contains(retry))
        assertTrue(
            "no application record is delivered",
            transport.calls.filterIsInstance<Sent>().none { it.isOn(retry) && recordTypeOf(it.bytes) != null },
        )
        assertTrue("no record is recreated", repository.peers().isEmpty())
    }

    @Test
    fun repairAfterRevokeMatchesFingerprints() = runTest {
        start()
        val client = PairingClient()
        pairToActive(ConnectionGeneration("aa", 1), client)
        assertEquals("the first pairing activated the record", TrustState.ACTIVE, repository.peers().single().state)
        val displayed = fingerprints.single()
        val computed = client.pairFingerprintDisplay()
        assertEquals("both identities define the pair fingerprint", computed, displayed)
        val first = repository.peers().single()

        coordinator.revoke(client.installationHex)
        assertTrue("the revocation removed the record", repository.peers().isEmpty())

        // The fence was released, so a deliberate re-pairing of the same id is allowed.
        pairToActive(ConnectionGeneration("bb", 2), client)
        val repaired = repository.peers().single()
        assertEquals("the new record is active", TrustState.ACTIVE, repaired.state)
        assertEquals("the installation id is unchanged", first.installationId, repaired.installationId)
        assertEquals("the same identities define the same fingerprint", first.fingerprint, repaired.fingerprint)
        assertEquals("the display matches too", computed, client.pairFingerprintDisplay())
        assertEquals("the fingerprint is published for the new pairing", 2, fingerprints.size)
    }

    @Test
    fun pairWhileRecordHeldIsRefused() = runTest {
        start()
        val client = PairingClient()
        seedActive(client)
        val existing = repository.peers().single()
        val gen = ConnectionGeneration("aa", 1)
        pairToProtected(gen, client)

        send(gen, client.clientIdentityFrame())

        val refusal = lastFrame(gen)
        assertEquals("the refusal is a protected app_error", RecordType.APP_ERROR.wire, recordTypeOf(refusal))
        assertEquals(
            "the refusal is the protected enrollment_rejected error",
            AppErrorCode.ENROLLMENT_REJECTED.wire,
            client.openNextPhoneFrame(refusal)[0].toInt() and U8_MASK,
        )
        assertEquals("the existing record is untouched", existing, repository.peers().single())
        assertEquals("no pending write replaced it", 1, repository.peers().size)
        assertTrue("the connection is closed", transport.closed.contains(gen))
    }

    @Test
    fun resetThenReconnectUsesFreshIdentity() = runTest {
        start()
        val client = PairingClient()
        seedActive(client)
        val original = repository.identity()
        val gen = ConnectionGeneration("aa", 1)
        reconnect(gen, client.reconnectClient())
        assertEquals("the peer session is live", 1, host.authenticatedSessions().size)

        coordinator.reset()

        assertTrue("the session is terminated", transport.closed.contains(gen))
        assertEquals(
            "every generation is closed before any deletion",
            listOf(CLOSED, STORAGE_RESET, KEY_DELETED),
            events,
        )
        assertTrue("the peer set is empty", repository.peers().isEmpty())
        val fresh = repository.identity()
        assertNotEquals("the next use creates a fresh identity", original.installationId, fresh.installationId)
        assertArrayEquals("the cache is refreshed after the reset", TrustEncoding.bytes(fresh.installationId), identities.current())

        // The old peer record is gone: a reconnect of that client fails generically.
        val retry = ConnectionGeneration("bb", 2)
        connect(retry)
        send(retry, client.reconnectClient().helloFrame.copyOf())
        assertArrayEquals("the refusal is generic", PairingFrames.preauthReject(), lastFrame(retry))
        assertTrue("the reconnect is closed", transport.closed.contains(retry))
        assertEquals("a completed reset publishes OK", listOf(TrustStatus.OK), statuses)
    }

    @Test
    fun failedRefreshAfterResetRefusesTheNextGeneration() = runTest {
        start()
        val client = PairingClient()
        seedActive(client)
        val deleted = repository.identity()
        val gen = ConnectionGeneration("aa", 1)
        reconnect(gen, client.reconnectClient())
        assertEquals("the peer session is live", 1, host.authenticatedSessions().size)

        // The store fails right after the reset's row and key deletions, so the production cache
        // cannot be refreshed. The deleted installation id must never be served again.
        storage.fault = Fault.READ
        val failure = try {
            coordinator.reset()
            null
        } catch (thrown: TrustException) {
            thrown
        }

        assertTrue("a failed refresh fails the reset instead of keeping the stale id", failure is TrustStoreException)
        assertEquals("the store failure is reported", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertTrue("the session was terminated first", transport.closed.contains(gen))

        // A generation created while no identity can be loaded is refused, never authenticated
        // with the deleted installation id.
        val refused = ConnectionGeneration("bb", 2)
        connect(refused)
        send(refused, client.reconnectClient().helloFrame.copyOf())
        assertTrue(
            "no phone hello is sent with a stale identity",
            transport.calls.filterIsInstance<Sent>().none { it.isOn(refused) },
        )
        assertTrue("the refused generation is closed", transport.closed.contains(refused))
        assertTrue("nothing authenticates", host.authenticatedSessions().isEmpty())

        // Once the store answers again, the next generation reloads a fresh identity.
        storage.fault = null
        val reloaded = identities.current()
        assertNotEquals("the deleted id is never served again", deleted.installationId, TrustEncoding.hex(reloaded))
        assertArrayEquals(
            "the reloaded id is the repository's current identity",
            TrustEncoding.bytes(repository.identity().installationId),
            reloaded,
        )
    }

    // -------------------------------------------------------------------------------------
    // Harness
    // -------------------------------------------------------------------------------------

    private enum class Fault { READ, WRITE }

    private class MemoryStorage(private val events: MutableList<String>) : TrustStorage {
        var fault: Fault? = null
        var snapshot: TrustSnapshot? = null
            private set
        private var transactions = 0

        /** The transaction number (1-based, counting from the next one) whose durable write fails. */
        var writeFaultAt: Int? = null

        fun nextTransaction(): Int = transactions + 1

        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
            transactions += 1
            if (fault == Fault.READ) throw IOException("undecodable trust payload")
            if (writeFaultAt == transactions) throw IOException("durable write failed")
            val (next, result) = block(snapshot)
            if (fault == Fault.WRITE) throw IOException("durable write failed")
            snapshot = next
            return result
        }

        override suspend fun reset() {
            events += STORAGE_RESET
            snapshot = null
        }
    }

    private class SoftwareKeys(private val events: MutableList<String>) : IdentityKeyStore {
        private var pair: KeyPair? = null

        override fun publicKey(): String? = pair?.let { TrustEncoding.publicKey(it.public) }

        override fun generate(): String {
            val generated = P256.generate()
            pair = generated
            return TrustEncoding.publicKey(generated.public)
        }

        override fun sign(message: ByteArray): ByteArray = TestPeers.signDer(checkNotNull(pair).private, message)

        override fun delete() {
            events += KEY_DELETED
            pair = null
        }
    }

    /** The service's trust facade: gates the suspend points a test races and refreshes the cache. */
    private class GatedTrust(
        private val delegate: InstallationTrustRepository,
    ) : InstallationTrustRepository by delegate {
        var peersGate: CompletableDeferred<Unit>? = null
        var peersEntered: CompletableDeferred<Unit>? = null
        var confirmGate: CompletableDeferred<Unit>? = null
        var confirmEntered: CompletableDeferred<Unit>? = null
        private val fences = mutableMapOf<String, Int>()

        val fenceReleased: Boolean get() = fences.isEmpty()

        override suspend fun peers(): List<TrustedPeer> {
            peersEntered?.complete(Unit)
            peersGate?.await()
            return delegate.peers()
        }

        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) {
            confirmEntered?.complete(Unit)
            confirmGate?.await()
            delegate.confirm(peerId, step, proof)
        }

        override suspend fun beginRevocation(peerId: String) {
            fences[peerId] = (fences[peerId] ?: 0) + 1
            delegate.beginRevocation(peerId)
        }

        override suspend fun endRevocation(peerId: String) {
            val remaining = (fences[peerId] ?: 1) - 1
            if (remaining <= 0) fences.remove(peerId) else fences[peerId] = remaining
            delegate.endRevocation(peerId)
        }
    }

    private class Sent(val gen: ConnectionGeneration, val bytes: ByteArray, val token: Long?)

    private class Closed(val gen: ConnectionGeneration)

    private class RecordingTransport(private val events: MutableList<String>) : SessionTransport {
        val calls = mutableListOf<Any>()
        val closed = mutableListOf<ConnectionGeneration>()

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
            calls += Sent(gen, bytes.copyOf(), token)
            return true
        }

        override fun close(gen: ConnectionGeneration) {
            closed += gen
            calls += Closed(gen)
            events += CLOSED
        }
    }

    /** A deterministic pairing core: a fixed point, a fixed confirmation and a fixed `Ke`. */
    private class FakePairingCore : PairingCore {
        private val handles = LinkedHashSet<Long>()
        private var next = 0L
        val ke = ByteArray(16) { 0x11 }
        val ca = ByteArray(32) { 0x0a }
        private val point = TestPeers.uncompressed(P256.generate().public as ECPublicKey)

        override fun start(role: Role, code: ByteArray, context: ByteArray): Started {
            val handle = Handle(++next)
            handles += handle.bits
            return Started(handle, point.copyOf())
        }

        override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray = ca.copyOf()

        override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray = ByteArray(0)

        override fun finish(handle: Handle): ByteArray {
            handles -= handle.bits
            return ke.copyOf()
        }

        override fun close(handle: Handle) {
            handles -= handle.bits
        }

        override fun openHandles(): Int = handles.size
    }

    /** The client half of one enrollment and of the recovery activation records. */
    private inner class PairingClient(
        val installationId: ByteArray = ByteArray(16) { (0x10 + it).toByte() },
    ) {
        val identity = TestIdentity.generate()
        val installationHex: String get() = TrustEncoding.hex(installationId)
        val publicKeyHex: String get() = TrustEncoding.publicKey(identity.pair.public)
        private val nonce = ByteArray(16) { (it + 1).toByte() }
        private val point = TestPeers.uncompressed(P256.generate().public as ECPublicKey)
        private val confirm = ByteArray(32) { 0x0b }
        private var phonePoint: ByteArray = ByteArray(0)
        private var context: ByteArray = ByteArray(0)
        private var phoneConfirm: ByteArray = ByteArray(0)
        private var phoneIdentityBody: ByteArray = ByteArray(0)
        private var clientIdentityBody: ByteArray = ByteArray(0)
        private lateinit var keys: EnrollmentKeys
        private lateinit var prefix: ByteArray
        private var activationKey: ByteArray? = null
        private var activationTranscript: ByteArray? = null
        private var recoverySession: TestClient? = null
        private var recoveryTx = 1uL
        private var tx = 0uL
        private var rx = 0uL

        fun requestFrame(): ByteArray = requestFrameOf(nonce)

        fun pointFrame(): ByteArray = PairingFrames.encode(PairingFrameType.PB, point)

        fun confirmFrame(): ByteArray = PairingFrames.encode(PairingFrameType.CB, confirm)

        fun acceptOffer(frame: ByteArray) {
            val offer = offerOf(frame)
            phonePoint = offer.point
            context = contextOf(offer.pairingId, offer.phoneNonce, nonce, offer.caps, CLIENT_CAPS)
        }

        fun acceptConfirmation(frame: ByteArray) {
            phoneConfirm = frame.copyOfRange(HEADER, frame.size)
            keys = EnrollmentSchedule.derive(core.ke, context)
            prefix = EnrollmentSchedule.sessionIdPrefix(context)
            // A fresh protected enrollment starts both record directions at sequence zero.
            tx = 0uL
            rx = 0uL
        }

        fun acceptPhoneIdentity(frame: ByteArray) {
            phoneIdentityBody = openPhone(frame)
        }

        /** Opens the next phone→client protected body, for wire-level assertions. */
        fun openNextPhoneFrame(frame: ByteArray): ByteArray = openPhone(frame)

        fun clientIdentityFrame(deviceType: DeviceType = DeviceType.DESKTOP): ByteArray {
            val record = EnrollmentRecords.create(
                installationId,
                LABEL,
                deviceType,
                TestPeers.uncompressed(identity.pair.public as ECPublicKey),
                ByteArray(16) { 0x33 },
            )
            clientIdentityBody = EnrollmentRecords.encode(record, IdentityRole.CLIENT)
            return seal(RecordType.ENROLL_IDENTITY.wire, clientIdentityBody)
        }

        fun commitFrame(): ByteArray = activationFrame(RecordType.ACTIVATION_COMMIT.wire, STAGE_COMMIT)

        fun ackFrame(): ByteArray = activationFrame(RecordType.ACTIVATION_ACK.wire, STAGE_ACK)

        /** The signed-recovery MACs and records use the live reconnect session and its `ET`. */
        fun useRecoveryKeys(reconnecting: TestClient, et: ByteArray) {
            activationKey = checkNotNull(reconnecting.keys.recovery) { "the record carries no recovery keys" }.kRecClient
            activationTranscript = et.copyOf()
            recoverySession = reconnecting
        }

        fun reconnectClient(): TestClient = TestClient(identity = identity, installationId = installationId)

        fun pairFingerprintDisplay(): String {
            val phone = EnrollmentRecords.decode(phoneIdentityBody.copyOf(), IdentityRole.PHONE)
            val pair = EnrollmentRecords.pairFingerprint(
                phone.identityPubkey,
                TestPeers.uncompressed(identity.pair.public as ECPublicKey),
            )
            return try {
                EnrollmentRecords.displayFingerprint(pair)
            } finally {
                pair.fill(0)
            }
        }

        private fun activationFrame(type: Int, stage: Byte): ByteArray {
            val transcript = activationTranscript ?: transcript()
            val key = activationKey ?: keys.clientActivationKey
            val mac = hmac(key, transcript + byteArrayOf(stage))
            return try {
                seal(type, mac)
            } finally {
                mac.fill(0)
            }
        }

        private fun transcript(): ByteArray = EnrollmentRecords.transcript(
            context,
            phonePoint,
            point,
            phoneConfirm,
            confirm,
            phoneIdentityBody,
            clientIdentityBody,
        )

        private fun seal(type: Int, body: ByteArray): ByteArray {
            val session = recoverySession
            return if (session == null) {
                RecordCodec.seal(keys.bToAAesKey, keys.bToANoncePrefix, prefix, Direction.CLIENT_TO_PHONE, type, tx++, body)
            } else {
                val sessionPrefix = session.keys.sessionId.copyOfRange(0, SESSION_PREFIX_BYTES)
                RecordCodec.seal(
                    session.keys.c2pKey,
                    session.keys.c2pPrefix,
                    sessionPrefix,
                    Direction.CLIENT_TO_PHONE,
                    type,
                    recoveryTx++,
                    body,
                )
            }
        }

        private fun openPhone(frame: ByteArray): ByteArray {
            val opened = RecordCodec.open(
                keys.aToBAesKey,
                keys.aToBNoncePrefix,
                prefix,
                Direction.PHONE_TO_CLIENT,
                rx++,
                frame,
            )
            return opened.body
        }
    }

    private companion object {
        const val BOOT = "boot-a"
        const val LABEL = "Desktop"
        const val HEADER = 3
        const val STAGE_COMMIT: Byte = 0x01
        const val STAGE_ACK: Byte = 0x03
        const val U8_MASK = 0xFF
        const val BITS_PER_BYTE = 8
        const val U16_BYTES = 2
        const val NONCE_BYTES = 16
        const val PAIRING_ID_BYTES = 16
        const val SESSION_PREFIX_BYTES = 8
        const val REQUEST_FIXED = NONCE_BYTES + 4
        const val VERSION_OFFSET = NONCE_BYTES
        const val CAPS_LEN_OFFSET = NONCE_BYTES + 2
        const val CONTEXT_FIXED = 30 + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES + NONCE_BYTES + 4
        const val CLOSED = "close"
        const val STORAGE_RESET = "storage-reset"
        const val KEY_DELETED = "key-delete"
        val CONTEXT_PREFIX = "Veles SPAKE2 public context v1".toByteArray(Charsets.US_ASCII)
        val CLIENT_CAPS = byteArrayOf(0x01, 0x03)

        data class Offer(val pairingId: ByteArray, val phoneNonce: ByteArray, val caps: ByteArray, val point: ByteArray)

        fun recordTypeOf(record: ByteArray): Int? {
            if (record.size < 5) return null
            if (record[0].toInt() and U8_MASK != 1) return null
            return ((record[3].toInt() and U8_MASK) shl BITS_PER_BYTE) or (record[4].toInt() and U8_MASK)
        }

        fun requestFrameOf(clientNonce: ByteArray): ByteArray {
            val payload = ByteArray(REQUEST_FIXED + CLIENT_CAPS.size)
            clientNonce.copyInto(payload, 0)
            payload[VERSION_OFFSET] = 0
            payload[VERSION_OFFSET + 1] = 1
            payload[CAPS_LEN_OFFSET] = (CLIENT_CAPS.size ushr BITS_PER_BYTE).toByte()
            payload[CAPS_LEN_OFFSET + 1] = CLIENT_CAPS.size.toByte()
            CLIENT_CAPS.copyInto(payload, REQUEST_FIXED)
            return PairingFrames.encode(PairingFrameType.REQUEST, payload)
        }

        fun offerOf(frame: ByteArray): Offer {
            val payload = frame.copyOfRange(HEADER, frame.size)
            var offset = 0
            val pairingId = payload.copyOfRange(offset, offset + PAIRING_ID_BYTES)
            offset += PAIRING_ID_BYTES
            val nonce = payload.copyOfRange(offset, offset + NONCE_BYTES)
            offset += NONCE_BYTES + U16_BYTES
            val capsLength = u16(payload, offset)
            offset += U16_BYTES
            val caps = payload.copyOfRange(offset, offset + capsLength)
            offset += capsLength
            return Offer(pairingId, nonce, caps, payload.copyOfRange(offset, payload.size))
        }

        fun contextOf(
            pairingId: ByteArray,
            phoneNonce: ByteArray,
            clientNonce: ByteArray,
            phoneCaps: ByteArray,
            clientCaps: ByteArray,
        ): ByteArray {
            val ctx = ByteArray(CONTEXT_FIXED + phoneCaps.size + clientCaps.size)
            var offset = 0
            CONTEXT_PREFIX.copyInto(ctx, offset)
            offset += CONTEXT_PREFIX.size
            offset = putU16(ctx, offset, 1)
            offset = putU16(ctx, offset, 1)
            offset = putU16(ctx, offset, 1)
            pairingId.copyInto(ctx, offset)
            offset += PAIRING_ID_BYTES
            ctx[offset++] = 0x01
            ctx[offset++] = 0x02
            phoneNonce.copyInto(ctx, offset)
            offset += NONCE_BYTES
            clientNonce.copyInto(ctx, offset)
            offset += NONCE_BYTES
            offset = putU16(ctx, offset, phoneCaps.size)
            phoneCaps.copyInto(ctx, offset)
            offset += phoneCaps.size
            offset = putU16(ctx, offset, clientCaps.size)
            clientCaps.copyInto(ctx, offset)
            return ctx
        }

        fun putU16(target: ByteArray, offset: Int, value: Int): Int {
            target[offset] = (value ushr BITS_PER_BYTE).toByte()
            target[offset + 1] = value.toByte()
            return offset + U16_BYTES
        }

        fun u16(source: ByteArray, offset: Int): Int {
            val high = (source[offset].toInt() and U8_MASK) shl BITS_PER_BYTE
            return high or (source[offset + 1].toInt() and U8_MASK)
        }

        fun hmac(key: ByteArray, data: ByteArray): ByteArray = Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(key, "HmacSHA256"))
            doFinal(data)
        }
    }
}
