package me.nagaev.veles.pairing

import kotlinx.coroutines.CancellationException
import me.nagaev.veles.session.AppErrorCode
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.RecordCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import java.security.GeneralSecurityException
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** One authenticated protected record received on the enrollment connection. */
internal class EnrollmentRecord(val type: RecordType, val body: ByteArray)

/**
 * The protected enrollment channel of one connection whose SPAKE2 execution verified `cB`
 * (OTP2-CRY-020a/020b, OTP2-STA-010a).
 *
 * The session is created only from the core's `Ke` and the canonical public context after the
 * phone verified `cB`; nothing in it can be built from a native handle, a peer hint or a wire
 * value. The local identity always comes from [InstallationTrustRepository.identity], and the
 * peer identity from the client's authenticated `enroll_identity`.
 *
 * It owns the enrollment record sequence counters (0 per direction), the session id prefix,
 * the two public SPAKE2 points and confirmations needed for `ET`, and the enrollment key
 * holder. Every record type outside the OTP2-CRY-020a allow-list rejects; [erase] zeroes every
 * key and every buffered value, and the machine calls it on every terminal path.
 */
@Suppress("LongParameterList") // One immutable field per enrollment value.
internal class EnrollmentSession private constructor(
    private val keys: EnrollmentKeys,
    private val context: ByteArray,
    private val sessionIdPrefix: ByteArray,
    private val phoneIdentityBody: ByteArray,
    private val phonePoint: ByteArray,
    private val clientPoint: ByteArray,
    private val phoneConfirm: ByteArray,
    private val clientConfirm: ByteArray,
    private val trust: InstallationTrustRepository,
) {
    private var rxSequence = 0uL
    private var txSequence = 0uL
    private var transcript: ByteArray? = null

    /** The peer id this enrollment persisted, or null when nothing was made durable. */
    var persistedPeerId: String? = null
        private set

    /** The enrollment key holder, exposed only for the erasure assertions. */
    internal val keysForTest: EnrollmentKeys get() = keys

    /** The phone's own canonical `enroll_identity` body, sealed as the first record. */
    fun ownIdentityFrame(): ByteArray {
        val frame = RecordCodec.seal(
            keys.aToBAesKey,
            keys.aToBNoncePrefix,
            sessionIdPrefix,
            Direction.PHONE_TO_CLIENT,
            RecordType.ENROLL_IDENTITY.wire,
            txSequence,
            phoneIdentityBody,
        )
        txSequence += 1uL
        return frame
    }

    /**
     * Opens one client→phone record at the next expected sequence. The record type must be in
     * the OTP2-CRY-020a enrollment allow-list; anything else throws [EnrollmentReject].
     */
    fun open(record: ByteArray): EnrollmentRecord {
        val opened = RecordCodec.open(
            keys.bToAAesKey,
            keys.bToANoncePrefix,
            sessionIdPrefix,
            Direction.CLIENT_TO_PHONE,
            rxSequence,
            record,
        )
        val type = RecordType.fromWire(opened.type)
        if (type == null || type !in ALLOWED_TYPES) {
            opened.body.fill(0)
            throw EnrollmentReject()
        }
        rxSequence += 1uL
        return EnrollmentRecord(type, opened.body)
    }

    /** OTP2-CRY-020b: `ET` over the context, both points, both confirmations and both bodies. */
    fun transcript(clientIdentityBody: ByteArray): ByteArray = EnrollmentRecords.transcript(
        context,
        phonePoint,
        clientPoint,
        phoneConfirm,
        clientConfirm,
        phoneIdentityBody,
        clientIdentityBody,
    )

    /**
     * OTP2-STA-012 then OTP2-STA-010a: the duplicate check runs before [addPending], and only a
     * durable complete pending record (with `ET`) opens the prepare gate. A failed persist
     * publishes nothing.
     */
    suspend fun persist(client: IdentityRecord, transcript: ByteArray): PersistResult {
        val peerId = TrustEncoding.hex(client.installationId)
        val existing = try {
            trust.peers()
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            return PersistResult.FAILED
        }
        if (existing.any { it.installationId == peerId }) return PersistResult.DUPLICATE
        val pending = PendingPeer(
            installationId = peerId,
            publicKey = TrustEncoding.hex(client.identityPubkey),
            label = client.label,
            deviceType = client.deviceType.wire,
            enrollmentTranscript = TrustEncoding.hex(transcript),
        )
        return try {
            trust.addPending(pending)
            persistedPeerId = peerId
            this.transcript = transcript.copyOf()
            PersistResult.PERSISTED
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            PersistResult.FAILED
        }
    }

    /** OTP2-CRY-019a `enrollment_prepare`: `HMAC-SHA-256(K_act_phone, ET || 0x00)`. */
    fun prepareFrame(): ByteArray {
        val et = transcript ?: throw EnrollmentReject()
        val mac = hmac(keys.phoneActivationKey, et + byteArrayOf(STAGE_PREPARE))
        return try {
            val frame = RecordCodec.seal(
                keys.aToBAesKey,
                keys.aToBNoncePrefix,
                sessionIdPrefix,
                Direction.PHONE_TO_CLIENT,
                RecordType.ENROLLMENT_PREPARE.wire,
                txSequence,
                mac,
            )
            txSequence += 1uL
            frame
        } finally {
            mac.fill(0)
        }
    }

    /** OTP2-STA-012 protected refusal: `app_error` with code `0x04` `enrollment_rejected`. */
    fun appErrorFrame(code: AppErrorCode): ByteArray {
        val reason = ERROR_REASON.toByteArray(Charsets.US_ASCII)
        val body = byteArrayOf(code.wire.toByte(), 0, reason.size.toByte()) + reason
        return try {
            val frame = RecordCodec.seal(
                keys.aToBAesKey,
                keys.aToBNoncePrefix,
                sessionIdPrefix,
                Direction.PHONE_TO_CLIENT,
                RecordType.APP_ERROR.wire,
                txSequence,
                body,
            )
            txSequence += 1uL
            frame
        } finally {
            body.fill(0)
        }
    }

    /** OTP2-CRY-011/019e: zeroes every secret and every buffered enrollment value. */
    fun erase() {
        keys.wipe()
        context.fill(0)
        sessionIdPrefix.fill(0)
        phoneIdentityBody.fill(0)
        phonePoint.fill(0)
        clientPoint.fill(0)
        phoneConfirm.fill(0)
        clientConfirm.fill(0)
        transcript?.fill(0)
        transcript = null
        persistedPeerId = null
        rxSequence = 0uL
        txSequence = 0uL
    }

    /** The outcome of [persist]; duplicate and storage failure take different wire paths. */
    enum class PersistResult { PERSISTED, DUPLICATE, FAILED }

    companion object {
        private const val STAGE_PREPARE: Byte = 0x00
        private const val HMAC_ALGORITHM = "HmacSHA256"
        private const val ERROR_REASON = "enrollment rejected"

        private val ALLOWED_TYPES = setOf(
            RecordType.APP_HEARTBEAT,
            RecordType.APP_ERROR,
            RecordType.ENROLL_IDENTITY,
            RecordType.ACTIVATION_COMMIT,
            RecordType.ACTIVATION_COMPLETE,
            RecordType.ACTIVATION_ACK,
            RecordType.ENROLLMENT_PREPARE,
        )

        /**
         * Derives the §5.1 schedule from the core's `Ke`, resolves the local identity from the
         * trust store, and builds the phone's own `enroll_identity` body. [shared] is zeroed by
         * the caller. Any failure wipes the freshly derived keys before propagating.
         */
        @Suppress("LongParameterList", "TooGenericExceptionCaught") // Wipes the fresh keys on any failure.
        suspend fun create(
            shared: ByteArray,
            context: ByteArray,
            phonePoint: ByteArray,
            clientPoint: ByteArray,
            phoneConfirm: ByteArray,
            clientConfirm: ByteArray,
            trust: InstallationTrustRepository,
            label: String,
            identityNonce: ByteArray,
        ): EnrollmentSession {
            val keys = EnrollmentSchedule.derive(shared, context)
            return try {
                val identity = trust.identity()
                val record = EnrollmentRecords.create(
                    TrustEncoding.bytes(identity.installationId),
                    label,
                    DeviceType.PHONE,
                    TrustEncoding.bytes(identity.publicKey),
                    identityNonce,
                )
                EnrollmentSession(
                    keys,
                    context.copyOf(),
                    EnrollmentSchedule.sessionIdPrefix(context),
                    EnrollmentRecords.encode(record, IdentityRole.PHONE),
                    phonePoint.copyOf(),
                    clientPoint.copyOf(),
                    phoneConfirm.copyOf(),
                    clientConfirm.copyOf(),
                    trust,
                )
            } catch (failure: Exception) {
                keys.wipe()
                throw failure
            }
        }

        private fun hmac(key: ByteArray, data: ByteArray): ByteArray = try {
            Mac.getInstance(HMAC_ALGORITHM).run {
                init(SecretKeySpec(key, HMAC_ALGORITHM))
                doFinal(data)
            }
        } catch (_: GeneralSecurityException) {
            throw EnrollmentReject()
        }
    }
}
