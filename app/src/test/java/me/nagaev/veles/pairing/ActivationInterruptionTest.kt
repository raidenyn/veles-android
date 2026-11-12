package me.nagaev.veles.pairing

import kotlinx.coroutines.runBlocking
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.PeerLookup
import me.nagaev.veles.session.PeerRecord
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.RecordCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.RecoveryKeys
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SessionEffect
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.session.assertPreAuthReject
import me.nagaev.veles.session.completeHandshake
import me.nagaev.veles.session.composeApplicationHandlers
import me.nagaev.veles.session.openPhoneRecord
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest
import java.security.interfaces.ECPublicKey
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * OTP-10 plan 9e: expiry, interruption and multi-peer behavior of the activation exchange.
 *
 * Every case runs the real [TrustRepository], the real [ActivationEngine] over a
 * [ProofRegistry]-backed [ActivationVerifier], the real [PhoneSessionEngine] and the real
 * [PhonePairingMachine] (over a deterministic [PairingCore]). Time is injected everywhere, so
 * the phone's 86 400 s boot-scoped pending lifetime is exercised without sleeping.
 *
 * The interruption contract is exactly: after any activation message is lost, the record is
 * resumable by a fresh signed recovery session, or it expires with no partial access, or it
 * never existed (a verified `cB` interrupted before the peer identity was persisted, whose only
 * path is a new pairing window). No interruption may authorize OTP data.
 */
class ActivationInterruptionTest {
    private val clock = FakeClock(0L)
    private val phoneIdentity = TestIdentity.generate()

    @Test
    fun phone_expiry_86400_fake_clock_deletes_record() = runBlocking {
        val store = Store()
        val repository = store.repository(BOOT_A)
        val client = TestClient()
        val peerId = seed(store, repository, client, TrustState.PENDING)

        clock.now = PENDING_LIFETIME_MILLIS - 1
        assertEquals(
            "one millisecond before the deadline the record survives",
            TrustState.PENDING,
            peer(repository, peerId).state,
        )

        clock.now = PENDING_LIFETIME_MILLIS
        assertTrue("the 86 400 s deadline deletes the incomplete record", repository.peers().isEmpty())

        val activeClient = TestClient()
        val activeId = seed(store, repository, activeClient, TrustState.ACTIVE)
        clock.now = PENDING_LIFETIME_MILLIS * 3
        assertEquals("an active record has no pending lifetime", TrustState.ACTIVE, peer(repository, activeId).state)
    }

    @Test
    fun boot_continuity_loss_deletes_incomplete_only() = runBlocking {
        val store = Store()
        val first = store.repository(BOOT_A)
        val pending = TestClient(installationId = ByteArray(16) { 0x01 })
        val committed = TestClient(installationId = ByteArray(16) { 0x02 })
        val active = TestClient(installationId = ByteArray(16) { 0x03 })
        seed(store, first, pending, TrustState.PENDING)
        seed(store, first, committed, TrustState.COMMITTED_WAITING_ACK)
        val activeId = seed(store, first, active, TrustState.ACTIVE)

        val restarted = store.repository(BOOT_B)

        assertEquals(
            "boot-continuity loss deletes only incomplete records",
            listOf(activeId),
            restarted.peers().map { it.installationId },
        )
        assertEquals(TrustState.ACTIVE, restarted.peers().single().state)
    }

    @Test
    fun interruption_at_every_activation_message() = runBlocking {
        cutAtEveryMessage().forEach { (cut, mirror) ->
            val store = Store()
            val repository = store.repository(BOOT_A)
            val client = TestClient()
            val peerId = seed(store, repository, client, TrustState.PENDING)

            val run = Exchange(store, repository, client, peerId, mirror)
            run.open()
            val state = run.forward(cut)
            assertTrue("cut $cut leaves a valid incomplete or active state", state in INCOMPLETE_OR_ACTIVE)

            val resumed = Exchange(store, repository, client, peerId, run.mirror)
            resumed.open()
            val ended = resumed.resume()
            assertEquals("cut $cut resumes to an active phone record", TrustState.ACTIVE, ended)
            if (run.mirror == Mirror.ACTIVE) {
                assertEquals("cut $cut completed activation", SessionClass.FULL, resumed.sessionClass)
                assertTrue("a completed activation authorizes OTP data", resumed.otpAuthorized())
            } else {
                assertFalse("cut $cut never authorizes OTP data", resumed.otpAuthorized())
            }
            assertTrue("cut $cut leaves a consistent mirror state", run.mirror in MIRROR_STATES)
        }
    }

    @Test
    fun one_sided_pending() = runBlocking {
        // The phone committed but the client never saw complete: a fresh recovery session
        // re-commits from the prepare and the phone's duplicate commit re-sends complete.
        resumeOneSided(TrustState.COMMITTED_WAITING_ACK, Mirror.PENDING) { exchange ->
            assertEquals(
                "the pending client re-commits after the prepare",
                RecordType.ACTIVATION_COMMIT.wire,
                exchange.commitType,
            )
            assertFalse("no OTP data on a one-sided pending", exchange.otpAuthorized())
        }
        // The client is already active-family and only lost the ack: it owes step 5.
        resumeOneSided(TrustState.COMMITTED_WAITING_ACK, Mirror.ACTIVE_PENDING) { exchange ->
            assertEquals(
                "the active-family client sends step 5",
                RecordType.ACTIVATION_ACK.wire,
                exchange.ackType,
            )
            assertFalse("no OTP data on a one-sided pending", exchange.otpAuthorized())
        }
        resumeOneSided(TrustState.ACTIVE, Mirror.ACTIVE_PENDING) { exchange ->
            assertEquals("an active phone keeps its state", TrustState.ACTIVE, exchange.download())
            assertFalse("no OTP data on a one-sided pending", exchange.otpAuthorized())
        }

        // The opposite one-sided case: a phone pending record whose client has no record at
        // all. Nothing may recover it; the only outcomes are expiry and a new pairing window.
        val store = Store()
        val repository = store.repository(BOOT_A)
        val absentClient = TestClient(installationId = ByteArray(16) { 0x7a })
        seed(store, repository, TestClient(), TrustState.PENDING)
        val effects = engine(repository, store.proofs).onInbound(absentClient.helloFrame)
        assertPreAuthReject(effects)
        clock.now = PENDING_LIFETIME_MILLIS
        assertTrue("the stranded phone pending record expires", repository.peers().isEmpty())
    }

    @Test
    fun no_record_after_cB_disconnect() = runBlocking {
        val store = Store()
        val repository = store.repository(BOOT_A)
        val core = FixedCore()
        val window = PairingWindow({ clock.now })
        val admission = PairingAdmission(PairingLimits(), { clock.now })
        val machine = machine(1L, window, admission, repository, core, store.proofs)
        val pairing = PairingClient(core)
        window.open()
        pairing.offer(machine)
        pairing.point(machine)
        pairing.confirm(machine)
        assertEquals(
            "a verified cB enters the protected enrollment state",
            PairingState.ENROLLMENT_PROTECTED,
            machine.state,
        )

        machine.onDisconnect()

        assertTrue(
            "an interruption after cB before the client identity persists nothing",
            repository.peers().isEmpty(),
        )
        assertEquals("the machine returns to idle", PairingState.IDLE, machine.state)
        assertFalse("the one-use code is consumed", window.isOpen())

        val client = TestClient()
        val effects = engine(repository, store.proofs).onInbound(client.helloFrame)
        assertPreAuthReject(effects)

        window.open()
        val reopened = PairingClient(core, ByteArray(16) { 0x22 }).offer(
            machine(2L, window, admission, repository, core, store.proofs),
        )
        assertNotEquals("only a new window on a fresh connection can continue", 0, reopened.pairingId.size)
    }

    @Test
    fun incomplete_records_authorize_no_otp() = runBlocking {
        listOf(TrustState.PENDING, TrustState.COMMITTED_WAITING_ACK).forEach { state ->
            val store = Store()
            val repository = store.repository(BOOT_A)
            val client = TestClient()
            val peerId = seed(store, repository, client, state)
            val engine = engine(repository, store.proofs)
            val session = completeSession(engine, client, PEER_STATE_BELOW_ACTIVE)
            assertEquals("$state fixes a recovery class", SessionClass.ACTIVATION_RECOVERY, session.sessionClass)
            sendUnprompted(engine, session)

            val effects = engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, 1uL, ByteArray(0)))

            assertTrue("$state terminates before any OTP delivery", effects.any { it is SessionEffect.Terminated })
            assertFalse("$state delivers no OTP data", effects.any { it is SessionEffect.Deliver })
            assertEquals("$state leaves the record unchanged", state, peer(repository, peerId).state)
        }
    }

    @Test
    fun two_desktops_sequential() = runBlocking {
        val store = Store()
        val repository = store.repository(BOOT_A)
        val core = FixedCore()
        val window = PairingWindow({ clock.now })
        val admission = PairingAdmission(PairingLimits(), { clock.now })

        val desktops = listOf(ByteArray(16) { 0x31 }, ByteArray(16) { 0x42 }).mapIndexed { index, id ->
            clock.now = 1_000L * (index + 1)
            window.open()
            val machine = machine(index + 1L, window, admission, repository, core, store.proofs)
            val client = PairingClient(core, id, ByteArray(NONCE_BYTES) { (0x60 + index).toByte() })
            client.offer(machine)
            client.point(machine)
            client.confirm(machine)
            client.enroll(machine)
            assertEquals(
                "desktop ${index + 1} activates on the enrollment connection",
                PairingState.ACTIVE,
                machine.state,
            )
            machine.onDisconnect()
            client
        }

        val records = repository.peers()
        assertEquals("both desktops are separate active records", 2, records.size)
        assertTrue("every paired desktop is active", records.all { it.state == TrustState.ACTIVE })

        desktops.forEachIndexed { index, desktop ->
            val engine = engine(repository, store.proofs)
            val client = TestClient(installationId = desktop.installationId, identity = desktop.identity)
            val session = completeSession(engine, client, PEER_STATE_ACTIVE)
            assertEquals("desktop ${index + 1} reconnects as a full session", SessionClass.FULL, session.sessionClass)
            assertEquals(
                "desktop ${index + 1} reconnects without a code",
                TrustState.ACTIVE,
                repository.peers().first { it.installationId == TrustEncoding.hex(desktop.installationId) }.state,
            )
            assertTrue("desktop ${index + 1} may pull OTP data", otpAuthorized(engine, client, session, 1uL))
        }
    }

    // ---------------------------------------------------------------------------------------
    // Activation exchange driver
    // ---------------------------------------------------------------------------------------

    private enum class Mirror { PENDING, ACTIVE_PENDING, ACTIVE }

    private enum class Cut { BEFORE_PREPARE, PREPARE_LOST, COMMIT_LOST, COMPLETE_LOST, ACK_LOST, ACK_DELIVERED }

    private fun cutAtEveryMessage(): List<Pair<Cut, Mirror>> = listOf(
        Cut.BEFORE_PREPARE to Mirror.PENDING,
        Cut.PREPARE_LOST to Mirror.PENDING,
        Cut.COMMIT_LOST to Mirror.PENDING,
        Cut.COMPLETE_LOST to Mirror.PENDING,
        Cut.ACK_LOST to Mirror.ACTIVE_PENDING,
        Cut.ACK_DELIVERED to Mirror.PENDING,
    )

    private inner class Exchange(
        private val store: Store,
        private val repository: TrustRepository,
        private val client: TestClient,
        private val peerId: String,
        var mirror: Mirror,
    ) {
        private lateinit var engine: PhoneSessionEngine
        private lateinit var session: SessionEffect.Authenticated
        var sessionClass: SessionClass? = null
            private set
        var commitType: Int? = null
            private set
        var ackType: Int? = null
            private set

        private val recovery: RecoveryKeys get() = client.keys.recovery ?: error("no recovery keys")

        suspend fun open() {
            engine = this@ActivationInterruptionTest.engine(repository, store.proofs)
            val peerState = if (mirror == Mirror.ACTIVE) PEER_STATE_ACTIVE else PEER_STATE_BELOW_ACTIVE
            session = completeSession(engine, client, peerState)
            sessionClass = session.sessionClass
        }

        /** Applies the handshake up to [cut] and returns the durable phone state. */
        @Suppress("ReturnCount") // One early return per interrupted activation message.
        suspend fun forward(cut: Cut): TrustState {
            if (cut == Cut.BEFORE_PREPARE) return download()
            val prepare = sendUnprompted(engine, session).single()
            if (cut == Cut.PREPARE_LOST) return download()
            commitType = RecordType.ACTIVATION_COMMIT.wire
            val commit = client.record(
                RecordType.ACTIVATION_COMMIT.wire,
                1uL,
                mac(recovery.kRecClient, STAGE_COMMIT),
            )
            if (cut == Cut.COMMIT_LOST) return download()
            client.openPhoneRecord(prepare.bytes, 1uL)
            val complete = engine.onInbound(commit).filterIsInstance<SessionEffect.Send>().single()
            client.openPhoneRecord(complete.bytes, 2uL)
            if (cut == Cut.COMPLETE_LOST) return download()
            mirror = Mirror.ACTIVE_PENDING
            ackType = RecordType.ACTIVATION_ACK.wire
            val ack = client.record(
                RecordType.ACTIVATION_ACK.wire,
                2uL,
                mac(recovery.kRecClient, STAGE_ACK),
            )
            if (cut == Cut.ACK_LOST) return download()
            engine.onInbound(ack)
            mirror = Mirror.ACTIVE
            return download()
        }

        /** Resumes the exchange on a fresh signed session until the phone record is active. */
        suspend fun resume(): TrustState {
            if (download() == TrustState.ACTIVE && mirror == Mirror.ACTIVE) return TrustState.ACTIVE
            val prepare = sendUnprompted(engine, session).singleOrNull()
            var sequence = 1uL
            if (mirror == Mirror.PENDING) {
                checkNotNull(prepare) { "a pending client must receive the recovery prepare" }
                client.openPhoneRecord(prepare.bytes, 1uL)
                commitType = RecordType.ACTIVATION_COMMIT.wire
                val commit = client.record(
                    RecordType.ACTIVATION_COMMIT.wire,
                    1uL,
                    mac(recovery.kRecClient, STAGE_COMMIT),
                )
                val complete = engine.onInbound(commit).filterIsInstance<SessionEffect.Send>().single()
                client.openPhoneRecord(complete.bytes, 2uL)
                mirror = Mirror.ACTIVE_PENDING
                sequence = 2uL
            }
            if (mirror == Mirror.ACTIVE_PENDING) {
                ackType = RecordType.ACTIVATION_ACK.wire
                val ack = client.record(
                    RecordType.ACTIVATION_ACK.wire,
                    sequence,
                    mac(recovery.kRecClient, STAGE_ACK),
                )
                engine.onInbound(ack)
                mirror = Mirror.ACTIVE
            }
            return download()
        }

        fun download(): TrustState = runBlocking { peer(repository, peerId).state }

        suspend fun otpAuthorized(): Boolean = otpAuthorized(engine, client, session, 1uL)
    }

    private suspend fun resumeOneSided(
        phoneState: TrustState,
        mirror: Mirror,
        assertResumed: suspend (Exchange) -> Unit,
    ) {
        val store = Store()
        val repository = store.repository(BOOT_A)
        val client = TestClient()
        val peerId = seed(store, repository, client, phoneState)
        val exchange = Exchange(store, repository, client, peerId, mirror)
        exchange.open()
        exchange.resume()
        assertEquals("the phone record ends active", TrustState.ACTIVE, exchange.download())
        assertResumed(exchange)
    }

    // ---------------------------------------------------------------------------------------
    // Shared harness
    // ---------------------------------------------------------------------------------------

    private suspend fun completeSession(
        engine: PhoneSessionEngine,
        client: TestClient,
        peerState: Int,
    ): SessionEffect.Authenticated {
        val effects = engine.completeHandshake(client, peerState, ET)
        return effects.filterIsInstance<SessionEffect.Authenticated>().single()
    }

    private suspend fun sendUnprompted(
        engine: PhoneSessionEngine,
        session: SessionEffect.Authenticated,
    ): List<SessionEffect.Send> = session.unprompted.map { owed ->
        engine.sendApplication(owed.type, owed.body).filterIsInstance<SessionEffect.Send>().single()
    }

    private suspend fun otpAuthorized(
        engine: PhoneSessionEngine,
        client: TestClient,
        session: SessionEffect.Authenticated,
        sequence: ULong,
    ): Boolean {
        if (session.sessionClass != SessionClass.FULL) return false
        val effects = engine.onInbound(client.record(RecordType.PULL_REQUEST.wire, sequence, ByteArray(0)))
        return effects.any { it is SessionEffect.Deliver && it.type == RecordType.PULL_REQUEST.wire }
    }

    private fun engine(repository: InstallationTrustRepository, proofs: ProofRegistry): PhoneSessionEngine = PhoneSessionEngine(
        localInstallationId = TestPeers.phoneInstallationId,
        signer = phoneIdentity.signer,
        lookup = lookup(repository),
        clock = { clock.now },
        ephemeral = { TestPeers.phoneEphemeral },
        nonce = { TestPeers.phoneNonce },
        handler = composeApplicationHandlers(ActivationEngine(repository, proofs), PullHandler()),
    )

    private fun lookup(repository: InstallationTrustRepository): PeerLookup = PeerLookup { id ->
        runBlocking { repository.peers() }.firstOrNull {
            TrustEncoding.bytes(it.installationId).contentEquals(id)
        }?.let {
            PeerRecord(
                installationId = TrustEncoding.bytes(it.installationId),
                identityPublicKey = TrustEncoding.bytes(it.publicKey),
                state = when (it.state) {
                    TrustState.ACTIVE -> LocalRecordState.ACTIVE
                    TrustState.PENDING -> LocalRecordState.PENDING
                    TrustState.COMMITTED_WAITING_ACK -> LocalRecordState.COMMITTED_WAITING_ACK
                },
                et = TrustEncoding.bytes(it.enrollmentTranscript),
            )
        }
    }

    private fun machine(
        conn: Long,
        window: PairingWindow,
        admission: PairingAdmission,
        repository: InstallationTrustRepository,
        core: PairingCore,
        proofs: ProofRegistry,
    ): PhonePairingMachine = PhonePairingMachine(
        window,
        admission,
        conn,
        repository,
        { core },
        { clock.now },
        PairingLimits(),
        { ByteArray(NONCE_BYTES) { 0x51 } },
        { "Phone" },
        { ByteArray(NONCE_BYTES) { 0x2a } },
        proofs = proofs,
    )

    @Suppress("MaxLineLength") // ktlint keeps this expression body on one line.
    private suspend fun peer(repository: InstallationTrustRepository, peerId: String): TrustedPeer = repository.peers().single { it.installationId == peerId }

    private suspend fun seed(
        store: Store,
        repository: TrustRepository,
        client: TestClient,
        state: TrustState,
    ): String {
        val peerId = TrustEncoding.hex(client.installationId)
        repository.addPending(
            PendingPeer(
                installationId = peerId,
                publicKey = TrustEncoding.hex(client.identity.publicKey),
                label = "Desktop",
                deviceType = 1,
                enrollmentTranscript = TrustEncoding.hex(ET),
            ),
        )
        if (state != TrustState.PENDING) {
            store.proofs.register(SEED_SESSION, listOf(ActivationProof(peerId, ActivationStep.COMMIT, SEED_COMMIT)))
            repository.confirm(peerId, ActivationStep.COMMIT, SEED_COMMIT)
            if (state == TrustState.ACTIVE) {
                store.proofs.register(SEED_SESSION, listOf(ActivationProof(peerId, ActivationStep.ACK, SEED_ACK)))
                repository.confirm(peerId, ActivationStep.ACK, SEED_ACK)
            }
        }
        return peerId
    }

    private fun mac(key: ByteArray, stage: Byte): ByteArray = try {
        Mac.getInstance(HMAC).run {
            init(SecretKeySpec(key, HMAC))
            doFinal(ET + byteArrayOf(stage))
        }
    } catch (failure: Exception) {
        throw IllegalStateException("test MAC failed", failure)
    }

    /** One trust store and its stable identity, shared across every repository restart. */
    private inner class Store {
        private val storage = InterruptionTrustStorage()
        val proofs = ProofRegistry()
        private val publicKey = TrustEncoding.publicKey(P256.generate().public)
        private val keys = object : IdentityKeyStore {
            private var generated = false
            override fun publicKey(): String? = if (generated) publicKey else null
            override fun generate(): String {
                generated = true
                return publicKey
            }

            override fun sign(message: ByteArray): ByteArray = ByteArray(0)
        }

        fun repository(boot: String): TrustRepository = TrustRepository(
            storage,
            keys,
            { clock.now },
            { boot },
            { clock.now },
            ActivationVerifier { peer, step, proof -> proofs.consume(peer, step, proof) },
        )
    }

    // ---------------------------------------------------------------------------------------
    // Deterministic pairing core and one desktop's scripted client
    // ---------------------------------------------------------------------------------------

    private class FixedCore : PairingCore {
        private val open = LinkedHashSet<Long>()
        private var next = 0L
        private val point = TestPeers.uncompressed(P256.generate().public as ECPublicKey)

        @Suppress("MaxLineLength") // ktlint keeps this expression body on one line.
        override fun start(role: Role, code: ByteArray, context: ByteArray): Started = Started(Handle(++next), point.copyOf()).also { open += it.handle.bits }

        override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray = CA.copyOf()

        override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray = ByteArray(0)

        override fun finish(handle: Handle): ByteArray {
            open -= handle.bits
            return KE.copyOf()
        }

        override fun close(handle: Handle) {
            open -= handle.bits
        }

        override fun openHandles(): Int = open.size

        companion object {
            val KE = ByteArray(16) { 0x11 }
            val CA = ByteArray(32) { 0x0a }
        }
    }

    private inner class PairingClient(
        private val core: PairingCore,
        val installationId: ByteArray = ByteArray(16) { 0x21 },
        private val clientNonce: ByteArray = ByteArray(NONCE_BYTES) { 0x22 },
    ) {
        val identity: TestIdentity = TestIdentity.generate()
        private val clientPoint = TestPeers.uncompressed(P256.generate().public as ECPublicKey)
        private val confirmationB = ByteArray(CONFIRMATION_BYTES) { 0x0b }
        private lateinit var context: ByteArray
        private lateinit var phonePoint: ByteArray
        private lateinit var keys: EnrollmentKeys
        private lateinit var prefix: ByteArray
        private lateinit var phoneIdentityBody: ByteArray
        private lateinit var et: ByteArray

        suspend fun offer(machine: PhonePairingMachine): Offer {
            val caps = byteArrayOf(0x01)
            val payload = ByteArray(REQUEST_FIXED + caps.size)
            clientNonce.copyInto(payload)
            putU16(payload, VERSION_OFFSET, 1)
            putU16(payload, CAPS_LEN_OFFSET, caps.size)
            caps.copyInto(payload, REQUEST_FIXED)
            val outgoing = machine.onFrame(PairingFrames.encode(PairingFrameType.REQUEST, payload))
            val frame = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes
            val offer = decodeOffer(frame)
            phonePoint = offer.point
            context = publicContext(offer)
            return offer
        }

        suspend fun point(machine: PhonePairingMachine) {
            val outgoing = machine.onFrame(PairingFrames.encode(PairingFrameType.PB, clientPoint))
            val ca = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes
                .copyOfRange(HEADER, HEADER + CONFIRMATION_BYTES)
            assertEquals("the point answer is cA", FixedCore.CA.size, ca.size)
        }

        suspend fun confirm(machine: PhonePairingMachine) {
            keys = EnrollmentSchedule.derive(FixedCore.KE, context)
            prefix = EnrollmentSchedule.sessionIdPrefix(context)
            val outgoing = machine.onFrame(PairingFrames.encode(PairingFrameType.CB, confirmationB))
            val identityFrame = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes
            phoneIdentityBody = EnrollmentRecords.encode(
                EnrollmentRecords.decode(openPhone(identityFrame, 0uL), IdentityRole.PHONE),
                IdentityRole.PHONE,
            )
        }

        /** Sends the client identity, then commit/complete/ack until the phone is active. */
        suspend fun enroll(machine: PhonePairingMachine) {
            val canonical = EnrollmentRecords.encode(
                EnrollmentRecords.create(
                    installationId,
                    "Desktop",
                    DeviceType.DESKTOP,
                    identity.publicKey,
                    ByteArray(NONCE_BYTES) { 0x33 },
                ),
                IdentityRole.CLIENT,
            )
            et = EnrollmentRecords.transcript(
                context,
                phonePoint,
                clientPoint,
                FixedCore.CA,
                confirmationB,
                phoneIdentityBody,
                canonical,
            )
            val identityOutgoing = machine.onFrame(
                RecordCodec.seal(
                    keys.bToAAesKey,
                    keys.bToANoncePrefix,
                    prefix,
                    Direction.CLIENT_TO_PHONE,
                    RecordType.ENROLL_IDENTITY.wire,
                    0uL,
                    canonical,
                ),
            )
            openPhone(identityOutgoing.filterIsInstance<Outgoing.Frame>().single().bytes, 1uL)
            val commitOutgoing = machine.onFrame(activationRecord(RecordType.ACTIVATION_COMMIT, 1uL, STAGE_COMMIT))
            val complete = openPhone(commitOutgoing.filterIsInstance<Outgoing.Frame>().single().bytes, 2uL)
            assertTrue(
                "the phone activation_complete binds ET under its activation key",
                MessageDigest.isEqual(activationMac(keys.phoneActivationKey, STAGE_COMPLETE), complete),
            )
            machine.onFrame(activationRecord(RecordType.ACTIVATION_ACK, 2uL, STAGE_ACK))
        }

        private fun activationRecord(type: RecordType, sequence: ULong, stage: Byte): ByteArray = RecordCodec.seal(
            keys.bToAAesKey,
            keys.bToANoncePrefix,
            prefix,
            Direction.CLIENT_TO_PHONE,
            type.wire,
            sequence,
            activationMac(keys.clientActivationKey, stage),
        )

        private fun activationMac(key: ByteArray, stage: Byte): ByteArray = try {
            Mac.getInstance(HMAC).run {
                init(SecretKeySpec(key, HMAC))
                doFinal(et + byteArrayOf(stage))
            }
        } catch (failure: Exception) {
            throw IllegalStateException("test MAC failed", failure)
        }

        private fun openPhone(record: ByteArray, sequence: ULong): ByteArray = RecordCodec.open(
            keys.aToBAesKey,
            keys.aToBNoncePrefix,
            prefix,
            Direction.PHONE_TO_CLIENT,
            sequence,
            record,
        ).body

        private fun decodeOffer(frame: ByteArray): Offer {
            val payload = frame.copyOfRange(HEADER, frame.size)
            var offset = 0
            val pairingId = payload.copyOfRange(offset, offset + PAIRING_ID_BYTES)
            offset += PAIRING_ID_BYTES
            val phoneNonce = payload.copyOfRange(offset, offset + NONCE_BYTES)
            offset += NONCE_BYTES + U16_BYTES
            val capsLength = u16(payload, offset)
            offset += U16_BYTES
            val caps = payload.copyOfRange(offset, offset + capsLength)
            offset += capsLength
            return Offer(pairingId, phoneNonce, caps, payload.copyOfRange(offset, offset + POINT_BYTES))
        }

        private fun publicContext(offer: Offer): ByteArray {
            val phoneCaps = PHONE_CAPS
            val clientCaps = byteArrayOf(0x01)
            val ctx = ByteArray(CONTEXT_FIXED + phoneCaps.size + clientCaps.size)
            var offset = 0
            CONTEXT_PREFIX.copyInto(ctx, offset)
            offset += CONTEXT_PREFIX.size
            offset = putU16(ctx, offset, 1)
            offset = putU16(ctx, offset, 1)
            offset = putU16(ctx, offset, 1)
            offer.pairingId.copyInto(ctx, offset)
            offset += PAIRING_ID_BYTES
            ctx[offset++] = 0x01
            ctx[offset++] = 0x02
            offer.phoneNonce.copyInto(ctx, offset)
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
    }

    private data class Offer(
        val pairingId: ByteArray,
        val phoneNonce: ByteArray,
        val caps: ByteArray,
        val point: ByteArray,
    )

    private companion object {
        const val NONCE_BYTES = 16
        const val HEADER = 3
        const val CONFIRMATION_BYTES = 32
        const val REQUEST_FIXED = NONCE_BYTES + 4
        const val VERSION_OFFSET = NONCE_BYTES
        const val CAPS_LEN_OFFSET = NONCE_BYTES + 2
        const val PAIRING_ID_BYTES = 16
        const val POINT_BYTES = 65
        const val U16_BYTES = 2
        const val CONTEXT_FIXED = 30 + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES + NONCE_BYTES + 4
        const val HMAC = "HmacSHA256"
        const val BOOT_A = "boot-a"
        const val BOOT_B = "boot-b"
        const val PEER_STATE_ACTIVE = 0x01
        const val PEER_STATE_BELOW_ACTIVE = 0x02
        const val PENDING_LIFETIME_MILLIS = 86_400_000L
        const val STAGE_COMMIT: Byte = 0x01
        const val STAGE_COMPLETE: Byte = 0x02
        const val STAGE_ACK: Byte = 0x03
        const val SEED_SESSION = "seed"
        val SEED_COMMIT = byteArrayOf(0x01)
        val SEED_ACK = byteArrayOf(0x02)
        val ET = ByteArray(32) { 0x11 }
        val PHONE_CAPS = byteArrayOf(0x01, 0x02)
        val CONTEXT_PREFIX = "Veles SPAKE2 public context v1".toByteArray(Charsets.US_ASCII)
        val INCOMPLETE_OR_ACTIVE = setOf(TrustState.PENDING, TrustState.COMMITTED_WAITING_ACK, TrustState.ACTIVE)
        val MIRROR_STATES = setOf(Mirror.PENDING, Mirror.ACTIVE_PENDING, Mirror.ACTIVE)

        fun putU16(target: ByteArray, offset: Int, value: Int): Int {
            target[offset] = (value ushr 8).toByte()
            target[offset + 1] = value.toByte()
            return offset + U16_BYTES
        }

        fun u16(bytes: ByteArray, offset: Int): Int = ((bytes[offset].toInt() and 0xFF) shl 8) or (bytes[offset + 1].toInt() and 0xFF)
    }
}

/** Accepts the authorized OTP pull only, so `otpAuthorized` observes a real delivery. */
private class PullHandler : ApplicationHandler {
    override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (Int, ByteArray) -> Unit,
    ) = Unit
}

private class InterruptionTrustStorage : TrustStorage {
    private var value: TrustSnapshot? = null

    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        val (next, result) = block(value)
        value = next
        return result
    }
}
