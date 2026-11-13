package me.nagaev.veles.session

import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ReconnectHelloTest {
    private val reconnect = SessionFixtures.RECONNECT
    private val inputs = SessionFixtures.input(reconnect)
    private val outputs = SessionFixtures.outputs(reconnect)
    private val phonePayload = bytes(outputs, "phone_hello_payload")
    private val clientPayload = bytes(outputs, "client_hello_payload")

    @Test fun fixtureHelloRoundTrips() {
        val phone = ReconnectHello.decode(phonePayload, Role.PHONE)
        assertArrayEquals(phonePayload, phone.encode())
        assertArrayEquals(bytes(inputs, "phone_installation_id"), phone.installationId)
        assertArrayEquals(bytes(outputs, "phone_ephemeral_pub"), phone.ephemeralPublicKey)
        assertArrayEquals(bytes(inputs, "phone_nonce"), phone.nonce)
        assertEquals(Role.PHONE, phone.role)
        assertArrayEquals(SessionFixtures.caps(inputs, "phone_caps"), phone.caps)

        val client = ReconnectHello.decode(clientPayload, Role.CLIENT)
        assertArrayEquals(clientPayload, client.encode())
        assertArrayEquals(bytes(inputs, "client_installation_id"), client.installationId)
        assertArrayEquals(bytes(outputs, "client_ephemeral_pub"), client.ephemeralPublicKey)
        assertArrayEquals(bytes(inputs, "client_nonce"), client.nonce)
        assertEquals(Role.CLIENT, client.role)
        assertArrayEquals(SessionFixtures.caps(inputs, "client_caps"), client.caps)
    }

    @Test fun rejectsNegPointFixtures() {
        val ids = SessionFixtures.ids("veles-neg-point-")
        assertEquals(4, ids.size)
        for (id in ids) {
            val malformed = bytes(SessionFixtures.deviation(id), "value")
            val spliced = phonePayload.copyOfRange(0, HELLO_POINT_OFFSET) +
                malformed +
                phonePayload.copyOfRange(HELLO_POINT_END, phonePayload.size)
            assertThrows("$id hello", SessionReject::class.java) {
                ReconnectHello.decode(spliced, Role.PHONE)
            }
            assertThrows("$id point", SessionReject::class.java) { P256.requireValidPoint(malformed) }
        }
    }

    @Test fun rejectsCapsUnsortedDuplicateMissing01() {
        for (caps in listOf(
            byteArrayOf(0x02, 0x01),
            byteArrayOf(0x01, 0x01),
            byteArrayOf(0x02),
        )) {
            assertThrows(SessionReject::class.java) {
                ReconnectHello.decode(phoneHello(caps = caps), Role.PHONE)
            }
        }
    }

    @Test fun rejectsUnknownCapability() {
        val payload = phoneHello(caps = byteArrayOf(0x01, 0x02))
        assertThrows(SessionReject::class.java) {
            ReconnectHello.decode(payload, Role.PHONE, knownCaps = byteArrayOf(0x01))
        }
    }

    @Test fun rejectsVersion2() {
        assertThrows(SessionReject::class.java) {
            ReconnectHello.decode(phoneHello(version = 2), Role.PHONE)
        }
    }

    @Test fun rejectsWrongRole() {
        assertThrows(SessionReject::class.java) { ReconnectHello.decode(phonePayload, Role.CLIENT) }
        assertThrows(SessionReject::class.java) { ReconnectHello.decode(clientPayload, Role.PHONE) }
    }

    private fun phoneHello(
        caps: ByteArray = SessionFixtures.caps(inputs, "phone_caps"),
        version: Int = 1,
    ): ByteArray {
        val installationId = bytes(inputs, "phone_installation_id")
        val point = bytes(outputs, "phone_ephemeral_pub")
        val nonce = bytes(inputs, "phone_nonce")
        val payload = ByteArray(HELLO_FIXED + caps.size)
        installationId.copyInto(payload, 0)
        point.copyInto(payload, HELLO_POINT_OFFSET)
        nonce.copyInto(payload, HELLO_NONCE_OFFSET)
        payload[HELLO_ROLE_OFFSET] = Role.PHONE.wire.toByte()
        payload[HELLO_VERSION_OFFSET] = (version ushr 8).toByte()
        payload[HELLO_VERSION_OFFSET + 1] = version.toByte()
        payload[HELLO_CAPS_LEN_OFFSET] = (caps.size ushr 8).toByte()
        payload[HELLO_CAPS_LEN_OFFSET + 1] = caps.size.toByte()
        caps.copyInto(payload, HELLO_FIXED)
        return payload
    }

    private fun bytes(source: JsonObject, key: String): ByteArray = SessionFixtures.bytes(source, key)

    private companion object {
        const val HELLO_FIXED = 102
        const val HELLO_POINT_OFFSET = 16
        const val HELLO_POINT_END = HELLO_POINT_OFFSET + 65
        const val HELLO_NONCE_OFFSET = HELLO_POINT_END
        const val HELLO_ROLE_OFFSET = HELLO_NONCE_OFFSET + 16
        const val HELLO_VERSION_OFFSET = HELLO_ROLE_OFFSET + 1
        const val HELLO_CAPS_LEN_OFFSET = HELLO_VERSION_OFFSET + 2
    }
}
