package me.nagaev.veles.session

import java.math.BigInteger
import java.security.GeneralSecurityException
import java.security.Signature

/**
 * Strict P-256 ECDSA codec: DER ↔ 64-byte P1363 `r || s`, low-S normalization and
 * low-S-only verification over a single SHA-256 of the message (OTP2-REC-020a).
 */
object EcdsaP1363 {
    private const val COORDINATE_BYTES = 32
    private const val SIGNATURE_BYTES = 64
    private const val DER_SEQUENCE_TAG = 0x30
    private const val DER_INTEGER_TAG = 0x02
    private const val DER_SHORT_FORM_LIMIT = 0x80
    private const val DER_HEADER_BYTES = 2
    private const val DER_MAX_INTEGER_CONTENT = 33
    private const val DER_HIGH_BIT = 0x80
    private const val U8_MASK = 0xFF

    private val ORDER = BigInteger("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
    private val HALF_ORDER = ORDER.shiftRight(1)

    /** Converts a canonical DER `SEQUENCE { INTEGER r, INTEGER s }` to P1363 fixed width. */
    fun fromDer(der: ByteArray): ByteArray {
        if (der.size < DER_HEADER_BYTES) reject()
        if ((der[0].toInt() and U8_MASK) != DER_SEQUENCE_TAG) reject()
        if ((der[1].toInt() and U8_MASK) >= DER_SHORT_FORM_LIMIT) reject()
        if (der.size != DER_HEADER_BYTES + (der[1].toInt() and U8_MASK)) reject()
        val (r, afterR) = readInteger(der, DER_HEADER_BYTES)
        val (s, afterS) = readInteger(der, afterR)
        if (afterS != der.size) reject()
        return fixed(r) + fixed(s)
    }

    /** Returns the low-S form of a 64-byte P1363 signature. */
    fun normalizeLowS(sig: ByteArray): ByteArray {
        if (sig.size != SIGNATURE_BYTES) reject()
        val s = BigInteger(1, sig.copyOfRange(COORDINATE_BYTES, SIGNATURE_BYTES))
        if (s >= ORDER) reject()
        val normalized = if (s > HALF_ORDER) ORDER.subtract(s) else s
        return sig.copyOfRange(0, COORDINATE_BYTES) + fixed(normalized)
    }

    /**
     * Verifies a 64-byte low-S signature over [message] with exactly one SHA-256. Any malformed
     * input, high-S encoding or verification failure throws [SessionReject].
     */
    fun requireValid(publicKey: ByteArray, message: ByteArray, sig: ByteArray) {
        if (sig.size != SIGNATURE_BYTES) reject()
        val r = BigInteger(1, sig.copyOfRange(0, COORDINATE_BYTES))
        val s = BigInteger(1, sig.copyOfRange(COORDINATE_BYTES, SIGNATURE_BYTES))
        if (r.signum() == 0 || s.signum() == 0) reject()
        if (r >= ORDER || s > HALF_ORDER) reject()
        val key = P256.requireValidPoint(publicKey)
        val verified = try {
            Signature.getInstance("SHA256withECDSA").run {
                initVerify(key)
                update(message)
                verify(toDer(r, s))
            }
        } catch (_: GeneralSecurityException) {
            reject()
        }
        if (!verified) reject()
    }

    private fun readInteger(der: ByteArray, offset: Int): Pair<BigInteger, Int> {
        if (offset + DER_HEADER_BYTES > der.size) reject()
        if ((der[offset].toInt() and U8_MASK) != DER_INTEGER_TAG) reject()
        val length = der[offset + 1].toInt() and U8_MASK
        if (length == 0 || length >= DER_SHORT_FORM_LIMIT || length > DER_MAX_INTEGER_CONTENT) reject()
        val start = offset + DER_HEADER_BYTES
        val end = start + length
        if (end > der.size) reject()
        val content = der.copyOfRange(start, end)
        if (((content[0].toInt() and U8_MASK) and DER_HIGH_BIT) != 0) reject()
        if (content.size > 1 && content[0] == 0.toByte() && (content[1].toInt() and DER_HIGH_BIT) == 0) reject()
        val significant = if (content[0] == 0.toByte()) content.size - 1 else content.size
        if (significant > COORDINATE_BYTES) reject()
        return BigInteger(1, content) to end
    }

    private fun fixed(value: BigInteger): ByteArray {
        val raw = value.toByteArray()
        return when {
            raw.size == COORDINATE_BYTES -> raw
            raw.size > COORDINATE_BYTES -> raw.copyOfRange(raw.size - COORDINATE_BYTES, raw.size)
            else -> ByteArray(COORDINATE_BYTES - raw.size) + raw
        }
    }

    private fun toDer(r: BigInteger, s: BigInteger): ByteArray {
        val rContent = r.toByteArray()
        val sContent = s.toByteArray()
        val contentLength = DER_HEADER_BYTES + rContent.size + DER_HEADER_BYTES + sContent.size
        if (contentLength >= DER_SHORT_FORM_LIMIT) reject()
        return byteArrayOf(
            DER_SEQUENCE_TAG.toByte(),
            contentLength.toByte(),
            DER_INTEGER_TAG.toByte(),
            rContent.size.toByte(),
        ) + rContent + byteArrayOf(
            DER_INTEGER_TAG.toByte(),
            sContent.size.toByte(),
        ) + sContent
    }

    private fun reject(): Nothing = throw SessionReject()
}
