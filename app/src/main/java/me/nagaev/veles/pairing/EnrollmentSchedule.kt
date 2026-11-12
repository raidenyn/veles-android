package me.nagaev.veles.pairing

import me.nagaev.veles.session.Hkdf
import java.nio.ByteBuffer
import java.security.GeneralSecurityException
import java.security.MessageDigest

/**
 * Generic enrollment failure. It deliberately carries no message text: a record, a
 * confirmation or a fingerprint value must never reach a diagnostic through an exception.
 */
class EnrollmentReject : Exception()

/**
 * OTP2-CRY-007a/007b: the enrollment salt, `enrollment_prk` and the six directional
 * derivations. Every label below is verbatim ASCII from §5.1; the salt length prefix is RFC
 * 9382's u64 little-endian `len(AAD)` and is the only little-endian length in this schedule.
 */
object EnrollmentSchedule {
    private const val KE_BYTES = 16
    private const val AES_KEY_BYTES = 32
    private const val NONCE_PREFIX_BYTES = 4
    private const val ENROLLMENT_NONCE_BYTES = 12
    private const val SESSION_PREFIX_BYTES = 8
    private const val MAX_CONTEXT_BYTES = 4096
    private const val U64_BYTES = 8
    private const val BYTE_MASK = 0xFFL
    private const val BITS_PER_BYTE = 8

    private const val SALT_LABEL = "Veles SPAKE2 enrollment salt v1"
    private const val A_TO_B_AES_LABEL = "Veles enrollment A-to-B AES key v1"
    private const val A_TO_B_NONCE_LABEL = "Veles enrollment A-to-B nonce v1"
    private const val A_CONFIRMATION_LABEL = "Veles enrollment A confirmation v1"
    private const val B_TO_A_AES_LABEL = "Veles enrollment B-to-A AES key v1"
    private const val B_TO_A_NONCE_LABEL = "Veles enrollment B-to-A nonce v1"
    private const val B_CONFIRMATION_LABEL = "Veles enrollment B confirmation v1"
    private const val SESSION_ID_LABEL = "Veles enrollment session v1"

    /**
     * Derives the enrollment keys from the core's 16-byte `Ke` and the canonical public
     * context. The intermediate PRK is zeroed before returning; the caller owns the returned
     * holder and must [EnrollmentKeys.wipe] it.
     */
    fun derive(shared: ByteArray, context: ByteArray): EnrollmentKeys {
        requireContext(context)
        if (shared.size != KE_BYTES) reject()
        val prk = Hkdf.extract(salt(context), shared)
        try {
            return EnrollmentKeys(
                aToBAesKey = Hkdf.expand(prk, label(A_TO_B_AES_LABEL), AES_KEY_BYTES),
                aToBNoncePrefix = Hkdf.expand(prk, label(A_TO_B_NONCE_LABEL), NONCE_PREFIX_BYTES),
                phoneActivationKey = Hkdf.expand(prk, label(A_CONFIRMATION_LABEL), AES_KEY_BYTES),
                bToAAesKey = Hkdf.expand(prk, label(B_TO_A_AES_LABEL), AES_KEY_BYTES),
                bToANoncePrefix = Hkdf.expand(prk, label(B_TO_A_NONCE_LABEL), NONCE_PREFIX_BYTES),
                clientActivationKey = Hkdf.expand(prk, label(B_CONFIRMATION_LABEL), AES_KEY_BYTES),
            )
        } finally {
            prk.fill(0)
        }
    }

    /** OTP2-CRY-020a: `SHA-256("Veles enrollment session v1" || ctx)` first 8 bytes. */
    fun sessionIdPrefix(context: ByteArray): ByteArray {
        requireContext(context)
        return sha256(label(SESSION_ID_LABEL) + context).copyOf(SESSION_PREFIX_BYTES)
    }

    /** OTP2-CRY-007c: the directional 4-byte prefix followed by u64be(sequence). */
    fun nonce(prefix: ByteArray, sequence: ULong): ByteArray {
        if (prefix.size != NONCE_PREFIX_BYTES) reject()
        val nonce = ByteArray(ENROLLMENT_NONCE_BYTES)
        prefix.copyInto(nonce)
        ByteBuffer.wrap(nonce).putLong(NONCE_PREFIX_BYTES, sequence.toLong())
        return nonce
    }

    private fun salt(context: ByteArray): ByteArray {
        val length = ByteArray(U64_BYTES)
        var remaining = context.size.toLong()
        for (index in 0 until U64_BYTES) {
            length[index] = (remaining and BYTE_MASK).toByte()
            remaining = remaining ushr BITS_PER_BYTE
        }
        return sha256(label(SALT_LABEL) + length + context)
    }

    private fun requireContext(context: ByteArray) {
        if (context.size > MAX_CONTEXT_BYTES) reject()
    }

    private fun sha256(data: ByteArray): ByteArray = try {
        MessageDigest.getInstance("SHA-256").digest(data)
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun label(value: String): ByteArray = value.toByteArray(Charsets.US_ASCII)

    private fun reject(): Nothing = throw EnrollmentReject()
}

/**
 * The six §5.1 enrollment outputs, held only in memory for the enrollment connection
 * (OTP2-CRY-019e). The holder redacts itself and zeroes every field on [wipe]; [use] wipes on
 * both the success and the failure path.
 */
class EnrollmentKeys internal constructor(
    val aToBAesKey: ByteArray,
    val aToBNoncePrefix: ByteArray,
    val phoneActivationKey: ByteArray,
    val bToAAesKey: ByteArray,
    val bToANoncePrefix: ByteArray,
    val clientActivationKey: ByteArray,
) {
    private var wiped = false

    val isWiped: Boolean get() = wiped

    fun <T> use(block: (EnrollmentKeys) -> T): T = try {
        block(this)
    } finally {
        wipe()
    }

    fun wipe() {
        fields().forEach { it.fill(0) }
        wiped = true
    }

    /** Every mutable field, for erasure assertions; never for logging. */
    fun fields(): List<ByteArray> = listOf(
        aToBAesKey,
        aToBNoncePrefix,
        phoneActivationKey,
        bToAAesKey,
        bToANoncePrefix,
        clientActivationKey,
    )

    override fun toString(): String = "EnrollmentKeys(wiped=$wiped)"
}
