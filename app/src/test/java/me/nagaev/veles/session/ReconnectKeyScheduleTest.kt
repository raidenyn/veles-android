package me.nagaev.veles.session

import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ReconnectKeyScheduleTest {
    private val reconnect = SessionFixtures.RECONNECT
    private val inputs = SessionFixtures.input(reconnect)
    private val outputs = SessionFixtures.outputs(reconnect)

    @Test fun fixtureScheduleMatches() {
        val phoneNonce = bytes(inputs, "phone_nonce")
        val clientNonce = bytes(inputs, "client_nonce")
        val sharedSecret = sharedSecret()

        assertArrayEquals(bytes(outputs, "ss"), sharedSecret)
        assertArrayEquals(
            bytes(outputs, "reconnect_salt"),
            ReconnectKeySchedule.salt(phoneNonce, clientNonce),
        )
        assertArrayEquals(
            bytes(outputs, "reconnect_prk"),
            ReconnectKeySchedule.prk(sharedSecret, phoneNonce, clientNonce),
        )

        val keys = ReconnectKeySchedule.derive(sharedSecret, phoneNonce, clientNonce)
        assertArrayEquals(bytes(outputs, "k_ba_aes"), keys.c2pKey)
        assertArrayEquals(bytes(outputs, "k_ba_nonce_prefix"), keys.c2pPrefix)
        assertArrayEquals(bytes(outputs, "k_ba_confirmation"), keys.clientConfirmKey)
        assertArrayEquals(bytes(outputs, "k_ab_aes"), keys.p2cKey)
        assertArrayEquals(bytes(outputs, "k_ab_nonce_prefix"), keys.p2cPrefix)
        assertArrayEquals(bytes(outputs, "k_ab_confirmation"), keys.phoneConfirmKey)
        assertArrayEquals(bytes(outputs, "session_id"), keys.sessionId)

        val rbase = ReconnectKeySchedule.rbase(
            bytes(outputs, "phone_hello_payload"),
            bytes(outputs, "client_hello_payload"),
        )
        assertArrayEquals(bytes(outputs, "rbase"), rbase)
        val confirmInput = ReconnectKeySchedule.confirmInput(
            rbase,
            bytes(outputs, "phone_sig"),
            bytes(outputs, "client_sig"),
        )
        assertArrayEquals(bytes(outputs, "reconnect_confirm_input"), confirmInput)
        assertArrayEquals(
            bytes(outputs, "conf_a"),
            ReconnectKeySchedule.confirmation(keys.phoneConfirmKey, confirmInput),
        )
        assertArrayEquals(
            bytes(outputs, "conf_b"),
            ReconnectKeySchedule.confirmation(keys.clientConfirmKey, confirmInput),
        )
    }

    @Test fun rejectsZeroSharedSecret() {
        assertThrows(SessionReject::class.java) {
            ReconnectKeySchedule.derive(
                ByteArray(SHARED_SECRET_BYTES),
                bytes(inputs, "phone_nonce"),
                bytes(inputs, "client_nonce"),
            )
        }
    }

    private fun sharedSecret(): ByteArray {
        val fromPhone = P256.ecdhX(
            P256.privateFromScalar(bytes(inputs, "phone_ephemeral_priv")),
            bytes(outputs, "client_ephemeral_pub"),
        )
        val fromClient = P256.ecdhX(
            P256.privateFromScalar(bytes(inputs, "client_ephemeral_priv")),
            bytes(outputs, "phone_ephemeral_pub"),
        )
        assertArrayEquals(fromPhone, fromClient)
        return fromPhone
    }

    private fun bytes(source: JsonObject, key: String): ByteArray = SessionFixtures.bytes(source, key)

    private companion object {
        const val SHARED_SECRET_BYTES = 32
    }
}
