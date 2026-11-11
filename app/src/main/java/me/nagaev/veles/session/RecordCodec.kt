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
    private const val CONFIRM_BODY_BYTES = 33
    private const val MAC_BODY_BYTES = 32
    private const val ENROLL_IDENTITY_MIN = 132
    private const val ENROLL_IDENTITY_MAX = 164
    private const val POLICY_STATE_BYTES = 2
    private const val POLICY_ALWAYS = 2
    private const val POLICY_RESERVED_MASK = 0xF0
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
        requireBody(type, body)
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
        requireBody(type, body)
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

    /**
     * The fixed body rules of the §7.3 table, applied to every seal and every successful open:
     * empty bodies, fixed widths, `enroll_identity` bounds, the `phone_policy_state` reserved
     * bits, and OTP2-ENC-003 bounded safe text for the `app_error` reason. Variable-length
     * application payloads (`pull_events`, `push_event`) stay with their handlers, which own
     * semantic event parsing.
     */
    internal fun requireBody(type: Int, body: ByteArray) {
        when (type) {
            RecordType.APP_HEARTBEAT.wire,
            RecordType.PULL_REQUEST.wire,
            RecordType.REMOVAL_REQUEST.wire,
            RecordType.REMOVAL_RESPONSE.wire,
            -> if (body.isNotEmpty()) reject()

            RecordType.RECONNECT_CONFIRM.wire -> if (body.size != CONFIRM_BODY_BYTES) reject()

            RecordType.ACTIVATION_COMMIT.wire,
            RecordType.ACTIVATION_COMPLETE.wire,
            RecordType.ACTIVATION_ACK.wire,
            RecordType.ENROLLMENT_PREPARE.wire,
            RecordType.EVENT_ACK.wire,
            -> if (body.size != MAC_BODY_BYTES) reject()

            RecordType.ENROLL_IDENTITY.wire ->
                if (body.size !in ENROLL_IDENTITY_MIN..ENROLL_IDENTITY_MAX) reject()

            RecordType.APP_ERROR.wire -> requireAppErrorBody(body)

            RecordType.PHONE_POLICY_STATE.wire -> {
                if (body.size != POLICY_STATE_BYTES) reject()
                if (u8(body, 0) > POLICY_ALWAYS) reject()
                if (u8(body, 1) and POLICY_RESERVED_MASK != 0) reject()
            }
        }
    }

    private fun requireAppErrorBody(body: ByteArray) {
        if (body.size < APP_ERROR_HEADER_BYTES) reject()
        if (AppErrorCode.fromWire(u8(body, 0)) == null) reject()
        val reasonLength = u16(body, 1)
        if (reasonLength > APP_ERROR_MAX_REASON) reject()
        if (body.size != APP_ERROR_HEADER_BYTES + reasonLength) reject()
        val decoder = Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        val reason = try {
            decoder.decode(ByteBuffer.wrap(body, APP_ERROR_HEADER_BYTES, reasonLength)).toString()
        } catch (_: CharacterCodingException) {
            reject()
        }
        // OTP2-ENC-003: no U+0000..U+001F and no U+007F in any Veles string.
        if (reason.any { it < ' ' || it == '\u007f' }) reject()
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
