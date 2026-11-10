package me.nagaev.veles.session

import java.security.GeneralSecurityException
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** RFC 5869 HKDF with HMAC-SHA-256 (OTP2-REC-020b). */
object Hkdf {
    private const val HASH_BYTES = 32
    private const val MAX_BLOCKS = 255
    private const val ALGORITHM = "HmacSHA256"

    fun extract(salt: ByteArray, ikm: ByteArray): ByteArray {
        val key = if (salt.isEmpty()) ByteArray(HASH_BYTES) else salt
        return mac(key, ikm)
    }

    fun expand(prk: ByteArray, info: ByteArray, length: Int): ByteArray {
        if (prk.isEmpty() || length <= 0 || length > MAX_BLOCKS * HASH_BYTES) reject()
        val output = ByteArray(length)
        var produced = 0
        var previous = ByteArray(0)
        var counter = 1
        while (produced < length) {
            previous = mac(prk, previous + info + byteArrayOf(counter.toByte()))
            val copy = minOf(previous.size, length - produced)
            previous.copyInto(output, produced, 0, copy)
            produced += copy
            counter += 1
        }
        return output
    }

    private fun mac(key: ByteArray, data: ByteArray): ByteArray = try {
        Mac.getInstance(ALGORITHM).run {
            init(SecretKeySpec(key, ALGORITHM))
            doFinal(data)
        }
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun reject(): Nothing = throw SessionReject()
}
