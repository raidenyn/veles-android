package me.nagaev.veles.pairing

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import me.nagaev.veles.session.Role
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP-10 phone trace runner: every `enrollment.json` scenario the OTP-10 inventory
 * executes through a verified `cB` is replayed against the real host core.
 *
 * The phone side is the production [PhonePairingMachine] over a real [PairingWindow] and
 * [PairingAdmission]; the client side is a trace peer built from the same `PairingCore`
 * interface (the `protocol/checks/state.mjs` interpreter's role, in Kotlin). The public
 * context is built here independently of the machine from §3.3, so a divergence in the
 * machine's canonical context makes the real core's confirmation fail.
 *
 * `simulate` flags in the traces (`pB_point_valid`, `cB_mac_ok`) are simulated OTP
 * observations (OTP2-FIX-004), never cryptographic evidence: an invalid point is an
 * all-zero SEC1 body and a bad `cB` is one flipped MAC byte. The positive `cA`/`cB`
 * material is real, produced by both roles of the core.
 *
 * Effects the terminal `cB` transition declares but this task does not implement
 * (`derive_enrollment_keys`, `send_enroll_identity`) are observed as the verified
 * transition itself: the enrollment session that derives the schedule from `Ke` and
 * sends the identity record is Task 7's `EnrollmentSession`, and the execution handle
 * stays open here for it.
 */
class EnrollmentScenarioTest : PairingTestBase() {
    private val real = VelesPairingCore()
    private val recording = RecordingCore(real)
    override val core: PairingCore = recording

    @Test
    fun every_executed_phone_trace_passes_on_the_real_core() = runBlocking {
        val executed = inventory.filterValues { it == "execute" }.keys
        val phone = fixtures.filter { it.role == "phone" && it.id in executed }.map { it.id }
        assertEquals("the through-cB traces are the executed phone set", THROUGH_CB_TRACES, phone.toSet())
        phone.forEach { runTrace(it) }
        assertEquals(
            "Task 7 must delete SYNTHETIC_UNTIL_TASK_7 once the enrollment session observes these",
            SYNTHETIC_UNTIL_TASK_7,
            syntheticUsed,
        )
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
            assertEquals("$id final_state", fixture.finalState, mapOf("phone_pairing:main" to run.finalState))
            run.stepStates.forEach { (index, expected) ->
                expected.forEach { (machine, state) ->
                    assertEquals("$id step $index $machine", state, run.stateAt[index])
                }
            }
            val synthetic = fixture.require.filter { it in SYNTHETIC_UNTIL_TASK_7 }
            if (synthetic.isNotEmpty()) {
                // Fail-closed stand-in: the machine verifies cB and keeps the execution for
                // Task 7, but derives no keys and emits no identity record yet. Deleting the
                // set below makes the normal `require` assertion fail until Task 7 observes
                // the real effects, so the stand-in cannot outlive its owner silently.
                syntheticUsed += synthetic
                assertTrue("$id must reach the verified cB transition", run.confirmationVerified)
                assertTrue("$id must keep the execution handle for Task 7", run.openHandlesAtEnd > 0)
                assertTrue("$id must emit no enrollment frame before Task 7", run.confirmationFrames.isEmpty())
            }
            fixture.require.filterNot { it in SYNTHETIC_UNTIL_TASK_7 }.forEach {
                assertTrue("$id requires $it", it in run.observations)
            }
            fixture.forbid.forEach { assertTrue("$id forbids $it", it !in run.observations) }
            fixture.order.forEach { name -> assertTrue("$id order name $name", name in run.sequence) }
            fixture.order.zipWithNext().forEach { (first, second) ->
                assertTrue("$id order $first before $second", run.sequence.indexOf(first) < run.sequence.indexOf(second))
            }
        }
        return run
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

        val observations = LinkedHashSet<String>()
        val sequence = mutableListOf<String>()
        val stateAt = HashMap<Int, String>()
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
        var confirmationVerified = false
            private set
        var confirmationFrames: List<String> = emptyList()
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
                    else -> error("${fixture.id} has no mapping for ${step.event}")
                }
                stateAt[index] = state()
            }
            openHandlesAtEnd = recording.openHandles()
            finalState = state()
        }

        fun teardown() {
            machines.forEach(PhonePairingMachine::onDisconnect)
            machines.clear()
            live.values.forEach { it.peer.close() }
            live.clear()
            code.fill(0)
        }

        /** The phone pairing state for the trace's `phone_pairing:main` machine. */
        fun state(): String = when {
            machines.any { it.state == PairingState.ENROLLMENT_PROTECTED } -> "enrollment_protected"
            machines.any { it.state == PairingState.AWAITING_CB } -> "awaiting_cB"
            machines.any { it.state == PairingState.SPAKE_IN_FLIGHT } -> "spake_in_flight"
            window.isOpen() -> "window_open"
            else -> "idle"
        }

        private fun onConnectionLost(connection: String?) {
            // The fixture omits the connection on `connection_lost`: it ends the live link.
            val label = connection ?: live.keys.lastOrNull() ?: return
            val current = live.remove(label) ?: return
            current.machine.onDisconnect()
            current.peer.close()
            if (window.isOpen()) observe("connection_lost_window_resumable")
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
            val outgoing = deliverToMachine(index, connection, sent?.let { PairingFrames.encode(PairingFrameType.CB, it) })
                ?: return
            val current = live.getValue(connection)

            when {
                current.machine.state == PairingState.ENROLLMENT_PROTECTED -> {
                    // §11.1 awaiting_cB -> enrollment_protected declares `derive_enrollment_keys`
                    // and `send_enroll_identity`; Task 7's EnrollmentSession owns both. The
                    // runner records the verified transition and that the machine emitted no
                    // frame, and `SYNTHETIC_UNTIL_TASK_7` keeps the gap explicit.
                    confirmationVerified = true
                    confirmationFrames = framesOf(outgoing)
                    observe("window_closed_code_erased")
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
                { recording },
                { now },
                PairingLimits(),
                {
                    ByteArray(NONCE_BYTES) {
                        ++nextNonce
                        nextNonce.toByte()
                    }
                },
            )
            machines += machine
            live[label] = Live(machine, TracePeer(fixture, core, nextNonce))
            return machine
        }

        private fun closeIfDisconnected(connection: String, outgoing: List<Outgoing>) {
            if (outgoing.any { it == Outgoing.Disconnect }) {
                terminated = true
                live.remove(connection)?.let { ended ->
                    ended.machine.onDisconnect()
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
            if (observations.add(name)) sequence += name
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
        private var confirmation: ByteArray? = null

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
            val started = core.start(Role.CLIENT, code, ctx)
            val current = PairingExecution(core, started.handle)
            execution = current
            point = started.point
            current.exchange(offer.point)
        }

        fun point(): ByteArray? = point

        fun acceptConfirmationA(confirmationA: ByteArray) {
            confirmation = execution?.confirm(confirmationA)
            if (confirmation == null) error("${fixture.id}: the client produced no cB")
        }

        fun confirmationB(): ByteArray? = confirmation

        fun close() {
            execution?.close()
            execution = null
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

    /**
     * Effects the verified-`cB` row declares that this task cannot observe yet: Task 7's
     * `EnrollmentSession` derives the schedule and sends the identity record. They are
     * asserted as the fail-closed stand-in in `SYNTHETIC_UNTIL_TASK_7`; Task 7 must delete
     * that set once its runner observes both for real.
     */
    private val syntheticUsed = mutableSetOf<String>()

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
        const val REQUEST_FIXED = NONCE_BYTES + 4
        const val VERSION_OFFSET = NONCE_BYTES
        const val CAPS_LEN_OFFSET = NONCE_BYTES + 2
        const val CONTEXT_FIXED = 30 + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES + NONCE_BYTES + 4
        const val OFFER = 0x02
        const val CONFIRM_A = 0x04
        const val PREAUTH_REJECT = 0x0F
        const val ATTEMPTS_PER_PAIRING_ID = 5
        const val ROLLING_ATTEMPTS_MAX = 5
        const val FRESH_PA_ROLLING_MAX = 5
        const val ROLLING_ATTEMPTS_MILLIS = 300_000L
        const val ROLLING_PA_MILLIS = 60_000L
        val SYNTHETIC_UNTIL_TASK_7 = setOf("derive_enrollment_keys", "send_enroll_identity")
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

        /** The enrollment traces that end at or before a verified `cB` with no persisted record. */
        val THROUGH_CB_TRACES = setOf(
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
        )
    }
}
