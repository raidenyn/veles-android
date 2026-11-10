package me.nagaev.veles.debugprobe

import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.RecordType

internal const val PROBE_TAG = "VelesProbe"
internal const val SYNTHETIC_MERCHANT = "Veles synthetic"
internal const val SYNTHETIC_CURRENCY = "XTS"
internal const val SYNTHETIC_AMOUNT_MAGNITUDE = 100L
internal const val SYNTHETIC_AMOUNT_SCALE = 2
internal const val SYNTHETIC_REMAINING_LIFETIME = 600L

private const val INSTALLATION_ID_BYTES = 16
private const val EVENT_ID_BYTES = 16
private const val MARKER_BODY_BYTES = 5
private const val CODE_PREFIX = 'V'
private const val MARKER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
private const val MAX_CODE_BYTES = 16
private const val U8_MASK = 0xFF
private const val BITS_PER_BYTE = 8
private const val BYTE_MASK = 0xFFL

/** One §8.1 synthetic event: the decoded marker, the event id and the exact record body. */
internal data class SyntheticEvent(
    val code: String,
    val eventId: ByteArray,
    val body: ByteArray,
)

/** Produces a fresh synthetic event (never a real notification or `OtpMessage`). */
internal fun interface ProbeEventSource {
    fun next(): SyntheticEvent
}

/**
 * Debug application handler: answers `pull_request` with `pull_events` carrying exactly one
 * synthetic §8.1 event. It reads no notification listener, no `OtpMessage` and no clipboard.
 */
internal class SyntheticProbeHandler(
    private val localInstallationId: ByteArray,
    private val clock: () -> Long,
    private val random: (Int) -> ByteArray,
    private val log: (String) -> Unit,
) : ApplicationHandler, ProbeEventSource {
    private var sessionIdPrefix: ByteArray? = null

    override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ) {
        if (!handles(type)) return
        // The handler owns the event; the copy is sealed into `pull_events` by the engine.
        reply(RecordType.PULL_EVENTS.wire, byteArrayOf(1) + next().body)
    }

    override fun onSessionEstablished(sessionIdPrefix: ByteArray) {
        this.sessionIdPrefix = sessionIdPrefix.copyOf()
    }

    override fun next(): SyntheticEvent {
        val event = createSyntheticEvent(
            localInstallationId = localInstallationId,
            receiptEpochSeconds = clock() / MILLIS_PER_SECOND,
            random = random,
        )
        log(
            "marker=${event.code} event=${hex(event.eventId)} " +
                "sid=${hex(sessionIdPrefix ?: ByteArray(SESSION_PREFIX_BYTES))}",
        )
        return event
    }

    companion object {
        private const val MILLIS_PER_SECOND = 1_000L
        private const val SESSION_PREFIX_BYTES = 8
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
        body[offset + index] = (value ushr (BITS_PER_BYTE * (Int.SIZE_BYTES - 1 - index)) and BYTE_MASK).toByte()
    }
    return offset + Int.SIZE_BYTES
}

private fun writeU64(body: ByteArray, offset: Int, value: Long): Int {
    for (index in 0 until Long.SIZE_BYTES) {
        body[offset + index] = (value ushr (BITS_PER_BYTE * (Long.SIZE_BYTES - 1 - index)) and BYTE_MASK).toByte()
    }
    return offset + Long.SIZE_BYTES
}

private fun writeLengthPrefixed(body: ByteArray, offset: Int, value: ByteArray): Int {
    val next = writeU16(body, offset, value.size)
    value.copyInto(body, next)
    return next + value.size
}

internal fun hex(value: ByteArray): String = value.joinToString("") { "%02x".format(it) }
