package me.nagaev.veles.pairing

import android.os.SystemClock

/** One admitted GATT generation, unique per connection for the process lifetime. */
typealias ConnId = Long

/** The answer to one admission question; neither branch carries a reason on the wire. */
sealed interface Decision {
    data object Allow : Decision

    data object Reject : Decision
}

/** What a pairing connection wants the host to do next. */
sealed interface Outgoing {
    /** One pre-auth frame to enqueue before anything else in the list. */
    class Frame(val bytes: ByteArray) : Outgoing

    /** Flush the send queue and close the generation. */
    data object Disconnect : Outgoing
}

/**
 * Phone pairing admission (OTP2-CRY-008, sec-spake-attempts).
 *
 * Every budget lives here so no connection object can reset one by being replaced.
 * Mutating calls share one lock, which makes [chargeAttempt] atomic with respect to
 * concurrent attempts: a charge either happens once or is refused, and nothing can
 * refund it. Reconnect traffic never reaches this object (the host routes only
 * `0x01`-`0x05` frames here), so the pairing budgets cannot be spent by a reconnect
 * handshake.
 *
 * The rolling deques are pruned on every use and capped at one entry past their
 * ceiling, so a flood inside one rolling window cannot grow them without bound. The
 * per-`pairing_id` counter and the per-connection `pA` counter are kept for the process
 * lifetime: ids and connections are one-shot, so each costs one small entry.
 */
class PairingAdmission(
    val limits: PairingLimits = PairingLimits(),
    private val clock: () -> Long = SystemClock::elapsedRealtime,
) {
    private val lock = Any()
    private val attempts = HashMap<PairingKey, Int>()
    private val rollingAttempts = ArrayDeque<Long>()
    private val paConnections = HashMap<ConnId, Int>()
    private val rollingPa = ArrayDeque<Long>()
    private val malformedConnections = LinkedHashMap<ConnId, Long>()
    private val requests = ArrayDeque<Long>()
    private var operations = 0

    /**
     * Admits one `pairing_request`. Every request counts, including the ones refused
     * here: the budget is spent at ingress so a flood cannot be free. The malformed
     * connection budget blocks further requests once the rolling ceiling is reached;
     * both are consulted before the `pA` budgets.
     *
     * [conn] is part of the API shape so a caller cannot ask anonymously; the
     * per-connection budget lives in [paAllowed], and every request counts regardless.
     */
    @Suppress("UnusedParameter")
    fun requestAllowed(conn: ConnId): Decision = synchronized(lock) {
        val now = clock()
        prune(requests, now - limits.requestRollingIntervalMillis, limits.requestRollingMax)
        pruneMalformed(now)
        if (requests.size <= limits.requestRollingMax) requests.addLast(now)
        when {
            requests.size > limits.requestRollingMax -> Decision.Reject
            malformedConnections.size >= limits.malformedConnectionsRollingMax -> Decision.Reject
            else -> Decision.Allow
        }
    }

    /** Admits one fresh `pA`: at most one per connection and the rolling ceiling. */
    fun paAllowed(conn: ConnId): Decision = synchronized(lock) {
        val now = clock()
        prune(rollingPa, now - limits.freshPaRollingIntervalMillis, limits.freshPaRollingMax)
        val granted = paConnections[conn] ?: 0
        if (granted >= limits.freshPaPerConnection || rollingPa.size >= limits.freshPaRollingMax) {
            return Decision.Reject
        }
        paConnections[conn] = granted + 1
        rollingPa.addLast(now)
        Decision.Allow
    }

    /**
     * Consumes one attempt for [pairingId] atomically; a refusal changes nothing and
     * there is no refund path (cancel, disconnect, deadline and confirmation failure
     * all keep a consumed attempt consumed). The per-id ceiling is evaluated before the
     * rolling one, so an over-limit attempt is attributed to the id that exhausted it.
     */
    fun chargeAttempt(pairingId: ByteArray): Decision = synchronized(lock) {
        val now = clock()
        prune(rollingAttempts, now - limits.rollingAttemptsIntervalMillis, limits.rollingAttemptsMax)
        val key = PairingKey.of(pairingId)
        val consumed = attempts[key] ?: 0
        when {
            consumed >= limits.attemptsPerPairingId -> Decision.Reject
            rollingAttempts.size >= limits.rollingAttemptsMax -> Decision.Reject
            else -> {
                attempts[key] = consumed + 1
                rollingAttempts.addLast(now)
                Decision.Allow
            }
        }
    }

    /**
     * Records one malformed or invalid-point pairing connection. Connections are keyed by
     * their generation id, so repeated bad frames on one connection cost one entry: the
     * ceiling counts connections, not frames.
     */
    fun malformed(conn: ConnId) = synchronized(lock) {
        val now = clock()
        pruneMalformed(now)
        if (malformedConnections.size <= limits.malformedConnectionsRollingMax) malformedConnections[conn] = now
    }

    /** Takes the single SPAKE2 in-flight slot; false means another execution holds it. */
    fun beginOp(): Boolean = synchronized(lock) {
        if (operations >= limits.spakeOperationsInFlight) return false
        operations++
        true
    }

    /** Releases the in-flight slot; a machine releases exactly the slot it took. */
    fun endOp() = synchronized(lock) {
        if (operations > 0) operations--
    }

    /** Test observation of the non-refundable consumption rule. */
    internal fun attemptsChargedForTest(pairingId: ByteArray): Int = synchronized(lock) {
        attempts[PairingKey.of(pairingId)] ?: 0
    }

    /** Test observation of the single in-flight slot. */
    internal fun operationsInFlightForTest(): Int = synchronized(lock) { operations }

    /**
     * Drops entries at or before [cutoff] and keeps one entry past the ceiling so the
     * over-limit comparison stays exact without letting a flood grow the deque.
     */
    private fun pruneMalformed(now: Long) {
        val cutoff = now - limits.malformedRollingIntervalMillis
        val entries = malformedConnections.entries.iterator()
        while (entries.hasNext()) {
            if (entries.next().value <= cutoff) entries.remove()
        }
    }

    private fun prune(deque: ArrayDeque<Long>, cutoff: Long, ceiling: Int) {
        while (deque.isNotEmpty() && deque.first() <= cutoff) deque.removeFirst()
        if (deque.size > ceiling) deque.removeFirst()
    }

    /** Byte-array map key with value equality and a bounded textual form. */
    private class PairingKey private constructor(private val hex: String) {
        override fun equals(other: Any?): Boolean = other is PairingKey && other.hex == hex

        override fun hashCode(): Int = hex.hashCode()

        companion object {
            fun of(bytes: ByteArray): PairingKey = PairingKey(
                bytes.joinToString("") { (it.toInt() and BYTE_MASK).toString(HEX_RADIX).padStart(2, '0') },
            )
        }
    }

    private companion object {
        const val BYTE_MASK = 0xFF
        const val HEX_RADIX = 16
    }
}
