package me.nagaev.veles.session

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OTP2-CRY-019f recovery activation keys: the `crypto.json` vectors, the no-`ET` case and the
 * mandatory erasure once a session resolves to the `full` class.
 */
class RecoveryKeysTest {
    private val reconnect = SessionFixtures.RECONNECT
    private val inputs = SessionFixtures.input(reconnect)
    private val outputs = SessionFixtures.outputs(reconnect)

    @Test
    fun recovery_keys_match_crypto_vectors() {
        val keys = ReconnectKeySchedule.derive(
            sharedSecret(),
            bytes(inputs, "phone_nonce"),
            bytes(inputs, "client_nonce"),
            bytes(inputs, "et"),
        )

        val recovery = keys.recovery ?: error("ET must derive the OTP2-CRY-019f recovery keys")
        assertArrayEquals(bytes(outputs, "k_rec_client"), recovery.kRecClient)
        assertArrayEquals(bytes(outputs, "k_rec_phone"), recovery.kRecPhone)
    }

    @Test
    fun no_et_no_recovery_keys() {
        val explicitNull = ReconnectKeySchedule.derive(
            sharedSecret(),
            bytes(inputs, "phone_nonce"),
            bytes(inputs, "client_nonce"),
            et = null,
        )
        assertNull(explicitNull.recovery)

        val defaulted = ReconnectKeySchedule.derive(
            sharedSecret(),
            bytes(inputs, "phone_nonce"),
            bytes(inputs, "client_nonce"),
        )
        assertNull(defaulted.recovery)
    }

    @Test
    fun recovery_keys_erased_in_full_class() = runTest {
        val client = TestClient()
        val record = PeerRecord(
            client.installationId,
            client.identity.publicKey,
            LocalRecordState.ACTIVE,
            bytes(inputs, "et"),
        )
        val handler = ContextCapturingHandler()
        val engine = TestPeers.engine(
            TestIdentity.generate().signer,
            TestPeers.lookup(record),
            FakeClock(),
            handler,
        )
        val progress = engine.driveToPeerConfirm(client)

        assertTrue(engine.onInbound(progress.clientConfirmRecord).isEmpty())
        val authenticated = engine.onSendComplete(progress.token, true)
            .single() as SessionEffect.Authenticated
        assertEquals(SessionClass.FULL, authenticated.sessionClass)
        assertTrue(engine.isOpen)

        // OTP2-CRY-019f: a full session never hands the recovery keys to the handler; the
        // holder was zeroed at the single class-fix point.
        val context = checkNotNull(handler.context) { "the handler must receive the session context" }
        assertEquals(SessionClass.FULL, context.sessionClass)
        assertNull(context.recovery)
    }

    @Test
    fun recovery_keys_erase_zeroes_both_directions() {
        val holder = RecoveryKeys(ByteArray(HASH_BYTES) { 7 }, ByteArray(HASH_BYTES) { 9 })

        holder.erase()

        assertArrayEquals(ByteArray(HASH_BYTES), holder.kRecClient)
        assertArrayEquals(ByteArray(HASH_BYTES), holder.kRecPhone)
    }

    private fun sharedSecret(): ByteArray = P256.ecdhX(
        P256.privateFromScalar(bytes(inputs, "phone_ephemeral_priv")),
        bytes(outputs, "client_ephemeral_pub"),
    )

    private fun bytes(source: JsonObject, key: String): ByteArray = SessionFixtures.bytes(source, key)

    private companion object {
        const val HASH_BYTES = 32
    }
}
