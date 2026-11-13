package me.nagaev.veles.session

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import me.nagaev.veles.pairing.ActivationEngine
import me.nagaev.veles.pairing.ProofRegistry
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import me.nagaev.veles.trust.peerFingerprint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.security.KeyPair
import java.security.SecureRandom
import java.security.interfaces.ECPublicKey
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * OTP-09 Task 10 conformance runner for the phone engine.
 *
 * Every in-scope `records.json` / `reconnect.json` scenario is replayed against the real
 * [PhoneSessionEngine]; the scenario inventory (`protocol/conformance/otp-09-scenarios.json`)
 * decides which ids execute, which are endpoint-effect checks for trace-validation negatives,
 * and which are deferred with an owning task. The OTP-10 inventory
 * (`protocol/conformance/otp-10-scenarios.json`) owns the `enrollment.json` traces and the ids
 * OTP-09 deferred to OTP-10: its entry for an id overrides the OTP-09 `deferred:OTP-10` entry,
 * and no other overlap between the two files is allowed. `enrollment.json` ids stay with the
 * OTP-10 enrollment runners, so this runner only executes `records.json` / `reconnect.json` ids.
 * The TypeScript runner drives the client engine over the same merged inventory.
 *
 * The harness follows the plan's step mapping: [ScenarioPeer] is a scripted client built from
 * the same codec, [ScenarioHandler] is passive (it records deliveries, acks and buffer state
 * but never replies on its own), and every `send_protected_record` step is exactly one
 * `sendApplication` call. One deliberate, inventory-recorded adaptation:
 * `st-rec-heartbeat-rate-terminates` is an `engine-effect` scenario for OTP2-REC-034's *receive*
 * direction, because design D4 makes early outbound heartbeats non-fatal; its five heartbeat
 * steps are therefore injected as peer records and the scenario asserts termination, erasure and
 * no delivery instead of claiming sends the engine never makes.
 */
class ScenarioConformanceTest {
    private val inventory = ScenarioFixtures.inventory
    private val otp10Inventory = ScenarioFixtures.otp10Inventory
    private val effectiveInventory = ScenarioFixtures.effectiveInventory
    private val executionInventory = ScenarioFixtures.executionInventory

    @Test
    fun inventoryCoversEveryFixtureId() {
        // Coverage is per fixture file: the OTP-09 inventory owns records.json/reconnect.json,
        // the OTP-10 inventory owns enrollment.json (minus the five OTP-02 fixture self-checks)
        // and the ids OTP-09 deferred to OTP-10.
        assertTrue(
            "the OTP-09 inventory must list records/reconnect ids only",
            inventory.keys.all { it in ScenarioFixtures.sessionIds },
        )
        assertEquals(
            "every records/reconnect fixture id needs an OTP-09 entry or an OTP-10 override",
            ScenarioFixtures.sessionIds,
            inventory.keys + otp10Inventory.keys.intersect(ScenarioFixtures.sessionIds),
        )
        assertEquals(
            "every enrollment fixture id except the self-checks must have an OTP-10 entry",
            ScenarioFixtures.enrollmentIds - FIXTURE_SELF_CHECK_IDS,
            otp10Inventory.keys - ScenarioFixtures.sessionIds,
        )
        inventory.forEach { (id, entry) -> assertClassification("otp-09", id, entry, OTP09_DEFERRAL) }
        otp10Inventory.forEach { (id, entry) ->
            assertClassification("otp-10", id, entry, OTP10_DEFERRAL)
        }
        assertEquals("execute scenarios", 21, inventory.values.count { it == "execute" })
        assertTrue(
            "the OTP-09 inventory must defer explicit work",
            inventory.values.any { it is String && it.startsWith("deferred:") },
        )
    }

    @Test
    fun otp10InventoryOwnsTheFormerOtp09DeferralsWithoutOverlap() {
        // OTP-10 is implemented: the ten former OTP-09 deferrals now live only in the
        // OTP-10 inventory (no OTP-09 entry may still override them).
        val deferredToOtp10 = inventory.filterValues { it == "deferred:OTP-10" }.keys
        assertEquals(
            "an OTP-10 entry may only override the OTP-09 deferred:OTP-10 entry of the same id",
            deferredToOtp10,
            otp10Inventory.keys.intersect(inventory.keys),
        )
        assertTrue(
            "an OTP-10 entry must never override an executed OTP-09 id",
            otp10Inventory.keys.intersect(inventory.filterValues { it == "execute" }.keys).isEmpty(),
        )
        assertEquals(
            "the OTP-10 inventory owns exactly the ten recovery ids OTP-09 deferred",
            10,
            otp10Inventory.keys.count { it in ScenarioFixtures.sessionIds },
        )
    }

    @Test
    fun nothingIsDeferredToOtp10() {
        // OTP-10 is implemented: the merged inventory owns every former deferral as an
        // executed scenario or an explicit engine-effect assertion.
        assertTrue(
            "no OTP-09 entry may still defer to OTP-10",
            inventory.values.none { it == "deferred:OTP-10" },
        )
        assertTrue(
            "no OTP-10 entry may defer to a plan task",
            otp10Inventory.values.none { it is String && OTP10_DEFERRAL.matches(it) },
        )
    }

    @Test
    fun enrollmentTracesBelongToTheEnrollmentRunners() {
        assertEquals(
            "the five fixture self-checks stay in the OTP-02 runner",
            5,
            ScenarioFixtures.enrollment.count { it.id in FIXTURE_SELF_CHECK_IDS },
        )
        assertTrue(
            "the OTP-10 inventory must not claim a fixture self-check",
            otp10Inventory.keys.intersect(FIXTURE_SELF_CHECK_IDS).isEmpty(),
        )
        assertTrue(
            "the OTP-09 inventory must never claim an enrollment.json id",
            inventory.keys.intersect(ScenarioFixtures.enrollmentIds).isEmpty(),
        )
        assertEquals(
            "the two inventories cover every session and enrollment fixture id exactly once",
            ScenarioFixtures.sessionIds + ScenarioFixtures.enrollmentIds - FIXTURE_SELF_CHECK_IDS,
            effectiveInventory.keys,
        )
    }

    @Test
    fun everyExecutedPhoneScenarioPasses() {
        executionInventory.filterValues { it == "execute" }.forEach { (id, _) ->
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
        executionInventory.filterValues { it is JsonObject }.forEach { (id, entry) ->
            val fixture = ScenarioFixtures.byId(id)
            if (fixture.role != "phone") return@forEach
            val run = runScenario(fixture)
            val expected = ((entry as JsonObject)["engine-effect"] as JsonObject)
            assertEquals("$id terminate", expected.bool("terminate"), run.terminated)
            assertEquals("$id erase", expected.bool("erase"), run.keysErased)
            assertEquals("$id deliver", expected.bool("deliver"), run.delivered)
            if (fixture.expected["stage"] != null) {
                assertEquals(
                    "$id is a trace-validation negative",
                    "false",
                    fixture.expected["accepted"]?.toString(),
                )
            }
        }
    }

    @Test
    fun heartbeatRateScenarioIsClassifiedAsReceiveSideEngineEffect() {
        val entry = inventory["st-rec-heartbeat-rate-terminates"]
        assertTrue("heartbeat rate must carry an explicit endpoint", entry is JsonObject)
        val effect = (entry as JsonObject)["engine-effect"] as JsonObject
        assertTrue("heartbeat rate terminates", effect.bool("terminate"))
        assertTrue("heartbeat rate erases keys", effect.bool("erase"))
        assertTrue("heartbeat rate delivers nothing", !effect.bool("deliver"))
        println(
            "st-rec-heartbeat-rate-terminates -> engine-effect on the OTP2-REC-034 receive " +
                "direction: D4 makes early outbound heartbeats non-fatal, so the five heartbeats " +
                "are injected as peer records",
        )
    }

    @Test
    fun deferredScenariosArePrintedWithTheirOwningTask() {
        effectiveInventory.filterValues { it is String && it.startsWith("deferred:") }
            .toSortedMap()
            .forEach { (id, owner) ->
                println("deferred $id -> ${(owner as String).removePrefix("deferred:")}")
            }
    }

    @Test
    fun everyEffectNameInAnExecutedScenarioIsMapped() {
        executionInventory.filterValues { it == "execute" || it is JsonObject }.keys.forEach { id ->
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
        val executed = executionInventory.filterValues { it == "execute" }
            .keys
            .map { ScenarioFixtures.byId(it) }
        val phone = executed.filter { it.role == "phone" }
        val client = executed.filter { it.role == "client" }
        assertEquals(
            "every execute id must belong to exactly one engine runner",
            executed.size,
            phone.size + client.size,
        )
        // The client-id set is read from the inventory (the fixture role of every execute entry),
        // not a fixed list, so an OTP-10 entry that flips a recovery trace to `execute` reaches the
        // TypeScript runner without editing this test.
        val inventoryClientIds = executionInventory.filterValues { it == "execute" }
            .keys
            .map { ScenarioFixtures.byId(it) }
            .filter { it.role == "client" }
            .map { it.id }
            .toSet()
        assertTrue("the inventory must drive at least one client scenario", inventoryClientIds.isNotEmpty())
        assertEquals(
            "the client engine scenarios are the TypeScript runner's set",
            inventoryClientIds,
            client.map { it.id }.toSet(),
        )
    }

    private fun assertClassification(source: String, id: String, entry: Any, deferral: Regex) {
        when (entry) {
            is String -> assertTrue(
                "$source $id has an unsupported classification $entry",
                entry == "execute" || deferral.matches(entry),
            )
            is JsonObject -> {
                val effect = entry["engine-effect"] as? JsonObject
                assertNotNull("$source $id engine-effect must be an object", effect)
                listOf("terminate", "erase", "deliver").forEach { key ->
                    assertNotNull("$source $id engine-effect.$key is required", effect?.get(key))
                }
            }
            else -> throw AssertionError("$source $id has an unsupported classification")
        }
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
        var previous = -1
        (effects["order"]?.jsonArray ?: emptyList()).forEach { element ->
            val name = element.toString().trim('"')
            val index = Effects.orderIndex(name, run)
            assertNotNull("$id order $name is observed", index)
            assertTrue("$id order $name occurs after index $previous", index!! >= previous)
            previous = index
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

    private companion object {
        /**
         * The five trace-validation self-checks stay with the OTP-02 fixture runner
         * (`protocol/checks/state.test.mjs`); they exercise the fixture format, not a machine.
         */
        val FIXTURE_SELF_CHECK_IDS = setOf(
            "st-neg-unknown-event",
            "st-neg-nonmonotonic-time",
            "st-neg-timeout-early",
            "st-neg-unknown-machine",
            "st-neg-step-expect-wrong",
        )

        /** The OTP-09 inventory defers implementation work to a later change. */
        val OTP09_DEFERRAL = Regex("deferred:OTP-[0-9]+(?:/[0-9]+)?")

        /** The OTP-10 inventory defers a scenario to the plan task that will execute it. */
        val OTP10_DEFERRAL = Regex("deferred:Task [0-9]+")
    }
}

// ---------------------------------------------------------------------------------------------
// Fixture and inventory reading
// ---------------------------------------------------------------------------------------------

/** The boot marker of the first process and of a later restart (OTP2-STA-010c). */
private const val BOOT_MARKER = "boot"
private const val BOOT_MARKER_RESTART = "boot-restart"

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
    val enrollment: List<ScenarioFixture> = fixtures("/fixtures/enrollment.json")

    /** Enrollment-file traces that drive the reconnect engine, not the pairing machine. */
    val enrollmentSessionIds: Set<String> = setOf("st-rec-no-peer-record-no-recovery")
    val enrollmentSession: List<ScenarioFixture> = enrollment.filter { it.id in enrollmentSessionIds }
    val all: List<ScenarioFixture> = records + reconnect + enrollmentSession
    val sessionIds: Set<String> = (records + reconnect).map { it.id }.toSet()
    val enrollmentIds: Set<String> = enrollment.map { it.id }.toSet()

    val inventory: Map<String, Any> = inventory("/otp-09-scenarios.json")
    val otp10Inventory: Map<String, Any> = inventory("/otp-10-scenarios.json")

    /** An OTP-10 entry of an id overrides the OTP-09 entry of the same id. */
    val effectiveInventory: Map<String, Any> = inventory + otp10Inventory

    /**
     * The OTP-09 runners execute `records.json`/`reconnect.json` ids only; `enrollment.json` ids
     * belong to the OTP-10 enrollment runners.
     */
    val executionInventory: Map<String, Any> =
        effectiveInventory.filterKeys { it in sessionIds || it in enrollmentSessionIds }

    private fun inventory(path: String): Map<String, Any> = read(path).entries.associate { (key, value) ->
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
    val unknownType: Boolean,
    val oversize: Boolean,
    val sequenceMismatch: Boolean,
    val recordOpened: Boolean,
    val heartbeatCrossed: Boolean,
    val eventCount: Int?,
    val authenticatedBefore: Boolean,
    val sessionClassAfter: SessionClass?,
    val expectedRxBefore: ULong,
    val inboundBefore: ULong,
    val inboundAfter: ULong,
    val outboundBefore: ULong,
    val inboundAtTeardown: ULong,
    val outboundAtTeardown: ULong,
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

/** One seeded snapshot; the real `TrustRepository` owns every later transition. */
private class ScenarioTrustStorage(snapshot: TrustSnapshot?) : TrustStorage {
    private var value: TrustSnapshot? = snapshot

    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        val (next, result) = block(value)
        value = next
        return result
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

    fun recovery(): RecoveryKeys = keys.recovery ?: error("this fixture's record carries no ET")
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

    fun acceptPhoneHello(
        phoneHelloPayload: ByteArray,
        phoneSignature: ByteArray,
        et: ByteArray? = null,
    ) {
        phoneHello = ReconnectHello.decode(phoneHelloPayload, Role.PHONE)
        rbase = ReconnectKeySchedule.rbase(phoneHelloPayload, helloPayload)
        signature = identity.signP1363(rbase)
        confirmInput = ReconnectKeySchedule.confirmInput(rbase, phoneSignature, signature)
        keys = ReconnectKeySchedule.derive(
            P256.ecdhX(ephemeral.private, phoneHello.ephemeralPublicKey),
            phoneHello.nonce,
            nonce,
            et,
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
        sessionIdOk: Boolean,
    ): ByteArray {
        val header = ByteArray(RecordCodec.HEADER)
        header[0] = PROTOCOL_VERSION.toByte()
        header[1] = SCHEMA_VERSION.toByte()
        header[2] = Direction.CLIENT_TO_PHONE.wire.toByte()
        header[3] = (type ushr BITS_PER_BYTE).toByte()
        header[4] = type.toByte()
        ByteBuffer.wrap(header).putLong(SEQUENCE_OFFSET, sequence.toLong())
        header[LENGTH_OFFSET] = (body.size ushr BITS_PER_BYTE).toByte()
        header[LENGTH_OFFSET + 1] = body.size.toByte()
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
    var activation: ActivationEngine? = null
    var repository: TrustRepository? = null
    val registry: ProofRegistry = ProofRegistry()
    val activationSends: MutableSet<Int> = mutableSetOf()
    val activationSendSteps: MutableMap<Int, Int> = mutableMapOf()
    var authenticated: Boolean = false
    var sessionClass: SessionClass? = null
    val sessionClasses: MutableList<SessionClass> = mutableListOf()
    var expectedRx: ULong = 0uL
    val terminations: MutableList<String> = mutableListOf()
    val phonePeerStates: MutableList<Int> = mutableListOf()
    var rbaseComputed: Boolean = false
    var signatureVerified: Boolean = false
    var seekCount: Int = 0
    var generations: Int = 0
    var closed: Boolean = false
    var keysErased: Boolean = false
    var deletedIncomplete: Boolean = false
    var revokedTrust: Boolean = false

    /** The real store and its stable identity, reused across a simulated restart. */
    var storage: ScenarioTrustStorage? = null
    var identityKeys: IdentityKeyStore? = null

    /** The boot marker this peer's repository currently reports; a restart changes it. */
    var bootMarker: String = BOOT_MARKER

    val state: String
        get() = when {
            engine?.isOpen == true -> "session_open"
            terminations.any { it != "app_error" } -> "terminated"
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

    /** Every durable phone-record transition, in the order the activation engine applied it. */
    val transitions: MutableList<Pair<String, String>> = mutableListOf()

    val terminated: Boolean get() = sessions.values.any { peer -> peer.terminations.any { it != "app_error" } }
    val keysErased: Boolean get() = sessions.values.any {
        it.terminations.isNotEmpty() && it.engine?.keysErasedForTest == true
    }
    val delivered: Boolean get() = steps.any { it.deliveredTypes.isNotEmpty() }

    fun finalState(): Map<String, String> {
        val result = mutableMapOf<String, String>()
        sessions.values.forEach { peer ->
            if (peer.engine != null) result["session:${peer.name}"] = peer.state
            if (peer.engine != null || peer.name in storeStates) {
                result["durable_phone_record:${peer.name}"] = storeState(peer.name)
            }
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
    private var oversizeBuilt = false

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

    private fun session(peer: String): PhoneSession = run.sessions.getOrPut(peer) {
        PhoneSession(peer).also { created ->
            val persisted = fixture.persisted["$RECORD_PREFIX$peer"]
            if (persisted != null && persisted != "deleted" && persisted != "absent") {
                val createdElapsed = fixture.persisted["created_monotonic_ms:$RECORD_PREFIX$peer"]?.toLongOrNull()
                    ?: clock.now
                created.repository = seedRepository(created, persisted, createdElapsed)
            }
        }
    }

    /**
     * Seeds the real OTP-04 store from `persisted_state`. Every later transition, MAC check and
     * proof consumption runs through [TrustRepository] and this session's [ProofRegistry].
     */
    private fun seedRepository(peer: PhoneSession, state: String, createdElapsed: Long): TrustRepository {
        val local = TestIdentity.generate()
        val et = fixtureEt() ?: ByteArray(HASH_BYTES)
        val clientPublic = TrustEncoding.hex(peer.peer.identityPublicKey)
        val record = TrustedPeer(
            installationId = TrustEncoding.hex(peer.peer.installationId),
            publicKey = clientPublic,
            label = "Desktop",
            deviceType = 1,
            enrollmentTranscript = TrustEncoding.hex(et),
            fingerprint = peerFingerprint(TrustEncoding.hex(local.publicKey), clientPublic),
            state = when (state) {
                "active" -> TrustState.ACTIVE
                "pending" -> TrustState.PENDING
                "committed_waiting_ack" -> TrustState.COMMITTED_WAITING_ACK
                else -> error("unsupported persisted record state $state")
            },
            createdAtMillis = createdElapsed,
            lastSeenMillis = null,
            createdElapsedMillis = createdElapsed,
            bootToken = BOOT_TOKEN,
            stateChangedAtMillis = createdElapsed,
        )
        val snapshot = TrustSnapshot(
            identity = InstallationIdentity(
                TrustEncoding.hex(ByteArray(16) { (0x40 + it).toByte() }),
                TrustEncoding.hex(local.publicKey),
            ),
            peers = listOf(record),
            bootMarker = BOOT_MARKER,
            continuityToken = BOOT_TOKEN,
        )
        val keys = object : IdentityKeyStore {
            override fun publicKey(): String = TrustEncoding.hex(local.publicKey)
            override fun generate(): String = TrustEncoding.hex(local.publicKey)
            override fun sign(message: ByteArray): ByteArray = ByteArray(0)
        }
        peer.storage = ScenarioTrustStorage(snapshot)
        peer.identityKeys = keys
        peer.bootMarker = BOOT_MARKER
        return repository(peer)
    }

    /**
     * One repository over this peer's existing durable storage with its *current* boot marker.
     * A restart rebuilds it this way, so OTP2-STA-010c's continuity rule (not the runner) is
     * what deletes incomplete records.
     */
    private fun repository(peer: PhoneSession): TrustRepository = TrustRepository(
        checkNotNull(peer.storage) { "a seeded store is required" },
        checkNotNull(peer.identityKeys) { "a seeded identity is required" },
        { clock.now },
        { peer.bootMarker },
        { clock.now },
        ActivationVerifier { trusted, step, proof -> peer.registry.consume(trusted, step, proof) },
    )

    /** Projects the real store into the fixture's durable-slot string and returns it. */
    private suspend fun refreshStore(peer: PhoneSession): String {
        val repository = peer.repository
        val state = repository?.peers()
            ?.firstOrNull { it.installationId == TrustEncoding.hex(peer.peer.installationId) }
            ?.state?.let {
                when (it) {
                    TrustState.ACTIVE -> "active"
                    TrustState.PENDING -> "pending"
                    TrustState.COMMITTED_WAITING_ACK -> "committed_waiting_ack"
                }
            } ?: if (run.storeStates[peer.name] == "absent") "absent" else "deleted"
        run.storeStates[peer.name] = state
        return state
    }

    private suspend fun execute(index: Int, step: ScenarioStep) {
        val unmapped = step.simulate.keys - MAPPED_SIMULATE_KEYS
        if (unmapped.isNotEmpty()) {
            throw AssertionError("unmapped simulate keys $unmapped in an executed id (${fixture.id})")
        }
        clock.now = step.tMs
        oversizeBuilt = false
        val peer = session(step.peer)
        val inboundBefore = peer.engine?.inboundSequenceForTest ?: 0uL
        val outboundBefore = peer.engine?.outboundSequenceForTest ?: 0uL
        val expectedRxBefore = peer.expectedRx
        val authenticatedBefore = peer.authenticated
        val storeBefore = run.storeStates[peer.name]
        val effects = when (step.event) {
            "reconnect_hello" -> hello(peer, step)
            "reconnect_signature" -> signature(peer, step)
            "reconnect_confirm" -> confirm(peer, step)
            "protected_record" -> protectedRecord(peer, step)
            "send_protected_record" -> {
                val type = parseWire(step.simulate["record_type"] ?: error("send_protected_record without record_type"))
                if (type == ScenarioHandler.TYPE_APP_HEARTBEAT) {
                    heartbeatReceiveStep(peer)
                } else {
                    sendProtectedRecord(peer, step)
                }
            }
            "counter_seek" -> seek(peer, step)
            "close_connection" -> {
                peer.engine?.teardown()
                emptyList()
            }
            "process_load" -> processLoad(peer, step)
            "pending_expiry" -> pendingExpiry(peer)
            "revoke" -> revoke(peer)
            "pairing_confirm_a" -> pairingConfirm(peer)
            else -> throw AssertionError("unmapped scenario event '${step.event}' in ${fixture.id}")
        }
        record(
            StepStart(index, step, inboundBefore, outboundBefore, expectedRxBefore, authenticatedBefore, storeBefore),
            peer,
            effects,
        )
    }

    private data class StepStart(
        val index: Int,
        val step: ScenarioStep,
        val inboundBefore: ULong,
        val outboundBefore: ULong,
        val expectedRxBefore: ULong,
        val authenticatedBefore: Boolean,
        val storeBefore: String?,
    )

    @Suppress("CyclomaticComplexMethod") // One explicit observation per mapped step key.
    private suspend fun record(start: StepStart, peer: PhoneSession, effects: List<SessionEffect>) {
        val index = start.index
        val step = start.step
        val inboundBefore = start.inboundBefore
        val outboundBefore = start.outboundBefore
        val expectedRxBefore = start.expectedRxBefore
        val authenticatedBefore = start.authenticatedBefore
        val storeAfter = refreshStore(peer)
        if (start.storeBefore != null && start.storeBefore != storeAfter) {
            run.transitions += start.storeBefore to storeAfter
        }
        val terminated = effects.filterIsInstance<SessionEffect.Terminated>().firstOrNull()?.reason
        val close = effects.any { it is SessionEffect.Close }
        val sent = effects.filterIsInstance<SessionEffect.Send>().mapNotNull { wireType(it.bytes) }
        sent.filter { it in ACTIVATION_TYPES }.forEach {
            peer.activationSends += it
            peer.activationSendSteps.putIfAbsent(it, index)
        }
        val delivered = effects.filterIsInstance<SessionEffect.Deliver>().map { it.type }
        if (terminated != null) peer.terminations += terminated
        if (close) peer.closed = true
        if (peer.engine?.keysErasedForTest == true) peer.keysErased = true
        val type = step.simulate["record_type"]?.let { parseWire(it) }
        val sequence = sequenceOf(step)
        val unauthorized = type != null &&
            step.event == "protected_record" &&
            (
                RecordType.fromWire(type)?.allows(Direction.CLIENT_TO_PHONE) != true ||
                    (peer.sessionClass != null && !RecordPolicy.authorized(type, Direction.CLIENT_TO_PHONE, peer.sessionClass!!))
                )
        val unknownType = type != null && RecordType.fromWire(type) == null
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
            "durable_phone_record:${peer.name}" to storeAfter,
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
            unknownType = unknownType,
            oversize = oversizeBuilt,
            sequenceMismatch = sequenceMismatch,
            recordOpened = opened,
            heartbeatCrossed = heartbeat,
            eventCount = if (type == ScenarioHandler.TYPE_PULL_EVENTS) {
                step.simulate["event_count"]?.toIntOrNull() ?: peer.buffer.lastEventCount
            } else {
                step.simulate["event_count"]?.toIntOrNull()
            },
            authenticatedBefore = authenticatedBefore,
            sessionClassAfter = peer.sessionClass,
            expectedRxBefore = expectedRxBefore,
            inboundBefore = inboundBefore,
            inboundAfter = peer.engine?.inboundSequenceForTest ?: 0uL,
            outboundBefore = outboundBefore,
            inboundAtTeardown = peer.engine?.inboundSequenceAtTeardownForTest ?: 0uL,
            outboundAtTeardown = peer.engine?.outboundSequenceAtTeardownForTest ?: 0uL,
            stateSnapshot = snapshot,
        )
    }

    private suspend fun hello(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        if (step.simulate["record_exists"] == "false") {
            if (run.storeStates[peer.name] != "absent") run.storeStates[peer.name] = "deleted"
            peer.repository = null
            peer.activation = null
        }
        // A terminated generation may be followed by a fresh signed reconnect in the same
        // fixture (for example after a recovery-only session was torn down).
        if (peer.engine != null && peer.engine?.isOpen == false) {
            peer.engine = null
            peer.expectedRx = 0uL
            peer.closed = false
        }
        val frame = if (step.simulate["hello_valid"] == "false") peer.peer.invalidCapsHello() else peer.peer.helloFrame
        val effects = engine(peer).onInbound(frame)
        if (effects.size == 2 && effects.all { it is SessionEffect.Send }) {
            val helloPayload = PreAuthCodec.decode((effects[0] as SessionEffect.Send).bytes).second
            val signature = SignatureFrame.decode(
                PreAuthCodec.decode((effects[1] as SessionEffect.Send).bytes).second,
                Role.PHONE,
            ).signature
            peer.peer.acceptPhoneHello(helloPayload, signature, fixtureEt())
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
        val engine = engine(peer)
        val effects = engine.onInbound(record)
        val authenticated = effects.filterIsInstance<SessionEffect.Authenticated>().firstOrNull()
        if (authenticated != null) {
            peer.authenticated = true
            peer.sessionClass = authenticated.sessionClass
            peer.sessionClasses += authenticated.sessionClass
            // The host sends what the freshly opened session owes (OTP2-STA-010b step 0).
            val unprompted = mutableListOf<SessionEffect>()
            authenticated.unprompted.forEach { application ->
                peer.activationSends += application.type
                unprompted += engine.sendApplication(application.type, application.body)
            }
            return effects + unprompted
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
            RecordType.ACTIVATION_COMMIT.wire -> activationBody(peer, STAGE_COMMIT, step.simulate["act_mac_ok"] != "false")
            RecordType.ACTIVATION_ACK.wire -> activationBody(peer, STAGE_ACK, step.simulate["act_mac_ok"] != "false")
            else -> ByteArray(0)
        }
        val oversize = step.simulate["length_ok"] == "false"
        val recordBody = if (oversize) ByteArray(OVERSIZE_RECORD_BYTES - RecordCodec.HEADER - RecordCodec.TAG) else body
        val record = peer.peer.record(
            type = type,
            sequence = sequence,
            body = recordBody,
            tagOk = step.simulate["tag_ok"] != "false",
            sessionIdOk = step.simulate["session_id_ok"] != "false",
        )
        oversizeBuilt = record.size > RecordCodec.MAX_RECORD
        check(!oversize || oversizeBuilt) {
            "${fixture.id}: the oversize fixture must build a record above RecordCodec.MAX_RECORD"
        }
        return engine(peer).onInbound(record)
    }

    private suspend fun sendProtectedRecord(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        val type = parseWire(step.simulate["record_type"] ?: error("send_protected_record without record_type"))
        // The activation handler must already have emitted these at the gate / in its commit
        // reply; the fixture step is an observation, never a fabricated send (OTP2-STA-010b).
        if (type == RecordType.ENROLLMENT_PREPARE.wire || type == RecordType.ACTIVATION_COMPLETE.wire) {
            check(peer.activationSends.contains(type)) {
                "${fixture.id}: the fixture observes a ${RecordType.fromWire(type)?.name} the engine never sent"
            }
            return emptyList()
        }
        val engine = engine(peer)
        val body = when (type) {
            ScenarioHandler.TYPE_PULL_EVENTS -> pullEventsBody(peer, step.simulate["event_count"]?.toInt() ?: 0)
            ScenarioHandler.TYPE_PUSH_EVENT -> pushEventBody(peer)
            ScenarioHandler.TYPE_EVENT_ACK -> ackBody(peer, true)
            ScenarioHandler.TYPE_PHONE_POLICY_STATE -> policyBody(step.simulate["pull_response"] == "true")
            else -> ByteArray(0)
        }
        return try {
            engine.sendApplication(type, body)
        } catch (_: SessionReject) {
            emptyList()
        }
    }

    /**
     * OTP2-REC-034's terminating rule is the receive direction, while the outbound gate only
     * ignores early heartbeats (design D4). Only `st-rec-heartbeat-rate-terminates` uses
     * `send_protected_record` with `app_heartbeat`; its inventory entry is an explicit
     * receive-side `engine-effect` assertion, so this step injects the peer heartbeat instead
     * of claiming a send the engine never makes.
     */
    private suspend fun heartbeatReceiveStep(peer: PhoneSession): List<SessionEffect> {
        check(fixture.id == "st-rec-heartbeat-rate-terminates") {
            "${fixture.id}: app_heartbeat send steps are outbound in this scenario"
        }
        val record = peer.peer.record(
            type = ScenarioHandler.TYPE_APP_HEARTBEAT,
            sequence = peer.expectedRx,
            body = ByteArray(0),
            tagOk = true,
            sessionIdOk = true,
        )
        return engine(peer).onInbound(record)
    }

    private fun policyBody(responseToPull: Boolean): ByteArray = byteArrayOf(
        POLICY_UNLOCKED_ONLY.toByte(),
        (if (responseToPull) RESPONSE_TO_PULL_FLAG else 0).toByte(),
    )

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

    private suspend fun processLoad(peer: PhoneSession, step: ScenarioStep): List<SessionEffect> {
        peer.engine = null
        peer.authenticated = false
        peer.sessionClass = null
        if (step.simulate["continuity_token_matches"] == "false") {
            // OTP2-STA-010c: a restart reports a new boot marker. The repository over the same
            // storage rotates the continuity token and deletes only the incomplete records;
            // the runner observes that real transition instead of revoking anything itself.
            val before = refreshStore(peer)
            peer.bootMarker = BOOT_MARKER_RESTART
            if (peer.storage != null) {
                peer.repository = repository(peer)
                peer.activation = null
                refreshStore(peer)
                if (before in INCOMPLETE_STATES && run.storeStates[peer.name] == "deleted") {
                    peer.deletedIncomplete = true
                }
            }
        }
        return emptyList()
    }

    /** OTP2-STA-010c: the real store applies the injected clock's 86 400 s deadline. */
    private suspend fun pendingExpiry(peer: PhoneSession): List<SessionEffect> {
        val before = run.storeStates[peer.name]
        refreshStore(peer)
        if (before in INCOMPLETE_STATES && run.storeStates[peer.name] == "deleted") {
            peer.deletedIncomplete = true
        }
        return emptyList()
    }

    private suspend fun revoke(peer: PhoneSession): List<SessionEffect> {
        peer.repository?.revoke(TrustEncoding.hex(peer.peer.installationId))
        peer.revokedTrust = true
        peer.repository = null
        peer.activation = null
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
        val repository = peer.repository
        val activation = repository?.let {
            peer.activation ?: ActivationEngine(it, peer.registry).also { activation -> peer.activation = activation }
        }
        val handler = if (activation == null) peer.handler else composeApplicationHandlers(peer.handler, activation)
        return PhoneSessionEngine(
            localInstallationId = TestPeers.phoneInstallationId,
            signer = phoneIdentity.signer,
            lookup = PeerLookup { id ->
                repository?.peers()?.firstOrNull {
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
            },
            clock = { clock.now },
            nonce = { ByteArray(16).also { SecureRandom().nextBytes(it) } },
            handler = handler,
        ).also {
            peer.engine = it
            peer.generations += 1
            peer.authenticated = false
            peer.sessionClass = null
            peer.closed = false
        }
    }

    private fun fixtureEt(): ByteArray? = fixture.persisted["et"]?.let { ByteArray(HASH_BYTES) }

    private fun activationBody(peer: PhoneSession, stage: Byte, macOk: Boolean): ByteArray {
        val keys = peer.peer.recovery()
        val et = fixtureEt() ?: error("an activation step requires the fixture's ET")
        val mac = try {
            Mac.getInstance(HMAC_ALGORITHM).run {
                init(SecretKeySpec(keys.kRecClient, HMAC_ALGORITHM))
                doFinal(et + byteArrayOf(stage))
            }
        } catch (failure: Exception) {
            throw AssertionError("activation MAC helper failed", failure)
        }
        if (!macOk) mac[mac.size - 1] = (mac[mac.size - 1].toInt() xor 0x01).toByte()
        return mac
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
        val INCOMPLETE_STATES = setOf("pending", "committed_waiting_ack")
        const val RECORD_PREFIX = "durable_phone_record:"
        const val OVERSIZE_RECORD_BYTES = RecordCodec.MAX_RECORD + 1
        const val HASH_BYTES = 32
        const val HMAC_ALGORITHM = "HmacSHA256"
        const val BOOT_TOKEN = "abababababababababababababababababababababababababababababababab"
        const val STAGE_COMMIT: Byte = 0x01
        const val STAGE_ACK: Byte = 0x03
        val ACTIVATION_TYPES = setOf(
            RecordType.ENROLLMENT_PREPARE.wire,
            RecordType.ACTIVATION_COMMIT.wire,
            RecordType.ACTIVATION_COMPLETE.wire,
            RecordType.ACTIVATION_ACK.wire,
        )
        const val POLICY_UNLOCKED_ONLY = 0
        const val RESPONSE_TO_PULL_FLAG = 0x08
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
            "act_mac_ok",
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
        "erase_trust_record_revoked",
        "reject_activation_mac_mismatch",
        "record_active_by_ack",
        "record_activated_by_ack",
        "record_committed",
        "activation_ack_processed",
        "enrollment_prepare_sent",
        "enrollment_prepare_processed",
        "pull_blocked_reported",
        "prepare_peer_reported_below_active",
        "reconnect_confirm_sent_peer_state_below_active",
        "activation_commit_processed",
        "commit_duplicate_accepted_no_regression",
        "activation_complete_sent",
        "erase_enrollment_remnants",
        "reset_session_slot",
    )

    fun isMapped(name: String): Boolean = name in known

    @Suppress("CyclomaticComplexMethod") // The mapping table is one branch per effect name.
    fun observe(name: String, run: ScenarioRun): Boolean {
        val steps = run.steps
        val sessions = run.sessions.values
        val terminations = sessions.flatMap { it.terminations }
        return when (name) {
            "mutual_authentication_complete" -> sessions.any { it.authenticated || it.sessionClass != null }
            "set_class_full" -> sessions.any {
                it.sessionClass == SessionClass.FULL || it.sessionClasses.contains(SessionClass.FULL)
            }
            "set_class_full_pending_peer" -> sessions.any {
                it.sessionClass == SessionClass.FULL_PENDING_PEER ||
                    it.sessionClasses.contains(SessionClass.FULL_PENDING_PEER)
            }
            "set_class_activation_recovery" -> sessions.any {
                it.sessionClass == SessionClass.ACTIVATION_RECOVERY ||
                    it.sessionClasses.contains(SessionClass.ACTIVATION_RECOVERY)
            }
            "terminate_session" -> terminations.any { it != "app_error" }
            "app_error_terminal" -> terminations.contains("app_error")
            "erase_session_keys" -> sessions.any { it.keysErased }
            "counter_not_advanced" -> counterNotAdvanced(steps)
            "advance_rx_sequence" -> steps.any { it.event == "protected_record" && it.accepted && it.inboundAdvanced }
            // The bounded engine reasons are the observation: rejections at the record-open
            // stage are `record_invalid`, counter exhaustion is `sequence`, class refusals are
            // `authorization`, and an invalid reconnect-context frame is `record_invalid`.
            "reject_sequence_violation" ->
                steps.any {
                    it.sequenceMismatch && it.terminated in setOf("record_invalid", "sequence")
                } ||
                    terminations.contains("sequence")
            "reject_bad_tag" ->
                steps.any { it.terminated == "record_invalid" && it.simulate["tag_ok"] == "false" }
            "reject_oversize_record" -> steps.any { it.terminated == "record_invalid" && it.oversize }
            "reject_replayed_or_foreign_session_record" ->
                steps.any { it.terminated == "record_invalid" && it.simulate["session_id_ok"] == "false" }
            "reject_unauthorized_record" ->
                steps.any {
                    it.terminated == "authorization" ||
                        (it.terminated == "record_invalid" && it.unknownType)
                }
            "reject_unauthenticated_record" ->
                steps.any {
                    it.event == "protected_record" &&
                        it.terminated == "record_invalid" &&
                        !it.authenticatedBefore
                }
            "reject_direction_violation" ->
                steps.any { it.event == "pairing_confirm_a" && it.terminated == "record_invalid" }
            "preauth_reject" -> steps.any { it.preAuthReject }
            "reject_unknown_installation" ->
                steps.any { it.event == "reconnect_hello" && it.simulate["record_exists"] == "false" && it.preAuthReject }
            "test_probe_counter_seek" -> sessions.any { it.seekCount > 0 }
            "heartbeat_processed" ->
                steps.any { it.event == "protected_record" && it.accepted && it.heartbeatCrossed }
            "heartbeat_sent" -> steps.any { it.sentTypes.contains(TYPE_APP_HEARTBEAT) }
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
            // OTP2-CRY-019g: only an activation MAC failure erases incomplete materials; a
            // runner-driven cancel/revoke must not satisfy this claim.
            "erase_incomplete_materials" -> steps.any {
                it.terminated == "authorization" && it.simulate["act_mac_ok"] == "false"
            } &&
                run.transitions.any { it.second == "deleted" && it.first in INCOMPLETE_STATES }
            "erase_trust_record_revoked" -> sessions.any { it.revokedTrust }
            "reject_activation_mac_mismatch" -> steps.any {
                it.terminated == "authorization" && it.simulate["act_mac_ok"] == "false"
            }
            "enrollment_prepare_sent" -> steps.any { it.sentTypes.contains(RecordType.ENROLLMENT_PREPARE.wire) }
            // The phone never receives a prepare; a client prepare is not an engine event here.
            "enrollment_prepare_processed" -> false
            "prepare_peer_reported_below_active" ->
                steps.any { it.sentTypes.contains(RecordType.ENROLLMENT_PREPARE.wire) }
            "reconnect_confirm_sent_peer_state_below_active" ->
                sessions.any { it.phonePeerStates.contains(0x02) }
            "activation_complete_sent" -> steps.any { it.sentTypes.contains(RecordType.ACTIVATION_COMPLETE.wire) }
            "activation_commit_processed" -> steps.any { it.deliveredTypes.contains(RecordType.ACTIVATION_COMMIT.wire) }
            "activation_ack_processed" -> steps.any { it.deliveredTypes.contains(RecordType.ACTIVATION_ACK.wire) }
            "record_committed" -> steps.any {
                it.deliveredTypes.contains(RecordType.ACTIVATION_COMMIT.wire) &&
                    storeStateOf(it) == "committed_waiting_ack"
            } ||
                run.transitions.any { it == "pending" to "committed_waiting_ack" }
            "record_activated_by_ack" -> activatedByAck(run)
            // A duplicate commit on an ACTIVE phone record is accepted without a transition; the
            // fixture forbids `record_committed` and `record_active_by_ack` alongside this.
            "commit_duplicate_accepted_no_regression" ->
                steps.any { it.accepted && it.deliveredTypes.contains(RecordType.ACTIVATION_COMMIT.wire) } &&
                    run.transitions.none { it.second == "committed_waiting_ack" }
            "erase_enrollment_remnants" -> activatedByAck(run)
            "reset_session_slot" -> sessions.any { it.generations > 1 }
            "record_active_by_ack" -> activatedByAck(run)
            "pull_blocked_reported" -> steps.any {
                it.sentTypes.contains(RecordType.PHONE_POLICY_STATE.wire) ||
                    it.deliveredTypes.contains(RecordType.PULL_EVENTS.wire)
            }
            else -> throw AssertionError("effect '$name' has no engine observation mapping")
        }
    }

    /** First step index for each `effects.order` name; null means the claim was never observed. */
    fun orderIndex(name: String, run: ScenarioRun): Int? = when (name) {
        "set_class_activation_recovery" ->
            run.steps.indexOfFirst { it.sessionClassAfter == SessionClass.ACTIVATION_RECOVERY }
        "enrollment_prepare_sent" ->
            run.steps.indexOfFirst { it.sentTypes.contains(RecordType.ENROLLMENT_PREPARE.wire) }
        "activation_commit_processed" ->
            run.steps.indexOfFirst { it.deliveredTypes.contains(RecordType.ACTIVATION_COMMIT.wire) }
        "record_committed" ->
            run.steps.indexOfFirst {
                it.deliveredTypes.contains(RecordType.ACTIVATION_COMMIT.wire) &&
                    storeStateOf(it) == "committed_waiting_ack"
            }
        "activation_complete_sent" ->
            run.steps.indexOfFirst { it.sentTypes.contains(RecordType.ACTIVATION_COMPLETE.wire) }
        "record_activated_by_ack", "erase_enrollment_remnants" -> activatedByAckStep(run)
        "reject_unauthorized_record" ->
            run.steps.indexOfFirst { it.terminated == "authorization" }
        "set_class_full" -> run.steps.indexOfFirst { it.sessionClassAfter == SessionClass.FULL }
        "otp_events_transferred" ->
            run.steps.indexOfFirst {
                it.accepted &&
                    (
                        it.sentTypes.contains(RecordType.PULL_EVENTS.wire) ||
                            it.deliveredTypes.contains(RecordType.PULL_EVENTS.wire)
                        ) &&
                    (it.eventCount ?: 0) > 0
            }
        else -> null
    }?.takeIf { it >= 0 }

    private fun activatedByAck(run: ScenarioRun): Boolean = activatedByAckStep(run) != null

    /** The ack step that actually moved the durable record from committed to active. */
    private fun activatedByAckStep(run: ScenarioRun): Int? {
        val previous = mutableMapOf<String, String>()
        for (step in run.steps) {
            val state = storeStateOf(step)
            if (
                step.deliveredTypes.contains(RecordType.ACTIVATION_ACK.wire) &&
                previous[step.peer] == "committed_waiting_ack" &&
                state == "active"
            ) {
                return step.index
            }
            if (state != null) previous[step.peer] = state
        }
        return null
    }

    /** Multi-peer: every step reports only its own peer's durable record. */
    private fun storeStateOf(step: StepOutcome): String? = step.stateSnapshot["durable_phone_record:${step.peer}"]

    /**
     * OTP2-STA-015 counter observation against the pre-teardown snapshot:
     * - a record rejected while opening (tag, length, session, sequence, unauthenticated) must
     *   leave both counters untouched;
     * - a record that opened and was then refused by its authorization class consumes exactly
     *   its own sequence (`expected + 1`) and never advances the outbound counter.
     */
    private fun counterNotAdvanced(steps: List<StepOutcome>): Boolean {
        val rejected = steps.filter { it.event == "protected_record" && it.terminated != null }
        if (rejected.isEmpty()) return false
        return rejected.all { step ->
            val expectedInbound = if (step.terminated == "authorization") {
                step.expectedRxBefore + 1uL
            } else {
                step.expectedRxBefore
            }
            step.inboundAtTeardown == expectedInbound &&
                step.outboundAtTeardown == step.outboundBefore
        }
    }

    private val INCOMPLETE_STATES = setOf("pending", "committed_waiting_ack")
    private const val TYPE_APP_HEARTBEAT = 0x0002
    private const val TYPE_PULL_REQUEST = 0x0200
    private const val TYPE_PULL_EVENTS = 0x0202
    private const val TYPE_PUSH_EVENT = 0x0203
    private const val TYPE_EVENT_ACK = 0x0204
}
