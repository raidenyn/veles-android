package me.nagaev.veles.debugprobe

import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionReject

internal const val PROBE_TAG = "VelesProbe"
internal const val SYNTHETIC_MERCHANT = "Veles synthetic"
internal const val SYNTHETIC_CURRENCY = "XTS"
internal const val SYNTHETIC_AMOUNT_MAGNITUDE = 100L
internal const val SYNTHETIC_AMOUNT_SCALE = 2
internal const val SYNTHETIC_REMAINING_LIFETIME = 600L

private const val INSTALLATION_ID_BYTES = 16
private const val EVENT_ID_BYTES = 16
private const val EVENT_ACK_BYTES = INSTALLATION_ID_BYTES + EVENT_ID_BYTES
private const val MARKER_BODY_BYTES = 5
private const val CODE_PREFIX = 'V'
private const val MARKER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
private const val MAX_CODE_BYTES = 16
private const val U8_MASK = 0xFF
private const val BITS_PER_BYTE = 8
private const val BYTE_MASK = 0xFFL
private const val LEDGER_MAX_EVENTS = 256
private const val SESSION_PREFIX_BYTES = 8

/** One §8.1 synthetic event: the decoded marker, the event id and the exact record body. */
internal data class SyntheticEvent(
    val code: String,
    val eventId: ByteArray,
    val body: ByteArray,
)

/** Produces a fresh synthetic event for one destination session (never real OTP data). */
internal fun interface ProbeEventSource {
    fun next(sessionIdPrefix: ByteArray?): SyntheticEvent
}

/**
 * Bounded memory of synthetic events this debug process issued. An acknowledgement for an
 * issued event settles exactly one event and duplicates are idempotent; an unknown event id is
 * the OTP2-REC-032a semantic no-op (the caller ignores the result).
 */
internal class SyntheticEventLedger {
    private val issued = LinkedHashSet<String>()
    private val delivered = LinkedHashSet<String>()

    @Synchronized
    fun issue(eventId: ByteArray) {
        val key = hex(eventId)
        if (issued.size >= LEDGER_MAX_EVENTS) issued.remove(issued.first())
        issued += key
    }

    @Synchronized
    fun acknowledge(eventId: ByteArray): Boolean {
        val key = hex(eventId)
        if (issued.remove(key)) {
            if (delivered.size >= LEDGER_MAX_EVENTS) delivered.remove(delivered.first())
            delivered += key
            return true
        }
        return delivered.contains(key)
    }
}

/**
 * Debug application handler: answers `pull_request` with `pull_events` carrying exactly one
 * synthetic §8.1 event, and accepts the matching `event_ack`. It reads no notification
 * listener, no `OtpMessage` and no clipboard.
 *
 * A handler instance is created per authenticated session through [forSession]: its log lines
 * carry that session's own id prefix, and its issued events are settled by that session's
 * acknowledgements. [SyntheticEventLedger] is shared across the instances so a pushed event is
 * settled by the acknowledgement of the session that received it.
 */
internal class SyntheticProbeHandler private constructor(
    private val localInstallationId: ByteArray,
    private val clock: () -> Long,
    private val random: (Int) -> ByteArray,
    private val log: (String) -> Unit,
    private val ledger: SyntheticEventLedger,
    private val sessionIdPrefix: ByteArray?,
) : ApplicationHandler,
    ProbeEventSource {
    constructor(
        localInstallationId: ByteArray,
        clock: () -> Long,
        random: (Int) -> ByteArray,
        log: (String) -> Unit,
        ledger: SyntheticEventLedger = SyntheticEventLedger(),
    ) : this(localInstallationId, clock, random, log, ledger, null)

    override fun forSession(sessionIdPrefix: ByteArray): ApplicationHandler = SyntheticProbeHandler(
        localInstallationId = localInstallationId,
        clock = clock,
        random = random,
        log = log,
        ledger = ledger,
        sessionIdPrefix = sessionIdPrefix.copyOf(),
    )

    override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire || type == RecordType.EVENT_ACK.wire

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ) {
        when (type) {
            // The handler owns the event; the copy is sealed into `pull_events` by the engine.
            RecordType.PULL_REQUEST.wire -> {
                // OTP2-REC-031: `pull_request` has an empty body; the codec already enforces it
                // and the handler refuses to answer anything else.
                if (body.isNotEmpty()) throw SessionReject()
                reply(RecordType.PULL_EVENTS.wire, byteArrayOf(1) + next(sessionIdPrefix).body)
            }
            RecordType.EVENT_ACK.wire -> acknowledge(body)
        }
    }

    override fun next(sessionIdPrefix: ByteArray?): SyntheticEvent {
        val event = createSyntheticEvent(
            localInstallationId = localInstallationId,
            receiptEpochSeconds = clock() / MILLIS_PER_SECOND,
            random = random,
        )
        ledger.issue(event.eventId)
        log(
            "marker=${event.code} event=${hex(event.eventId)} " +
                "sid=${hex(sessionIdPrefix ?: ByteArray(SESSION_PREFIX_BYTES))}",
        )
        return event
    }

    /**
     * §8.1/event_ack: `phone_installation_id || event_id`. The shape and the installation id are
     * validated; an unknown or already-settled event id is the OTP2-REC-032a semantic no-op
     * (authenticated, well-formed, settles nothing, mutates nothing, not an error).
     */
    private fun acknowledge(body: ByteArray) {
        if (body.size != EVENT_ACK_BYTES) throw SessionReject()
        if (!body.copyOfRange(0, INSTALLATION_ID_BYTES).contentEquals(localInstallationId)) {
            throw SessionReject()
        }
        ledger.acknowledge(body.copyOfRange(INSTALLATION_ID_BYTES, EVENT_ACK_BYTES))
    }

    private companion object {
        private const val MILLIS_PER_SECOND = 1_000L
    }
}

/**
 * Encodes one §8.1 event exactly: installation id and event id (16 bytes each), epoch
 * seconds (u64be), lifetime (u32be), code and merchant as `len u16be || UTF-8`, amount
 * magnitude (u64be), amount scale (u8) and the three ASCII currency bytes.
 */
internal fun createSyntheticEvent(
    localInstallationId: ByteArray,
    receiptEpochSeconds: Long,
    random: (Int) -> ByteArray,
): SyntheticEvent {
    require(localInstallationId.size == INSTALLATION_ID_BYTES) { "installation id is 16 bytes" }
    val entropy = random(EVENT_ID_BYTES + MARKER_BODY_BYTES)
    require(entropy.size == EVENT_ID_BYTES + MARKER_BODY_BYTES) { "probe entropy is 21 bytes" }
    val eventId = entropy.copyOfRange(0, EVENT_ID_BYTES)
    val marker = buildString {
        append(CODE_PREFIX)
        for (index in EVENT_ID_BYTES until EVENT_ID_BYTES + MARKER_BODY_BYTES) {
            append(MARKER_ALPHABET[(entropy[index].toInt() and U8_MASK) % MARKER_ALPHABET.length])
        }
    }
    check(marker.toByteArray(Charsets.UTF_8).size <= MAX_CODE_BYTES)
    val merchant = SYNTHETIC_MERCHANT.toByteArray(Charsets.UTF_8)
    val body = ByteArray(
        INSTALLATION_ID_BYTES + EVENT_ID_BYTES + Long.SIZE_BYTES + Int.SIZE_BYTES +
            2 + marker.length + 2 + merchant.size + Long.SIZE_BYTES + 1 + SYNTHETIC_CURRENCY.length,
    )
    var offset = 0
    localInstallationId.copyInto(body, offset)
    offset += INSTALLATION_ID_BYTES
    eventId.copyInto(body, offset)
    offset += EVENT_ID_BYTES
    offset = writeU64(body, offset, receiptEpochSeconds)
    offset = writeU32(body, offset, SYNTHETIC_REMAINING_LIFETIME)
    offset = writeLengthPrefixed(body, offset, marker.toByteArray(Charsets.UTF_8))
    offset = writeLengthPrefixed(body, offset, merchant)
    offset = writeU64(body, offset, SYNTHETIC_AMOUNT_MAGNITUDE)
    body[offset++] = SYNTHETIC_AMOUNT_SCALE.toByte()
    SYNTHETIC_CURRENCY.toByteArray(Charsets.US_ASCII).copyInto(body, offset)
    return SyntheticEvent(code = marker, eventId = eventId, body = body)
}

private fun writeU16(body: ByteArray, offset: Int, value: Int): Int {
    body[offset] = (value ushr BITS_PER_BYTE).toByte()
    body[offset + 1] = value.toByte()
    return offset + 2
}

private fun writeU32(body: ByteArray, offset: Int, value: Long): Int {
    for (index in 0 until Int.SIZE_BYTES) {
        body[offset + index] =
            (value ushr (BITS_PER_BYTE * (Int.SIZE_BYTES - 1 - index)) and BYTE_MASK).toByte()
    }
    return offset + Int.SIZE_BYTES
}

private fun writeU64(body: ByteArray, offset: Int, value: Long): Int {
    for (index in 0 until Long.SIZE_BYTES) {
        body[offset + index] =
            (value ushr (BITS_PER_BYTE * (Long.SIZE_BYTES - 1 - index)) and BYTE_MASK).toByte()
    }
    return offset + Long.SIZE_BYTES
}

private fun writeLengthPrefixed(body: ByteArray, offset: Int, value: ByteArray): Int {
    val next = writeU16(body, offset, value.size)
    value.copyInto(body, next)
    return next + value.size
}

internal fun hex(value: ByteArray): String = value.joinToString("") { "%02x".format(it) }
