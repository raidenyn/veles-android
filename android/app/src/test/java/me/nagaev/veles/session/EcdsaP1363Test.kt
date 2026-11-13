package me.nagaev.veles.session

import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.math.BigInteger
import java.security.MessageDigest
import java.security.PublicKey
import java.security.Signature
import java.security.interfaces.ECPublicKey

class EcdsaP1363Test {
    private val reconnect = SessionFixtures.RECONNECT
    private val outputs = SessionFixtures.outputs(reconnect)
    private val rbase = bytes(outputs, "rbase")
    private val phonePublic = bytes(outputs, "phone_identity_pub")
    private val clientPublic = bytes(outputs, "client_identity_pub")
    private val phoneSignature = bytes(outputs, "phone_sig")

    @Test fun verifiesFixtureSignatures() {
        val clientSignature = bytes(outputs, "client_sig")
        assertEquals(64, phoneSignature.size)
        assertEquals(64, clientSignature.size)
        EcdsaP1363.requireValid(phonePublic, rbase, phoneSignature)
        EcdsaP1363.requireValid(clientPublic, rbase, clientSignature)
    }

    @Test fun rejectsNegSigFixtures() {
        for (id in listOf("veles-neg-sig-high-s", "veles-neg-sig-der", "veles-neg-sig-truncated")) {
            val signature = bytes(SessionFixtures.deviation(id), "value")
            assertThrows(id, SessionReject::class.java) {
                EcdsaP1363.requireValid(phonePublic, rbase, signature)
            }
        }
    }

    @Test fun rejectsDoubleHash() {
        val keys = P256.generate()
        val publicPoint = publicPoint(keys.public)
        val doubleHashed = MessageDigest.getInstance("SHA-256").digest(rbase)
        val der = Signature.getInstance("SHA256withECDSA").run {
            initSign(keys.private)
            update(doubleHashed)
            sign()
        }
        val signature = EcdsaP1363.normalizeLowS(EcdsaP1363.fromDer(der))
        // The signature is a valid single-hash ECDSA signature over the double-hashed message...
        EcdsaP1363.requireValid(publicPoint, doubleHashed, signature)
        // ...so verification over rbase itself must reject the extra hash.
        assertThrows(SessionReject::class.java) {
            EcdsaP1363.requireValid(publicPoint, rbase, signature)
        }
    }

    @Test fun fromDerRejectsOverlongInteger() {
        val r = ByteArray(32) { 0x11 }
        val s = ByteArray(32) { 0x22 }
        // A 34-byte INTEGER content with two leading zero bytes is not canonical DER.
        val overlong = byteArrayOf(0x30, 0x46, 0x02, 0x22, 0x00, 0x00) +
            r +
            byteArrayOf(0x02, 0x20) +
            s
        assertThrows(SessionReject::class.java) { EcdsaP1363.fromDer(overlong) }

        // A 33-byte INTEGER with a redundant leading zero is not canonical either.
        val nonMinimal = byteArrayOf(0x30, 0x45, 0x02, 0x21, 0x00, 0x11) +
            ByteArray(31) { 0x11 } +
            byteArrayOf(0x02, 0x20) +
            s
        assertThrows(SessionReject::class.java) { EcdsaP1363.fromDer(nonMinimal) }

        // The canonical fixture DER does convert back to the committed 64-byte signature.
        val der = bytes(SessionFixtures.deviation("veles-neg-sig-der"), "value")
        assertArrayEquals(phoneSignature, EcdsaP1363.fromDer(der))
    }

    @Test fun normalizeLowSFlipsHighS() {
        val highS = bytes(SessionFixtures.deviation("veles-neg-sig-high-s"), "value")
        assertEquals(64, highS.size)
        assertArrayEquals(phoneSignature, EcdsaP1363.normalizeLowS(highS))
        assertArrayEquals(phoneSignature, EcdsaP1363.normalizeLowS(phoneSignature))
    }

    private fun bytes(source: JsonObject, key: String): ByteArray = SessionFixtures.bytes(source, key)

    private fun publicPoint(key: PublicKey): ByteArray {
        val point = (key as ECPublicKey).w
        return byteArrayOf(0x04) + coordinate(point.affineX) + coordinate(point.affineY)
    }

    private fun coordinate(value: BigInteger): ByteArray {
        val raw = value.toByteArray()
        return when {
            raw.size == COORDINATE_BYTES -> raw
            raw.size > COORDINATE_BYTES -> raw.copyOfRange(raw.size - COORDINATE_BYTES, raw.size)
            else -> ByteArray(COORDINATE_BYTES - raw.size) + raw
        }
    }

    private companion object {
        const val COORDINATE_BYTES = 32
    }
}
