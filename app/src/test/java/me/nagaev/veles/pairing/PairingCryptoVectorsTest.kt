package me.nagaev.veles.pairing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import me.nagaev.veles.session.Role
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP-10 crypto-vector consumer for the Kotlin core seam. Plain JUnit, no
 * Robolectric: this class drives the real host-built Rust core through the JNI
 * boundary the phone ships. The fixed-scalar pA/pB/cA/cB/Ke outputs of the positive
 * vectors belong to the OTP-03 conformance library (the production core generates
 * its own scalars), so the positives here prove that both roles derive the same
 * `Ke` and that the confirmation chain verifies; the negatives prove the fixed
 * points and a wrong code fail closed with no open handle. Role mismatch fails at
 * confirmation; a fully swapped role pair is cryptographically self-consistent, so
 * its rejection is the phone's fixed-role policy (OTP2-CRY-002) above this seam.
 */
class PairingCryptoVectorsTest : PairingTestBase() {
    @Test
    fun pairing_code_vectors_round_trip_on_the_real_core() {
        val positives = fixtures().filter { it.str("id").startsWith("veles-pairing-code-") }
        assertEquals(2, positives.size)

        for (fixture in positives) {
            roundTrip(fixture.obj("input").str("code"), hex(fixture.obj("expected").obj("outputs").str("ctx")))
        }
        assertEquals(0, core.openHandles())
    }

    @Test
    fun leading_zero_code_000042_round_trips_on_the_real_core() {
        roundTrip("000042", baseContext())
        assertEquals(0, core.openHandles())
    }

    @Test
    fun wrong_code_vector_fails_confirmation_without_leaking_a_handle() {
        val fixture = fixture("veles-neg-wrong-code")
        val input = fixture.obj("input")
        val deviation = input.obj("deviation")
        val context = contextOf(deviation.str("negates"))
        val phone = core.start(Role.PHONE, input.str("code").toByteArray(Charsets.US_ASCII), context)
        val client = core.start(Role.CLIENT, deviation.str("value").toByteArray(Charsets.US_ASCII), context)

        PairingExecution(core, phone.handle).use { phoneSide ->
            PairingExecution(core, client.handle).use { clientSide ->
                val ca = phoneSide.exchange(client.point)
                assertEquals(CONFIRMATION_BYTES, ca.size)
                assertEquals(0, clientSide.exchange(phone.point).size)
                assertThrows(
                    "a wrong code must fail the SPAKE2 confirmation generically",
                    PairingCoreException::class.java,
                ) { clientSide.confirm(ca) }
                assertThrows("the phone cannot finish without a verified cB", PairingCoreException::class.java) {
                    phoneSide.finish()
                }
            }
        }
        assertEquals(0, core.openHandles())
    }

    @Test
    fun role_mismatch_vector_fails_confirmation() {
        val base = fixture("veles-neg-role-mismatch").obj("input").obj("deviation").str("negates")
        val context = contextOf(base)
        val code = fixture(base).obj("input").str("code").toByteArray(Charsets.US_ASCII)
        val first = core.start(Role.PHONE, code, context)
        val second = core.start(Role.PHONE, code, context)

        PairingExecution(core, first.handle).use { firstSide ->
            PairingExecution(core, second.handle).use { secondSide ->
                val firstCa = firstSide.exchange(second.point)
                val secondCa = secondSide.exchange(first.point)
                assertEquals(CONFIRMATION_BYTES, firstCa.size)
                assertEquals(CONFIRMATION_BYTES, secondCa.size)
                assertThrows(PairingCoreException::class.java) { firstSide.confirm(secondCa) }
                assertThrows(PairingCoreException::class.java) { secondSide.confirm(firstCa) }
            }
        }
        assertEquals(0, core.openHandles())
    }

    @Test
    fun role_swap_vector_is_a_fixed_role_policy_not_a_core_break() {
        val base = fixture("veles-neg-role-swap").obj("input").obj("deviation").str("negates")
        val context = contextOf(base)
        val code = fixture(base).obj("input").str("code").toByteArray(Charsets.US_ASCII)
        // The core exposes both SPAKE2 roles; it does not pin them to endpoints. A
        // fully swapped pair (phone runs B, client runs A) is cryptographically
        // self-consistent, so the vector's role-stage rejection is the fixed-role
        // policy of the phone machine (OTP2-CRY-002, Task 5), not a core failure.
        // The wire contract is what makes the swap visible: the phone's B side
        // returns no confirmation at exchange and the client's A side does.
        val phone = core.start(Role.CLIENT, code, context)
        val client = core.start(Role.PHONE, code, context)

        PairingExecution(core, phone.handle).use { phoneSide ->
            PairingExecution(core, client.handle).use { clientSide ->
                val ca = clientSide.exchange(phone.point)
                assertEquals("party A produces the first confirmation", CONFIRMATION_BYTES, ca.size)
                assertTrue("party B produces no confirmation at exchange", phoneSide.exchange(client.point).isEmpty())
                val cb = phoneSide.confirm(ca)
                assertEquals(CONFIRMATION_BYTES, cb.size)
                assertEquals(0, clientSide.confirm(cb).size)
                assertArrayEquals(phoneSide.finish(), clientSide.finish())
            }
        }
        assertEquals(0, core.openHandles())
    }

    @Test
    fun point_negatives_are_rejected_and_destroy_the_handle() {
        val ids = listOf(
            "veles-neg-point-off-curve",
            "veles-neg-point-zero-coordinates",
            "veles-neg-point-infinity",
            "veles-neg-point-compressed",
        )
        for (id in ids) {
            val point = hex(fixture(id).obj("input").obj("deviation").str("value"))
            val started = core.start(Role.PHONE, baseCode(), baseContext())
            assertThrows(id, PairingCoreException::class.java) { core.exchange(started.handle, point) }
            assertEquals("$id must destroy the handle", 0, core.openHandles())
            assertThrows(PairingCoreException::class.java) { core.finish(started.handle) }
        }
    }

    @Test
    fun shared_secret_identity_vector_fails_closed_on_both_views() {
        val deviation = fixture("veles-neg-shared-secret-identity").obj("input").obj("deviation")

        val phone = core.start(Role.PHONE, baseCode(), baseContext())
        assertThrows(PairingCoreException::class.java) {
            core.exchange(phone.handle, hex(deviation.str("value")))
        }
        assertEquals(0, core.openHandles())

        val client = core.start(Role.CLIENT, baseCode(), baseContext())
        assertThrows(PairingCoreException::class.java) {
            core.exchange(client.handle, hex(deviation.str("value2")))
        }
        assertEquals(0, core.openHandles())
    }

    private fun roundTrip(code: String, context: ByteArray) {
        val codeBytes = code.toByteArray(Charsets.US_ASCII)
        val phone = core.start(Role.PHONE, codeBytes, context)
        val client = core.start(Role.CLIENT, codeBytes, context)
        assertEquals(POINT_BYTES, phone.point.size)
        assertEquals(POINT_BYTES, client.point.size)

        PairingExecution(core, phone.handle).use { phoneSide ->
            PairingExecution(core, client.handle).use { clientSide ->
                val ca = phoneSide.exchange(client.point)
                assertEquals(CONFIRMATION_BYTES, ca.size)
                assertEquals(0, clientSide.exchange(phone.point).size)
                val cb = clientSide.confirm(ca)
                assertEquals(CONFIRMATION_BYTES, cb.size)
                assertEquals(0, phoneSide.confirm(cb).size)
                val phoneKey = phoneSide.finish()
                val clientKey = clientSide.finish()
                assertEquals(KEY_BYTES, phoneKey.size)
                assertArrayEquals("both roles must derive the same Ke", phoneKey, clientKey)
            }
        }
    }

    private fun baseCode(): ByteArray = fixture(BASE_POSITIVE).obj("input").str("code").toByteArray(Charsets.US_ASCII)

    private fun baseContext(): ByteArray = contextOf(BASE_POSITIVE)

    private fun contextOf(id: String): ByteArray = hex(fixture(id).obj("expected").obj("outputs").str("ctx"))

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

    private fun hex(value: String): ByteArray {
        val text = value.removePrefix("0x")
        require(text.length % 2 == 0) { "malformed hex value" }
        return ByteArray(text.length / 2) { index ->
            text.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
    }

    private companion object {
        const val BASE_POSITIVE = "veles-pairing-code-000000"
        const val POINT_BYTES = 65
        const val CONFIRMATION_BYTES = 32
        const val KEY_BYTES = 16
    }
}
