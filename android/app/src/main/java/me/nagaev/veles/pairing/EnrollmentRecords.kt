package me.nagaev.veles.pairing

import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.security.GeneralSecurityException
import java.security.MessageDigest

/** OTP2-REC-010 `device_type` values. Only these three exist in protocol version 1. */
enum class DeviceType(val wire: Int) {
    PHONE(0x00),
    DESKTOP(0x01),
    WATCH(0x02),
    ;

    companion object {
        fun fromWire(value: Int): DeviceType? = entries.firstOrNull { it.wire == value }
    }
}

/**
 * The sender's role. It fixes both the OTP2-REC-011a fingerprint `role_byte` and the
 * OTP2-REC-010 `device_type` set: the phone's record is `0x00`, a client record is
 * `0x01` (desktop) or `0x02` (watch).
 */
enum class IdentityRole(val fingerprintByte: Int, private val deviceTypes: Set<DeviceType>) {
    PHONE(0x01, setOf(DeviceType.PHONE)),
    CLIENT(0x02, setOf(DeviceType.DESKTOP, DeviceType.WATCH)),
    ;

    fun accepts(deviceType: DeviceType): Boolean = deviceType in deviceTypes
}

/** One canonical `enroll_identity` plaintext body (OTP2-REC-010). */
class IdentityRecord(
    val installationId: ByteArray,
    val label: String,
    val deviceType: DeviceType,
    val identityPubkey: ByteArray,
    val fingerprint: ByteArray,
    val nonce: ByteArray,
)

/**
 * OTP2-REC-010/011a/011: the `enroll_identity` body codec, the own-record fingerprint, the
 * pair fingerprint and the canonical enrollment transcript `ET` (OTP2-CRY-020b).
 *
 * The body is `installation_id[16] || label_len u16be || label || device_type u8 ||
 * identity_pubkey[65] || fingerprint[32] || nonce[16]`, 132..164 bytes. Decoding validates
 * the body structure, the role-bound device type, the point format and the OTP2-ENC-003 safe
 * text; [decodeFingerprintVerified] additionally recomputes the own-record fingerprint and rejects before
 * a caller can store anything. Full P-256 point validation belongs to the session layer
 * (`P256.requireValidPoint`) when the identity is authenticated.
 */
@Suppress("TooManyFunctions") // Body codec, fingerprint checks and the enrollment transcript
object EnrollmentRecords {
    private const val INSTALLATION_BYTES = 16
    private const val LABEL_MAX_BYTES = 32
    private const val PUBKEY_BYTES = 65
    private const val FINGERPRINT_BYTES = 32
    private const val NONCE_BYTES = 16
    private const val BODY_MIN_BYTES = 132
    private const val BODY_MAX_BYTES = 164
    private const val POINT_TAG = 0x04
    private const val LENGTH_OFFSET = 16
    private const val LABEL_OFFSET = 18
    private const val U8_MASK = 0xFF
    private const val BITS_PER_BYTE = 8
    private const val HIGH_BYTE_SHIFT = 8
    private const val MAX_CONTEXT_BYTES = 4096
    private const val DISPLAY_BYTES = 6
    private const val DISPLAY_GROUP_CHARS = 4
    private const val HEX_BYTE_CHARS = 2
    private const val HEX_RADIX = 16
    private const val CONFIRM_BYTES = 32
    private const val HASH_BYTES = 32

    private const val RECORD_FINGERPRINT_LABEL = "Veles enrollment record fingerprint v1"
    private const val PAIR_FINGERPRINT_LABEL = "Veles identity fingerprint v1"
    private const val TRANSCRIPT_LABEL = "Veles enrollment transcript v1"

    /** Builds a record and computes its OTP2-REC-011a own-record fingerprint. */
    fun create(
        installationId: ByteArray,
        label: String,
        deviceType: DeviceType,
        identityPubkey: ByteArray,
        nonce: ByteArray,
    ): IdentityRecord {
        if (installationId.size != INSTALLATION_BYTES) reject()
        requireLabel(label)
        requirePoint(identityPubkey)
        if (nonce.size != NONCE_BYTES) reject()
        val record = IdentityRecord(installationId, label, deviceType, identityPubkey, ByteArray(FINGERPRINT_BYTES), nonce)
        val fingerprint = recordFingerprint(record, roleFor(deviceType))
        return IdentityRecord(installationId, label, deviceType, identityPubkey, fingerprint, nonce)
    }

    /** Encodes the canonical body. The role-bound device type is enforced here. */
    fun encode(record: IdentityRecord, role: IdentityRole): ByteArray {
        val labelBytes = requireRecord(record, role)
        val body = record.installationId +
            u16(labelBytes.size) +
            labelBytes +
            byteArrayOf(record.deviceType.wire.toByte()) +
            record.identityPubkey +
            record.fingerprint +
            record.nonce
        if (body.size !in BODY_MIN_BYTES..BODY_MAX_BYTES) reject()
        return body
    }

    /** Decodes one body, enforcing bounds, role-bound device type, point format and safe text. */
    fun decode(body: ByteArray, role: IdentityRole): IdentityRecord {
        if (body.size !in BODY_MIN_BYTES..BODY_MAX_BYTES) reject()
        val labelLength = u16(body, LENGTH_OFFSET)
        if (labelLength > LABEL_MAX_BYTES) reject()
        if (body.size != BODY_MIN_BYTES + labelLength) reject()
        val label = decodeLabel(body, LABEL_OFFSET, labelLength)
        val deviceOffset = LABEL_OFFSET + labelLength
        val deviceType = DeviceType.fromWire(u8(body, deviceOffset)) ?: reject()
        if (!role.accepts(deviceType)) reject()
        val pubkeyOffset = deviceOffset + 1
        val fingerprintOffset = pubkeyOffset + PUBKEY_BYTES
        val nonceOffset = fingerprintOffset + FINGERPRINT_BYTES
        if (nonceOffset + NONCE_BYTES != body.size) reject()
        val identityPubkey = body.copyOfRange(pubkeyOffset, fingerprintOffset)
        requirePoint(identityPubkey)
        return IdentityRecord(
            installationId = body.copyOfRange(0, INSTALLATION_BYTES),
            label = label,
            deviceType = deviceType,
            identityPubkey = identityPubkey,
            fingerprint = body.copyOfRange(fingerprintOffset, nonceOffset),
            nonce = body.copyOfRange(nonceOffset, nonceOffset + NONCE_BYTES),
        )
    }

    /**
     * Decodes and verifies the own-record fingerprint before returning. This proves the
     * OTP2-REC-011a binding only; it is NOT a full authentication: the point is still only
     * format-checked. Callers that persist must use [decodeForStorage] with a validator.
     */
    fun decodeFingerprintVerified(body: ByteArray, role: IdentityRole): IdentityRecord {
        val record = decode(body, role)
        verifyOwnFingerprint(record, role)
        return record
    }

    /**
     * Decode, verify the own-record fingerprint, run [validatePoint] on the identity public
     * key, and only then invoke [store]. Task 7 passes `P256::requireValidPoint` here; a bad
     * fingerprint or an invalid point invokes neither the later validation nor the store.
     */
    fun decodeForStorage(
        body: ByteArray,
        role: IdentityRole,
        validatePoint: (ByteArray) -> Unit,
        store: (IdentityRecord) -> Unit,
    ): IdentityRecord {
        val record = decodeFingerprintVerified(body, role)
        validatePoint(record.identityPubkey)
        store(record)
        return record
    }

    /** OTP2-REC-011a: `SHA-256(label || role_byte || installation_id || identity_pubkey)`. */
    fun recordFingerprint(record: IdentityRecord, role: IdentityRole): ByteArray {
        if (!role.accepts(record.deviceType)) reject()
        if (record.installationId.size != INSTALLATION_BYTES) reject()
        requirePoint(record.identityPubkey)
        return sha256(
            label(RECORD_FINGERPRINT_LABEL) +
                byteArrayOf(role.fingerprintByte.toByte()) +
                record.installationId +
                record.identityPubkey,
        )
    }

    /** OTP2-REC-011: `SHA-256(label || phone_identity_pubkey || client_identity_pubkey)`. */
    fun pairFingerprint(phoneIdentityPubkey: ByteArray, clientIdentityPubkey: ByteArray): ByteArray {
        requirePoint(phoneIdentityPubkey)
        requirePoint(clientIdentityPubkey)
        return sha256(label(PAIR_FINGERPRINT_LABEL) + phoneIdentityPubkey + clientIdentityPubkey)
    }

    /** First 6 bytes of `fp_pair` as `XXXX XXXX XXXX` (12 uppercase hex characters). */
    fun displayFingerprint(pairFingerprint: ByteArray): String {
        if (pairFingerprint.size != HASH_BYTES) reject()
        val display = pairFingerprint.copyOf(DISPLAY_BYTES).toHex()
        return display.chunked(DISPLAY_GROUP_CHARS).joinToString(" ")
    }

    /**
     * OTP2-CRY-020b canonical `ET`. It binds the public context, both public SPAKE2 points and
     * confirmations, and both canonical identity bodies; it contains no secret-derived value.
     */
    fun transcript(
        context: ByteArray,
        phonePoint: ByteArray,
        clientPoint: ByteArray,
        phoneConfirm: ByteArray,
        clientConfirm: ByteArray,
        phoneIdentityBody: ByteArray,
        clientIdentityBody: ByteArray,
    ): ByteArray {
        if (context.size > MAX_CONTEXT_BYTES) reject()
        requirePoint(phonePoint)
        requirePoint(clientPoint)
        requireSized(phoneConfirm, CONFIRM_BYTES)
        requireSized(clientConfirm, CONFIRM_BYTES)
        requireIdentityBodySize(phoneIdentityBody)
        requireIdentityBodySize(clientIdentityBody)
        return sha256(
            label(TRANSCRIPT_LABEL) +
                lengthPrefixed(context) +
                lengthPrefixed(phonePoint) +
                lengthPrefixed(clientPoint) +
                lengthPrefixed(phoneConfirm) +
                lengthPrefixed(clientConfirm) +
                lengthPrefixed(phoneIdentityBody) +
                lengthPrefixed(clientIdentityBody),
        )
    }

    /** The role a record's `device_type` implies; used only by [create]. */
    private fun roleFor(deviceType: DeviceType): IdentityRole = when (deviceType) {
        DeviceType.PHONE -> IdentityRole.PHONE
        DeviceType.DESKTOP, DeviceType.WATCH -> IdentityRole.CLIENT
    }

    private fun requireRecord(record: IdentityRecord, role: IdentityRole): ByteArray {
        if (!role.accepts(record.deviceType)) reject()
        val labelBytes = requireLabel(record.label)
        requirePoint(record.identityPubkey)
        requireSized(record.fingerprint, FINGERPRINT_BYTES)
        requireSized(record.nonce, NONCE_BYTES)
        if (record.installationId.size != INSTALLATION_BYTES) reject()
        return labelBytes
    }

    private fun verifyOwnFingerprint(record: IdentityRecord, role: IdentityRole) {
        val expected = recordFingerprint(record, role)
        // OTP2-REC-011a: constant-time comparison, never a direct equality.
        if (!MessageDigest.isEqual(expected, record.fingerprint)) reject()
    }

    private fun requireLabel(label: String): ByteArray {
        val bytes = label.toByteArray(Charsets.UTF_8)
        if (bytes.size > LABEL_MAX_BYTES) reject()
        val roundTrip = decodeStrict(bytes)
        if (roundTrip != label) reject()
        requireSafeText(label)
        return bytes
    }

    private fun decodeLabel(body: ByteArray, offset: Int, length: Int): String {
        val label = decodeStrict(body.copyOfRange(offset, offset + length))
        requireSafeText(label)
        return label
    }

    private fun decodeStrict(bytes: ByteArray): String = try {
        Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes))
            .toString()
    } catch (_: CharacterCodingException) {
        reject()
    }

    /** OTP2-ENC-003: no U+0000..U+001F and no U+007F in any Veles string. */
    private fun requireSafeText(value: String) {
        if (value.any { it < ' ' || it == '\u007f' }) reject()
    }

    private fun requirePoint(point: ByteArray) {
        requireSized(point, PUBKEY_BYTES)
        if ((point[0].toInt() and U8_MASK) != POINT_TAG) reject()
    }

    private fun requireIdentityBodySize(body: ByteArray) {
        if (body.size !in BODY_MIN_BYTES..BODY_MAX_BYTES) reject()
    }

    private fun requireSized(value: ByteArray, size: Int) {
        if (value.size != size) reject()
    }

    private fun lengthPrefixed(value: ByteArray): ByteArray = u16(value.size) + value

    private fun u16(value: Int): ByteArray = byteArrayOf((value ushr HIGH_BYTE_SHIFT).toByte(), value.toByte())

    private fun u8(bytes: ByteArray, offset: Int): Int = bytes[offset].toInt() and U8_MASK

    private fun u16(bytes: ByteArray, offset: Int): Int = (u8(bytes, offset) shl BITS_PER_BYTE) or u8(bytes, offset + 1)

    private fun sha256(data: ByteArray): ByteArray = try {
        MessageDigest.getInstance("SHA-256").digest(data)
    } catch (_: GeneralSecurityException) {
        reject()
    }

    private fun label(value: String): ByteArray = value.toByteArray(Charsets.US_ASCII)

    private fun ByteArray.toHex(): String = joinToString("") { (it.toInt() and U8_MASK).toString(HEX_RADIX).padStart(HEX_BYTE_CHARS, '0') }
        .uppercase()

    private fun reject(): Nothing = throw EnrollmentReject()
}
