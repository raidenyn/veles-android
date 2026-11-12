package me.nagaev.veles.pairing

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.RecordCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest
import java.security.interfaces.ECPublicKey
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * OTP-10 phone trace runner: every `enrollment.json` scenario the OTP-10 inventory
 * executes on the phone is replayed against the real host core and the real trust store.
 *
 * The phone side is the production [PhonePairingMachine] over a real [PairingWindow],
 * [PairingAdmission] and [TrustRepository]; the client side is a trace peer built from the
 * same `PairingCore` interface (the `protocol/checks/state.mjs` interpreter's role, in
 * Kotlin). The public context is built here independently of the machine from §3.3, so a
 * divergence in the machine's canonical context makes the real core's confirmation fail.
 *
 * `simulate` flags in the traces (`pB_point_valid`, `cB_mac_ok`, `aead_ok`,
 * `fingerprint_ok`, `device_type_ok`, `duplicate_installation`, `persist_ok`) are simulated
 * OTP observations (OTP2-FIX-004), never cryptographic evidence: an invalid point is an
 * all-zero SEC1 body, a bad `cB` is one flipped MAC byte, a bad `aead_ok` is one flipped
 * ciphertext byte. The positive `cA`/`cB` and both `enroll_identity` records are real,
 * produced by both roles of the core and by the real record codec.
 *
 * The phone's own `enroll_identity` and the `enrollment_prepare` MAC are decrypted and
 * checked with the client's independently derived §5.1 schedule, so `derive_enrollment_keys`
 * and `send_enroll_identity` are observed as real effects, never as a stand-in.
 */
class EnrollmentScenarioTest : PairingTestBase() {
    private val real = VelesPairingCore()
    private val recording = RecordingCore(real)
    override val core: PairingCore = recording

    @Test
    fun every_executed_phone_trace_passes_on_the_real_core() = runBlocking {
        val executed = inventory.filterValues { it == "execute" }.keys
        val phone = fixtures
            .filter { it.role == "phone" && it.id in executed && it.id !in SESSION_TRACE_IDS }
            .map { it.id }
        assertEquals("the executed phone traces are exactly the phone enrollment set", EXECUTED_PHONE_TRACES, phone.toSet())
        phone.forEach { runTrace(it) }
    }

    @Test
    fun engine_effect_trace_terminates_erases_and_mutates_nothing() = runBlocking {
        val entry = inventory["st-neg-same-connection-pb-after-invalid-point"] as? JsonObject
        assertNotNull("the negative must stay an engine-effect entry", entry)
        val effect = checkNotNull(entry).getValue("engine-effect").jsonObject
        assertTrue("terminate", effect.bool("terminate"))
        assertTrue("erase", effect.bool("erase"))
        assertFalse("deliver", effect.bool("deliver"))

        val fixture = fixtures.first { it.id == "st-neg-same-connection-pb-after-invalid-point" }
        val run = runTrace(fixture.id, assertTrace = false)

        assertFalse("a second pB on the dead connection is an invalid trace", run.accepted)
        assertEquals("the observed stage must equal the fixture's", fixture.stage, run.stage)
        assertTrue("the invalid point terminated the connection", run.terminated)
        assertEquals("the execution is erased", 0, run.openHandlesAtEnd)
        assertTrue("the second frame took the host's drop path", run.droppedSteps.isNotEmpty())
        assertEquals("the never-read frame calls no core operation", 0, run.violatingCoreCalls)
        assertEquals("the never-read frame opens no handle", 0, run.violatingHandles)
        assertTrue("the never-read frame puts nothing on the wire", run.violatingFrames.isEmpty())
        assertTrue("the rejected trace mutates no state", run.noStateMutation)
    }

    private suspend fun runTrace(id: String, assertTrace: Boolean = true): TraceRun {
        val fixture = fixtures.first { it.id == id }
        val run = TraceRun(fixture)
        try {
            run.execute()
        } finally {
            run.teardown()
        }
        if (assertTrace) {
            assertEquals("$id accepted: ${run.reason}", true, run.accepted)
            val actualFinal = mapOf(
                "phone_pairing:main" to run.finalState,
                "durable_phone_record:main" to run.durableState(),
            ).filterKeys { it in fixture.finalState }
            assertEquals("$id final_state", fixture.finalState, actualFinal)
            run.stepStates.forEach { (index, expected) ->
                expected.forEach { (machine, state) ->
                    assertEquals("$id step $index $machine", state, run.stateAt[index]?.get(machine))
                }
            }
            fixture.require.forEach { assertTrue("$id requires $it", it in run.observations) }
            fixture.forbid.forEach { assertTrue("$id forbids $it", it !in run.observations) }
            assertOrder(id, fixture.order, run.sequence)
        }
        return run
    }

    /** The fixture `order` is a subsequence: a state observation may legitimately repeat. */
    private fun assertOrder(id: String, expected: List<String>, actual: List<String>) {
        var from = 0
        expected.forEach { name ->
            val offset = actual.subList(from, actual.size).indexOf(name)
            assertTrue("$id order name $name", offset >= 0)
            from += offset + 1
        }
    }

    private inner class TraceRun(private val fixture: TraceFixture) {
        var now = 0L
            private set
        private var code = ByteArray(0)
        private val window = PairingWindow({ now })
        private val admission = PairingAdmission(PairingLimits(), { now })
        private val live = LinkedHashMap<String, Live>()
        private val machines = mutableListOf<PhonePairingMachine>()
        private val connIds = HashMap<String, Long>()
        private val paGrants = HashMap<String, Int>()
        private val paGrantTimes = mutableListOf<Long>()
        private val charges = mutableListOf<Pair<Long, String>>()

        private val storage = TraceTrustStorage()
        private val phoneKeyStore = PhoneIdentityKeyStore(TrustEncoding.publicKey(PHONE_KEYS.public))
        private val repository = TrustRepository(
            storage,
            phoneKeyStore,
            { now },
            { BOOT },
            { now },
            ActivationVerifier { _, _, _ -> true },
        )
        private val trust = RunTrust(repository)
        private var phoneIdentityHex: String = ""
        private var seededPeer: TrustedPeer? = null
        private var duplicateId: ByteArray? = null
        private var recordDeleted = false
        private var recordEverPersisted = false
        private var persistAttempted = false

        val observations = LinkedHashSet<String>()
        val sequence = mutableListOf<String>()
        val stateAt = HashMap<Int, Map<String, String>>()
        val stepStates: Map<Int, Map<String, String>> = fixture.steps.mapIndexedNotNull { index, step ->
            step.expect.takeIf { it.isNotEmpty() }?.let { index to it }
        }.toMap()
        var terminated = false
            private set
        var openHandlesAtEnd = 0
            private set
        var finalState = "idle"
            private set
        var violatingFrames: List<String> = emptyList()
            private set
        var violatingCoreCalls = 0
            private set
        var violatingHandles = 0
            private set

        /** Steps whose frame took the host's "generation already gone" drop path. */
        val droppedSteps = mutableListOf<Int>()
        var noStateMutation = false
            private set

        /** Every frame this run actually handed to the wire, keyed by the step that emitted it. */
        private val wireLog = mutableListOf<Pair<Int, String>>()
        private var violation: Violation? = null
        private var stepFingerprint = ""
        private var stepCalls = 0
        private var stepHandles = 0
        private var nextNonce = 0

        val accepted: Boolean get() = violation == null
        val reason: String? get() = violation?.reason
        val stage: String? get() = violation?.stage?.wire

        suspend fun execute() {
            phoneIdentityHex = repository.identity().installationId
            seedPersistedState()
            for ((index, step) in fixture.steps.withIndex()) {
                if (violation != null) return
                now = step.tMs
                stepFingerprint = fingerprint()
                stepCalls = recording.totalCalls()
                stepHandles = recording.openHandles()
                when (step.event) {
                    "enable_pairing" -> {
                        window.open()
                        code = window.codeBytes()
                        observe("generate_pairing_id_and_code")
                    }
                    "window_expired" -> {
                        assertFalse("${fixture.id}: the window is past its deadline", window.isOpen())
                        observe("window_closed_code_erased")
                    }
                    "connection_lost" -> onConnectionLost(step.explicitConnection)
                    "pairing_request" -> onRequest(index, step.connection)
                    "pairing_client_point" -> onPoint(index, step.connection, step.simulate)
                    "pairing_confirm_b" -> onConfirmB(index, step.connection, step.simulate)
                    "pairing_confirm_a" -> onDirectionViolation(index, step.connection)
                    "enroll_identity" -> onEnrollIdentity(index, step)
                    "activation_commit" -> onActivationCommit(index, step)
                    "activation_ack" -> onActivationAck(index)
                    "protected_record" -> onUnauthorizedRecord(index, step)
                    "cancel_pairing" -> onCancel()
                    "inactivity_timeout" -> onInactivity()
                    else -> error("${fixture.id} has no mapping for ${step.event}")
                }
                stateAt[index] = mapOf(
                    "phone_pairing:main" to state(),
                    "durable_phone_record:main" to durableState(),
                )
            }
            openHandlesAtEnd = recording.openHandles()
            finalState = state()
        }

        fun teardown() {
            runBlocking { machines.forEach { it.onDisconnect() } }
            machines.clear()
            live.values.forEach { it.peer.close() }
            live.clear()
            code.fill(0)
        }

        /** The `durable_phone_record:main` state for the trace's persisted record. */
        fun durableState(): String {
            val peer = runBlocking { repository.peers() }.firstOrNull()
            if (peer != null) {
                return when (peer.state) {
                    TrustState.PENDING -> "pending"
                    TrustState.COMMITTED_WAITING_ACK -> "committed_waiting_ack"
                    TrustState.ACTIVE -> "active"
                }
            }
            return if (recordDeleted || recordEverPersisted || persistAttempted) "deleted" else "absent"
        }

        /** True once the window holds no code; a code read is the only public observation. */
        private fun codeErased(): Boolean = try {
            window.codeBytes()
            false
        } catch (_: IllegalStateException) {
            true
        }

        /** The phone pairing state for the trace's `phone_pairing:main` machine. */
        fun state(): String = when {
            machines.any { it.state == PairingState.ACTIVE } -> "active"
            machines.any { it.state == PairingState.COMMITTED_WAITING_ACK } -> "committed_waiting_ack"
            machines.any { it.state == PairingState.AWAITING_COMMIT } -> "awaiting_commit"
            machines.any { it.state == PairingState.ENROLLMENT_PROTECTED } -> "enrollment_protected"
            machines.any { it.state == PairingState.AWAITING_CB } -> "awaiting_cB"
            machines.any { it.state == PairingState.SPAKE_IN_FLIGHT } -> "spake_in_flight"
            window.isOpen() -> "window_open"
            else -> "idle"
        }

        private fun seedPersistedState() {
            val state = fixture.persistedState["durable_phone_record:main"]
            val installationId = fixture.persistedState["installation_id"]?.removePrefix("0x")
            if (installationId != null) duplicateId = TrustEncoding.bytes(installationId)
            when (state) {
                "active" -> {
                    val id = installationId ?: return
                    runBlocking {
                        repository.addPending(
                            PendingPeer(id, TrustEncoding.publicKey(CLIENT_PUBLIC.public), "Desktop", 1, ET_HEX),
                        )
                        repository.confirm(id, ActivationStep.COMMIT, byteArrayOf(1))
                        repository.confirm(id, ActivationStep.ACK, byteArrayOf(1))
                    }
                    seededPeer = runBlocking { repository.peers() }.single()
                }
                "deleted" -> recordDeleted = true
                null, "absent", "pending", "committed_waiting_ack" -> Unit
                else -> error("${fixture.id}: unsupported persisted state $state")
            }
        }

        private fun onConnectionLost(connection: String?) {
            // The fixture omits the connection on `connection_lost`: it ends the live link.
            val label = connection ?: live.keys.lastOrNull() ?: return
            val current = live.remove(label) ?: return
            val stateBefore = current.machine.state
            runBlocking { current.machine.onDisconnect() }
            current.peer.close()
            if (stateBefore == PairingState.ENROLLMENT_PROTECTED || stateBefore == PairingState.AWAITING_COMMIT) {
                observe("close_enrollment_connection")
                if (durableState() == "pending" || durableState() == "committed_waiting_ack") {
                    observe("connection_lost_pending_record_continues_via_recovery")
                } else {
                    observe("connection_lost_no_usable_peer_record_repair_only")
                }
            } else if (window.isOpen()) {
                observe("connection_lost_window_resumable")
            }
        }

        private suspend fun onRequest(index: Int, connection: String) {
            val openBefore = window.isOpen()
            val inFlightBefore = admission.operationsInFlightForTest()
            val grantsBefore = paGrants[connection] ?: 0
            val rollingGrantsBefore = paGrantTimes.count { it > now - ROLLING_PA_MILLIS }
            val machine = live[connection]?.machine ?: newConnection(connection)
            val peer = live.getValue(connection).peer

            val outgoing = machine.onFrame(peer.requestFrame())
            when (frameType(outgoing)) {
                OFFER -> {
                    observe("emit_pairing_offer")
                    paGrants[connection] = grantsBefore + 1
                    paGrantTimes += now
                    peer.acceptOffer(offerOf(outgoing), code)
                }
                PREAUTH_REJECT -> {
                    recordWire(index, outgoing)
                    observe("preauth_reject")
                    when {
                        !openBefore -> observe("reject_window_not_open")
                        inFlightBefore > 0 -> observe("reject_second_concurrent_request")
                        grantsBefore > 0 -> observe("reject_pa_per_connection")
                        rollingGrantsBefore >= FRESH_PA_ROLLING_MAX -> observe("reject_pa_rolling")
                        else -> recordViolation(
                            index,
                            ViolationStage.TRANSITION,
                            "unattributed request refusal open=$openBefore inFlight=$inFlightBefore " +
                                "grants=$grantsBefore rolling=$rollingGrantsBefore window=${window.isOpen()} " +
                                "id=${hex(window.pairingId)}",
                        )
                    }
                    closeIfDisconnected(connection, outgoing)
                }
                else -> error("${fixture.id} step $index: unexpected request answer")
            }
        }

        private suspend fun onPoint(index: Int, connection: String, simulate: Map<String, String>) {
            val valid = simulate["pB_point_valid"] != "false"
            val point = if (valid) live[connection]?.peer?.point() else INVALID_POINT
            val idBefore = window.pairingId
            val chargedBefore = admission.attemptsChargedForTest(idBefore)
            val stateBefore = live[connection]?.machine?.state
            val outgoing = deliverToMachine(index, connection, point?.let { PairingFrames.encode(PairingFrameType.PB, it) })
                ?: return
            val current = live.getValue(connection)

            when (frameType(outgoing)) {
                CONFIRM_A -> {
                    recordWire(index, outgoing)
                    observe("consume_attempt")
                    charges += now to hex(idBefore)
                    observe("compute_cA")
                    observe("send_confirm_a")
                    current.peer.acceptConfirmationA(confirmationAOf(outgoing))
                }
                PREAUTH_REJECT -> {
                    recordWire(index, outgoing)
                    observe("preauth_reject")
                    val charged = admission.attemptsChargedForTest(idBefore) > chargedBefore
                    when {
                        !valid -> observe("reject_malformed_point_no_consumption")
                        !window.isOpen() -> observe("reject_window_expired_no_compute")
                        chargedFor(idBefore) >= ATTEMPTS_PER_PAIRING_ID -> observe("reject_attempts_per_pairing_id")
                        rollingCharges() >= ROLLING_ATTEMPTS_MAX -> observe("reject_attempts_rolling")
                        !charged -> error("${fixture.id} step $index: refused with no attributable reason")
                    }
                    if (!window.isOpen()) observe("window_closed_code_erased")
                    if (stateBefore == PairingState.SPAKE_IN_FLIGHT) observe("execution_disconnected")
                    closeIfDisconnected(connection, outgoing)
                }
                else -> error("${fixture.id} step $index: unexpected point answer")
            }
        }

        private suspend fun onConfirmB(index: Int, connection: String, simulate: Map<String, String>) {
            val confirmation = live[connection]?.peer?.confirmationB()
            val sent = confirmation?.let { if (simulate["cB_mac_ok"] == "false") flip(it) else it }
            val confirmsBefore = recording.count("confirm")
            val stateBefore = live[connection]?.machine?.state
            val windowOpenBefore = window.isOpen()
            val outgoing = deliverToMachine(index, connection, sent?.let { PairingFrames.encode(PairingFrameType.CB, it) })
                ?: return
            val current = live.getValue(connection)

            when {
                current.machine.state == PairingState.ENROLLMENT_PROTECTED -> {
                    // §11.1 awaiting_cB -> enrollment_protected declares derive_enrollment_keys
                    // and send_enroll_identity: both are real here. The client finishes its
                    // execution for the same Ke and authenticates the phone's record before it
                    // releases its own.
                    assertTrue("${fixture.id} step $index: the window was open before cB", windowOpenBefore)
                    assertFalse("${fixture.id} step $index: the verified cB closes the window", window.isOpen())
                    assertTrue("${fixture.id} step $index: the verified cB erases the code", codeErased())
                    observe("pairing_code_consumed_one_use")
                    observe("close_pairing_window")
                    observe("derive_enrollment_keys")
                    val record = current.peer.completeEnrollment(outgoing)
                    assertEquals(
                        "${fixture.id} step $index: the phone record carries the trust-store identity",
                        phoneIdentityHex,
                        TrustEncoding.hex(record.installationId),
                    )
                    observe("send_enroll_identity")
                }
                frameType(outgoing) == PREAUTH_REJECT -> {
                    recordWire(index, outgoing)
                    observe("preauth_reject")
                    when {
                        !window.isOpen() -> {
                            observe("reject_window_expired_no_compute")
                            observe("window_closed_code_erased")
                        }
                        recording.count("confirm") > confirmsBefore && stateBefore == PairingState.AWAITING_CB -> {
                            observe("generic_pairing_failure")
                        }
                        else -> error("${fixture.id} step $index: refused confirmation without a reason")
                    }
                    closeIfDisconnected(connection, outgoing)
                }
                else -> error("${fixture.id} step $index: unexpected confirmation answer")
            }
        }

        private suspend fun onEnrollIdentity(index: Int, step: TraceStep) {
            val connection = step.explicitConnection ?: live.keys.lastOrNull() ?: "main"
            val current = live[connection]
                ?: error("${fixture.id} step $index: no live connection for the client identity")
            if (step.simulate["persist_ok"] == "false") trust.failPersist = true
            val frame = current.peer.clientIdentityFrame(step.simulate, duplicateId)
            val outgoing = deliverToMachine(index, connection, frame)
                ?: return
            val protectedRecord = outgoing.filterIsInstance<Outgoing.Frame>().firstOrNull()?.bytes
                ?.takeIf { it.size >= RecordCodec.HEADER + RecordCodec.TAG && it[0].toInt() == 1 && it[1].toInt() == 1 }
            when {
                protectedRecord != null && recordTypeOf(protectedRecord) == PREPARE_TYPE -> {
                    // The prepare may only follow a durable pending record carrying the same
                    // ET. Capture the durable state at the instant the frame is returned, so a
                    // machine that sent first or persisted another transcript cannot pass.
                    val expectedEt = current.peer.expectedTranscript()
                    val persisted = runBlocking { repository.peers() }.firstOrNull()
                    val durablePending = persisted?.state == TrustState.PENDING
                    if (durablePending && persisted?.enrollmentTranscript == hex(expectedEt)) {
                        observe("compute_et")
                        observe("persist_pending_record")
                        recordEverPersisted = true
                    }
                    expectedEt.fill(0)
                    current.peer.verifyPrepare(protectedRecord)
                    current.peer.captureTranscript()
                    observe("send_enrollment_prepare")
                    if (durablePending) observe("record_persisted_pending")
                }
                protectedRecord != null && recordTypeOf(protectedRecord) == ERROR_TYPE -> {
                    observe("reject_duplicate_installation")
                    observe("send_app_error_enrollment_rejected")
                    assertEquals("${fixture.id} step $index: the duplicate refusal terminates", PairingState.IDLE, current.machine.state)
                    assertNull("${fixture.id} step $index: the keys are erased", current.machine.enrollmentKeysForTest())
                    observe("erase_pairing_state")
                    val existing = seededPeer
                    assertNotNull("${fixture.id} step $index: a seeded record must exist", existing)
                    assertEquals(
                        "${fixture.id} step $index: the existing record is unchanged",
                        existing,
                        runBlocking { repository.peers() }.single(),
                    )
                    assertEquals("${fixture.id} step $index: the guard runs before addPending", 0, trust.addPendingCalls)
                    closeIfDisconnected(connection, outgoing)
                }
                frameType(outgoing) == PREAUTH_REJECT -> {
                    if (step.simulate["persist_ok"] == "false") {
                        // The failure is only the persist path if the machine really reached
                        // the store and published nothing.
                        assertEquals("${fixture.id} step $index: the persist attempt reached the store", 1, trust.addPendingCalls)
                        assertTrue(
                            "${fixture.id} step $index: no usable record was published",
                            runBlocking { repository.peers() }.isEmpty(),
                        )
                        observe("persist_failed_no_usable_record_published")
                        persistAttempted = true
                        recordDeleted = true
                    }
                    if (step.simulate["device_type_ok"] == "false") observe("reject_client_device_type")
                    assertEquals("${fixture.id} step $index: the failure returns idle", PairingState.IDLE, current.machine.state)
                    assertNull("${fixture.id} step $index: the keys are erased", current.machine.enrollmentKeysForTest())
                    observe("erase_pairing_state")
                    observe("close_enrollment_connection")
                    closeIfDisconnected(connection, outgoing)
                }
                else -> error("${fixture.id} step $index: unexpected enrollment answer")
            }
            trust.failPersist = false
        }

        /**
         * OTP2-STA-010b step 1 on the enrollment connection: the client's activation_commit is
         * verified against the enrollment keys, moves the record to committed_waiting_ack and
         * the phone answers activation_complete bound to the same ET.
         */
        private suspend fun onActivationCommit(index: Int, step: TraceStep) {
            val connection = step.explicitConnection ?: live.keys.lastOrNull() ?: "main"
            val current = live[connection]
                ?: error("${fixture.id} step $index: no live connection for the activation commit")
            val frame = current.peer.activationFrame(
                ACTIVATION_COMMIT_TYPE,
                STAGE_COMMIT,
                macOk = step.simulate["act_mac_ok"] != "false",
            )
            val outgoing = deliverToMachine(index, connection, frame)
                ?: return
            val response = outgoing.filterIsInstance<Outgoing.Frame>().singleOrNull()?.bytes
                ?: error("${fixture.id} step $index: the commit answer carries no frame")
            assertEquals("${fixture.id} step $index: the commit is answered by complete", ACTIVATION_COMPLETE_TYPE, recordTypeOf(response))
            current.peer.verifyComplete(response)
            assertEquals(
                "${fixture.id} step $index: the commit moves the machine",
                PairingState.COMMITTED_WAITING_ACK,
                current.machine.state,
            )
            assertEquals("${fixture.id} step $index: the durable record is committed", "committed_waiting_ack", durableState())
            observe("persist_committed_waiting_ack")
            observe("record_committed")
        }

        /** OTP2-STA-010b step 6: the verified ack activates the record and ends the enrollment. */
        private suspend fun onActivationAck(index: Int) {
            val connection = live.keys.lastOrNull() ?: "main"
            val current = live[connection]
                ?: error("${fixture.id} step $index: no live connection for the activation ack")
            val outgoing = deliverToMachine(
                index,
                connection,
                current.peer.activationFrame(ACTIVATION_ACK_TYPE, STAGE_ACK),
            ) ?: return
            assertTrue(
                "${fixture.id} step $index: the verified ack ends the enrollment connection",
                outgoing.contains(Outgoing.Disconnect),
            )
            assertEquals("${fixture.id} step $index: the ack activates the machine", PairingState.ACTIVE, current.machine.state)
            assertEquals("${fixture.id} step $index: the durable record is active", "active", durableState())
            assertNull("${fixture.id} step $index: the enrollment keys are erased", current.machine.enrollmentKeysForTest())
            // The host ends the generation on Outgoing.Disconnect and does not report link loss
            // to the machine, which already erased its own enrollment state.
            terminated = true
            live.remove(connection)
            current.peer.close()
            observe("record_active_by_ack")
            observe("record_activated_by_ack")
            observe("erase_enrollment_keys")
            observe("erase_enrollment_remnants")
            observe("close_enrollment_connection")
        }

        /** OTP2-REC-035: the enrollment connection never carries OTP data. */
        private suspend fun onUnauthorizedRecord(index: Int, step: TraceStep) {
            val connection = step.explicitConnection ?: live.keys.lastOrNull() ?: "main"
            val current = live[connection]
                ?: error("${fixture.id} step $index: no live connection for the unauthorized record")
            val type = step.simulate.getValue("record_type").removePrefix("0x").toInt(16)
            val frame = current.peer.recordFrame(type)
            val outgoing = deliverToMachine(index, connection, frame)
                ?: return
            assertEquals("${fixture.id} step $index: an OTP record is refused generically", PREAUTH_REJECT, frameType(outgoing))
            observe("reject_unauthorized_enrollment_record")
            assertNull("${fixture.id} step $index: the enrollment keys are erased", current.machine.enrollmentKeysForTest())
            observe("erase_enrollment_keys")
            closeIfDisconnected(connection, outgoing)
            observe("close_enrollment_connection")
        }

        private suspend fun onCancel() {
            val entry = live.entries.lastOrNull() ?: return
            val current = entry.value
            val durableBefore = durableState()
            val hadIncomplete = durableBefore == "pending" || durableBefore == "committed_waiting_ack"

            val outgoing = current.machine.cancel()

            assertTrue("${fixture.id}: cancel tears the connection down", outgoing.contains(Outgoing.Disconnect))
            assertEquals("${fixture.id}: cancel returns the machine to idle", PairingState.IDLE, current.machine.state)
            assertNull("${fixture.id}: cancel erases the enrollment keys", current.machine.enrollmentKeysForTest())
            assertFalse("${fixture.id}: cancel closes the window", window.isOpen())
            assertTrue("${fixture.id}: cancel erases the code", codeErased())
            observe("pairing_cancelled")
            observe("erase_pairing_state")
            observe("close_pairing_window")
            if (hadIncomplete) {
                assertEquals("${fixture.id}: cancel deletes through revocation", listOf(hex(CLIENT_INSTALLATION)), trust.revoked)
                assertTrue("${fixture.id}: the pending record is gone", runBlocking { repository.peers() }.isEmpty())
                observe("erase_incomplete_materials")
                recordDeleted = true
            }
            terminated = true
            live.remove(entry.key)
            current.peer.close()
        }

        private suspend fun onInactivity() {
            val liveEntries = live.entries.toList()
            liveEntries.forEach { (label, current) ->
                val stateBefore = current.machine.state
                val outgoing = current.machine.onTick()
                if (stateBefore == PairingState.ENROLLMENT_PROTECTED || stateBefore == PairingState.AWAITING_COMMIT) {
                    observe("pairing_step_inactivity_timeout")
                    if (durableState() == "pending" || durableState() == "committed_waiting_ack") {
                        observe("inactivity_timeout_pending_record_continues_via_recovery")
                    } else {
                        observe("inactivity_timeout_no_usable_peer_record_repair_only")
                    }
                    observe("close_enrollment_connection")
                } else if (window.isOpen()) {
                    observe("connection_lost_window_resumable")
                }
                if (outgoing.any { it == Outgoing.Disconnect }) {
                    terminated = true
                    live.remove(label)
                    current.peer.close()
                }
            }
        }

        private suspend fun onDirectionViolation(index: Int, connection: String) {
            val stateBefore = live[connection]?.machine?.state
            val outgoing = deliverToMachine(
                index,
                connection,
                PairingFrames.encode(PairingFrameType.CA, ByteArray(CONFIRMATION_BYTES)),
            ) ?: return
            if (frameType(outgoing) != PREAUTH_REJECT) error("${fixture.id} step $index: direction mismatch accepted")
            recordWire(index, outgoing)
            observe("preauth_reject")
            observe("reject_direction_violation")
            if (stateBefore == PairingState.SPAKE_IN_FLIGHT || stateBefore == PairingState.AWAITING_CB) {
                observe("execution_disconnected")
            }
            closeIfDisconnected(connection, outgoing)
        }

        private fun newConnection(label: String): PhonePairingMachine {
            val machine = PhonePairingMachine(
                window,
                admission,
                connIds.getOrPut(label) { connIds.size + 1L },
                trust,
                { recording },
                { now },
                PairingLimits(),
                {
                    ByteArray(NONCE_BYTES) {
                        ++nextNonce
                        nextNonce.toByte()
                    }
                },
                { "Phone" },
                { ByteArray(NONCE_BYTES) { 0x2a } },
                proofs = ProofRegistry(),
            )
            machines += machine
            live[label] = Live(machine, TracePeer(fixture, core, nextNonce))
            return machine
        }

        private fun closeIfDisconnected(connection: String, outgoing: List<Outgoing>) {
            if (outgoing.any { it == Outgoing.Disconnect }) {
                terminated = true
                live.remove(connection)?.let { ended ->
                    runBlocking { ended.machine.onDisconnect() }
                    ended.peer.close()
                }
            }
        }

        /**
         * Models `SessionHost.onRecord` for one step: the generation is looked up once and,
         * when it left the session table, the record is dropped before any engine or
         * pairing call. The step counters captured at dispatch time prove that the drop
         * spent no core call and no handle; [droppedSteps] records that the delivery model
         * really took that path.
         */
        private suspend fun deliverToMachine(index: Int, connection: String, frame: ByteArray?): List<Outgoing>? {
            val current = live[connection]
            if (current == null) {
                droppedSteps += index
                recordViolation(index, ViolationStage.TRANSITION, "a frame arrived for generation '$connection' after it ended")
                return null
            }
            if (frame == null) {
                recordViolation(index, ViolationStage.TRANSITION, "no frame for a live generation")
                return null
            }
            val outgoing = current.machine.onFrame(frame)
            recordWire(index, outgoing)
            return outgoing
        }

        /** Compares the step counters captured before dispatch with the state after it. */
        private fun recordViolation(index: Int, violationStage: ViolationStage, detail: String) {
            violation = Violation(violationStage, detail)
            violatingCoreCalls = recording.totalCalls() - stepCalls
            violatingHandles = recording.openHandles() - stepHandles
            violatingFrames = wireLog.filter { it.first == index }.map { it.second }
            noStateMutation = fingerprint() == stepFingerprint && violatingCoreCalls == 0 && violatingHandles == 0
        }

        private fun recordWire(index: Int, outgoing: List<Outgoing>) {
            framesOf(outgoing).forEach { wireLog += index to it }
        }

        private fun framesOf(outgoing: List<Outgoing>): List<String> = outgoing.filterIsInstance<Outgoing.Frame>().map { hex(it.bytes) }

        private fun observe(name: String) {
            observations.add(name)
            sequence += name
        }

        private fun chargedFor(pairingId: ByteArray): Int = charges.count { it.second == hex(pairingId) }

        private fun rollingCharges(): Int = charges.count { it.first > now - ROLLING_ATTEMPTS_MILLIS }

        /** Window, budget and handle state that no rejected step may change. */
        private fun fingerprint(): String = listOf(
            window.isOpen(),
            hex(window.pairingId),
            charges.size,
            paGrants.values.sum(),
            admission.attemptsChargedForTest(window.pairingId),
            admission.malformedConnectionsForTest(),
            admission.operationsInFlightForTest(),
            recording.openHandles(),
        ).joinToString(":")
    }

    /** The scripted client of one connection, built from the same core interface. */
    private inner class TracePeer(
        private val fixture: TraceFixture,
        private val core: PairingCore,
        nonceSeed: Int,
    ) {
        private val clientNonce = ByteArray(NONCE_BYTES) { (nonceSeed and 0xFF).toByte() }
        private val caps = CLIENT_CAPS
        private var execution: PairingExecution? = null
        private var point: ByteArray? = null
        private var phonePoint: ByteArray? = null
        private var confirmationA: ByteArray? = null
        private var confirmationB: ByteArray? = null
        private var context: ByteArray? = null
        private var keys: EnrollmentKeys? = null
        private var prefix: ByteArray? = null
        private var phoneIdentityBody: ByteArray? = null
        private var clientIdentityBody: ByteArray? = null
        private var clientTx: EnrollmentKeys? = null
        private var txSequence = 0uL
        private var transcript: ByteArray? = null

        fun requestFrame(): ByteArray {
            val payload = ByteArray(REQUEST_FIXED + caps.size)
            clientNonce.copyInto(payload)
            payload[VERSION_OFFSET + 1] = 1
            payload[CAPS_LEN_OFFSET + 1] = caps.size.toByte()
            caps.copyInto(payload, REQUEST_FIXED)
            return PairingFrames.encode(PairingFrameType.REQUEST, payload)
        }

        fun acceptOffer(offer: Offer, code: ByteArray) {
            val ctx = contextOf(offer.pairingId, offer.phoneNonce, clientNonce, offer.caps, caps)
            context = ctx
            val started = core.start(Role.CLIENT, code, ctx)
            val current = PairingExecution(core, started.handle)
            execution = current
            point = started.point
            phonePoint = offer.point.copyOf()
            current.exchange(offer.point)
        }

        fun point(): ByteArray? = point

        fun acceptConfirmationA(confirmationA: ByteArray) {
            this.confirmationA = confirmationA.copyOf()
            confirmationB = execution?.confirm(confirmationA)
            if (confirmationB == null) error("${fixture.id}: the client produced no cB")
        }

        fun confirmationB(): ByteArray? = confirmationB

        /**
         * The client's half of the verified-cB transition: `finish` yields the same `Ke`
         * (RFC 9382), the §5.1 schedule is derived independently, and the phone's
         * `enroll_identity` is authenticated before anything is released.
         */
        fun completeEnrollment(outgoing: List<Outgoing>): IdentityRecord {
            val ctx = context ?: error("${fixture.id}: no context")
            val shared = execution?.finish() ?: error("${fixture.id}: no client execution")
            execution = null
            val derived = try {
                EnrollmentSchedule.derive(shared, ctx)
            } finally {
                shared.fill(0)
            }
            keys = derived
            prefix = EnrollmentSchedule.sessionIdPrefix(ctx)
            val frame = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes
            val body = RecordCodec.open(
                derived.aToBAesKey,
                derived.aToBNoncePrefix,
                checkNotNull(prefix),
                Direction.PHONE_TO_CLIENT,
                0uL,
                frame,
            ).body
            val record = EnrollmentRecords.decodeFingerprintVerified(body, IdentityRole.PHONE)
            phoneIdentityBody = EnrollmentRecords.encode(record, IdentityRole.PHONE)
            body.fill(0)
            return record
        }

        /** Seals the client's own `enroll_identity`, honouring the trace's simulated faults. */
        fun clientIdentityFrame(simulate: Map<String, String>, duplicateId: ByteArray?): ByteArray {
            val deviceType = if (simulate["device_type_ok"] == "false") DeviceType.PHONE else DeviceType.DESKTOP
            val installationId = if (simulate["duplicate_installation"] == "true") {
                duplicateId ?: CLIENT_INSTALLATION
            } else {
                CLIENT_INSTALLATION
            }
            val record = EnrollmentRecords.create(
                installationId,
                "Desktop",
                deviceType,
                TestPeers.uncompressed(CLIENT_PUBLIC.public as ECPublicKey),
                CLIENT_IDENTITY_NONCE,
            )
            val role = if (deviceType == DeviceType.PHONE) IdentityRole.PHONE else IdentityRole.CLIENT
            val body = EnrollmentRecords.encode(record, role)
            if (simulate["fingerprint_ok"] == "false") {
                body[body.size - NONCE_BYTES - FINGERPRINT_BYTES] =
                    (body[body.size - NONCE_BYTES - FINGERPRINT_BYTES].toInt() xor 1).toByte()
            }
            clientIdentityBody = body.copyOf()
            val derived = keys ?: error("${fixture.id}: enrollment keys missing")
            clientTx = derived
            val frame = RecordCodec.seal(
                derived.bToAAesKey,
                derived.bToANoncePrefix,
                checkNotNull(prefix),
                Direction.CLIENT_TO_PHONE,
                RecordType.ENROLL_IDENTITY.wire,
                txSequence,
                body,
            )
            txSequence += 1uL
            if (simulate["aead_ok"] == "false") frame[frame.size - 1] = (frame[frame.size - 1].toInt() xor 1).toByte()
            return frame
        }

        /** `ET` over the two canonical bodies and the complete SPAKE2 exchange. */
        fun expectedTranscript(): ByteArray = EnrollmentRecords.transcript(
            checkNotNull(context),
            checkNotNull(phonePoint),
            checkNotNull(point),
            checkNotNull(confirmationA),
            checkNotNull(confirmationB),
            checkNotNull(phoneIdentityBody),
            checkNotNull(clientIdentityBody),
        )

        /** Retains the canonical transcript for the activation MACs of this connection. */
        fun captureTranscript() {
            transcript = expectedTranscript()
        }

        /** OTP2-CRY-019a: a client activation record over `ET` under the enrollment key. */
        fun activationFrame(type: Int, stage: Byte, macOk: Boolean = true): ByteArray {
            val derived = clientTx ?: error("${fixture.id}: no client identity was sent")
            val et = transcript ?: error("${fixture.id}: no transcript for an activation record")
            val mac = Mac.getInstance(HMAC_ALGORITHM).run {
                init(SecretKeySpec(derived.clientActivationKey, HMAC_ALGORITHM))
                doFinal(et + byteArrayOf(stage))
            }
            if (!macOk) mac[mac.size - 1] = (mac[mac.size - 1].toInt() xor 0x01).toByte()
            return try {
                RecordCodec.seal(
                    derived.bToAAesKey,
                    derived.bToANoncePrefix,
                    checkNotNull(prefix),
                    Direction.CLIENT_TO_PHONE,
                    type,
                    txSequence++,
                    mac,
                )
            } finally {
                mac.fill(0)
            }
        }

        /** A well-formed record of any type, used for the OTP-on-enrollment refusal. */
        fun recordFrame(type: Int): ByteArray = RecordCodec.seal(
            checkNotNull(clientTx).bToAAesKey,
            checkNotNull(clientTx).bToANoncePrefix,
            checkNotNull(prefix),
            Direction.CLIENT_TO_PHONE,
            type,
            txSequence++,
            ByteArray(0),
        )

        /** OTP2-CRY-019a: activation_complete must bind the same ET under the phone key. */
        fun verifyComplete(frame: ByteArray) {
            val derived = clientTx ?: error("${fixture.id}: no client identity was sent")
            val opened = RecordCodec.open(
                derived.aToBAesKey,
                derived.aToBNoncePrefix,
                checkNotNull(prefix),
                Direction.PHONE_TO_CLIENT,
                COMPLETE_SEQUENCE,
                frame,
            )
            val et = transcript ?: error("${fixture.id}: no transcript")
            val expected = Mac.getInstance(HMAC_ALGORITHM).run {
                init(SecretKeySpec(derived.phoneActivationKey, HMAC_ALGORITHM))
                doFinal(et + byteArrayOf(STAGE_COMPLETE))
            }
            try {
                assertTrue(
                    "${fixture.id}: activation_complete binds ET under the phone activation key",
                    MessageDigest.isEqual(expected, opened.body),
                )
            } finally {
                expected.fill(0)
                opened.body.fill(0)
            }
        }

        /** OTP2-CRY-019a: the prepare MAC must be over the same `ET` under the client-derived key. */
        fun verifyPrepare(frame: ByteArray) {
            val derived = keys ?: error("${fixture.id}: enrollment keys missing")
            val opened = RecordCodec.open(
                derived.aToBAesKey,
                derived.aToBNoncePrefix,
                checkNotNull(prefix),
                Direction.PHONE_TO_CLIENT,
                1uL,
                frame,
            )
            val et = expectedTranscript()
            val expected = try {
                Mac.getInstance("HmacSHA256").run {
                    init(SecretKeySpec(derived.phoneActivationKey, "HmacSHA256"))
                    doFinal(et + byteArrayOf(0))
                }
            } catch (failure: Exception) {
                error("${fixture.id}: HMAC unavailable: $failure")
            }
            assertTrue(
                "${fixture.id}: prepare MAC binds ET under the phone activation key",
                MessageDigest.isEqual(expected, opened.body),
            )
        }

        fun close() {
            execution?.close()
            execution = null
            keys?.wipe()
            keys = null
            context?.fill(0)
            context = null
            prefix?.fill(0)
            prefix = null
            phoneIdentityBody?.fill(0)
            phoneIdentityBody = null
            clientIdentityBody?.fill(0)
            clientIdentityBody = null
            confirmationA?.fill(0)
            confirmationA = null
            confirmationB?.fill(0)
            confirmationB = null
            point?.fill(0)
            point = null
            phonePoint?.fill(0)
            phonePoint = null
        }
    }

    private class Live(val machine: PhonePairingMachine, val peer: TracePeer)

    /** The stage a rejected trace fails at, as observed from the violation kind. */
    private enum class ViolationStage(val wire: String) {
        /** A frame arrived for a generation the host already dropped. */
        TRANSITION("transition"),
    }

    private class Violation(val stage: ViolationStage, val reason: String)

    private data class Offer(
        val pairingId: ByteArray,
        val phoneNonce: ByteArray,
        val caps: ByteArray,
        val point: ByteArray,
    )

    /** Parses a `pairing_offer` payload: 16+16+u16+u16+caps+65. */
    private fun offerOf(outgoing: List<Outgoing>): Offer {
        val frame = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes
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
        return Offer(pairingId, nonce, caps, payload.copyOfRange(offset, offset + POINT_BYTES))
    }

    /** The `pairing_confirm_a` payload: the 32-byte cA body. */
    private fun confirmationAOf(outgoing: List<Outgoing>): ByteArray {
        val frame = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes
        return frame.copyOfRange(HEADER, HEADER + CONFIRMATION_BYTES)
    }

    /**
     * §3.3 canonical public context, implemented independently of the machine: the two
     * builders must agree byte for byte or the real core's confirmation fails.
     */
    private fun contextOf(
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

    private fun putU16(target: ByteArray, offset: Int, value: Int): Int {
        target[offset] = (value ushr BITS_PER_BYTE).toByte()
        target[offset + 1] = value.toByte()
        return offset + U16_BYTES
    }

    private fun u16(source: ByteArray, offset: Int): Int {
        val high = (source[offset].toInt() and U8_MASK) shl BITS_PER_BYTE
        return high or (source[offset + 1].toInt() and U8_MASK)
    }

    private fun frameType(outgoing: List<Outgoing>): Int? {
        val frame = outgoing.filterIsInstance<Outgoing.Frame>().firstOrNull() ?: return null
        return frame.bytes[0].toInt() and U8_MASK
    }

    private fun recordTypeOf(record: ByteArray): Int {
        val high = (record[3].toInt() and U8_MASK) shl BITS_PER_BYTE
        return high or (record[4].toInt() and U8_MASK)
    }

    private fun flip(bytes: ByteArray): ByteArray = bytes.copyOf().also { it[it.size - 1] = (it[it.size - 1].toInt() xor 1).toByte() }

    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { (it.toInt() and U8_MASK).toString(16).padStart(2, '0') }

    /** Counts core calls so a deadline rejection can prove no password-sensitive work ran. */
    private class RecordingCore(private val delegate: PairingCore) : PairingCore {
        private val calls = mutableListOf<String>()

        fun count(name: String): Int = synchronized(calls) { calls.count { it == name } }

        fun totalCalls(): Int = synchronized(calls) { calls.size }

        override fun start(role: Role, code: ByteArray, context: ByteArray): Started = record("start") {
            delegate.start(role, code, context)
        }

        override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray = record("exchange") {
            delegate.exchange(handle, peerPoint)
        }

        override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray = record("confirm") {
            delegate.confirm(handle, peerConfirmation)
        }

        override fun finish(handle: Handle): ByteArray = record("finish") { delegate.finish(handle) }

        override fun close(handle: Handle) = record("close") { delegate.close(handle) }

        override fun openHandles(): Int = delegate.openHandles()

        private inline fun <T> record(name: String, body: () -> T): T {
            synchronized(calls) { calls += name }
            return body()
        }
    }

    // -----------------------------------------------------------------------------------------
    // Fixture and inventory reading
    // -----------------------------------------------------------------------------------------

    private class TraceFixture(val root: JsonObject) {
        val input: JsonObject = root.getValue("input").jsonObject
        val expected: JsonObject = root.getValue("expected").jsonObject
        val id: String = string(root, "id")
        val role: String = string(input, "role")
        val steps: List<TraceStep> = input.getValue("steps").jsonArray.map { TraceStep(it.jsonObject) }
        val finalState: Map<String, String> = (expected["final_state"] as? JsonPrimitive)?.content?.let { assignments(it) }.orEmpty()
        val stage: String? = (expected["stage"] as? JsonPrimitive)?.content
        val require: List<String> = effects("require")
        val forbid: List<String> = effects("forbid")
        val order: List<String> = effects("order")
        val persistedState: Map<String, String> =
            (input["persisted_state"] as? JsonObject)?.entries
                ?.associate { (key, value) -> key to (value as JsonPrimitive).content }
                .orEmpty()

        private fun effects(name: String): List<String> {
            val require = expected["effects"] as? JsonObject ?: return emptyList()
            return require[name]?.jsonArray?.map { string(it) } ?: emptyList()
        }
    }

    private class TraceStep(val root: JsonObject) {
        val tMs: Long = string(root, "t_ms").toLong()
        val event: String = string(root, "event")
        val explicitConnection: String? = (root["connection"] as? JsonPrimitive)?.content
        val connection: String = explicitConnection ?: "main"
        val simulate: Map<String, String> = (root["simulate"] as? JsonObject)?.entries?.associate { (key, value) ->
            key to (value as JsonPrimitive).content
        }.orEmpty()
        val expect: Map<String, String> = (root["expect"] as? JsonObject)?.entries?.associate { (key, value) ->
            key to (value as JsonPrimitive).content
        }.orEmpty()
    }

    private val inventory: Map<String, Any> by lazy {
        read("/otp-10-scenarios.json").entries.associate { (key, value) ->
            key to when (value) {
                is JsonPrimitive -> value.content
                is JsonObject -> value
                else -> error("unsupported inventory entry $key")
            }
        }
    }

    private val fixtures: List<TraceFixture> by lazy {
        read("/fixtures/enrollment.json").getValue("fixtures").jsonArray.map { TraceFixture(it.jsonObject) }
    }

    private fun read(path: String): JsonObject {
        val stream = checkNotNull(javaClass.getResourceAsStream(path)) { "missing $path" }
        return Json.parseToJsonElement(stream.use { it.readBytes().decodeToString() }).jsonObject
    }

    private companion object {
        const val U8_MASK = 0xFF
        const val BITS_PER_BYTE = 8
        const val U16_BYTES = 2
        const val HEADER = 3
        const val NONCE_BYTES = 16
        const val PAIRING_ID_BYTES = 16
        const val POINT_BYTES = 65
        const val CONFIRMATION_BYTES = 32
        const val FINGERPRINT_BYTES = 32
        const val REQUEST_FIXED = NONCE_BYTES + 4
        const val VERSION_OFFSET = NONCE_BYTES
        const val CAPS_LEN_OFFSET = NONCE_BYTES + 2
        const val CONTEXT_FIXED = 30 + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES + NONCE_BYTES + 4
        const val OFFER = 0x02
        const val CONFIRM_A = 0x04
        const val PREAUTH_REJECT = 0x0F
        const val ERROR_TYPE = 0x0003
        const val PREPARE_TYPE = 0x0104
        const val ACTIVATION_COMMIT_TYPE = 0x0101
        const val ACTIVATION_COMPLETE_TYPE = 0x0102
        const val ACTIVATION_ACK_TYPE = 0x0103
        const val COMPLETE_SEQUENCE = 2uL
        const val STAGE_COMMIT: Byte = 0x01
        const val STAGE_COMPLETE: Byte = 0x02
        const val STAGE_ACK: Byte = 0x03
        const val HMAC_ALGORITHM = "HmacSHA256"
        const val ATTEMPTS_PER_PAIRING_ID = 5
        const val ROLLING_ATTEMPTS_MAX = 5
        const val FRESH_PA_ROLLING_MAX = 5
        const val ROLLING_ATTEMPTS_MILLIS = 300_000L
        const val ROLLING_PA_MILLIS = 60_000L
        const val BOOT = "boot-a"
        const val ET_HEX = "0303030303030303030303030303030303030303030303030303030303030303"
        val PHONE_KEYS = P256.generate()
        val CLIENT_PUBLIC = P256.generate()
        val CLIENT_INSTALLATION = ByteArray(NONCE_BYTES) { (0x10 + it).toByte() }
        val CLIENT_IDENTITY_NONCE = ByteArray(NONCE_BYTES) { 0x33 }
        val CONTEXT_PREFIX = "Veles SPAKE2 public context v1".toByteArray(Charsets.US_ASCII)
        val CLIENT_CAPS = byteArrayOf(0x01, 0x03)
        val INVALID_POINT = ByteArray(65)

        fun string(json: JsonObject, key: String): String = (json.getValue(key) as JsonPrimitive).content

        fun assignments(value: String): Map<String, String> = value
            .trim('"')
            .split(';')
            .filter { it.isNotBlank() }
            .associate { entry ->
                val (key, state) = entry.split('=', limit = 2)
                key to state
            }

        fun string(element: JsonElement): String = (element as JsonPrimitive).content

        fun JsonObject.bool(key: String): Boolean = (getValue(key) as JsonPrimitive).content == "true"

        /**
         * The `enrollment.json` phone traces whose declared effects this task observes for
         * real: the window, admission, deadline and cB rows plus Task 7's enrollment flow.
         * The activation rows (`st-enrollment-full-success-...`,
         * `st-en-prepare-sent-after-persist-pairing`) stay deferred to the activation tasks,
         * and the client-role rows to Task 8.
         */
        val EXECUTED_PHONE_TRACES = setOf(
            "st-en-attempts-five-ok-sixth-rejected",
            "st-en-rolling-attempts-boundaries",
            "st-en-malformed-point-no-consumption",
            "st-en-valid-pb-consumes-before-sensitive-operations",
            "st-en-pa-churn-rolling-and-per-connection",
            "st-en-second-request-in-flight-rejected",
            "st-en-window-expiry-closes",
            "st-en-role-mismatch-frame-rejected",
            "st-en-window-deadline-pb-299999-allowed",
            "st-en-window-deadline-pb-300000-rejected",
            "st-en-window-deadline-pb-300001-rejected",
            "st-en-window-deadline-cb-299999-allowed",
            "st-en-window-deadline-cb-300000-rejected",
            "st-en-window-deadline-cb-300001-rejected",
            "st-en-phone-identity-device-type-mismatch",
            "st-en-duplicate-installation-rejected",
            "st-en-cancel-cancels-pending",
            "st-en-code-one-use-window-closed-after-cb",
            "st-en-cb-disconnect-before-identity-repair-only",
            "st-en-inactivity-before-identity-repair-only",
            "st-neg-phone-persist-failure-sends-no-prepare",
            "st-en-phone-pending-survives-interruption-with-et",
            "st-enrollment-full-success-then-no-otp-on-enrollment-connection",
            "st-en-prepare-sent-after-persist-pairing",
        )

        /** Enrollment-file traces that drive the reconnect engine, not the pairing machine. */
        val SESSION_TRACE_IDS = setOf("st-rec-no-peer-record-no-recovery")
    }
}

/** The trace store: one in-memory snapshot, replaced atomically by every transaction. */
private class TraceTrustStorage : TrustStorage {
    private var value: TrustSnapshot? = null

    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        val (next, result) = block(value)
        value = next
        return result
    }
}

/** A trust store that owns its installation key only after the repository generates it. */
private class PhoneIdentityKeyStore(private val publicKeyHex: String) : IdentityKeyStore {
    private var present = false

    override fun publicKey(): String? = if (present) publicKeyHex else null

    override fun generate(): String {
        present = true
        return publicKeyHex
    }

    override fun sign(message: ByteArray): ByteArray = ByteArray(0)
}

/**
 * The run's trust store with the two observable operations: `addPending` can be made to fail
 * (the persist-failure trace) and `revoke` records the cancel deletion.
 */
private class RunTrust(private val delegate: TrustRepository) : InstallationTrustRepository {
    var addPendingCalls = 0
        private set
    var failPersist = false
    val revoked = mutableListOf<String>()

    override suspend fun identity(): InstallationIdentity = delegate.identity()
    override suspend fun peers(): List<TrustedPeer> = delegate.peers()
    override suspend fun activePeers(): List<TrustedPeer> = delegate.activePeers()

    override suspend fun addPending(peer: PendingPeer) {
        addPendingCalls += 1
        if (failPersist) throw TrustException()
        delegate.addPending(peer)
    }

    override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = delegate.confirm(peerId, step, proof)

    override suspend fun revoke(peerId: String) {
        revoked += peerId
        delegate.revoke(peerId)
    }

    override suspend fun markAuthenticated(peerId: String) = delegate.markAuthenticated(peerId)
}
