package me.nagaev.veles.pairing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Test
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * OTP-10 Task 6 vector consumer for the enrollment schedule, identity records, `ET` and both
 * fingerprints. Reads the one shared `protocol/v1/fixtures/crypto.json`; no copied fixture truth.
 *
 * The activation MAC outputs are reproduced only as test-local HMACs: they prove that `ET` is
 * the exact activation input bound by OTP2-CRY-019a, so an altered identity or context changes
 * every activation proof. The production activation MAC helper and its state machine belong to
 * Task 7 (`EnrollmentSession`).
 */
class EnrollmentVectorsTest {
    private val fixture: JsonObject by lazy { fixture("veles-enrollment-schedule") }
    private val input: JsonObject by lazy { fixture.obj("input") }
    private val outputs: JsonObject by lazy { fixture.obj("expected").obj("outputs") }
    private val phoneBody: ByteArray by lazy { hex(outputs.str("phone_identity_body")) }
    private val clientBody: ByteArray by lazy { hex(outputs.str("client_identity_body")) }
    private val context: ByteArray by lazy { hex(input.str("ctx")) }
    private val et: ByteArray by lazy { hex(outputs.str("et")) }

    @Test
    fun enrollment_schedule_matches_the_fixture() {
        val keys = EnrollmentSchedule.derive(hex(input.str("ke")), context)
        try {
            assertArrayEquals(outputs.hexField("k_ab_aes"), keys.aToBAesKey)
            assertArrayEquals(outputs.hexField("k_ab_nonce_prefix"), keys.aToBNoncePrefix)
            assertArrayEquals(outputs.hexField("k_act_a"), keys.phoneActivationKey)
            assertArrayEquals(outputs.hexField("k_ba_aes"), keys.bToAAesKey)
            assertArrayEquals(outputs.hexField("k_ba_nonce_prefix"), keys.bToANoncePrefix)
            assertArrayEquals(outputs.hexField("k_act_b"), keys.clientActivationKey)
        } finally {
            keys.wipe()
        }
    }

    @Test
    fun enrollment_session_prefix_and_nonces_match_the_fixture() {
        assertArrayEquals(outputs.hexField("session_id_prefix"), EnrollmentSchedule.sessionIdPrefix(context))
        assertArrayEquals(
            outputs.hexField("enrollment_nonce_ab_seq0"),
            EnrollmentSchedule.nonce(outputs.hexField("k_ab_nonce_prefix"), 0uL),
        )
        assertArrayEquals(
            outputs.hexField("enrollment_nonce_ba_seq7"),
            EnrollmentSchedule.nonce(outputs.hexField("k_ba_nonce_prefix"), 7uL),
        )
    }

    @Test
    fun identity_records_and_fingerprints_match_the_fixture() {
        val phone = EnrollmentRecords.decode(phoneBody, IdentityRole.PHONE)
        assertArrayEquals(hex(input.obj("phone_identity").str("installation_id")), phone.installationId)
        assertEquals(input.obj("phone_identity").str("label"), phone.label)
        assertEquals(DeviceType.PHONE, phone.deviceType)
        assertArrayEquals(outputs.hexField("phone_identity_pub"), phone.identityPubkey)
        assertArrayEquals(outputs.hexField("fp_self_phone"), phone.fingerprint)
        assertArrayEquals(outputs.hexField("fp_self_phone"), EnrollmentRecords.recordFingerprint(phone, IdentityRole.PHONE))
        assertArrayEquals(phoneBody, EnrollmentRecords.encode(phone, IdentityRole.PHONE))

        val client = EnrollmentRecords.decode(clientBody, IdentityRole.CLIENT)
        assertEquals(input.obj("client_identity").str("label"), client.label)
        assertEquals(DeviceType.DESKTOP, client.deviceType)
        assertArrayEquals(outputs.hexField("client_identity_pub"), client.identityPubkey)
        assertArrayEquals(outputs.hexField("fp_self_client"), client.fingerprint)
        assertArrayEquals(outputs.hexField("fp_self_client"), EnrollmentRecords.recordFingerprint(client, IdentityRole.CLIENT))
        assertArrayEquals(clientBody, EnrollmentRecords.encode(client, IdentityRole.CLIENT))

        val pair = EnrollmentRecords.pairFingerprint(phone.identityPubkey, client.identityPubkey)
        assertArrayEquals(outputs.hexField("fp_pair"), pair)
        assertEquals(outputs.str("fp_pair_display"), EnrollmentRecords.displayFingerprint(pair))
    }

    @Test
    fun create_identity_record_reproduces_the_fixture_body() {
        val phone = EnrollmentRecords.create(
            installationId = hex(input.obj("phone_identity").str("installation_id")),
            label = input.obj("phone_identity").str("label"),
            deviceType = DeviceType.PHONE,
            identityPubkey = outputs.hexField("phone_identity_pub"),
            nonce = hex(input.obj("phone_identity").str("nonce")),
        )
        assertArrayEquals(outputs.hexField("fp_self_phone"), phone.fingerprint)
        assertArrayEquals(phoneBody, EnrollmentRecords.encode(phone, IdentityRole.PHONE))
    }

    @Test
    fun enrollment_transcript_matches_the_fixture() {
        assertArrayEquals(
            et,
            EnrollmentRecords.transcript(
                context = context,
                phonePoint = hex(input.str("p_a")),
                clientPoint = hex(input.str("p_b")),
                phoneConfirm = hex(input.str("c_a")),
                clientConfirm = hex(input.str("c_b")),
                phoneIdentityBody = phoneBody,
                clientIdentityBody = clientBody,
            ),
        )
    }

    @Test
    fun altered_identity_or_context_changes_ET_and_blocks_activation() {
        val keys = EnrollmentSchedule.derive(hex(input.str("ke")), context)
        try {
            val alteredIdentity = phoneBody.copyOf().also { it[it.lastIndex] = (it.last() + 1).toByte() }
            val alteredContext = context.copyOf().also { it[it.lastIndex] = (it.last() + 1).toByte() }

            val alteredIdentityEt = transcript(phoneBody = alteredIdentity)
            val alteredContextEt = transcript(context = alteredContext)
            assertNotEquals(et.toHex(), alteredIdentityEt.toHex())
            assertNotEquals(et.toHex(), alteredContextEt.toHex())

            // OTP2-CRY-019a: every activation proof is HMAC(K_act, ET || stage).
            val prepare = activationMac(keys.phoneActivationKey, et, 0x00)
            assertArrayEquals(outputs.hexField("act_prepare_mac"), prepare)
            assertFalse(
                "an altered identity must not produce the recorded activation proof",
                activationMac(keys.phoneActivationKey, alteredIdentityEt, 0x00).contentEquals(prepare),
            )
            assertFalse(
                "an altered context must not produce the recorded activation proof",
                activationMac(keys.phoneActivationKey, alteredContextEt, 0x00).contentEquals(prepare),
            )
            assertArrayEquals(outputs.hexField("act_commit_mac"), activationMac(keys.clientActivationKey, et, 0x01))
            assertArrayEquals(outputs.hexField("act_complete_mac"), activationMac(keys.phoneActivationKey, et, 0x02))
            assertArrayEquals(outputs.hexField("act_ack_mac"), activationMac(keys.clientActivationKey, et, 0x03))
        } finally {
            keys.wipe()
        }
    }

    @Test
    fun context_mismatch_changes_the_schedule_and_the_transcript() {
        val negative = fixture("veles-neg-context-mismatch")
        assertEquals("context", negative.obj("expected").str("stage"))
        assertEquals("confirmation_mismatch", negative.obj("expected").str("proof"))
        val mismatched = hex(negative.obj("input").obj("deviation").str("value"))

        val base = EnrollmentSchedule.derive(hex(input.str("ke")), context)
        val other = EnrollmentSchedule.derive(hex(input.str("ke")), mismatched)
        try {
            assertFalse(base.aToBAesKey.contentEquals(other.aToBAesKey))
            assertFalse(base.bToAAesKey.contentEquals(other.bToAAesKey))
            assertFalse(base.phoneActivationKey.contentEquals(other.phoneActivationKey))
            assertNotEquals(et.toHex(), transcript(context = mismatched).toHex())
        } finally {
            base.wipe()
            other.wipe()
        }
    }

    private fun transcript(
        context: ByteArray = this.context,
        phoneBody: ByteArray = this.phoneBody,
    ): ByteArray = EnrollmentRecords.transcript(
        context = context,
        phonePoint = hex(input.str("p_a")),
        clientPoint = hex(input.str("p_b")),
        phoneConfirm = hex(input.str("c_a")),
        clientConfirm = hex(input.str("c_b")),
        phoneIdentityBody = phoneBody,
        clientIdentityBody = clientBody,
    )

    private fun activationMac(key: ByteArray, transcript: ByteArray, stage: Int): ByteArray = Mac.getInstance("HmacSHA256").run {
        init(SecretKeySpec(key, "HmacSHA256"))
        doFinal(transcript + byteArrayOf(stage.toByte()))
    }

    private fun fixture(id: String): JsonObject = fixtures().firstOrNull { it.str("id") == id } ?: error("missing crypto fixture id $id")

    private fun fixtures(): List<JsonObject> {
        val resource = javaClass.getResourceAsStream("/fixtures/crypto.json")
            ?: error("missing /fixtures/crypto.json: the test source set must map protocol/v1")
        val root = Json.parseToJsonElement(resource.readBytes().decodeToString()).jsonObject
        val fixtures = root["fixtures"] as? JsonArray ?: error("crypto.json must carry a fixtures array")
        return fixtures.jsonArray.map { it.jsonObject }
    }

    private fun JsonObject.str(key: String): String = (this[key] as? JsonPrimitive)?.content ?: error("missing fixture string field $key")

    private fun JsonObject.obj(key: String): JsonObject = this[key] as? JsonObject ?: error("missing fixture object $key")

    private fun JsonObject.hexField(key: String): ByteArray = hex(str(key))

    private fun hex(value: String): ByteArray {
        val text = value.removePrefix("0x")
        require(text.length % 2 == 0) { "malformed hex value" }
        return ByteArray(text.length / 2) { index ->
            text.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
    }

    private fun ByteArray.toHex(): String = joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0') }
}
