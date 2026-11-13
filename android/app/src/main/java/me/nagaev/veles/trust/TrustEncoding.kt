package me.nagaev.veles.trust

import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.PublicKey
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec

internal object TrustEncoding {
    private const val COORDINATE_BYTES = 32
    private const val SEC1_BYTES = 65
    private const val HEX_RADIX = 16
    private const val UNCOMPRESSED_TAG = 4
    private const val X_END = 2 + COORDINATE_BYTES * 2
    fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }
    fun requireHex(value: String, size: Int) = checkTrust(value.length == size * 2 && value.all { it in "0123456789abcdef" })
    fun bytes(hex: String): ByteArray {
        requireHex(hex, hex.length / 2)
        return hex.chunked(2).map { it.toInt(HEX_RADIX).toByte() }.toByteArray()
    }
    fun publicKey(key: PublicKey): String {
        val point = (key as ECPublicKey).w
        fun coordinate(n: BigInteger) = n.toByteArray().takeLast(COORDINATE_BYTES).toByteArray().let {
            ByteArray(COORDINATE_BYTES - it.size) + it
        }
        return hex(byteArrayOf(UNCOMPRESSED_TAG.toByte()) + coordinate(point.affineX) + coordinate(point.affineY))
    }
    fun validatePublicKey(value: String) {
        requireHex(value, SEC1_BYTES)
        checkTrust(value.startsWith("04"))
        val x = BigInteger(value.substring(2, X_END), HEX_RADIX)
        val y = BigInteger(value.substring(X_END), HEX_RADIX)
        val parameters = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java)
        val p = (parameters.curve.field as java.security.spec.ECFieldFp).p
        checkTrust(x < p && y < p)
        checkTrust(
            y.modPow(BigInteger.TWO, p) ==
                (x * x * x + parameters.curve.a * x + parameters.curve.b).mod(p),
        )
    }
}
