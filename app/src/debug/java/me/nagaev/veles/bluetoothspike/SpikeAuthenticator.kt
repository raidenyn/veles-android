package me.nagaev.veles.bluetoothspike

import java.security.MessageDigest
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

internal enum class SpikeProofRole(val wireName: String) {
    SERVER("server"),
    CLIENT("client"),
}

internal object SpikeAuthenticator {
    private const val TEST_KEY = "VELES_WEB_BLUETOOTH_SPIKE_ONLY_2026"
    private val encoder = Base64.getUrlEncoder().withoutPadding()

    fun proof(
        role: SpikeProofRole,
        clientNonce: String,
        serverNonce: String,
        sessionId: String,
    ): String {
        val transcript = "veles-spike-v1|${role.wireName}|$clientNonce|$serverNonce|$sessionId"
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(TEST_KEY.encodeToByteArray(), "HmacSHA256"))
        return encoder.encodeToString(mac.doFinal(transcript.encodeToByteArray()))
    }

    fun verify(
        role: SpikeProofRole,
        clientNonce: String,
        serverNonce: String,
        sessionId: String,
        candidate: String,
    ): Boolean = MessageDigest.isEqual(
        proof(role, clientNonce, serverNonce, sessionId).encodeToByteArray(),
        candidate.encodeToByteArray(),
    )
}
