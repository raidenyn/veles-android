package me.nagaev.veles.session

import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.GeneralSecurityException
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.interfaces.ECPrivateKey
import java.security.interfaces.ECPublicKey
import java.security.spec.ECFieldFp
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPrivateKeySpec
import java.security.spec.ECPublicKeySpec
import javax.crypto.KeyAgreement

/** P-256 point validation, key generation and ECDH over JCA (OTP2-CRY-005a, OTP2-REC-020b). */
object P256 {
    private const val CURVE_NAME = "secp256r1"
    private const val COORDINATE_BYTES = 32
    private const val POINT_BYTES = 65
    private const val UNCOMPRESSED_TAG = 0x04
    private const val U8_MASK = 0xFF

    /**
     * Validates an uncompressed SEC1 point strictly: exact length and `0x04` prefix first, then
     * the all-zero identity shape, the field range, and the curve equation. Some JCA providers
     * skip the curve check, so it is performed here as well.
     */
    fun requireValidPoint(uncompressed: ByteArray): ECPublicKey {
        if (uncompressed.size != POINT_BYTES) reject()
        if ((uncompressed[0].toInt() and U8_MASK) != UNCOMPRESSED_TAG) reject()
        val parameters = parameters()
        val x = BigInteger(1, uncompressed.copyOfRange(1, 1 + COORDINATE_BYTES))
        val y = BigInteger(1, uncompressed.copyOfRange(1 + COORDINATE_BYTES, POINT_BYTES))
        if (x.signum() == 0 && y.signum() == 0) reject()
        val p = (parameters.curve.field as ECFieldFp).p
        if (x >= p || y >= p) reject()
        if (!onCurve(x, y, parameters)) reject()
        return try {
            KeyFactory.getInstance("EC")
                .generatePublic(ECPublicKeySpec(ECPoint(x, y), parameters)) as ECPublicKey
        } catch (_: GeneralSecurityException) {
            reject()
        }
    }

    fun generate(): KeyPair = try {
        KeyPairGenerator.getInstance("EC")
            .apply { initialize(ECGenParameterSpec(CURVE_NAME)) }
            .generateKeyPair()
    } catch (_: GeneralSecurityException) {
        reject()
    }

    fun privateFromScalar(s: ByteArray): ECPrivateKey {
        if (s.size != COORDINATE_BYTES) reject()
        val value = BigInteger(1, s)
        val parameters = parameters()
        if (value.signum() == 0 || value >= parameters.order) reject()
        return try {
            KeyFactory.getInstance("EC")
                .generatePrivate(ECPrivateKeySpec(value, parameters)) as ECPrivateKey
        } catch (_: GeneralSecurityException) {
            reject()
        }
    }

    /** ECDH shared secret as the 32-byte X coordinate; an identity or all-zero result rejects. */
    fun ecdhX(priv: PrivateKey, peer: ByteArray): ByteArray {
        val peerKey = requireValidPoint(peer)
        val secret = try {
            KeyAgreement.getInstance("ECDH").run {
                init(priv)
                doPhase(peerKey, true)
                generateSecret()
            }
        } catch (_: GeneralSecurityException) {
            reject()
        }
        if (secret.size > COORDINATE_BYTES) reject()
        val x = if (secret.size == COORDINATE_BYTES) secret else ByteArray(COORDINATE_BYTES - secret.size) + secret
        if (x.all { it == 0.toByte() }) reject()
        return x
    }

    private fun onCurve(x: BigInteger, y: BigInteger, parameters: ECParameterSpec): Boolean {
        val p = (parameters.curve.field as ECFieldFp).p
        val left = y.multiply(y).mod(p)
        // `curve.a` is -3 mod p for P-256; using it keeps the equation y² = x³ + ax + b exact.
        val right = x.multiply(x).multiply(x)
            .add(parameters.curve.a.multiply(x))
            .add(parameters.curve.b)
            .mod(p)
        return left == right
    }

    private fun parameters(): ECParameterSpec = try {
        AlgorithmParameters.getInstance("EC")
            .apply { init(ECGenParameterSpec(CURVE_NAME)) }
            .getParameterSpec(ECParameterSpec::class.java)
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun reject(): Nothing = throw SessionReject()
}
