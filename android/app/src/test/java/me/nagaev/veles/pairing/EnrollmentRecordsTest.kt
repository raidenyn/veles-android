package me.nagaev.veles.pairing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.RecordPolicy
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionClass
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP-10 Task 6 behavior tests: the `enroll_identity` codec, role-bound `device_type`, body
 * bounds, the verify-before-store boundary, the reconnect policy, and key erasure.
 */
class EnrollmentRecordsTest {
    @Test
    fun round_trips_identity_records_for_every_role_bound_device_type() {
        val cases = listOf(
            IdentityRole.PHONE to DeviceType.PHONE,
            IdentityRole.CLIENT to DeviceType.DESKTOP,
            IdentityRole.CLIENT to DeviceType.WATCH,
        )
        for ((role, deviceType) in cases) {
            val record = record(deviceType)
            val body = EnrollmentRecords.encode(record, role)
            val decoded = EnrollmentRecords.decode(body, role)
            assertArrayEquals(record.installationId, decoded.installationId)
            assertEquals(record.label, decoded.label)
            assertEquals(deviceType, decoded.deviceType)
            assertArrayEquals(record.identityPubkey, decoded.identityPubkey)
            assertArrayEquals(record.fingerprint, decoded.fingerprint)
            assertArrayEquals(record.nonce, decoded.nonce)
            assertArrayEquals(body, EnrollmentRecords.encode(decoded, role))
        }
    }

    @Test
    fun device_type_roles_enforced() {
        val phoneRole = IdentityRole.PHONE
        val clientRole = IdentityRole.CLIENT

        for (deviceType in listOf(DeviceType.DESKTOP, DeviceType.WATCH)) {
            val record = record(deviceType)
            assertThrows("a phone record must not carry $deviceType", EnrollmentReject::class.java) {
                EnrollmentRecords.encode(record, phoneRole)
            }
            assertThrows(EnrollmentReject::class.java) { EnrollmentRecords.decode(encodeUnchecked(record), phoneRole) }
        }

        val phoneRecord = record(DeviceType.PHONE)
        assertThrows(EnrollmentReject::class.java) { EnrollmentRecords.encode(phoneRecord, clientRole) }
        assertThrows(EnrollmentReject::class.java) { EnrollmentRecords.decode(encodeUnchecked(phoneRecord), clientRole) }

        assertThrows("unknown device types are rejected", EnrollmentReject::class.java) {
            EnrollmentRecords.decode(bodyWithDeviceType(0x03), IdentityRole.PHONE)
        }
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentRecords.decode(bodyWithDeviceType(0x03), IdentityRole.CLIENT)
        }
    }

    @Test
    fun record_length_bounds() {
        val empty = record(DeviceType.PHONE, label = "")
        val full = record(DeviceType.DESKTOP, label = "a".repeat(32))
        assertEquals(132, EnrollmentRecords.encode(empty, IdentityRole.PHONE).size)
        assertEquals(164, EnrollmentRecords.encode(full, IdentityRole.CLIENT).size)

        assertThrows("label over 32 UTF-8 bytes rejects at length validation", EnrollmentReject::class.java) {
            EnrollmentRecords.encode(record(DeviceType.PHONE, label = "a".repeat(33)), IdentityRole.PHONE)
        }
        assertThrows(EnrollmentReject::class.java) { EnrollmentRecords.decode(ByteArray(131), IdentityRole.PHONE) }
        assertThrows(EnrollmentReject::class.java) { EnrollmentRecords.decode(ByteArray(165), IdentityRole.PHONE) }
        assertThrows("trailing bytes reject", EnrollmentReject::class.java) {
            EnrollmentRecords.decode(EnrollmentRecords.encode(empty, IdentityRole.PHONE) + 0x00, IdentityRole.PHONE)
        }

        // OTP2-ENC-003 safe text: control characters and lone surrogates never enter a record.
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentRecords.encode(record(DeviceType.PHONE, label = "a\u0001b"), IdentityRole.PHONE)
        }
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentRecords.encode(record(DeviceType.PHONE, label = "\uD800"), IdentityRole.PHONE)
        }

        // OTP2-REC-010 point format: exactly 65 bytes and the uncompressed tag.
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentRecords.encode(record(DeviceType.PHONE, pubkey = ByteArray(65) { 0x03 }), IdentityRole.PHONE)
        }
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentRecords.encode(record(DeviceType.PHONE, pubkey = ByteArray(64)), IdentityRole.PHONE)
        }

        // OTP2-CRY-007a/007b bounds: a 16-byte Ke and a context of at most 4096 bytes.
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentSchedule.derive(ByteArray(15) { 1 }, ByteArray(8) { 2 })
        }
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentSchedule.derive(ByteArray(16) { 1 }, ByteArray(4097) { 2 })
        }
        EnrollmentSchedule.derive(ByteArray(16) { 1 }, ByteArray(4096) { 2 }).wipe()
    }

    @Test
    fun bad_record_fingerprint_stores_nothing() {
        val good = EnrollmentRecords.encode(record(DeviceType.PHONE), IdentityRole.PHONE)
        val validated = mutableListOf<ByteArray>()
        val stored = mutableListOf<IdentityRecord>()
        EnrollmentRecords.decodeForStorage(
            good,
            IdentityRole.PHONE,
            validatePoint = { validated += it },
            store = { stored += it },
        )
        assertEquals(1, validated.size)
        assertEquals(1, stored.size)

        val bad = good.copyOf().also { it[it.size - 17] = (it[it.size - 17] + 1).toByte() }
        val validatedAfterBad = mutableListOf<ByteArray>()
        val afterBad = mutableListOf<IdentityRecord>()
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentRecords.decodeForStorage(
                bad,
                IdentityRole.PHONE,
                validatePoint = { validatedAfterBad += it },
                store = { afterBad += it },
            )
        }
        assertTrue("a bad fingerprint must not reach point validation", validatedAfterBad.isEmpty())
        assertTrue("a bad fingerprint must store nothing", afterBad.isEmpty())
        assertEquals(1, stored.size)

        val afterInvalidPoint = mutableListOf<IdentityRecord>()
        assertThrows(IllegalArgumentException::class.java) {
            EnrollmentRecords.decodeForStorage(
                good,
                IdentityRole.PHONE,
                validatePoint = { throw IllegalArgumentException("invalid point") },
                store = { afterInvalidPoint += it },
            )
        }
        assertTrue("an invalid point must store nothing", afterInvalidPoint.isEmpty())
    }

    @Test
    fun enroll_identity_rejected_in_reconnect() {
        for (sessionClass in SessionClass.entries) {
            for (direction in Direction.entries) {
                assertFalse(
                    "enroll_identity must stay rejected in $sessionClass/$direction",
                    RecordPolicy.authorized(RecordType.ENROLL_IDENTITY.wire, direction, sessionClass),
                )
            }
        }
        assertTrue(
            RecordPolicy.authorized(
                RecordType.APP_HEARTBEAT.wire,
                Direction.CLIENT_TO_PHONE,
                SessionClass.FULL,
            ),
        )
    }

    @Test
    fun keys_zeroed_after_use() {
        val keys = EnrollmentSchedule.derive(ByteArray(16) { 0x11 }, ByteArray(32) { 0x22 })
        assertEquals("a key holder must never print key material", "EnrollmentKeys(wiped=false)", keys.toString())
        assertFalse(keys.isWiped)

        val observed = keys.use { holder ->
            assertFalse(holder.isWiped)
            holder.aToBAesKey.copyOf()
        }
        assertEquals(32, observed.size)
        assertTrue(keys.isWiped)
        assertEquals("EnrollmentKeys(wiped=true)", keys.toString())
        for (field in keys.fields()) {
            assertTrue("every derived field must be zeroed", field.all { it == 0.toByte() })
        }
        keys.wipe()

        var reuseRan = false
        assertThrows("a wiped holder must not be usable", EnrollmentReject::class.java) {
            keys.use { reuseRan = true }
        }
        assertFalse("the block must not run on a wiped holder", reuseRan)

        val failing = EnrollmentSchedule.derive(ByteArray(16) { 0x33 }, ByteArray(32) { 0x44 })
        assertThrows(IllegalStateException::class.java) {
            failing.use { error("caller failure") }
        }
        assertTrue("use() must wipe on the failure path too", failing.isWiped)
        assertEquals("EnrollmentKeys(wiped=true)", failing.toString())
    }

    @Test
    fun session_prefix_is_context_only_and_nonce_is_direction_scoped() {
        val context = ByteArray(48) { it.toByte() }
        val first = EnrollmentSchedule.sessionIdPrefix(context)
        assertEquals(8, first.size)
        assertArrayEquals(first, EnrollmentSchedule.sessionIdPrefix(context))
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentSchedule.sessionIdPrefix(ByteArray(4097))
        }
        assertThrows(EnrollmentReject::class.java) {
            EnrollmentSchedule.nonce(ByteArray(3), 0uL)
        }
        val nonce = EnrollmentSchedule.nonce(byteArrayOf(0x2e, 0x4e.toByte(), 0xa7.toByte(), 0xee.toByte()), 7uL)
        assertEquals(12, nonce.size)
        assertArrayEquals(byteArrayOf(0x2e, 0x4e, 0xa7.toByte(), 0xee.toByte(), 0, 0, 0, 0, 0, 0, 0, 7), nonce)
    }

    @Test
    fun shared_encoding_fixtures_cover_the_body_codec() {
        val fixtures = fixtures("encoding.json").filter { it.obj("input").strOrNull("layout") == "enroll_identity" }
        assertTrue("the shared encoding fixture set must stay present", fixtures.isNotEmpty())
        for (fixture in fixtures) {
            val id = fixture.str("id")
            val body = hex(fixture.obj("input").str("bytes"))
            val expected = fixture.obj("expected")
            if ((expected["accepted"] as JsonPrimitive).content == "true") {
                val role = if (expected.obj("fields").str("device_type") == "0x00") {
                    IdentityRole.PHONE
                } else {
                    IdentityRole.CLIENT
                }
                val decoded = EnrollmentRecords.decode(body, role)
                assertArrayEquals(id, body, EnrollmentRecords.encode(decoded, role))
                assertEquals(id, expected.obj("fields").str("label"), decoded.label)
                assertEquals(id, expected.obj("fields").str("installation_id"), "0x" + decoded.installationId.toHex())
            } else {
                for (role in IdentityRole.entries) {
                    assertThrows("$id must reject for $role", EnrollmentReject::class.java) {
                        EnrollmentRecords.decode(body, role)
                    }
                }
            }
        }
    }

    private fun record(
        deviceType: DeviceType,
        label: String = "phone-label",
        pubkey: ByteArray = validPoint(),
    ): IdentityRecord = EnrollmentRecords.create(
        installationId = ByteArray(16) { it.toByte() },
        label = label,
        deviceType = deviceType,
        identityPubkey = pubkey,
        nonce = ByteArray(16) { (0x40 + it).toByte() },
    )

    /** Encodes a body without role validation so decode's own role rule can be exercised. */
    private fun encodeUnchecked(record: IdentityRecord): ByteArray {
        val label = record.label.toByteArray(Charsets.UTF_8)
        return record.installationId +
            byteArrayOf((label.size ushr 8).toByte(), label.size.toByte()) +
            label +
            byteArrayOf(record.deviceType.wire.toByte()) +
            record.identityPubkey +
            record.fingerprint +
            record.nonce
    }

    private fun bodyWithDeviceType(deviceType: Int): ByteArray {
        val body = encodeUnchecked(record(DeviceType.PHONE))
        val labelLength = ((body[16].toInt() and 0xFF) shl 8) or (body[17].toInt() and 0xFF)
        body[18 + labelLength] = deviceType.toByte()
        return body
    }

    private fun validPoint(): ByteArray = hex(
        "0x0424eebc61af2f15cb5f9f87a067a2eb975da046957959c92d2c97adf1e516e5" +
            "d511844429db6690479ba3f99a82404710572526d6fcb2e21f725e7aedd60f7da1",
    )

    private fun fixtures(name: String): List<JsonObject> {
        val resource = javaClass.getResourceAsStream("/fixtures/$name")
            ?: error("missing /fixtures/$name: the test source set must map protocol/v1")
        val root = Json.parseToJsonElement(resource.readBytes().decodeToString()).jsonObject
        val fixtures = root["fixtures"] as? JsonArray ?: error("$name must carry a fixtures array")
        return fixtures.jsonArray.map { it.jsonObject }
    }

    private fun JsonObject.str(key: String): String = (this[key] as? JsonPrimitive)?.content ?: error("missing fixture string field $key")

    private fun JsonObject.strOrNull(key: String): String? = (this[key] as? JsonPrimitive)?.content

    private fun JsonObject.obj(key: String): JsonObject = this[key] as? JsonObject ?: error("missing fixture object $key")

    private fun hex(value: String): ByteArray {
        val text = value.removePrefix("0x")
        require(text.length % 2 == 0) { "malformed hex value" }
        return ByteArray(text.length / 2) { index ->
            text.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
    }

    private fun ByteArray.toHex(): String = joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0') }
}
