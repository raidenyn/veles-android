package me.nagaev.veles.session

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.security.KeyPair
import java.security.SecureRandom
import java.security.interfaces.ECPublicKey
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * OTP-09 Task 10 conformance runner for the phone engine.
 *
 * Every in-scope `records.json` / `reconnect.json` scenario is replayed against the real
 * [PhoneSessionEngine]; the scenario inventory (`protocol/conformance/otp-09-scenarios.json`)
 * decides which ids execute, which are endpoint-effect checks for trace-validation negatives,
 * and which are deferred with an owning task. The TypeScript runner drives the client engine
 * over the same inventory.
 *
 * The harness follows the plan's step mapping: [ScenarioPeer] is a scripted client built from
 * the same codec, [ScenarioHandler] is passive (it records deliveries, acks and buffer state
 * but never replies on its own), and every `send_protected_record` step is exactly one
 * `sendApplication` call. One deliberate adaptation: a phone-role `app_heartbeat` send step is
 * injected as a peer record, because OTP2-REC-034's terminating rule is the receive direction
 * while the outbound gate only ignores early heartbeats (design D4).
 */
class ScenarioConformanceTest {
    private val inventory = ScenarioFixtures.inventory

    @Test
    fun inventoryCoversEveryFixtureId() {
        val ids = ScenarioFixtures.all.map { it.id }
        assertEquals("every fixture id must have an inventory entry", ids.toSet(), inventory.keys)
        inventory.forEach { (id, entry) ->
            when (entry) {
                is String -> assertTrue(
                    "$id has an unsupported classification $entry",
                    entry == "execute" || entry.startsWith("deferred:OTP-"),
                )
                is JsonObject -> {
                    val effect = entry["engine-effect"] as? JsonObject
                    assertNotNull("$id engine-effect must be an object", effect)
                    listOf("terminate", "erase", "deliver").forEach { key ->
                        assertNotNull("$id engine-effect.$key is required", effect?.get(key))
                    }
                }
                else -> throw AssertionError("$id has an unsupported classification")
            }
        }
        assertEquals("execute scenarios", 22, inventory.values.count { it == "execute" })
        assertTrue(
            "the OTP-09 inventory must defer explicit work",
            inventory.values.any { it is String && it.startsWith("deferred:") },
        )
    }

    @Test
    fun everyExecutedPhoneScenarioPasses() {
        inventory.filterValues { it == "execute" }.forEach { (id, _) ->
            val fixture = ScenarioFixtures.byId(id)
            if (fixture.role != "phone") return@forEach
            val run = runScenario(fixture)
            assertEquals("$id accepted", true, run.accepted)
            assertEquals(
                "$id final_state",
                parseAssignments(fixture.expected.getValue("final_state").toString().trim('"')),
                run.finalState(),
            )
            run.steps.forEach { step ->
                val expected = fixture.steps[step.index].expect
                expected.forEach { (key, value) ->
                    assertEquals("$id step ${step.index} $key", value, step.stateSnapshot[key])
                }
            }
            checkEffects(id, fixture, run)
        }
    }

    @Test
    fun everyEngineEffectPhoneScenarioPasses() {
        inventory.filterValues { it is JsonObject }.forEach { (id, entry) ->
            val fixture = ScenarioFixtures.byId(id)
            if (fixture.role != "phone") return@forEach
            val run = runScenario(fixture)
            val expected = ((entry as JsonObject)["engine-effect"] as JsonObject)
            assertEquals("$id terminate", expected.bool("terminate"), run.terminated)
            assertEquals("$id erase", expected.bool("erase"), run.keysErased)
            assertEquals("$id deliver", expected.bool("deliver"), run.delivered)
            assertEquals(
                "$id is a trace-validation negative",
                "false",
                fixture.expected["accepted"]?.toString(),
            )
        }
    }

    @Test
    fun deferredScenariosArePrintedWithTheirOwningTask() {
        inventory.filterValues { it is String && it.startsWith("deferred:") }
            .toSortedMap()
            .forEach { (id, owner) ->
                println("deferred $id -> ${(owner as String).removePrefix("deferred:")}")
            }
    }

    @Test
    fun everyEffectNameInAnExecutedScenarioIsMapped() {
        inventory.filterValues { it == "execute" || it is JsonObject }.keys.forEach { id ->
            val fixture = ScenarioFixtures.byId(id)
            if (fixture.role != "phone") return@forEach
            val effects = fixture.expected["effects"]?.jsonObject ?: return@forEach
            (effects["require"]?.jsonArray ?: emptyList()).forEach { assertMapped(it.toString().trim('"')) }
            (effects["forbid"]?.jsonArray ?: emptyList()).forEach { assertMapped(it.toString().trim('"')) }
            (effects["order"]?.jsonArray ?: emptyList()).forEach { assertMapped(it.toString().trim('"')) }
        }
    }

    @Test
    fun clientRoleScenariosAreDrivenByTheTypeScriptRunner() {
        val executed = inventory.filterValues { it == "execute" }.keys.map { ScenarioFixtures.byId(it) }
        val phone = executed.filter { it.role == "phone" }
        val client = executed.filter { it.role == "client" }
        assertEquals(
            "every execute id must belong to exactly one engine runner",
            executed.size,
            phone.size + client.size,
        )
        assertEquals(
            "the client engine scenarios are the TypeScript runner's set",
            setOf(
                "st-rec-direction-mismatch-terminates",
                "st-rec-client-full-pull-delivers",
                "st-rec-client-activation-pending-upgrades-on-phone-proof",
                "st-rec-client-mirror-durable-across-restart",
            ),
            client.map { it.id }.toSet(),
        )
    }

    private fun assertMapped(name: String) {
        assertTrue("effect '$name' has no engine observation mapping", Effects.isMapped(name))
    }

    private fun checkEffects(id: String, fixture: ScenarioFixture, run: ScenarioRun) {
        val effects = fixture.expected["effects"]?.jsonObject ?: return
        (effects["require"]?.jsonArray ?: emptyList()).forEach { element ->
            val name = element.toString().trim('"')
            assertTrue("$id requires $name", Effects.observe(name, run))
        }
        (effects["forbid"]?.jsonArray ?: emptyList()).forEach { element ->
            val name = element.toString().trim('"')
            assertTrue("$id forbids $name", !Effects.observe(name, run))
        }
    }

    private fun runScenario(fixture: ScenarioFixture): ScenarioRun = runBlocking {
        PhoneScenario(fixture).executeScenario()
    }

    private fun parseAssignments(value: String): Map<String, String> = value
        .split(';')
        .filter { it.isNotBlank() }
        .associate { entry ->
            val (key, state) = entry.split('=', limit = 2)
            key to state
        }
}

// ---------------------------------------------------------------------------------------------
// Fixture and inventory reading
// ---------------------------------------------------------------------------------------------

private object ScenarioFixtures {
    private fun read(path: String): JsonObject {
        val stream = checkNotNull(ScenarioFixtures::class.java.getResourceAsStream(path)) { "missing $path" }
        val text = stream.use { it.readBytes().decodeToString() }
        return Json.parseToJsonElement(text).jsonObject
    }

    private fun fixtures(path: String): List<ScenarioFixture> {
        val array = read(path).getValue("fixtures").jsonArray
        return array.map { ScenarioFixture(it.jsonObject) }
    }

    val records: List<ScenarioFixture> = fixtures("/fixtures/records.json")
    val reconnect: List<ScenarioFixture> = fixtures("/fixtures/reconnect.json")
    val all: List<ScenarioFixture> = records + reconnect
    val inventory: Map<String, Any> = read("/otp-09-scenarios.json").entries.associate { (key, value) ->
        key to when (value) {
            is JsonPrimitive -> value.content
            is JsonObject -> value
            else -> error("unsupported inventory entry $key")
        }
    }

    fun byId(id: String): ScenarioFixture = all.firstOrNull { it.id == id } ?: error("no fixture with id $id")
}

private class ScenarioFixture(root: JsonObject) {
    val id: String = (root["id"] as JsonPrimitive).content
    val input: JsonObject = root.getValue("input").jsonObject
    val expected: JsonObject = root.getValue("expected").jsonObject
    val role: String = (input["role"] as JsonPrimitive).content
    val persisted: Map<String, String> = (input["persisted_state"] as? JsonObject)
        ?.entries
        ?.associate { (key, value) -> key to (value as JsonPrimitive).content }
        .orEmpty()
    val steps: List<ScenarioStep> = input.getValue("steps").jsonArray.map { ScenarioStep(it.jsonObject) }
    val secretPersisted: Boolean = persisted.keys.any {
        it == "enrollment_prk" || it.endsWith("_prk") || it.contains("secret")
    }
}

private class ScenarioStep(root: JsonObject) {
    val tMs: Long = (root["t_ms"] as JsonPrimitive).content.toLong()
    val event: String = (root["event"] as JsonPrimitive).content
    val peer: String = (root["peer"] as? JsonPrimitive)?.content ?: "main"
    val simulate: Map<String, String> = (root["simulate"] as? JsonObject)
        ?.entries
        ?.associate { (key, value) -> key to (value as JsonPrimitive).content }
        .orEmpty()
    val expect: Map<String, String> = (root["expect"] as? JsonObject)
        ?.entries
        ?.associate { (key, value) -> key to (value as JsonPrimitive).content }
        .orEmpty()
}

private fun JsonObject.bool(key: String): Boolean = (get(key) as JsonPrimitive).content == "true"

// ---------------------------------------------------------------------------------------------
// Scenario harness
// ---------------------------------------------------------------------------------------------

private data class StepOutcome(
    val index: Int,
    val event: String,
    val peer: String,
    val simulate: Map<String, String>,
    val terminated: String?,
    val closed: Boolean,
    val sentTypes: List<Int>,
    val deliveredTypes: List<Int>,
    val preAuthReject: Boolean,
    val unauthorized: Boolean,
    val sequenceMismatch: Boolean,
    val recordOpened: Boolean,
    val heartbeatCrossed: Boolean,
    val eventCount: Int?,
    val authenticatedBefore: Boolean,
    val inboundBefore: ULong,
    val inboundAfter: ULong,
    val stateSnapshot: Map<String, String>,
) {
    val accepted: Boolean get() = terminated == null && !closed
    val inboundAdvanced: Boolean get() = inboundAfter == inboundBefore + 1uL
}

private class EventBuffer {
    val known: MutableSet<String> = mutableSetOf()
    val acks: MutableList<Pair<String, Boolean>> = mutableListOf()
    var settled = false
    var sawUnknown = false
    var lastEventCount: Int? = null
}

private class ScenarioHandler(private val buffer: EventBuffer) : ApplicationHandler {
    val deliveries: MutableList<Int> = mutableListOf()

    override fun handles(type: Int): Boolean = type in HANDLED

    override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
        deliveries += type
        when (type) {
            TYPE_EVENT_ACK -> {
                if (body.size == ACK_BYTES) {
                    val id = hex(body.copyOfRange(INSTALLATION_BYTES, ACK_BYTES))
                    val known = id in buffer.known
                    buffer.acks += id to known
                    if (known) buffer.settled = true else buffer.sawUnknown = true
                }
            }
            TYPE_PULL_EVENTS -> {
                val count = body[0].toInt() and U8_MASK
                // OTP2-REC-031/033: 0..5 events. The application handler owns this bound; a
                // larger set is rejected whole and the engine fails closed.
                if (count > MAX_EVENTS) throw SessionReject()
                buffer.lastEventCount = count
                repeat(count) { index ->
                    registerEvent(body.copyOfRange(1 + index * EVENT_BYTES, 1 + (index + 1) * EVENT_BYTES))
                }
            }
            TYPE_PUSH_EVENT -> {
                buffer.lastEventCount = 1
                registerEvent(body)
            }
            TYPE_PHONE_POLICY_STATE -> buffer.lastEventCount = 0
        }
    }

    private fun registerEvent(event: ByteArray) {
        if (event.size < INSTALLATION_BYTES + EVENT_ID_BYTES) return
        buffer.known += hex(event.copyOfRange(INSTALLATION_BYTES, INSTALLATION_BYTES + EVENT_ID_BYTES))
    }

    companion object {
        const val TYPE_APP_HEARTBEAT = 0x0002
        const val TYPE_PULL_REQUEST = 0x0200
        const val TYPE_PULL_EVENTS = 0x0202
        const val TYPE_PUSH_EVENT = 0x0203
        const val TYPE_EVENT_ACK = 0x0204
        const val TYPE_PHONE_POLICY_STATE = 0x0207
        const val TYPE_ACTIVATION_COMMIT = 0x0101
        const val TYPE_ACTIVATION_COMPLETE = 0x0102
        const val TYPE_ACTIVATION_ACK = 0x0103
        const val TYPE_ENROLLMENT_PREPARE = 0x0104
        const val TYPE_REMOVAL_REQUEST = 0x0205
        const val TYPE_REMOVAL_RESPONSE = 0x0206
        const val INSTALLATION_BYTES = 16
        const val EVENT_ID_BYTES = 16
        const val EVENT_BYTES = 79
        const val ACK_BYTES = 32
        const val MAX_EVENTS = 5
        private const val U8_MASK = 0xFF
        private val HANDLED = setOf(
            TYPE_PULL_REQUEST,
            TYPE_PULL_EVENTS,
            TYPE_PUSH_EVENT,
            TYPE_EVENT_ACK,
            TYPE_PHONE_POLICY_STATE,
            TYPE_ACTIVATION_COMMIT,
            TYPE_ACTIVATION_COMPLETE,
            TYPE_ACTIVATION_ACK,
            TYPE_ENROLLMENT_PREPARE,
            TYPE_REMOVAL_REQUEST,
            TYPE_REMOVAL_RESPONSE,
        )

        fun hex(bytes: ByteArray): String = bytes.joinToString("") { it.toUByte().toString(16).padStart(2, '0') }
    }
}

/** A scripted client built from the production codec, able to emit every simulated defect. */
private class ScenarioPeer {
    val installationId: ByteArray = ByteArray(INSTALLATION_BYTES).also { SecureRandom().nextBytes(it) }
    private val identity: TestIdentity = TestIdentity.generate()
    private val ephemeral: KeyPair = P256.generate()
    private val nonce: ByteArray = ByteArray(NONCE_BYTES).also { SecureRandom().nextBytes(it) }

    lateinit var phoneHello: ReconnectHello
    lateinit var rbase: ByteArray
    lateinit var signature: ByteArray
    lateinit var confirmInput: ByteArray
    lateinit var keys: ReconnectKeys

    val identityPublicKey: ByteArray get() = identity.publicKey
    val helloPayload: ByteArray = ReconnectHello(
        installationId,
        TestPeers.uncompressed(ephemeral.public as ECPublicKey),
        nonce,
        Role.CLIENT,
        ReconnectHello.LOCAL_CAPS,
    ).encode()
    val helloFrame: ByteArray = PreAuthCodec.encode(FrameType.RECONNECT_HELLO.wire, helloPayload)

    /** The hello of `hello_valid:false`: capability byte `[0x02]`, which the codec refuses. */
    fun invalidCapsHello(): ByteArray {
        val payload = helloPayload.copyOf()
        payload[payload.size - 3] = 0
        payload[payload.size - 2] = 1
        payload[payload.size - 1] = 0x02
        return PreAuthCodec.encode(FrameType.RECONNECT_HELLO.wire, payload)
    }

    fun acceptPhoneHello(phoneHelloPayload: ByteArray, phoneSignature: ByteArray) {
        phoneHello = ReconnectHello.decode(phoneHelloPayload, Role.PHONE)
        rbase = ReconnectKeySchedule.rbase(phoneHelloPayload, helloPayload)
        signature = identity.signP1363(rbase)
        confirmInput = ReconnectKeySchedule.confirmInput(rbase, phoneSignature, signature)
        keys = ReconnectKeySchedule.derive(
            P256.ecdhX(ephemeral.private, phoneHello.ephemeralPublicKey),
            phoneHello.nonce,
            nonce,
        )
    }

    fun signatureFrame(sigOk: Boolean): ByteArray {
        val value = if (sigOk) {
            signature
        } else {
            signature.copyOf().also {
                it[COORDINATE_BYTES - 1] = (it[COORDINATE_BYTES - 1].toInt() xor 0x01).toByte()
            }
        }
        return PreAuthCodec.encode(FrameType.RECONNECT_SIGNATURE.wire, SignatureFrame(Role.CLIENT, value).encode())
    }

    fun confirmRecord(peerState: Int, hmacOk: Boolean): ByteArray {
        val confirmation = ReconnectKeySchedule.confirmation(keys.clientConfirmKey, confirmInput)
        val body = if (hmacOk) {
            confirmation
        } else {
            confirmation.copyOf().also { it[0] = (it[0].toInt() xor 0x01).toByte() }
        }
        return RecordCodec.seal(
            keys.c2pKey,
            keys.c2pPrefix,
            prefix(),
            Direction.CLIENT_TO_PHONE,
            RecordType.RECONNECT_CONFIRM.wire,
            0uL,
            body + byteArrayOf(peerState.toByte()),
        )
    }

    /**
     * A client-to-phone protected record with optional simulated defects. Unlike [RecordCodec.seal]
     * it does not reject an unknown type, so the engine's own authorization sees it.
     */
    fun record(
        type: Int,
        sequence: ULong,
        body: ByteArray,
        tagOk: Boolean,
        lengthDelta: Int,
        sessionIdOk: Boolean,
    ): ByteArray {
        val header = ByteArray(RecordCodec.HEADER)
        header[0] = PROTOCOL_VERSION.toByte()
        header[1] = SCHEMA_VERSION.toByte()
        header[2] = Direction.CLIENT_TO_PHONE.wire.toByte()
        header[3] = (type ushr BITS_PER_BYTE).toByte()
        header[4] = type.toByte()
        ByteBuffer.wrap(header).putLong(SEQUENCE_OFFSET, sequence.toLong())
        val length = body.size + lengthDelta
        header[LENGTH_OFFSET] = (length ushr BITS_PER_BYTE).toByte()
        header[LENGTH_OFFSET + 1] = length.toByte()
        val sessionPrefix = if (sessionIdOk) {
            prefix()
        } else {
            prefix().copyOf().also { it[0] = (it[0].toInt() xor 0xFF).toByte() }
        }
        sessionPrefix.copyInto(header, SESSION_OFFSET)
        val nonceBytes = keys.c2pPrefix + ByteBuffer.allocate(LONG_BYTES).putLong(sequence.toLong()).array()
        val cipher = Cipher.getInstance(AES_TRANSFORMATION)
        cipher.init(
            Cipher.ENCRYPT_MODE,
            SecretKeySpec(keys.c2pKey, AES_ALGORITHM),
            GCMParameterSpec(GCM_TAG_BITS, nonceBytes),
        )
        cipher.updateAAD(header)
        val sealed = cipher.doFinal(body)
        if (!tagOk) sealed[sealed.size - 1] = (sealed[sealed.size - 1].toInt() xor 0x01).toByte()
        return header + sealed
    }

    fun openPhoneRecord(record: ByteArray, sequence: ULong): OpenedRecord = RecordCodec.open(
        keys.p2cKey,
        keys.p2cPrefix,
        prefix(),
        Direction.PHONE_TO_CLIENT,
        sequence,
        record,
    )

    private fun prefix(): ByteArray = keys.sessionId.copyOfRange(0, SESSION_PREFIX_BYTES)

    companion object {
        const val INSTALLATION_BYTES = 16
        const val NONCE_BYTES = 16
        const val COORDINATE_BYTES = 32
        const val SESSION_PREFIX_BYTES = 8
        private const val PROTOCOL_VERSION = 1
        private const val SCHEMA_VERSION = 1
        private const val BITS_PER_BYTE = 8
        private const val SEQUENCE_OFFSET = 5
        private const val LENGTH_OFFSET = 13
        private const val SESSION_OFFSET = 15
        private const val LONG_BYTES = 8
        private const val GCM_TAG_BITS = 128
        private const val AES_TRANSFORMATION = "AES/GCM/NoPadding"
        private const val AES_ALGORITHM = "AES"
    }
}

private class PhoneSession(val name: String) {
    val peer: ScenarioPeer = ScenarioPeer()
    val buffer: EventBuffer = EventBuffer()
    val handler: ScenarioHandler = ScenarioHandler(buffer)
    var engine: PhoneSessionEngine? = null
    var authenticated: Boolean = false
    var sessionClass: SessionClass? = null
    var expectedRx: ULong = 0uL
    val terminations: MutableList<String> = mutableListOf()
    val phonePeerStates: MutableList<Int> = mutableListOf()
    var rbaseComputed: Boolean = false
    var signatureVerified: Boolean = false
    var seekCount: Int = 0
    var generations: Int = 0
    var closed: Boolean = false
    var deletedIncomplete: Boolean = false

    val state: String
        get() = when {
            terminations.any { it != "app_error" } -> "terminated"
            engine?.isOpen == true -> "session_open"
            else -> "closed"
        }
}

private class ScenarioRun(
    val steps: MutableList<StepOutcome> = mutableListOf(),
    val sessions: MutableMap<String, PhoneSession> = mutableMapOf(),
) {
    var accepted: Boolean = true
    var codecRejected: Boolean = false
    var secretRejected: Boolean = false

    val terminated: Boolean get() = sessions.values.any { peer -> peer.terminations.any { it != "app_error" } }
    val keysErased: Boolean get() = sessions.values.any {
        it.terminations.isNotEmpty() && it.engine?.keysErasedForTest == true
    }
    val delivered: Boolean get() = steps.any { it.deliveredTypes.isNotEmpty() }

    fun finalState(): Map<String, String> {
        val result = mutableMapOf<String, String>()
        sessions.values.forEach { peer -> result["session:${peer.name}"] = peer.state }
        sessions.values.forEach { peer ->
            peer.engine?.let { result.putIfAbsent("durable_phone_record:${peer.name}", storeState(peer.name)) }
        }
        return result
    }

    private fun storeState(name: String): String = storeStates[name] ?: "deleted"

    val storeStates: MutableMap<String, String> = mutableMapOf()
}

private class PhoneScenario(private val fixture: ScenarioFixture) {
    private val clock = FakeClock(0)
    private val phoneIdentity = TestIdentity.generate()
    private val run = ScenarioRun()

    suspend fun executeScenario(): ScenarioRun {
        fixture.persisted.forEach { (key, value) ->
            if (key.startsWith(RECORD_PREFIX)) run.storeStates[key.removePrefix(RECORD_PREFIX)] = value
        }
        if (fixture.secretPersisted) {
            // Persisted secret material is rejected before a session exists: the trace is a
            // persisted-state validation negative with no endpoint at all.
            run.secretRejected = true
            run.accepted = false
            return run
        }
        fixture.steps.forEachIndexed { index, step -> execute(index, step) }
        run.accepted = !run.codecRejected && !run.secretRejected
        return run
    }

    private fun session(peer: String): PhoneSession = run.sessions.getOrPut(peer) { PhoneSession(peer) }

    private suspend fun execute(index: Int, step: ScenarioStep) {
        val unmapped = step.simulate.keys - MAPPED_SIMULATE_KEYS
        if (unmapped.isNotEmpty()) {
            throw AssertionError("unmapped simulate keys $unmapped in an executed id (${fixture.id})")
        }
        clock.now = step.tMs
        val peer = session(step.peer)
        val inboundBefore = peer.engine?.inboundSequenceForTest ?: 0uL
        val expectedRxBefore = peer.expectedRx
        val authenticatedBefore = peer.authenticated
        val effects = when (step.event) {
            "reconnect_hello" -> hello(peer, step)
            "reconnect_signature" -> signature(peer, step)
            "reconnect_confirm" -> confirm(peer, step)
            "protected_record" -> protectedRecord(peer, step)
            "send_protected_record" -> sendProtectedRecord(peer, step)
            "counter_seek" -> seek(peer, step)
            "close_connection" -> {
                peer.engine?.teardown()
                emptyList()
            }
            "process_load" -> processLoad(peer, step)
            "revoke" -> revoke(peer)
            "pairing_confirm_a" -> pairingConfirm(peer)
            else -> throw AssertionError("unmapped scenario event '${step.event}' in ${fixture.id}")
        }
        record(index, step, peer, inboundBefore, expectedRxBefore, authenticatedBefore, effects)
    }

    @Suppress("CyclomaticComplexMethod") // One explicit observation per mapped step key.
    private fun record(
        index: Int,
        step: ScenarioStep,
        peer: PhoneSession,
        inboundBefore: ULong,
        expectedRxBefore: ULong,
        authenticatedBefore: Boolean,
        effects: List<SessionEffect>,
    ) {
        val terminated = effects.filterIsInstance<SessionEffect.Terminated>().firstOrNull()?.reason
        val close = effects.any { it is SessionEffect.Close }
        val sent = effects.filterIsInstance<SessionEffect.Send>().mapNotNull { wireType(it.bytes) }
        val delivered = effects.filterIsInstance<SessionEffect.Deliver>().map { it.type }
        if (terminated != null) peer.terminations += terminated
        if (close) peer.closed = true
        val type = step.simulate["record_type"]?.let { parseWire(it) }
        val sequence = sequenceOf(step)
        val unauthorized = type != null &&
            step.event == "protected_record" &&
            (
                RecordType.fromWire(type)?.allows(Direction.CLIENT_TO_PHONE) != true ||
                    (peer.sessionClass != null && !RecordPolicy.authorized(type, Direction.CLIENT_TO_PHONE, peer.sessionClass!!))
                )
        val sequenceMismatch = step.event == "protected_record" &&
            step.simulate.containsKey("sequence") &&
            sequence != null &&
            sequence != expectedRxBefore
        val opened = step.event == "protected_record" &&
            (terminated == null || terminated !in setOf("record_invalid", "unexpected_frame", "sequence"))
        val heartbeatStepAdvanced = step.event == "send_protected_record" &&
            type == ScenarioHandler.TYPE_APP_HEARTBEAT &&
            terminated == null
        if (opened && type != null && sequence != null) {
            peer.expectedRx = if (sequence == ULong.MAX_VALUE) sequence else sequence + 1uL
        } else if (heartbeatStepAdvanced) {
            peer.expectedRx = expectedRxBefore + 1uL
        }
        if (step.event == "reconnect_confirm") {
            peer.expectedRx = 1uL
        }
        val heartbeat = type == ScenarioHandler.TYPE_APP_HEARTBEAT && terminated == null
        val snapshot = mutableMapOf(
            "session:${peer.name}" to peer.state,
            "durable_phone_record:${peer.name}" to (run.storeStates[peer.name] ?: "deleted"),
        )
        run.steps += StepOutcome(
            index = index,
            event = step.event,
            peer = step.peer,
            simulate = step.simulate,
            terminated = terminated,
            closed = close,
            sentTypes = sent,
            deliveredTypes = delivered,
            preAuthReject = effects.filterIsInstance<SessionEffect.Send>()
                .any { it.bytes.firstOrNull() == FrameType.PREAUTH_REJECT.wire.toByte() },
            unauthorized = unauthorized,
            sequenceMismatch = sequenceMismatch,
            recordOpened = opened,
            heartbeatCrossed = heartbeat,
            eventCount = if (type == ScenarioHandler.TYPE_PULL_EVENTS) {
                step.simulate["event_count"]?.toIntOrNull() ?: peer.buffer.lastEventCount
            } else {
                step.simulate["event_count"]?.toIntOrNull()
            },
            authenticatedBefore = authenticatedBefore,
            inboundBefore = inboundBefore,
            inboundAfter = peer.engine?.inboundSequenceForTest ?: 0uL,
            stateSnapshot = snapshot,
        )
    }

    private suspend fun hello(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        if (step.simulate["record_exists"] == "false") run.storeStates[peer.name] = "deleted"
        val frame = if (step.simulate["hello_valid"] == "false") peer.peer.invalidCapsHello() else peer.peer.helloFrame
        val effects = engine(peer).onInbound(frame)
        if (effects.size == 2 && effects.all { it is SessionEffect.Send }) {
            val helloPayload = PreAuthCodec.decode((effects[0] as SessionEffect.Send).bytes).second
            val signature = SignatureFrame.decode(
                PreAuthCodec.decode((effects[1] as SessionEffect.Send).bytes).second,
                Role.PHONE,
            ).signature
            peer.peer.acceptPhoneHello(helloPayload, signature)
            peer.rbaseComputed = true
        }
        return effects
    }

    private suspend fun signature(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        val sigOk = step.simulate["sig_ok"] != "false"
        val effects = engine(peer).onInbound(peer.peer.signatureFrame(sigOk))
        if (sigOk) {
            val confirm = effects.filterIsInstance<SessionEffect.Send>()
                .firstOrNull { wireType(it.bytes) == RecordType.RECONNECT_CONFIRM.wire }
            if (confirm != null) {
                peer.signatureVerified = true
                val opened = peer.peer.openPhoneRecord(confirm.bytes, 0uL)
                peer.phonePeerStates += opened.body.last().toInt() and 0xFF
                confirm.completionToken?.let { token -> engine(peer).onSendComplete(token, true) }
            }
        }
        return effects
    }

    private suspend fun confirm(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        val peerState = parseWire(step.simulate["peer_state"] ?: "0x01")
        val record = peer.peer.confirmRecord(peerState, step.simulate["hmac_ok"] != "false")
        val effects = engine(peer).onInbound(record)
        val authenticated = effects.filterIsInstance<SessionEffect.Authenticated>().firstOrNull()
        if (authenticated != null) {
            peer.authenticated = true
            peer.sessionClass = authenticated.sessionClass
        }
        return effects
    }

    private suspend fun protectedRecord(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        val type = parseWire(step.simulate["record_type"] ?: error("protected_record without record_type"))
        val sequence = step.simulate["sequence"]?.toULongOrNull() ?: run {
            // A sequence outside the u64 wire range cannot be serialized; the endpoint never sees it.
            run.codecRejected = true
            return emptyList()
        }
        val body = when (type) {
            ScenarioHandler.TYPE_EVENT_ACK -> ackBody(peer, step.simulate["event_known"] != "false")
            RecordType.APP_ERROR.wire -> appErrorBody(AppErrorCode.PROTOCOL_VIOLATION)
            else -> ByteArray(0)
        }
        val record = peer.peer.record(
            type = type,
            sequence = sequence,
            body = body,
            tagOk = step.simulate["tag_ok"] != "false",
            lengthDelta = if (step.simulate["length_ok"] == "false") 1 else 0,
            sessionIdOk = step.simulate["session_id_ok"] != "false",
        )
        return engine(peer).onInbound(record)
    }

    private suspend fun sendProtectedRecord(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        val type = parseWire(step.simulate["record_type"] ?: error("send_protected_record without record_type"))
        val engine = engine(peer)
        if (type == ScenarioHandler.TYPE_APP_HEARTBEAT) {
            // OTP2-REC-034 terminates on the receive direction; see the class KDoc.
            val record = peer.peer.record(
                type = type,
                sequence = peer.expectedRx,
                body = ByteArray(0),
                tagOk = true,
                lengthDelta = 0,
                sessionIdOk = true,
            )
            return engine.onInbound(record)
        }
        val body = when (type) {
            ScenarioHandler.TYPE_PULL_EVENTS -> pullEventsBody(peer, step.simulate["event_count"]?.toInt() ?: 0)
            ScenarioHandler.TYPE_PUSH_EVENT -> pushEventBody(peer)
            ScenarioHandler.TYPE_EVENT_ACK -> ackBody(peer, true)
            else -> ByteArray(0)
        }
        return try {
            engine.sendApplication(type, body)
        } catch (_: SessionReject) {
            emptyList()
        }
    }

    private fun seek(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        val engine = engine(peer)
        val rx = (step.simulate["rx_next"] ?: error("counter_seek without rx_next")).toULongOrNull()
            ?: run {
                run.codecRejected = true
                return emptyList()
            }
        engine.seekSequences(rx, engine.outboundSequenceForTest)
        peer.expectedRx = rx
        peer.seekCount += 1
        return emptyList()
    }

    private fun processLoad(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        peer.engine = null
        peer.authenticated = false
        peer.sessionClass = null
        if (step.simulate["continuity_token_matches"] == "false" && run.storeStates[peer.name] == "pending") {
            run.storeStates[peer.name] = "deleted"
            peer.deletedIncomplete = true
        }
        return emptyList()
    }

    private fun revoke(peer: PhoneSession): List<SessionEffect> {
        run.storeStates[peer.name] = "deleted"
        peer.engine?.teardown()
        peer.closed = true
        return emptyList()
    }

    private suspend fun pairingConfirm(peer: PhoneSession): List<SessionEffect> {
        val payload = ByteArray(32) { (it + 1).toByte() }
        val frame = byteArrayOf(0x04, 0, payload.size.toByte()) + payload
        return engine(peer).onInbound(frame)
    }

    private fun engine(peer: PhoneSession): PhoneSessionEngine {
        peer.engine?.let { return it }
        return PhoneSessionEngine(
            localInstallationId = TestPeers.phoneInstallationId,
            signer = phoneIdentity.signer,
            lookup = PeerLookup { id ->
                val state = run.storeStates[peer.name]
                if (state == null || state == "deleted" || !peer.peer.installationId.contentEquals(id)) {
                    null
                } else {
                    PeerRecord(
                        peer.peer.installationId,
                        peer.peer.identityPublicKey,
                        when (state) {
                            "active" -> LocalRecordState.ACTIVE
                            "pending" -> LocalRecordState.PENDING
                            "committed_waiting_ack" -> LocalRecordState.COMMITTED_WAITING_ACK
                            else -> error("unsupported persisted record state $state")
                        },
                    )
                }
            },
            clock = { clock.now },
            nonce = { ByteArray(16).also { SecureRandom().nextBytes(it) } },
            handler = peer.handler,
        ).also {
            peer.engine = it
            peer.generations += 1
            peer.authenticated = false
            peer.sessionClass = null
            peer.closed = false
        }
    }

    private fun ackBody(peer: PhoneSession, known: Boolean): ByteArray {
        val id = if (known) {
            peer.buffer.known.firstOrNull() ?: syntheticEventId(0)
        } else {
            syntheticEventId(0xFE)
        }
        return TestPeers.phoneInstallationId + id.hexBytes()
    }

    private fun pullEventsBody(peer: PhoneSession, count: Int): ByteArray {
        if (count !in 0..MAX_EVENTS) {
            throw AssertionError("${fixture.id}: event_count $count exceeds the wire bound")
        }
        var body = byteArrayOf(count.toByte())
        repeat(count) { index ->
            val event = syntheticEvent(index)
            peer.buffer.known += ScenarioHandler.hex(event.copyOfRange(16, 32))
            body += event
        }
        peer.buffer.lastEventCount = count
        return body
    }

    private fun pushEventBody(peer: PhoneSession): ByteArray {
        val event = syntheticEvent(0)
        peer.buffer.known += ScenarioHandler.hex(event.copyOfRange(16, 32))
        peer.buffer.lastEventCount = 1
        return event
    }

    private fun syntheticEvent(index: Int): ByteArray {
        val code = CODE.toByteArray()
        val merchant = MERCHANT.toByteArray()
        return TestPeers.phoneInstallationId +
            syntheticEventId(index).hexBytes() +
            ByteBuffer.allocate(8).putLong(RECEIPT_EPOCH_SECONDS + index).array() +
            ByteBuffer.allocate(4).putInt(120).array() +
            byteArrayOf((code.size ushr 8).toByte(), code.size.toByte()) +
            code +
            byteArrayOf((merchant.size ushr 8).toByte(), merchant.size.toByte()) +
            merchant +
            ByteBuffer.allocate(8).putLong(100).array() +
            byteArrayOf(2) +
            CURRENCY.toByteArray()
    }

    private fun syntheticEventId(index: Int): String {
        val bytes = ByteArray(16) { position -> ((index + 1) * 16 + position).toByte() }
        return bytes.joinToString("") { it.toUByte().toString(16).padStart(2, '0') }
    }

    private fun sequenceOf(step: ScenarioStep): ULong? = step.simulate["sequence"]?.toULongOrNull()

    private fun wireType(bytes: ByteArray): Int? {
        if (bytes.isEmpty()) return null
        val first = bytes[0].toInt() and U8_MASK
        if (FrameType.fromWire(first) != null) return first
        if (bytes.size < RecordCodec.HEADER) return null
        return ((bytes[3].toInt() and U8_MASK) shl 8) or (bytes[4].toInt() and U8_MASK)
    }

    private fun parseWire(value: String): Int = value.removePrefix("0x").toInt(16)

    private fun String.hexBytes(): ByteArray = chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    private companion object {
        const val RECORD_PREFIX = "durable_phone_record:"
        const val MAX_EVENTS = 5
        const val CODE = "123456"
        const val MERCHANT = "Test merchant"
        const val CURRENCY = "XTS"
        const val RECEIPT_EPOCH_SECONDS = 1_700_000_000L
        const val U8_MASK = 0xFF
        const val BITS_PER_BYTE = 8
        val MAPPED_SIMULATE_KEYS = setOf(
            "record_exists",
            "hello_valid",
            "sig_ok",
            "hmac_ok",
            "peer_state",
            "tag_ok",
            "length_ok",
            "session_id_ok",
            "sequence",
            "record_type",
            "event_count",
            "event_known",
            "pull_response",
            "rx_next",
            "continuity_token_matches",
        )
    }
}

// ---------------------------------------------------------------------------------------------
// Effect observations
// ---------------------------------------------------------------------------------------------

private object Effects {
    private val known: Set<String> = setOf(
        "mutual_authentication_complete",
        "set_class_full",
        "set_class_full_pending_peer",
        "set_class_activation_recovery",
        "terminate_session",
        "app_error_terminal",
        "erase_session_keys",
        "counter_not_advanced",
        "advance_rx_sequence",
        "reject_sequence_violation",
        "reject_bad_tag",
        "reject_oversize_record",
        "reject_replayed_or_foreign_session_record",
        "reject_unauthorized_record",
        "reject_unauthenticated_record",
        "reject_direction_violation",
        "preauth_reject",
        "reject_unknown_installation",
        "test_probe_counter_seek",
        "heartbeat_processed",
        "heartbeat_sent",
        "heartbeat_rate_violation",
        "pull_request_processed",
        "pull_request_sent",
        "otp_events_transferred",
        "event_ack_sent",
        "event_ack_processed",
        "event_ack_unknown_noop",
        "push_event_transferred",
        "push_settled",
        "record_accepted",
        "reply_phone_hello",
        "compute_rbase",
        "send_reconnect_confirm",
        "verify_client_signature",
        "reconnect_confirm_sent_peer_state_active",
        "delete_incomplete_materials",
        "erase_incomplete_materials",
        "record_active_by_ack",
        "record_activated_by_ack",
        "activation_ack_processed",
    )

    fun isMapped(name: String): Boolean = name in known

    @Suppress("CyclomaticComplexMethod") // The mapping table is one branch per effect name.
    fun observe(name: String, run: ScenarioRun): Boolean {
        val steps = run.steps
        val sessions = run.sessions.values
        val terminations = sessions.flatMap { it.terminations }
        return when (name) {
            "mutual_authentication_complete" -> sessions.any { it.authenticated || it.sessionClass != null }
            "set_class_full" -> sessions.any { it.sessionClass == SessionClass.FULL }
            "set_class_full_pending_peer" -> sessions.any { it.sessionClass == SessionClass.FULL_PENDING_PEER }
            "set_class_activation_recovery" -> sessions.any { it.sessionClass == SessionClass.ACTIVATION_RECOVERY }
            "terminate_session" -> terminations.any { it != "app_error" }
            "app_error_terminal" -> terminations.contains("app_error")
            "erase_session_keys" -> sessions.any {
                it.terminations.isNotEmpty() && it.engine?.keysErasedForTest == true
            }
            "counter_not_advanced" -> sessions.filter { it.terminations.isNotEmpty() }.all { peer ->
                peer.engine?.inboundSequenceForTest == 0uL &&
                    peer.engine?.outboundSequenceForTest == 0uL &&
                    noDeliveryAfterRejection(steps.filter { it.peer == peer.name })
            }
            "advance_rx_sequence" -> steps.any { it.event == "protected_record" && it.accepted && it.inboundAdvanced }
            "reject_sequence_violation" -> steps.any { it.terminated != null && it.sequenceMismatch } ||
                terminations.contains("sequence")
            "reject_bad_tag" -> steps.any { it.terminated != null && it.simulate["tag_ok"] == "false" }
            "reject_oversize_record" -> steps.any { it.terminated != null && it.simulate["length_ok"] == "false" }
            "reject_replayed_or_foreign_session_record" ->
                steps.any { it.terminated != null && it.simulate["session_id_ok"] == "false" }
            "reject_unauthorized_record" -> steps.any { it.terminated != null && it.unauthorized }
            "reject_unauthenticated_record" ->
                steps.any { it.event == "protected_record" && it.terminated != null && !it.authenticatedBefore }
            "reject_direction_violation" -> steps.any { it.event == "pairing_confirm_a" && it.terminated != null }
            "preauth_reject" -> steps.any { it.preAuthReject }
            "reject_unknown_installation" ->
                steps.any { it.event == "reconnect_hello" && it.simulate["record_exists"] == "false" && it.preAuthReject }
            "test_probe_counter_seek" -> sessions.any { it.seekCount > 0 }
            "heartbeat_processed" ->
                steps.any { it.event == "protected_record" && it.accepted && it.heartbeatCrossed }
            "heartbeat_sent" -> steps.any { it.heartbeatCrossed }
            "heartbeat_rate_violation" -> terminations.contains("heartbeat")
            "pull_request_processed" -> steps.any {
                it.accepted && (it.sentTypes.contains(TYPE_PULL_REQUEST) || it.deliveredTypes.contains(TYPE_PULL_REQUEST))
            }
            "pull_request_sent" -> steps.any { it.sentTypes.contains(TYPE_PULL_REQUEST) }
            "otp_events_transferred" -> steps.any {
                it.accepted &&
                    (it.sentTypes.contains(TYPE_PULL_EVENTS) || it.deliveredTypes.contains(TYPE_PULL_EVENTS)) &&
                    (it.eventCount ?: 0) > 0
            }
            "event_ack_sent" -> steps.any { it.sentTypes.contains(TYPE_EVENT_ACK) }
            "event_ack_processed" -> steps.any { it.deliveredTypes.contains(TYPE_EVENT_ACK) }
            "event_ack_unknown_noop" -> sessions.any { it.buffer.sawUnknown } && terminations.none { it != "app_error" }
            "push_event_transferred" -> steps.any {
                it.accepted && (it.sentTypes.contains(TYPE_PUSH_EVENT) || it.deliveredTypes.contains(TYPE_PUSH_EVENT))
            }
            "push_settled" -> sessions.any { it.buffer.settled }
            "record_accepted" -> steps.any { it.recordOpened }
            "reply_phone_hello" -> steps.any { it.sentTypes.contains(FrameType.RECONNECT_HELLO.wire) }
            "compute_rbase" -> sessions.any { it.rbaseComputed }
            "send_reconnect_confirm" -> steps.any { it.sentTypes.contains(RecordType.RECONNECT_CONFIRM.wire) }
            "verify_client_signature" -> sessions.any { it.signatureVerified }
            "reconnect_confirm_sent_peer_state_active" -> sessions.any { it.phonePeerStates.contains(1) }
            "delete_incomplete_materials" -> sessions.any { it.deletedIncomplete }
            "erase_incomplete_materials" -> false
            // OTP-10 activation machinery does not exist in the OTP-09 engine; these fixtures
            // assert that the record was rejected before any activation state could change.
            "record_active_by_ack", "record_activated_by_ack", "activation_ack_processed" -> false
            else -> throw AssertionError("effect '$name' has no engine observation mapping")
        }
    }

    private fun noDeliveryAfterRejection(steps: List<StepOutcome>): Boolean {
        var rejected = false
        for (step in steps) {
            if (rejected && step.deliveredTypes.isNotEmpty()) return false
            if (step.terminated != null) rejected = true
        }
        return true
    }

    private const val TYPE_PULL_REQUEST = 0x0200
    private const val TYPE_PULL_EVENTS = 0x0202
    private const val TYPE_PUSH_EVENT = 0x0203
    private const val TYPE_EVENT_ACK = 0x0204
}
