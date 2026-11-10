package me.nagaev.veles.session

import java.math.BigInteger
import java.security.KeyPair
import java.security.MessageDigest
import java.security.interfaces.ECPublicKey

/**
 * Phone-role reconnect engine: bytes in, [SessionEffect]s out (veles-v1.md §11.3 phone rows).
 *
 * The engine is a pure state machine. Time, randomness, the identity signer and the trust
 * lookup are injected, and it performs no repository writes: the host owns `lastSeen`, applied
 * on the [SessionEffect.Authenticated] effect for the current connection generation.
 *
 * The phone signs the transcript before it sees the client signature, re-checks the trust
 * record immediately before sealing its `reconnect_confirm`, and only reports
 * [SessionEffect.Authenticated] once the peer's confirm has verified AND its own confirm has
 * completed with `ok` — whichever of the two finishes second.
 */
@Suppress("TooManyFunctions") // Handshake states, record opening/sealing, timers and authorization
class PhoneSessionEngine(
    localInstallationId: ByteArray,
    private val signer: IdentitySigner,
    private val lookup: PeerLookup,
    private val clock: () -> Long,
    private val ephemeral: () -> KeyPair = P256::generate,
    private val nonce: () -> ByteArray,
    private val handler: ApplicationHandler? = null,
) {
    private val localInstallationId: ByteArray = localInstallationId.copyOf()

    private var state = State.AWAITING_CLIENT_HELLO
    private var peerInstallationId: ByteArray? = null
    private var peerPublicKey: ByteArray? = null
    private var localRecord: PeerRecord? = null
    private var ephemeralKeyPair: KeyPair? = null
    private var localNonce: ByteArray? = null
    private var clientHello: ReconnectHello? = null
    private var rbase: ByteArray? = null
    private var phoneSignature: ByteArray? = null
    private var peerSignature: ByteArray? = null
    private var confirmInput: ByteArray? = null
    private var keys: ReconnectKeys? = null
    private var sessionIdPrefix: ByteArray? = null
    private var fixedClass: SessionClass? = null
    private var peerState = 0
    private var confirmToken: Long? = null
    private var confirmSendComplete = false
    private var peerConfirmVerified = false
    private var authenticated = false
    private var nextToken = 0L
    private var outboundSequence: ULong = 0uL
    private var inboundSequence: ULong = 0uL
    private var lastActivityAt = 0L
    private var lastOutboundAt = 0L
    private var handshakeDeadlineAt = 0L

    val isOpen: Boolean get() = state == State.OPEN

    /** Non-null only after [SessionEffect.Authenticated]. */
    val sessionClass: SessionClass? get() = fixedClass

    val peerId: ByteArray? get() = peerInstallationId

    suspend fun onInbound(bytes: ByteArray): List<SessionEffect> = when (state) {
        State.AWAITING_CLIENT_HELLO -> onClientHello(bytes)
        State.AWAITING_CLIENT_SIGNATURE -> onClientSignature(bytes)
        State.AWAITING_PEER_CONFIRM -> onPeerConfirm(bytes)
        State.OPEN -> onProtectedRecord(bytes)
        State.CLOSED -> emptyList()
    }

    @Suppress("ReturnCount") // Input after teardown, a foreign token and a failed send all fail closed.
    suspend fun onSendComplete(token: Long, ok: Boolean): List<SessionEffect> {
        if (state == State.CLOSED || authenticated) return emptyList()
        if (token != confirmToken) return emptyList()
        if (!ok) return terminate(STAGE_SEND_FAILED)
        confirmSendComplete = true
        if (!peerConfirmVerified) return emptyList()
        return authenticate()
    }

    suspend fun sendApplication(type: Int, body: ByteArray): List<SessionEffect> {
        if (state != State.OPEN) throw SessionReject()
        return listOf(SessionEffect.Send(sealApplication(type, body)))
    }

    /** Deadlines first, then the 60 s outbound-silence heartbeat (plan heartbeat driver). */
    suspend fun onTick(): List<SessionEffect> {
        val now = clock()
        return when (state) {
            State.CLOSED, State.AWAITING_CLIENT_HELLO -> emptyList()
            State.AWAITING_CLIENT_SIGNATURE, State.AWAITING_PEER_CONFIRM -> handshakeTick(now)
            State.OPEN -> openTick(now)
        }
    }

    fun teardown() {
        teardownInternal()
    }

    @Suppress("ReturnCount") // Each validation failure rejects before any state is applied.
    private suspend fun onClientHello(bytes: ByteArray): List<SessionEffect> {
        val frame = decodeFrame(bytes) ?: return rejectPreAuth()
        if (frame.first != FrameType.RECONNECT_HELLO.wire) return rejectPreAuth()
        val hello = decodeClientHello(frame.second) ?: return rejectPreAuth()
        val record = lookup.find(hello.installationId) ?: return rejectPreAuth()
        val keyPair = ephemeral()
        val localNonceValue = nonce()
        val phoneHelloPayload = try {
            ReconnectHello(
                localInstallationId,
                point(keyPair),
                localNonceValue,
                Role.PHONE,
                ReconnectHello.LOCAL_CAPS.copyOf(),
            ).encode()
        } catch (_: SessionReject) {
            return rejectPreAuth()
        }
        val transcript = ReconnectKeySchedule.rbase(phoneHelloPayload, frame.second)
        val signature = try {
            EcdsaP1363.normalizeLowS(EcdsaP1363.fromDer(signer.signDer(transcript)))
        } catch (_: SessionReject) {
            return rejectPreAuth()
        }

        peerInstallationId = hello.installationId.copyOf()
        peerPublicKey = record.identityPublicKey.copyOf()
        localRecord = record
        ephemeralKeyPair = keyPair
        localNonce = localNonceValue
        clientHello = hello
        rbase = transcript
        phoneSignature = signature
        state = State.AWAITING_CLIENT_SIGNATURE
        handshakeDeadlineAt = clock() + SessionLimits.HANDSHAKE_TIMEOUT_MS
        return listOf(
            sendFrame(FrameType.RECONNECT_HELLO.wire, phoneHelloPayload),
            sendFrame(FrameType.RECONNECT_SIGNATURE.wire, SignatureFrame(Role.PHONE, signature).encode()),
        )
    }

    @Suppress("ReturnCount") // Each validation failure rejects before any key is derived.
    private suspend fun onClientSignature(bytes: ByteArray): List<SessionEffect> {
        val frame = decodeFrame(bytes) ?: return rejectPreAuth()
        if (frame.first != FrameType.RECONNECT_SIGNATURE.wire) return rejectPreAuth()
        val signature = decodeSignature(frame.second) ?: return rejectPreAuth()
        val peerKey = peerPublicKey ?: return rejectPreAuth()
        val transcript = rbase ?: return rejectPreAuth()
        if (!verifySignature(peerKey, transcript, signature)) return rejectPreAuth()

        val installationId = peerInstallationId ?: return rejectPreAuth()
        val current = lookup.find(installationId) ?: return rejectPreAuth()
        if (!current.identityPublicKey.contentEquals(peerKey)) return rejectPreAuth()
        val hello = clientHello ?: return rejectPreAuth()
        val keyPair = ephemeralKeyPair ?: return rejectPreAuth()
        val localNonceValue = localNonce ?: return rejectPreAuth()
        val ownSignature = phoneSignature ?: return rejectPreAuth()
        val derived = deriveKeys(keyPair, hello, localNonceValue) ?: return rejectPreAuth()

        val input = ReconnectKeySchedule.confirmInput(transcript, ownSignature, signature)
        val ownState = if (current.state == LocalRecordState.ACTIVE) PEER_STATE_ACTIVE else PEER_STATE_BELOW_ACTIVE
        val body = ReconnectKeySchedule.confirmation(derived.phoneConfirmKey, input) + byteArrayOf(ownState.toByte())
        val prefix = derived.sessionId.copyOfRange(0, SESSION_PREFIX_BYTES)
        val record = try {
            sealConfirm(derived, prefix, body)
        } catch (_: SessionReject) {
            derived.erase()
            return rejectPreAuth()
        }

        keys = derived
        sessionIdPrefix = prefix
        confirmInput = input
        peerSignature = signature
        localRecord = current
        confirmSendComplete = false
        confirmToken = nextToken
        nextToken += 1
        outboundSequence = CONFIRM_SEQUENCE + 1uL
        lastOutboundAt = clock()
        state = State.AWAITING_PEER_CONFIRM
        handshakeDeadlineAt = clock() + SessionLimits.HANDSHAKE_TIMEOUT_MS
        return listOf(SessionEffect.Send(record, confirmToken))
    }

    @Suppress("ReturnCount") // Fail closed on every record, type or confirmation failure.
    private suspend fun onPeerConfirm(bytes: ByteArray): List<SessionEffect> {
        if (isPreAuthFrame(bytes)) return terminate(STAGE_UNEXPECTED_FRAME)
        val open = keys ?: return terminate(STAGE_RECORD_INVALID)
        val prefix = sessionIdPrefix ?: return terminate(STAGE_RECORD_INVALID)
        val opened = try {
            RecordCodec.open(open.c2pKey, open.c2pPrefix, prefix, Direction.CLIENT_TO_PHONE, inboundSequence, bytes)
        } catch (_: SessionReject) {
            return terminate(STAGE_RECORD_INVALID)
        }
        lastActivityAt = clock()
        if (opened.type != RecordType.RECONNECT_CONFIRM.wire) return terminate(STAGE_CONFIRM_REQUIRED)
        val confirmedPeerState = peerStateOf(opened.body) ?: return terminate(STAGE_CONFIRM_INVALID)

        peerState = confirmedPeerState
        peerConfirmVerified = true
        inboundSequence = CONFIRM_SEQUENCE + 1uL
        if (!confirmSendComplete) return emptyList()
        return authenticate()
    }

    @Suppress("ReturnCount") // Every record rejection ends the session without delivering anything.
    private suspend fun onProtectedRecord(bytes: ByteArray): List<SessionEffect> {
        if (isPreAuthFrame(bytes)) return terminate(STAGE_UNEXPECTED_FRAME)
        val open = keys ?: return terminate(STAGE_RECORD_INVALID)
        val prefix = sessionIdPrefix ?: return terminate(STAGE_RECORD_INVALID)
        val opened = try {
            RecordCodec.open(open.c2pKey, open.c2pPrefix, prefix, Direction.CLIENT_TO_PHONE, inboundSequence, bytes)
        } catch (_: SessionReject) {
            return terminate(STAGE_RECORD_INVALID)
        }
        inboundSequence += 1uL
        lastActivityAt = clock()
        return deliver(opened)
    }

    @Suppress("ReturnCount") // Framework records and unhandled authorized records fail closed or end.
    private suspend fun deliver(opened: OpenedRecord): List<SessionEffect> {
        if (opened.type == RecordType.APP_ERROR.wire) return terminate(STAGE_APP_ERROR)
        if (opened.type == RecordType.APP_HEARTBEAT.wire) return emptyList()
        val type = RecordType.fromWire(opened.type) ?: return terminate(STAGE_AUTHORIZATION)
        val cls = fixedClass ?: return terminate(STAGE_AUTHORIZATION)
        if (!authorizes(type, cls)) return terminate(STAGE_AUTHORIZATION)
        val applicationHandler = handler ?: return terminate(STAGE_UNHANDLED)
        if (!applicationHandler.handles(opened.type)) return terminate(STAGE_UNHANDLED)
        val replies = mutableListOf<SessionEffect>()
        try {
            applicationHandler.handle(opened.type, opened.body) { replyType, replyBody ->
                replies += SessionEffect.Send(sealApplication(replyType, replyBody))
            }
        } catch (_: SessionReject) {
            return terminate(STAGE_AUTHORIZATION)
        }
        return listOf(SessionEffect.Deliver(opened.type, opened.body)) + replies
    }

    @Suppress("ReturnCount") // Expiry is checked before generating a heartbeat.
    private fun openTick(now: Long): List<SessionEffect> {
        if (now - lastActivityAt >= SessionLimits.IDLE_TIMEOUT_MS) return terminate(STAGE_IDLE)
        if (now - lastOutboundAt < HEARTBEAT_SILENCE_MS) return emptyList()
        val record = try {
            sealApplication(RecordType.APP_HEARTBEAT.wire, ByteArray(0))
        } catch (_: SessionReject) {
            return terminate(STAGE_HEARTBEAT)
        }
        return listOf(SessionEffect.Send(record))
    }

    private fun handshakeTick(now: Long): List<SessionEffect> {
        if (now >= handshakeDeadlineAt) return terminate(STAGE_HANDSHAKE_TIMEOUT)
        return emptyList()
    }

    private fun sealConfirm(derived: ReconnectKeys, prefix: ByteArray, body: ByteArray): ByteArray = RecordCodec.seal(
        derived.p2cKey,
        derived.p2cPrefix,
        prefix,
        Direction.PHONE_TO_CLIENT,
        RecordType.RECONNECT_CONFIRM.wire,
        CONFIRM_SEQUENCE,
        body,
    )

    private fun sealApplication(type: Int, body: ByteArray): ByteArray {
        requireAuthorized(type)
        val open = keys ?: throw SessionReject()
        val prefix = sessionIdPrefix ?: throw SessionReject()
        val record = RecordCodec.seal(
            open.p2cKey,
            open.p2cPrefix,
            prefix,
            Direction.PHONE_TO_CLIENT,
            type,
            outboundSequence,
            body,
        )
        outboundSequence += 1uL
        lastOutboundAt = clock()
        if (state == State.OPEN) lastActivityAt = lastOutboundAt
        return record
    }

    @Suppress("ThrowsCount") // One generic rejection for every §7.3/§11.4 authorization failure.
    private fun requireAuthorized(type: Int) {
        val recordType = RecordType.fromWire(type) ?: throw SessionReject()
        if (!recordType.allows(Direction.PHONE_TO_CLIENT)) throw SessionReject()
        val cls = fixedClass ?: throw SessionReject()
        if (!authorizes(recordType, cls)) throw SessionReject()
    }

    /** §11.4 classes plus the class-independent framework records. */
    private fun authorizes(type: RecordType, cls: SessionClass): Boolean = when (type) {
        RecordType.RECONNECT_CONFIRM, RecordType.APP_HEARTBEAT, RecordType.APP_ERROR -> true
        RecordType.ENROLLMENT_PREPARE, RecordType.ACTIVATION_COMMIT,
        RecordType.ACTIVATION_COMPLETE, RecordType.ACTIVATION_ACK,
        -> cls != SessionClass.FULL
        RecordType.PULL_REQUEST, RecordType.PULL_EVENTS, RecordType.PUSH_EVENT, RecordType.EVENT_ACK,
        RecordType.REMOVAL_REQUEST, RecordType.REMOVAL_RESPONSE, RecordType.PHONE_POLICY_STATE,
        ->
            cls == SessionClass.FULL
        RecordType.ENROLL_IDENTITY -> false
    }

    @Suppress("ReturnCount") // Both gate halves must be present before the class is fixed.
    private fun authenticate(): List<SessionEffect> {
        if (authenticated) return emptyList()
        val record = localRecord ?: return terminate(STAGE_RECORD_INVALID)
        val installationId = peerInstallationId ?: return terminate(STAGE_RECORD_INVALID)
        val prefix = sessionIdPrefix ?: return terminate(STAGE_RECORD_INVALID)
        val cls = selectClass(record, peerState)
        fixedClass = cls
        authenticated = true
        state = State.OPEN
        lastActivityAt = clock()
        return listOf(SessionEffect.Authenticated(cls, installationId.copyOf(), prefix.copyOf()))
    }

    private fun selectClass(record: PeerRecord, confirmedPeerState: Int): SessionClass = when (record.state) {
        LocalRecordState.ACTIVE ->
            if (confirmedPeerState == PEER_STATE_ACTIVE) SessionClass.FULL else SessionClass.FULL_PENDING_PEER
        LocalRecordState.PENDING, LocalRecordState.COMMITTED_WAITING_ACK -> SessionClass.ACTIVATION_RECOVERY
    }

    @Suppress("ReturnCount") // A malformed confirmation yields no peer state.
    private fun peerStateOf(body: ByteArray): Int? {
        if (body.size != CONFIRM_BODY_BYTES) return null
        val open = keys ?: return null
        val input = confirmInput ?: return null
        val expected = ReconnectKeySchedule.confirmation(open.clientConfirmKey, input)
        if (!MessageDigest.isEqual(expected, body.copyOfRange(0, HASH_BYTES))) return null
        val stateByte = body[HASH_BYTES].toInt() and U8_MASK
        return stateByte.takeIf { it == PEER_STATE_ACTIVE || it == PEER_STATE_BELOW_ACTIVE }
    }

    private fun deriveKeys(keyPair: KeyPair, hello: ReconnectHello, localNonceValue: ByteArray): ReconnectKeys? {
        val shared = try {
            P256.ecdhX(keyPair.private, hello.ephemeralPublicKey)
        } catch (_: SessionReject) {
            return null
        }
        return try {
            ReconnectKeySchedule.derive(shared, localNonceValue, hello.nonce)
        } catch (_: SessionReject) {
            null
        } finally {
            shared.fill(0)
        }
    }

    private fun verifySignature(publicKey: ByteArray, transcript: ByteArray, signature: ByteArray): Boolean = try {
        EcdsaP1363.requireValid(publicKey, transcript, signature)
        true
    } catch (_: SessionReject) {
        false
    }

    private fun decodeFrame(bytes: ByteArray): Pair<Int, ByteArray>? = try {
        PreAuthCodec.decode(bytes)
    } catch (_: SessionReject) {
        null
    }

    private fun decodeClientHello(payload: ByteArray): ReconnectHello? = try {
        ReconnectHello.decode(payload, Role.CLIENT)
    } catch (_: SessionReject) {
        null
    }

    private fun decodeSignature(payload: ByteArray): ByteArray? = try {
        SignatureFrame.decode(payload, Role.CLIENT).signature
    } catch (_: SessionReject) {
        null
    }

    private fun sendFrame(type: Int, payload: ByteArray): SessionEffect.Send = SessionEffect.Send(PreAuthCodec.encode(type, payload))

    private fun isPreAuthFrame(bytes: ByteArray): Boolean = bytes.isNotEmpty() && FrameType.fromWire(bytes[0].toInt() and U8_MASK) != null

    private fun point(keyPair: KeyPair): ByteArray {
        val publicKey = keyPair.public as? ECPublicKey ?: throw SessionReject()
        val point = ByteArray(POINT_BYTES)
        point[0] = UNCOMPRESSED_TAG.toByte()
        coordinate(publicKey.w.affineX).copyInto(point, 1)
        coordinate(publicKey.w.affineY).copyInto(point, 1 + COORDINATE_BYTES)
        return point
    }

    private fun coordinate(value: BigInteger): ByteArray {
        val raw = value.toByteArray()
        return when {
            raw.size == COORDINATE_BYTES -> raw
            raw.size > COORDINATE_BYTES -> raw.copyOfRange(raw.size - COORDINATE_BYTES, raw.size)
            else -> ByteArray(COORDINATE_BYTES - raw.size) + raw
        }
    }

    private fun rejectPreAuth(): List<SessionEffect> {
        val frame = PreAuthCodec.encode(FrameType.PREAUTH_REJECT.wire, ByteArray(0))
        teardownInternal()
        return listOf(SessionEffect.Send(frame), SessionEffect.Close)
    }

    private fun terminate(reason: String): List<SessionEffect> {
        teardownInternal()
        return listOf(SessionEffect.Terminated(reason), SessionEffect.Close)
    }

    private fun teardownInternal() {
        keys?.erase()
        keys = null
        ephemeralKeyPair = null
        localNonce = null
        clientHello = null
        rbase?.fill(0)
        rbase = null
        phoneSignature?.fill(0)
        phoneSignature = null
        peerSignature?.fill(0)
        peerSignature = null
        confirmInput?.fill(0)
        confirmInput = null
        sessionIdPrefix?.fill(0)
        sessionIdPrefix = null
        localRecord = null
        peerInstallationId = null
        peerPublicKey = null
        confirmToken = null
        confirmSendComplete = false
        peerConfirmVerified = false
        authenticated = false
        fixedClass = null
        peerState = 0
        nextToken = 0L
        outboundSequence = 0uL
        inboundSequence = 0uL
        lastActivityAt = 0L
        lastOutboundAt = 0L
        handshakeDeadlineAt = 0L
        state = State.CLOSED
    }

    private enum class State { AWAITING_CLIENT_HELLO, AWAITING_CLIENT_SIGNATURE, AWAITING_PEER_CONFIRM, OPEN, CLOSED }

    private companion object {
        /** Plan heartbeat driver: emit `app_heartbeat` after 60 s with no outbound record. */
        const val HEARTBEAT_SILENCE_MS = 60_000L
        const val CONFIRM_BODY_BYTES = 33
        const val HASH_BYTES = 32
        const val COORDINATE_BYTES = 32
        const val POINT_BYTES = 65
        const val SESSION_PREFIX_BYTES = 8
        const val UNCOMPRESSED_TAG = 0x04
        const val U8_MASK = 0xFF
        const val PEER_STATE_ACTIVE = 0x01
        const val PEER_STATE_BELOW_ACTIVE = 0x02
        const val CONFIRM_SEQUENCE: ULong = 0uL

        const val STAGE_UNEXPECTED_FRAME = "unexpected_frame"
        const val STAGE_RECORD_INVALID = "record_invalid"
        const val STAGE_CONFIRM_REQUIRED = "confirm_required"
        const val STAGE_CONFIRM_INVALID = "confirm_invalid"
        const val STAGE_SEND_FAILED = "send_failed"
        const val STAGE_HANDSHAKE_TIMEOUT = "handshake_timeout"
        const val STAGE_IDLE = "idle"
        const val STAGE_HEARTBEAT = "heartbeat"
        const val STAGE_APP_ERROR = "app_error"
        const val STAGE_AUTHORIZATION = "authorization"
        const val STAGE_UNHANDLED = "unhandled"
    }
}
