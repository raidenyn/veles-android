package me.nagaev.veles.pairing

import android.os.SystemClock
import kotlinx.coroutines.CancellationException
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SessionReject
import java.security.SecureRandom

/** The phone pairing machine's state (`phone_pairing` in `protocol/v1/states.json`). */
enum class PairingState {
    IDLE,
    WINDOW_OPEN,
    SPAKE_IN_FLIGHT,
    AWAITING_CB,
    ENROLLMENT_PROTECTED,
}

/**
 * One pairing connection as the host drives it. [PhonePairingMachine] is the only
 * production implementation; the host's default rejects `pairing_request` without a
 * window machine, which keeps every non-pairing test free of production wiring.
 */
interface PairingConnection {
    suspend fun onFrame(bytes: ByteArray): List<Outgoing>

    /** Drives the 30 s step inactivity timeout; the host ticks every second. */
    fun onTick(): List<Outgoing>

    fun onDisconnect()

    fun cancel()
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

    override fun onTick(): List<Outgoing> = emptyList()

    override fun onDisconnect() = Unit

    override fun cancel() = Unit
}

/**
 * One connection's pairing execution on top of the phone-wide [PairingWindow] and
 * [PairingAdmission] (§11.1 through a verified `cB`).
 *
 * Admission order for a `pairing_client_point` is exactly the normative one: framing and
 * `pairing_id`, the window deadline, [P256.requireValidPoint], the atomic non-refundable
 * charge, the deadline again, and only then the password-sensitive `exchange`. Every
 * failure before the verified `cB` leaves through [reject], which closes the handle,
 * releases the in-flight slot and emits the identical empty `preauth_reject` plus a
 * disconnect.
 *
 * The execution handle stays open after a verified `cB`: the protected enrollment
 * channel (Task 7's `EnrollmentSession`) finishes it and consumes `Ke`. Every other exit
 * path — disconnect, inactivity, cancel, admission failure — closes it.
 *
 * The machine serializes on a plain lock: [onTick], [onDisconnect] and [cancel] are
 * non-suspending in the connection seam, and the host already serializes every call for a
 * generation under its own `Mutex`. Task 7 can move this to a `Mutex` together with the
 * suspend trust-store calls; until then `synchronized` is the only lock those three
 * lifecycle calls can take.
 */
class PhonePairingMachine(
    private val window: PairingWindow,
    private val admission: PairingAdmission,
    private val conn: ConnId,
    private val coreProvider: () -> PairingCore,
    private val clock: () -> Long = SystemClock::elapsedRealtime,
    private val limits: PairingLimits = PairingLimits(),
    private val nonce: () -> ByteArray = { ByteArray(NONCE_BYTES).also(SecureRandom()::nextBytes) },
) : PairingConnection {
    private val lock = Any()
    private var execution: PairingExecution? = null
    private var pairingId: ByteArray? = null
    private var clientNonce: ByteArray? = null
    private var context: ByteArray? = null
    private var holdsOperation = false
    private var lastActivity = 0L

    @Volatile
    var state: PairingState = PairingState.IDLE
        private set

    override suspend fun onFrame(bytes: ByteArray): List<Outgoing> = try {
        synchronized(lock) { handleFrame(bytes) }
    } catch (cancellation: CancellationException) {
        throw cancellation
    } catch (_: Exception) {
        // Every pre-confirmation failure must fail closed with the identical generic
        // frame; an unexpected core failure must not leave the peer connected.
        synchronized(lock) { reject() }
    }

    override fun onTick(): List<Outgoing> = synchronized(lock) {
        if (state !in ACTIVE_STATES) return emptyList()
        if (clock() - lastActivity < limits.requestInactivityTimeoutMillis) return emptyList()
        eraseExecution()
        state = if (window.isOpen()) PairingState.WINDOW_OPEN else PairingState.IDLE
        listOf(Outgoing.Disconnect)
    }

    override fun onDisconnect() = synchronized(lock) {
        eraseExecution()
        eraseSecrets()
        state = if (window.isOpen()) PairingState.WINDOW_OPEN else PairingState.IDLE
    }

    override fun cancel() = synchronized(lock) {
        eraseExecution()
        eraseSecrets()
        state = PairingState.IDLE
        window.cancel()
    }

    @Suppress("ReturnCount") // One branch per distinct admission or protocol failure.
    private fun handleFrame(bytes: ByteArray): List<Outgoing> {
        val frame = try {
            PairingFrames.decode(bytes)
        } catch (_: PairingFrameReject) {
            // Only `0x01`-`0x05` frames are pairing traffic: a stray reconnect frame is
            // rejected generically but never spends the malformed pairing-connection budget.
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
    private fun onRequest(payload: ByteArray): List<Outgoing> {
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
            state = PairingState.SPAKE_IN_FLIGHT
            lastActivity = clock()
            val offer = offerPayload(currentId, phoneNonce, started.point)
            listOf(Outgoing.Frame(PairingFrames.encode(PairingFrameType.PA, offer)))
        } catch (_: Exception) {
            reject()
        }
    }

    @Suppress("ReturnCount") // One branch per admission gate, in normative order.
    private fun onPoint(payload: ByteArray): List<Outgoing> {
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
        state = PairingState.AWAITING_CB
        lastActivity = clock()
        return listOf(Outgoing.Frame(PairingFrames.encode(PairingFrameType.CA, confirmation)))
    }

    @Suppress("ReturnCount") // One branch per admission gate, in normative order.
    private fun onConfirmation(payload: ByteArray): List<Outgoing> {
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
        state = PairingState.ENROLLMENT_PROTECTED
        lastActivity = clock()
        return emptyList()
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
        state = if (window.isOpen()) PairingState.WINDOW_OPEN else PairingState.IDLE
        return listOf(Outgoing.Frame(PairingFrames.preauthReject()), Outgoing.Disconnect)
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

    private fun eraseSecrets() {
        pairingId?.fill(0)
        pairingId = null
        clientNonce?.fill(0)
        clientNonce = null
        context?.fill(0)
        context = null
    }

    private companion object {
        val ACTIVE_STATES = setOf(
            PairingState.SPAKE_IN_FLIGHT,
            PairingState.AWAITING_CB,
            PairingState.ENROLLMENT_PROTECTED,
        )
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
