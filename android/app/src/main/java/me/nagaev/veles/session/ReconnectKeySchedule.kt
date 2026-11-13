package me.nagaev.veles.session

import java.security.GeneralSecurityException
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

private const val TRANSCRIPT_LABEL = "Veles reconnect transcript v1"
private const val SALT_LABEL = "Veles reconnect salt v1"
private const val C2P_AES_LABEL = "Veles reconnect B-to-A AES key v1"
private const val C2P_NONCE_LABEL = "Veles reconnect B-to-A nonce v1"
private const val C2P_CONFIRM_LABEL = "Veles reconnect B-to-A confirmation v1"
private const val P2C_AES_LABEL = "Veles reconnect A-to-B AES key v1"
private const val P2C_NONCE_LABEL = "Veles reconnect A-to-B nonce v1"
private const val P2C_CONFIRM_LABEL = "Veles reconnect A-to-B confirmation v1"
private const val SESSION_ID_LABEL = "Veles reconnect session ID v1"
private const val CONFIRM_INPUT_LABEL = "Veles reconnect confirmation input v1"
private const val REC_CLIENT_LABEL = "Veles recovery client activation v1"
private const val REC_PHONE_LABEL = "Veles recovery phone activation v1"

/** OTP2-REC-020b/020c reconnect key material. */
data class ReconnectKeys(
    val c2pKey: ByteArray,
    val c2pPrefix: ByteArray,
    val clientConfirmKey: ByteArray,
    val p2cKey: ByteArray,
    val p2cPrefix: ByteArray,
    val phoneConfirmKey: ByteArray,
    val sessionId: ByteArray,
    /**
     * OTP2-CRY-019f recovery activation keys, non-null only when the peer record carried `ET`.
     * They stay zeroed once the session class resolves to `full`.
     */
    val recovery: RecoveryKeys? = null,
) {
    /** OTP2-STA-015: zero every derived byte array on teardown. */
    fun erase() {
        c2pKey.fill(0)
        c2pPrefix.fill(0)
        clientConfirmKey.fill(0)
        p2cKey.fill(0)
        p2cPrefix.fill(0)
        phoneConfirmKey.fill(0)
        sessionId.fill(0)
        recovery?.erase()
    }
}

/** OTP2-CRY-019f recovery activation keys: one Expand call per direction, info = label || ET. */
data class RecoveryKeys(
    val kRecClient: ByteArray,
    val kRecPhone: ByteArray,
) {
    /** OTP2-CRY-019f: zero both directional keys, in place. */
    fun erase() {
        kRecClient.fill(0)
        kRecPhone.fill(0)
    }
}

/** The OTP2-REC-020a/b/c reconnect transcript, ECDH schedule and confirmation HMACs. */
@Suppress("TooManyFunctions") // Transcript, HKDF schedule, confirmations and erasure
object ReconnectKeySchedule {
    private const val NONCE_BYTES = 16
    private const val SHARED_SECRET_BYTES = 32
    private const val SIGNATURE_BYTES = 64
    private const val HASH_BYTES = 32
    private const val AES_KEY_BYTES = 32
    private const val NONCE_PREFIX_BYTES = 4
    private const val SESSION_ID_BYTES = 32
    private const val HMAC_ALGORITHM = "HmacSHA256"

    fun rbase(phoneHello: ByteArray, clientHello: ByteArray): ByteArray = label(TRANSCRIPT_LABEL) + phoneHello + clientHello

    internal fun salt(phoneNonce: ByteArray, clientNonce: ByteArray): ByteArray {
        requireSized(phoneNonce, NONCE_BYTES)
        requireSized(clientNonce, NONCE_BYTES)
        return sha256(label(SALT_LABEL) + phoneNonce + clientNonce)
    }

    internal fun prk(ss: ByteArray, phoneNonce: ByteArray, clientNonce: ByteArray): ByteArray {
        requireSharedSecret(ss)
        return Hkdf.extract(salt(phoneNonce, clientNonce), ss)
    }

    fun derive(
        ss: ByteArray,
        phoneNonce: ByteArray,
        clientNonce: ByteArray,
        et: ByteArray? = null,
    ): ReconnectKeys {
        val prk = prk(ss, phoneNonce, clientNonce)
        try {
            return ReconnectKeys(
                c2pKey = Hkdf.expand(prk, label(C2P_AES_LABEL), AES_KEY_BYTES),
                c2pPrefix = Hkdf.expand(prk, label(C2P_NONCE_LABEL), NONCE_PREFIX_BYTES),
                clientConfirmKey = Hkdf.expand(prk, label(C2P_CONFIRM_LABEL), AES_KEY_BYTES),
                p2cKey = Hkdf.expand(prk, label(P2C_AES_LABEL), AES_KEY_BYTES),
                p2cPrefix = Hkdf.expand(prk, label(P2C_NONCE_LABEL), NONCE_PREFIX_BYTES),
                phoneConfirmKey = Hkdf.expand(prk, label(P2C_CONFIRM_LABEL), AES_KEY_BYTES),
                sessionId = Hkdf.expand(prk, label(SESSION_ID_LABEL), SESSION_ID_BYTES),
                recovery = et?.let { recoveryKeys(prk, it) },
            )
        } finally {
            prk.fill(0)
        }
    }

    /** OTP2-CRY-019f: both directions are derived whenever the peer record carries `ET`. */
    private fun recoveryKeys(prk: ByteArray, et: ByteArray): RecoveryKeys {
        requireSized(et, HASH_BYTES)
        return RecoveryKeys(
            kRecClient = Hkdf.expand(prk, label(REC_CLIENT_LABEL) + et, AES_KEY_BYTES),
            kRecPhone = Hkdf.expand(prk, label(REC_PHONE_LABEL) + et, AES_KEY_BYTES),
        )
    }

    fun confirmInput(rbase: ByteArray, phoneSig: ByteArray, clientSig: ByteArray): ByteArray {
        requireSized(phoneSig, SIGNATURE_BYTES)
        requireSized(clientSig, SIGNATURE_BYTES)
        return sha256(label(CONFIRM_INPUT_LABEL) + rbase + phoneSig + clientSig)
    }

    fun confirmation(key: ByteArray, input: ByteArray): ByteArray {
        requireSized(key, AES_KEY_BYTES)
        requireSized(input, HASH_BYTES)
        return hmac(key, input)
    }

    private fun label(value: String): ByteArray = value.toByteArray(Charsets.UTF_8)

    private fun requireSized(value: ByteArray, size: Int) {
        if (value.size != size) reject()
    }

    private fun requireSharedSecret(ss: ByteArray) {
        if (ss.size != SHARED_SECRET_BYTES || ss.all { it == 0.toByte() }) reject()
    }

    private fun sha256(data: ByteArray): ByteArray = try {
        MessageDigest.getInstance("SHA-256").digest(data)
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun hmac(key: ByteArray, data: ByteArray): ByteArray = try {
        Mac.getInstance(HMAC_ALGORITHM).run {
            init(SecretKeySpec(key, HMAC_ALGORITHM))
            doFinal(data)
        }
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun reject(): Nothing = throw SessionReject()
}
