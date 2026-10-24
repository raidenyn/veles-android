package me.nagaev.veles.protocol

import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP-02 Plan Task 6 shared-fixture consumer. Reads the exact same fixture
 * files every other language consumer reads (repo root `protocol/v1/`, mapped
 * into the JVM test resources — no checked-in copy), asserting byte-for-byte
 * hex/base64 decoding and canonical u64 decimal-string preservation.
 *
 * Test-only: this class must never be imported by product code.
 *
 * OTP-03 hooks named explicitly (not implemented here): the Android runtime
 * SPAKE2/AEAD record engine and NotificationListener integration will
 * implement OTP2-CRY/STA/REC exactly and consume these same vectors for
 * conformance; OTP-02 evidence stops at shared-fixture byte-level assertions.
 */
class ProtocolFixturesTest {

    private companion object {
        const val U64_MAX_DECIMAL = "18446744073709551615"
        const val U64_OVERFLOW_DECIMAL = "18446744073709551616"
        const val BASE64_ROUND_TRIP_STRING = "AP+AgQ3+"
        const val BASE64_ROUND_TRIP_HEX = "00ff80810dfe"
        const val BASE64_SIMPLE_VALID = "AQ=="
        val BASE64_ROUND_TRIP_BYTES = byteArrayOf(0x00, 0xff.toByte(), 0x80.toByte(), 0x81.toByte(), 0x0d, 0xfe.toByte())
        val BASE64_SIMPLE_VALID_BYTES = byteArrayOf(0x01)
    }

    // ---------- shared-root fixture loading ----------

    private fun fixtureEnvelope(name: String): JsonObject {
        val resource = javaClass.getResourceAsStream("/fixtures/$name")
            ?: error("missing fixture resource /fixtures/$name: the test sourceset must map the shared protocol/v1 root")
        val text = resource.readBytes().decodeToString()
        val element = Json.parseToJsonElement(text).jsonObject
        val version = checkNotNull((element["version"] as? JsonPrimitive)?.content?.toIntOrNull()) {
            "fixture envelope $name must carry version 1"
        }
        assertEquals(1, version)
        val fixtures = element["fixtures"] as? JsonArray ?: error("fixture envelope $name must carry a fixtures array")
        assertTrue("fixture envelope $name must be non-empty", fixtures.isNotEmpty())
        return element
    }

    private fun fixturesOf(name: String): List<JsonObject> = fixtureEnvelope(name)["fixtures"]!!.jsonArray.map { it.jsonObject }

    private fun allFixtures(vararg names: String): List<JsonObject> = names.flatMap { fixturesOf(it) }

    private fun fixtureById(id: String, vararg names: String): JsonObject = allFixtures(*names).firstOrNull { it.str("id") == id }
        ?: error("missing fixture id $id")

    private fun JsonObject.str(key: String): String {
        val value = checkNotNull(this[key] as? JsonPrimitive) { "missing fixture string field $key" }
        return value.content
    }

    private fun JsonObject.optStr(key: String): String? = (this[key] as? JsonPrimitive)?.content

    private fun JsonObject.obj(key: String): JsonObject {
        val value = checkNotNull(this[key] as? JsonObject) { "missing fixture object $key" }
        return value
    }

    // ---------- canonical byte codecs (test-only, mirror the Node contract) ----------

    private fun decodeHex(value: String): ByteArray {
        require(value.length % 2 == 0) { "malformed hex byte string (odd length): \"$value\"" }
        require(value.all { it in '0'..'9' || it in 'a'..'f' }) {
            "malformed hex byte string (not lowercase hex): \"$value\""
        }
        return value.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    }

    private fun encodeHex(bytes: ByteArray): String = bytes.joinToString("") { it.toUByte().toString(16).padStart(2, '0') }

    // Canonical RFC 4648 base64 only: multiples of 4 with at most two trailing
    // pad characters, standard alphabet, no embedded padding — java.util.Base64
    // alone is more permissive (it accepts unpadded input), so the length and
    // padding-position rules are enforced on top of its decoder.
    private fun decodeBase64(value: String): ByteArray {
        val padCount = value.count { it == '=' }
        require(value.length % 4 == 0) {
            "malformed base64 record (length ${value.length} is not a multiple of 4): \"$value\""
        }
        require(padCount <= 2) { "malformed base64 record (more than two pad characters): \"$value\"" }
        require(value.takeWhile { it != '=' }.length + padCount == value.length) {
            "malformed base64 record (padding must only be at the end): \"$value\""
        }
        require(value.all { it in 'A'..'Z' || it in 'a'..'z' || it in '0'..'9' || it == '+' || it == '/' || it == '=' }) {
            "malformed base64 record (non-canonical alphabet): \"$value\""
        }
        return java.util.Base64.getDecoder().decode(value).also { decoded ->
            require(encodeBase64(decoded) == value) {
                "malformed base64 record (non-canonical pad bits): \"$value\""
            }
        }
    }

    private fun encodeBase64(bytes: ByteArray): String = java.util.Base64.getEncoder().encodeToString(bytes)

    // ---------- tests ----------

    @Test
    fun `encoding and crypto fixture ids load from the shared protocol root`() {
        val encodingIds = fixturesOf("encoding.json").map { it.str("id") }
        assertEquals(encodingIds.size, encodingIds.toSet().size)
        assertTrue(encodingIds.contains("enc-record-push-max-seq-u64"))
        val cryptoIds = fixturesOf("crypto.json").map { it.str("id") }
        assertEquals(cryptoIds.size, cryptoIds.toSet().size)
        assertTrue(
            cryptoIds.containsAll(
                listOf(
                    "rfc9382-p256-sha256-hkdf-hmac-server-client",
                    "veles-record-push-event",
                ),
            ),
        )
    }

    @Test
    fun `accepted hex fixtures decode byte-for-byte and re-encode canonically`() {
        for (fixture in allFixtures("encoding.json")) {
            if (fixture.obj("expected").optStr("accepted") != "true") continue
            val bytesHex = fixture.obj("input").str("bytes")
            val decoded = decodeHex(bytesHex)
            assertEquals(bytesHex, encodeHex(decoded))
        }
    }

    @Test
    fun `u64 decimal strings are preserved exactly by the fixture JSON`() {
        val maxSequence = fixtureById("enc-record-push-max-seq-u64", "encoding.json")
            .obj("expected").obj("fields").str("sequence_number")
        assertEquals(U64_MAX_DECIMAL, maxSequence)
        assertEquals(maxSequence, java.math.BigInteger(maxSequence).toString())
        assertEquals(maxSequence.toULong(), ULong.MAX_VALUE)
        assertNull("the value one beyond the u64 maximum must not parse as ULong", U64_OVERFLOW_DECIMAL.toULongOrNull())
        val maxReceipt = fixtureById("enc-body-otp-event-max-bounds", "encoding.json")
            .obj("expected").obj("fields").str("receipt_epoch_seconds")
        assertEquals(U64_MAX_DECIMAL, maxReceipt)
        // limits.json carries sequence_max as a canonical decimal string.
        val limits = loadRootResourceJson("limits.json")
        val sequenceMax = checkNotNull(limits["limits"] as? JsonArray) { "limits.json must carry a limits array" }
            .filterIsInstance<JsonObject>()
            .first { it.str("name") == "sequence_max" }
        assertEquals(U64_MAX_DECIMAL, (sequenceMax["value"] as JsonPrimitive).content)
        // The u64-maximum sequence state trace survives JSON parsing with the
        // full decimal string intact.
        val state = fixtureById("st-rec-max-u64-sequence-then-terminated", "records.json")
        assertTrue(state.toString().contains(U64_MAX_DECIMAL))
    }

    private fun loadRootResourceJson(name: String): JsonObject {
        val resource = javaClass.getResourceAsStream("/$name")
            ?: error("missing protocol resource /$name: the test sourceset must map the shared protocol/v1 root")
        return Json.parseToJsonElement(resource.readBytes().decodeToString()).jsonObject
    }

    @Test
    fun `malformed hex byte strings fail to decode`() {
        for (malformed in listOf("AB", "abc", "0g00", "dead bee", "0xzz")) {
            try {
                decodeHex(malformed)
                error("decodeHex accepted malformed hex: \"$malformed\"")
            } catch (_: IllegalArgumentException) {
                // expected: lowercase even-length hex only
            }
        }
    }

    @Test
    fun `canonical base64 records decode byte-for-byte and round-trip`() {
        val roundTripFixture = fixtureById("brd-ev-message-binary-round-trip-preserved", "bridge.json")
        val embedded = roundTripFixture.obj("input").str("text")
            .let { Json.parseToJsonElement(it).jsonObject.str("record_base64") }
        assertEquals(BASE64_ROUND_TRIP_STRING, embedded)
        val decoded = decodeBase64(embedded)
        assertArrayEquals(BASE64_ROUND_TRIP_BYTES, decoded)
        assertEquals(BASE64_ROUND_TRIP_HEX, encodeHex(decoded))
        assertEquals(BASE64_ROUND_TRIP_STRING, encodeBase64(decoded))
        assertArrayEquals(BASE64_SIMPLE_VALID_BYTES, decodeBase64(BASE64_SIMPLE_VALID))
    }

    @Test
    fun `malformed base64 records taken from the shared fixtures fail to decode`() {
        for (id in listOf(
            "brd-req-send-base64-odd-length",
            "brd-req-send-base64-whitespace",
            "brd-req-send-base64-url-alphabet",
        )) {
            val fixture = fixtureById(id, "bridge.json")
            val recordBase64 = fixture.obj("input").str("text")
                .let { Json.parseToJsonElement(it).jsonObject.str("record_base64") }
            try {
                decodeBase64(recordBase64)
                error("decodeBase64 accepted malformed base64 from $id: \"$recordBase64\"")
            } catch (_: IllegalArgumentException) {
                // expected: RFC 4648 canonical alphabet with strict padding
            }
        }
    }

    @Test
    fun `crypto fixture outputs decode byte-for-byte and negatives bind to existing positives`() {
        val crypto = fixturesOf("crypto.json")
        val anchors = crypto.filter { it.str("id").startsWith("rfc9382-p256-sha256-hkdf-hmac-") }
        assertTrue("the RFC 9382 published anchors must be present", anchors.isNotEmpty())
        val positiveIds = crypto
            .filter { it.obj("expected").optStr("rejected") != "true" }
            .map { it.str("id") }
            .toSet()
        var hexByteOutputs = 0
        for (fixture in crypto) {
            val id = fixture.str("id")
            if (fixture.obj("expected").optStr("rejected") == "true") {
                val base = fixture.obj("input").obj("deviation").str("negates")
                assertTrue("negative \"$id\" references missing positive \"$base\"", base in positiveIds)
                continue
            }
            for ((name, output) in fixture.obj("expected").obj("outputs")) {
                val primitive = checkNotNull(output as? JsonPrimitive) { "$id.$name must be a string" }
                assertTrue("crypto output $id.$name must remain a JSON string", primitive.isString)
                val value = primitive.content
                if (value.startsWith("0x")) {
                    assertEquals(value, "0x" + encodeHex(decodeHex(value.removePrefix("0x"))))
                    hexByteOutputs += 1
                }
                // Other committed outputs are decimal lengths or display
                // strings ("94", "2732 08ED 8F9B ...") — they are not byte
                // strings and must pass through unchanged.
            }
        }
        assertTrue("crypto fixtures must carry 0x-prefixed hex byte outputs", hexByteOutputs > 0)
    }

    @Test
    fun `missing fixture ids fail for every consumer file`() {
        for (name in listOf("encoding.json", "crypto.json", "bridge.json")) {
            try {
                fixtureById("this-fixture-must-not-exist", name)
                error("lookup accepted a missing fixture id in $name")
            } catch (_: IllegalStateException) {
                // expected
            }
        }
    }

    @Test(expected = IllegalStateException::class)
    fun `absent resources fail loudly rather than silently passing`() {
        fixtureEnvelope("thisfile-does-not-exist.json")
    }

    @Test(expected = SerializationException::class)
    fun `malformed fixture JSON fails to parse`() {
        val envelope = fixtureEnvelope("encoding.json")
        Json.parseToJsonElement(envelope.toString() + "@@@")
    }
}
