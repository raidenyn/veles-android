package me.nagaev.veles.session

import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.security.GeneralSecurityException
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** A verified and decrypted protected record. */
data class OpenedRecord(val type: Int, val sequence: ULong, val body: ByteArray)

/**
 * §7 protected record format: 23-byte header (also the AAD) || AES-256-GCM ciphertext || tag,
 * nonce `prefix[4] || u64be(sequence)`, at most 4096 bytes per record.
 */
@Suppress("TooManyFunctions") // Header, nonce, AES-GCM, bounds and body validators
object RecordCodec {
    const val HEADER = 23
    const val TAG = 16
    const val MAX_RECORD = 4096
    const val MAX_CIPHERTEXT = 4057

    private const val PROTOCOL_VERSION = 1
    private const val SCHEMA_VERSION = 1
    private const val U8_MASK = 0xFF
    private const val BITS_PER_BYTE = 8
    private const val TYPE_OFFSET = 3
    private const val SEQUENCE_OFFSET = 5
    private const val LENGTH_OFFSET = 13
    private const val SESSION_OFFSET = 15
    private const val SESSION_PREFIX_BYTES = 8
    private const val NONCE_PREFIX_BYTES = 4
    private const val NONCE_BYTES = 12
    private const val AES_KEY_BYTES = 32
    private const val GCM_TAG_BITS = 128
    private const val APP_ERROR_HEADER_BYTES = 3
    private const val APP_ERROR_MAX_REASON = 64
    private const val AES_TRANSFORMATION = "AES/GCM/NoPadding"

    fun seal(
        key: ByteArray,
        prefix: ByteArray,
        sessionIdPrefix: ByteArray,
        direction: Direction,
        type: Int,
        sequence: ULong,
        body: ByteArray,
    ): ByteArray {
        requireKeyMaterial(key, prefix, sessionIdPrefix)
        requireType(type, direction)
        if (body.size > MAX_CIPHERTEXT) reject()
        val header = header(direction, type, sequence, body.size, sessionIdPrefix)
        val record = header + encrypt(key, nonce(prefix, sequence), header, body)
        if (record.size > MAX_RECORD) reject()
        return record
    }

    fun open(
        key: ByteArray,
        prefix: ByteArray,
        sessionIdPrefix: ByteArray,
        direction: Direction,
        expectedSequence: ULong,
        record: ByteArray,
    ): OpenedRecord {
        requireKeyMaterial(key, prefix, sessionIdPrefix)
        if (record.size < HEADER + TAG || record.size > MAX_RECORD) reject()
        if (u8(record, 0) != PROTOCOL_VERSION || u8(record, 1) != SCHEMA_VERSION) reject()
        if (u8(record, 2) != direction.wire) reject()
        val type = u16(record, TYPE_OFFSET)
        requireType(type, direction)
        val sequence = ByteBuffer.wrap(record).getLong(SEQUENCE_OFFSET).toULong()
        if (sequence != expectedSequence) reject()
        val ciphertextLength = u16(record, LENGTH_OFFSET)
        if (ciphertextLength > MAX_CIPHERTEXT) reject()
        if (record.size != HEADER + ciphertextLength + TAG) reject()
        if (!sessionIdPrefix.contentEquals(record.copyOfRange(SESSION_OFFSET, HEADER))) reject()
        val header = record.copyOfRange(0, HEADER)
        val body = decrypt(key, nonce(prefix, sequence), header, record.copyOfRange(HEADER, record.size))
        validateBody(type, body)
        return OpenedRecord(type, sequence, body)
    }

    private fun header(
        direction: Direction,
        type: Int,
        sequence: ULong,
        ciphertextLength: Int,
        sessionIdPrefix: ByteArray,
    ): ByteArray {
        val header = ByteArray(HEADER)
        header[0] = PROTOCOL_VERSION.toByte()
        header[1] = SCHEMA_VERSION.toByte()
        header[2] = direction.wire.toByte()
        header[TYPE_OFFSET] = (type ushr BITS_PER_BYTE).toByte()
        header[TYPE_OFFSET + 1] = type.toByte()
        ByteBuffer.wrap(header).putLong(SEQUENCE_OFFSET, sequence.toLong())
        header[LENGTH_OFFSET] = (ciphertextLength ushr BITS_PER_BYTE).toByte()
        header[LENGTH_OFFSET + 1] = ciphertextLength.toByte()
        sessionIdPrefix.copyInto(header, SESSION_OFFSET)
        return header
    }

    private fun nonce(prefix: ByteArray, sequence: ULong): ByteArray {
        val nonce = ByteArray(NONCE_BYTES)
        prefix.copyInto(nonce, 0)
        ByteBuffer.wrap(nonce).putLong(NONCE_PREFIX_BYTES, sequence.toLong())
        return nonce
    }

    private fun requireKeyMaterial(key: ByteArray, prefix: ByteArray, sessionIdPrefix: ByteArray) {
        if (key.size != AES_KEY_BYTES) reject()
        if (prefix.size != NONCE_PREFIX_BYTES) reject()
        if (sessionIdPrefix.size != SESSION_PREFIX_BYTES) reject()
    }

    private fun requireType(type: Int, direction: Direction): RecordType {
        val recordType = RecordType.fromWire(type) ?: reject()
        if (!recordType.allows(direction)) reject()
        return recordType
    }

    private fun validateBody(type: Int, body: ByteArray) {
        if (type != RecordType.APP_ERROR.wire) return
        if (body.size < APP_ERROR_HEADER_BYTES) reject()
        if (AppErrorCode.fromWire(u8(body, 0)) == null) reject()
        val reasonLength = u16(body, 1)
        if (reasonLength > APP_ERROR_MAX_REASON) reject()
        if (body.size != APP_ERROR_HEADER_BYTES + reasonLength) reject()
        val decoder = Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        try {
            decoder.decode(ByteBuffer.wrap(body, APP_ERROR_HEADER_BYTES, reasonLength))
        } catch (_: CharacterCodingException) {
            reject()
        }
    }

    private fun encrypt(key: ByteArray, nonce: ByteArray, header: ByteArray, body: ByteArray): ByteArray = try {
        Cipher.getInstance(AES_TRANSFORMATION).run {
            init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(GCM_TAG_BITS, nonce))
            updateAAD(header)
            doFinal(body)
        }
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun decrypt(key: ByteArray, nonce: ByteArray, header: ByteArray, ciphertext: ByteArray): ByteArray = try {
        Cipher.getInstance(AES_TRANSFORMATION).run {
            init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(GCM_TAG_BITS, nonce))
            updateAAD(header)
            doFinal(ciphertext)
        }
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun u8(bytes: ByteArray, offset: Int): Int = bytes[offset].toInt() and U8_MASK

    private fun u16(bytes: ByteArray, offset: Int): Int = (u8(bytes, offset) shl BITS_PER_BYTE) or u8(bytes, offset + 1)

    private fun reject(): Nothing = throw SessionReject()
}
