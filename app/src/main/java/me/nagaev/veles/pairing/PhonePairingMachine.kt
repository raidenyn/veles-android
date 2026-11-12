package me.nagaev.veles.pairing

import android.os.SystemClock
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import me.nagaev.veles.session.AppErrorCode
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SessionReject
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.TrustState
import java.security.SecureRandom

/** The phone pairing machine's state (`phone_pairing` in `protocol/v1/states.json`). */
enum class PairingState {
    IDLE,
    WINDOW_OPEN,
    SPAKE_IN_FLIGHT,
    AWAITING_CB,
    ENROLLMENT_PROTECTED,
    AWAITING_COMMIT,
    COMMITTED_WAITING_ACK,
    ACTIVE,
}

/**
 * One pairing connection as the host drives it. [PhonePairingMachine] is the only
 * production implementation; the host's default rejects `pairing_request` without a
 * window machine, which keeps every non-pairing test free of production wiring.
 *
 * Every entry point is suspending: the enrollment flow writes the trust store
 * (`addPending`/`revoke`), so the machine serializes on a [Mutex] instead of a plain
 * monitor. `SessionHost` already serializes each generation, and it is the only
 * production caller of the lifecycle methods.
 */
interface PairingConnection {
    suspend fun onFrame(bytes: ByteArray): List<Outgoing>

    /** Drives the 30 s step inactivity timeout; the host ticks every second. */
    suspend fun onTick(): List<Outgoing>

    suspend fun onDisconnect()

    /** Local cancel: tears the connection down and deletes this enrollment's pending record. */
    suspend fun cancel(): List<Outgoing>
}

/**
 * Rejects pairing frames generically; used when no window machine is wired. One instance
 * per generation, because the default factory is called once per connection and a shared
 * one-shot object would leave later generations connected and unanswered.
 */
internal class RejectingPairingConnection : PairingConnection {
    private var rejected = false

    override suspend fun onFrame(bytes: ByteArray): List<Outgoing> {
        val alreadyRejected = synchronized(this) {
            val first = !rejected
            rejected = true
            !first
        }
        if (alreadyRejected) return emptyList()
        return listOf(Outgoing.Frame(PairingFrames.preauthReject()), Outgoing.Disconnect)
    }

    override suspend fun onTick(): List<Outgoing> = emptyList()

    override suspend fun onDisconnect() = Unit

    override suspend fun cancel(): List<Outgoing> = emptyList()
}

/**
 * One connection's pairing execution on top of the phone-wide [PairingWindow] and
 * [PairingAdmission] (§11.1 through `awaiting_commit`).
 *
 * Admission order for a `pairing_client_point` is exactly the normative one: framing and
 * `pairing_id`, the window deadline, [P256.requireValidPoint], the atomic non-refundable
 * charge, the deadline again, and only then the password-sensitive `exchange`. Every
 * failure before the verified `cB` leaves through [reject], which closes the handle,
 * releases the in-flight slot and emits the identical empty `preauth_reject` plus a
 * disconnect.
 *
 * At the verified `cB` (OTP2-CRY-009a) the code is consumed and the window closed, the
 * handle is finished for `Ke`, and the §5.1 schedule is derived in memory. The phone then
 * sends its own `enroll_identity`; only after the client's authenticated record passed
 * OTP2-REC-010/011a and the OTP2-STA-012 duplicate guard may it durably persist the complete
 * pending record (with `ET`) and send `enrollment_prepare`. Any failure after `cB` erases
 * the enrollment keys and keeps the code consumed; the only continuation is the signed
 * recovery path of a later connection.
 *
 * A native handle, a connection hint or any wire value never selects the local identity:
 * [EnrollmentSession.create] resolves it from the trust store.
 */
@Suppress("LongParameterList", "TooManyFunctions") // One seam per §11.1 step and one dependency per collaborator.
class PhonePairingMachine(
    private val window: PairingWindow,
    private val admission: PairingAdmission,
    private val conn: ConnId,
    private val trust: InstallationTrustRepository,
    private val coreProvider: () -> PairingCore,
    private val clock: () -> Long = SystemClock::elapsedRealtime,
    private val limits: PairingLimits = PairingLimits(),
    private val nonce: () -> ByteArray = { ByteArray(NONCE_BYTES).also(SecureRandom()::nextBytes) },
    private val label: () -> String = { DEVICE_LABEL },
    private val identityNonce: () -> ByteArray = { ByteArray(IDENTITY_NONCE_BYTES).also(SecureRandom()::nextBytes) },
    private val proofs: ProofRegistry,
) : PairingConnection {
    private val mutex = Mutex()
    private var execution: PairingExecution? = null
    private var enrollment: EnrollmentSession? = null
    private var pairingId: ByteArray? = null
    private var clientNonce: ByteArray? = null
    private var context: ByteArray? = null
    private var phonePoint: ByteArray? = null
    private var clientPoint: ByteArray? = null
    private var phoneConfirm: ByteArray? = null
    private var clientConfirm: ByteArray? = null
    private var holdsOperation = false
    private var lastActivity = 0L

    @Volatile
    var state: PairingState = PairingState.IDLE
        private set

    /** The live enrollment key holder, for the OTP2-CRY-011/019e erasure assertions. */
    internal fun enrollmentKeysForTest(): EnrollmentKeys? = enrollment?.keysForTest

    override suspend fun onFrame(bytes: ByteArray): List<Outgoing> = try {
        mutex.withLock {
            // After a verified cB every frame is a protected enrollment record, including a
            // malformed one: routing it through the pairing decoder would spend the
            // malformed-pairing budget on authenticated traffic and still fail closed.
            if (state == PairingState.ENROLLMENT_PROTECTED ||
                state == PairingState.AWAITING_COMMIT ||
                state == PairingState.COMMITTED_WAITING_ACK ||
                state == PairingState.ACTIVE
            ) {
                onProtectedRecord(bytes)
            } else {
                handleFrame(bytes)
            }
        }
    } catch (cancellation: CancellationException) {
        throw cancellation
    } catch (_: Exception) {
        // Every post-confirmation failure must fail closed with the identical generic
        // frame; an unexpected core failure must not leave the peer connected.
        mutex.withLock { reject() }
    }

    override suspend fun onTick(): List<Outgoing> = mutex.withLock {
        if (state !in ACTIVE_STATES) return@withLock emptyList()
        if (clock() - lastActivity < limits.requestInactivityTimeoutMillis) return@withLock emptyList()
        eraseExecution()
        eraseEnrollment()
        eraseSecrets()
        state = if (window.isOpen()) PairingState.WINDOW_OPEN else PairingState.IDLE
        listOf(Outgoing.Disconnect)
    }

    override suspend fun onDisconnect() = mutex.withLock {
        eraseExecution()
        eraseEnrollment()
        eraseSecrets()
        state = if (window.isOpen()) PairingState.WINDOW_OPEN else PairingState.IDLE
    }

    override suspend fun cancel(): List<Outgoing> = mutex.withLock {
        // Only a record this enrollment persisted may be deleted: an earlier active or
        // unrelated peer record is never touched by a cancel.
        val peerId = enrollment?.persistedPeerId
        eraseExecution()
        eraseEnrollment()
        eraseSecrets()
        state = PairingState.IDLE
        window.cancel()
        if (peerId != null) {
            try {
                trust.revoke(peerId)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: Exception) {
                // Local state is already erased; a store failure leaves only an inactive
                // record, never an open window or a live key.
            }
        }
        listOf(Outgoing.Disconnect)
    }

    @Suppress("ReturnCount") // One branch per distinct admission or protocol failure.
    private suspend fun handleFrame(bytes: ByteArray): List<Outgoing> {
        val frame = try {
            PairingFrames.decode(bytes)
        } catch (_: PairingFrameReject) {
            // Only `0x01`-`0x05` frames are pairing traffic: a stray reconnect frame is
            // rejected generically but never spends the malformed pair-connection budget.
            if (isPairingType(bytes)) admission.malformed(conn)
            return reject()
        }
        return when (frame.type) {
            PairingFrameType.REQUEST -> onRequest(frame.payload)
            PairingFrameType.PB -> onPoint(frame.payload)
            PairingFrameType.CB -> onConfirmation(frame.payload)
            // The phone's own outbound frames: a role/context mismatch (OTP2-CRY-002).
            PairingFrameType.PA, PairingFrameType.CA -> malformedReject()
        }
    }

    @Suppress("ReturnCount") // One branch per admission gate, in normative order.
    private suspend fun onRequest(payload: ByteArray): List<Outgoing> {
        // Every `pairing_request` spends the request budget at ingress, including a busy,
        // window-closed, over-limit or malformed one (limits.json:pairing_request_rolling_max).
        if (admission.requestAllowed(conn) == Decision.Reject) return reject()
        if (state != PairingState.WINDOW_OPEN && state != PairingState.IDLE) return reject()
        if (!window.isOpen()) return reject()
        val request = Request.parse(payload) ?: return malformedReject()
        if (!admission.beginOp()) return reject()
        holdsOperation = true
        if (admission.paAllowed(conn) == Decision.Reject) return reject()

        val currentId = window.pairingId
        if (currentId.isEmpty()) return reject()
        val phoneNonce = nonce()
        val ctx = contextOf(currentId, phoneNonce, request.clientNonce, request.caps)
        val started = startExecution(ctx) ?: return reject()
        if (started.point.size != POINT_BYTES) return reject()
        return try {
            pairingId = currentId
            clientNonce = request.clientNonce.copyOf()
            context = ctx
            phonePoint = started.point.copyOf()
            state = PairingState.SPAKE_IN_FLIGHT
            lastActivity = clock()
            val offer = offerPayload(currentId, phoneNonce, started.point)
            listOf(Outgoing.Frame(PairingFrames.encode(PairingFrameType.PA, offer)))
        } catch (_: Exception) {
            reject()
        }
    }

    @Suppress("ReturnCount") // One branch per admission gate, in normative order.
    private suspend fun onPoint(payload: ByteArray): List<Outgoing> {
        // A point outside an execution is malformed pairing input (and so counts against
        // the malformed-connection budget, like a first-frame `0x02`-`0x03`).
        if (state != PairingState.SPAKE_IN_FLIGHT) return malformedReject()
        if (payload.size != POINT_BYTES) return malformedReject()
        if (!windowLive()) return reject()
        try {
            P256.requireValidPoint(payload)
        } catch (_: SessionReject) {
            // Never charged: the point is validated before the attempt is consumed.
            return malformedReject()
        }
        val bound = pairingId ?: return reject()
        if (admission.chargeAttempt(bound) == Decision.Reject) return reject()
        // The deadline is rechecked after the charge; the attempt stays consumed.
        if (!windowLive()) return reject()
        val current = execution ?: return reject()
        val confirmation = try {
            current.exchange(payload)
        } catch (_: PairingCoreException) {
            return reject()
        }
        if (confirmation.size != CONFIRMATION_BYTES) return reject()
        clientPoint = payload.copyOf()
        phoneConfirm = confirmation.copyOf()
        state = PairingState.AWAITING_CB
        lastActivity = clock()
        return listOf(Outgoing.Frame(PairingFrames.encode(PairingFrameType.CA, confirmation)))
    }

    @Suppress("ReturnCount") // One branch per normative step of the verified-cB transition.
    private suspend fun onConfirmation(payload: ByteArray): List<Outgoing> {
        // A confirmation outside `awaiting_cB` is malformed pairing input (and so counts
        // against the malformed-connection budget, like a first-frame `0x05`).
        if (state != PairingState.AWAITING_CB) return malformedReject()
        if (payload.size != CONFIRMATION_BYTES) return malformedReject()
        if (!windowLive()) return reject()
        val current = execution ?: return reject()
        try {
            current.confirm(payload)
        } catch (_: PairingCoreException) {
            // The attempt was already consumed; the window stays open for a fresh connection.
            return reject()
        }
        // OTP2-CRY-009a: a verified cB consumes the one-use code and closes the window.
        window.cancel()
        clientConfirm = payload.copyOf()
        val session = try {
            openEnrollment(current)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            return reject()
        }
        enrollment = session
        state = PairingState.ENROLLMENT_PROTECTED
        lastActivity = clock()
        return try {
            // OTP2-REC-010 order: the phone releases its record only after verifying cB.
            listOf(Outgoing.Frame(session.ownIdentityFrame()))
        } catch (_: Exception) {
            reject()
        }
    }

    /** Derives `Ke`, the §5.1 schedule and the phone's own identity record from `cB`. */
    private suspend fun openEnrollment(current: PairingExecution): EnrollmentSession {
        val pts = phonePoint
        val peer = clientPoint
        val ca = phoneConfirm
        val cb = clientConfirm
        val ctx = context
        if (pts == null || peer == null || ca == null || cb == null || ctx == null) throw EnrollmentReject()
        val shared = current.finish()
        // The handle is consumed by `finish`; clear it before any later throw can try to close it.
        execution = null
        return try {
            EnrollmentSession.create(
                shared = shared,
                context = ctx,
                phonePoint = pts,
                clientPoint = peer,
                phoneConfirm = ca,
                clientConfirm = cb,
                trust = trust,
                label = label(),
                identityNonce = identityNonce(),
            )
        } finally {
            shared.fill(0)
        }
    }

    @Suppress("ReturnCount") // One branch per allow-list type and its handler.
    private suspend fun onProtectedRecord(bytes: ByteArray): List<Outgoing> {
        // OTP2-REC-035: the enrollment connection never carries OTP data, and activation
        // ends the enrollment keys. Any record after activation is unauthorized.
        if (state == PairingState.ACTIVE) return reject()
        val session = enrollment ?: return reject()
        val record = try {
            session.open(bytes)
        } catch (_: Exception) {
            return reject()
        }
        return try {
            when (record.type) {
                RecordType.ENROLL_IDENTITY -> onClientIdentity(session, record.body)
                RecordType.ACTIVATION_COMMIT -> onActivationCommit(session, record.body)
                RecordType.ACTIVATION_ACK -> onActivationAck(session, record.body)
                // OTP2-CRY-020a framework records are authorized but carry no state here.
                RecordType.APP_HEARTBEAT -> if (record.body.isEmpty()) emptyList() else reject()
                // Any other authorized type has no handler on the enrollment connection:
                // fail closed rather than half-process an activation record.
                else -> reject()
            }
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: EnrollmentReject) {
            reject()
        } finally {
            record.body.fill(0)
        }
    }

    /**
     * OTP2-STA-010b step 1 on the enrollment connection: the verified commit moves the pending
     * record to `committed_waiting_ack` and the phone releases `activation_complete`. A wrong
     * MAC deletes this enrollment's incomplete record and terminates (OTP2-CRY-019g).
     */
    @Suppress("ReturnCount") // One branch per failure of the verified commit.
    private suspend fun onActivationCommit(session: EnrollmentSession, body: ByteArray): List<Outgoing> {
        val peerId = session.persistedPeerId ?: return reject()
        if (!session.verifyActivation(body, STAGE_COMMIT)) return eraseIncompleteFailure(session, peerId)
        val peer = try {
            trust.peers().firstOrNull { it.installationId == peerId }
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            return reject()
        } ?: return reject()
        if (peer.state == TrustState.PENDING) {
            proofs.register(session.sessionIdHex, listOf(ActivationProof(peerId, ActivationStep.COMMIT, body.copyOf())))
            val written = try {
                trust.confirm(peerId, ActivationStep.COMMIT, body.copyOf())
                true
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: Exception) {
                false
            }
            if (!written) {
                proofs.revoke(session.sessionIdHex)
                return reject()
            }
        } else if (peer.state != TrustState.COMMITTED_WAITING_ACK) {
            return reject()
        }
        state = PairingState.COMMITTED_WAITING_ACK
        lastActivity = clock()
        return listOf(
            try {
                Outgoing.Frame(session.activationCompleteFrame())
            } catch (_: Exception) {
                return reject()
            },
        )
    }

    /** OTP2-STA-010b step 3/6: the verified ack activates the record and ends the enrollment. */
    @Suppress("ReturnCount") // One branch per failure of the verified ack.
    private suspend fun onActivationAck(session: EnrollmentSession, body: ByteArray): List<Outgoing> {
        val peerId = session.persistedPeerId ?: return reject()
        if (!session.verifyActivation(body, STAGE_ACK)) return eraseIncompleteFailure(session, peerId)
        val peer = try {
            trust.peers().firstOrNull { it.installationId == peerId }
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            return reject()
        } ?: return reject()
        if (peer.state != TrustState.COMMITTED_WAITING_ACK) return reject()
        proofs.register(session.sessionIdHex, listOf(ActivationProof(peerId, ActivationStep.ACK, body.copyOf())))
        val written = try {
            trust.confirm(peerId, ActivationStep.ACK, body.copyOf())
            true
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            false
        }
        if (!written) {
            proofs.revoke(session.sessionIdHex)
            return reject()
        }
        // OTP2-CRY-019e: every enrollment key and remnant is erased once the record is active.
        eraseExecution()
        eraseEnrollment()
        eraseSecrets()
        state = PairingState.ACTIVE
        lastActivity = clock()
        return emptyList()
    }

    /** A wrong activation MAC deletes only this enrollment's incomplete record, then fails. */
    private suspend fun eraseIncompleteFailure(
        session: EnrollmentSession,
        peerId: String,
    ): List<Outgoing> {
        proofs.revoke(session.sessionIdHex)
        try {
            val peer = trust.peers().firstOrNull { it.installationId == peerId }
            if (peer != null && peer.state != TrustState.ACTIVE) trust.revoke(peerId)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            // The connection still fails closed; a store failure leaves no usable record.
        }
        return reject()
    }

    @Suppress("ReturnCount") // Distinct outcomes: bad record, duplicate, persist failure, prepare.
    private suspend fun onClientIdentity(session: EnrollmentSession, body: ByteArray): List<Outgoing> {
        // OTP2-REC-010/011a and OTP2-CRY-005a: structure, role-bound device type, own-record
        // fingerprint and the full P-256 point check all run before any store is touched.
        val client = try {
            var received: IdentityRecord? = null
            EnrollmentRecords.decodeForStorage(body, IdentityRole.CLIENT, P256::requireValidPoint) { received = it }
            received ?: throw EnrollmentReject()
        } catch (_: Exception) {
            return reject()
        }
        val canonical = try {
            EnrollmentRecords.encode(client, IdentityRole.CLIENT)
        } catch (_: Exception) {
            return reject()
        }
        val et = try {
            session.transcript(canonical)
        } catch (_: Exception) {
            canonical.fill(0)
            return reject()
        } finally {
            canonical.fill(0)
        }
        return try {
            when (session.persist(client, et)) {
                EnrollmentSession.PersistResult.DUPLICATE -> {
                    val error = try {
                        session.appErrorFrame(AppErrorCode.ENROLLMENT_REJECTED)
                    } catch (_: Exception) {
                        return reject()
                    }
                    // OTP2-STA-012: terminate, erase pairing state and keep the existing record.
                    terminate(error)
                }
                EnrollmentSession.PersistResult.FAILED -> reject()
                EnrollmentSession.PersistResult.PERSISTED -> {
                    val prepare = try {
                        session.prepareFrame()
                    } catch (_: Exception) {
                        return reject()
                    }
                    // OTP2-STA-010a: awaiting_commit is entered only by a sent prepare.
                    state = PairingState.AWAITING_COMMIT
                    lastActivity = clock()
                    listOf(Outgoing.Frame(prepare))
                }
            }
        } finally {
            et.fill(0)
        }
    }

    /** True while the window is open and still bound to this execution's `pairing_id`. */
    private fun windowLive(): Boolean {
        if (!window.isOpen()) return false
        val current = window.pairingId
        val bound = pairingId ?: return false
        return current.isNotEmpty() && current.contentEquals(bound)
    }

    private fun startExecution(ctx: ByteArray): Started? {
        val code = try {
            window.codeBytes()
        } catch (_: IllegalStateException) {
            return null
        }
        return try {
            val core = coreProvider()
            val started = core.start(Role.PHONE, code, ctx)
            execution = PairingExecution(core, started.handle)
            started
        } catch (_: PairingCoreException) {
            null
        } catch (_: IllegalArgumentException) {
            null
        } finally {
            code.fill(0)
        }
    }

    /** Closes the handle, releases the slot and returns the generic failure plus disconnect. */
    private fun reject(): List<Outgoing> {
        eraseExecution()
        eraseEnrollment()
        eraseSecrets()
        state = if (window.isOpen()) PairingState.WINDOW_OPEN else PairingState.IDLE
        return listOf(Outgoing.Frame(PairingFrames.preauthReject()), Outgoing.Disconnect)
    }

    /** Erases the session, keeps the window consumed and closes with [error] first. */
    private fun terminate(error: ByteArray): List<Outgoing> {
        eraseExecution()
        eraseEnrollment()
        eraseSecrets()
        state = if (window.isOpen()) PairingState.WINDOW_OPEN else PairingState.IDLE
        return listOf(Outgoing.Frame(error), Outgoing.Disconnect)
    }

    private fun malformedReject(): List<Outgoing> {
        admission.malformed(conn)
        return reject()
    }

    private fun eraseExecution() {
        execution?.close()
        execution = null
        if (holdsOperation) {
            admission.endOp()
            holdsOperation = false
        }
    }

    private fun eraseEnrollment() {
        enrollment?.erase()
        enrollment = null
    }

    private fun eraseSecrets() {
        pairingId?.fill(0)
        pairingId = null
        clientNonce?.fill(0)
        clientNonce = null
        context?.fill(0)
        context = null
        phonePoint?.fill(0)
        phonePoint = null
        clientPoint?.fill(0)
        clientPoint = null
        phoneConfirm?.fill(0)
        phoneConfirm = null
        clientConfirm?.fill(0)
        clientConfirm = null
    }

    private companion object {
        val ACTIVE_STATES = setOf(
            PairingState.SPAKE_IN_FLIGHT,
            PairingState.AWAITING_CB,
            PairingState.ENROLLMENT_PROTECTED,
            PairingState.AWAITING_COMMIT,
            PairingState.COMMITTED_WAITING_ACK,
            PairingState.ACTIVE,
        )
        const val STAGE_COMMIT: Byte = 0x01
        const val STAGE_ACK: Byte = 0x03
        const val DEVICE_LABEL = "Phone"
        const val IDENTITY_NONCE_BYTES = 16
    }
}

private fun isPairingType(bytes: ByteArray): Boolean {
    if (bytes.isEmpty()) return false
    return PairingFrameType.fromCode(bytes[0].toInt() and U8_MASK) != null
}

/** One validated `pairing_request` payload: the client nonce and its capability set. */
private class Request(val clientNonce: ByteArray, val caps: ByteArray) {
    companion object {
        @Suppress("ReturnCount") // Fixed-length, version and caps are three distinct rejects.
        fun parse(payload: ByteArray): Request? {
            if (payload.size < REQUEST_FIXED) return null
            if (u16(payload, VERSION_OFFSET) != PROTOCOL_VERSION) return null
            val capsLength = u16(payload, CAPS_LEN_OFFSET)
            if (payload.size != REQUEST_FIXED + capsLength) return null
            val caps = payload.copyOfRange(REQUEST_FIXED, payload.size)
            if (!validCaps(caps)) return null
            return Request(payload.copyOfRange(0, NONCE_BYTES), caps)
        }

        /** Sorted, duplicate-free, known capabilities with the mandatory tag first. */
        private fun validCaps(caps: ByteArray): Boolean {
            if (caps.isEmpty() || caps.size > MAX_CAPS) return false
            if (caps[0] != MANDATORY_CAP) return false
            if (!caps.all { it in KNOWN_CAPS }) return false
            return caps.toList().zipWithNext().all { (first, second) -> first < second }
        }
    }
}

/** `pairing_offer` payload: `pairing_id || phone_nonce || version || caps_len || caps || pA`. */
private fun offerPayload(pairingId: ByteArray, phoneNonce: ByteArray, point: ByteArray): ByteArray {
    val caps = PHONE_CAPS
    val payload = ByteArray(PAYLOAD_FIXED + caps.size + point.size)
    var offset = 0
    pairingId.copyInto(payload, offset)
    offset += PAIRING_ID_BYTES
    phoneNonce.copyInto(payload, offset)
    offset += NONCE_BYTES
    offset = putU16(payload, offset, PROTOCOL_VERSION)
    offset = putU16(payload, offset, caps.size)
    caps.copyInto(payload, offset)
    offset += caps.size
    point.copyInto(payload, offset)
    return payload
}

/** §3.3 canonical public context (AAD); both endpoints must build the same bytes. */
private fun contextOf(
    pairingId: ByteArray,
    phoneNonce: ByteArray,
    clientNonce: ByteArray,
    clientCaps: ByteArray,
): ByteArray {
    val phoneCaps = PHONE_CAPS
    val ctx = ByteArray(CONTEXT_FIXED + phoneCaps.size + clientCaps.size)
    var offset = 0
    CONTEXT_PREFIX.copyInto(ctx, offset)
    offset += CONTEXT_PREFIX.size
    offset = putU16(ctx, offset, PROTOCOL_VERSION)
    offset = putU16(ctx, offset, SCHEMA_VERSION)
    offset = putU16(ctx, offset, CIPHERSUITE_ID)
    pairingId.copyInto(ctx, offset)
    offset += PAIRING_ID_BYTES
    ctx[offset++] = ROLE_PHONE_MARKER
    ctx[offset++] = ROLE_CLIENT_MARKER
    phoneNonce.copyInto(ctx, offset)
    offset += NONCE_BYTES
    clientNonce.copyInto(ctx, offset)
    offset += NONCE_BYTES
    offset = putU16(ctx, offset, phoneCaps.size)
    phoneCaps.copyInto(ctx, offset)
    offset += phoneCaps.size
    offset = putU16(ctx, offset, clientCaps.size)
    clientCaps.copyInto(ctx, offset)
    return ctx
}

private fun putU16(target: ByteArray, offset: Int, value: Int): Int {
    target[offset] = (value ushr BITS_PER_BYTE).toByte()
    target[offset + 1] = value.toByte()
    return offset + U16_BYTES
}

private fun u16(bytes: ByteArray, offset: Int): Int {
    val high = (bytes[offset].toInt() and U8_MASK) shl BITS_PER_BYTE
    return high or (bytes[offset + 1].toInt() and U8_MASK)
}

private const val PAIRING_ID_BYTES = 16
private const val NONCE_BYTES = 16
private const val POINT_BYTES = 65
private const val CONFIRMATION_BYTES = 32
private const val REQUEST_FIXED = NONCE_BYTES + 4
private const val VERSION_OFFSET = NONCE_BYTES
private const val CAPS_LEN_OFFSET = NONCE_BYTES + 2
private const val PAYLOAD_FIXED = PAIRING_ID_BYTES + NONCE_BYTES + 4
private const val CONTEXT_FIXED = 30 + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES + NONCE_BYTES + 4
private const val PROTOCOL_VERSION = 1
private const val SCHEMA_VERSION = 1
private const val CIPHERSUITE_ID = 1
private const val MAX_CAPS = 16
private const val U16_BYTES = 2
private const val BITS_PER_BYTE = 8
private const val U8_MASK = 0xFF
private val ROLE_PHONE_MARKER = 0x01.toByte()
private val ROLE_CLIENT_MARKER = 0x02.toByte()
private val MANDATORY_CAP = 0x01.toByte()
private val KNOWN_CAPS = 0x01.toByte()..0x03.toByte()
private val CONTEXT_PREFIX = "Veles SPAKE2 public context v1".toByteArray(Charsets.US_ASCII)

/** VELES_RECORD_V1 (mandatory) and DIRECT_GATT, sorted ascending as §3.3 requires. */
private val PHONE_CAPS = byteArrayOf(0x01, 0x02)
