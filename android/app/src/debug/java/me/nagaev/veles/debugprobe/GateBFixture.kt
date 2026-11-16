package me.nagaev.veles.debugprobe

/** Monotonic elapsed-realtime milliseconds; no fixture behaviour reads a wall clock for expiry. */
internal fun interface ElapsedClock {
    fun elapsedMillis(): Long
}

/**
 * Debug-only Gate B history fixture (OTP-12 design D3): at most five synthetic §8.1 records
 * kept in memory, each with a fixed event id, the seed's wall-clock receipt epoch and its own
 * expiry on the injected elapsed-realtime clock. A pull re-encodes every unexpired record with
 * `remaining_lifetime` = floor(remaining seconds); a record under one second is omitted.
 *
 * A seed replaces only the history and arms the fixture; [disarm] returns answering to the
 * probe. Nothing is persisted and no notification, `OtpMessage` or clipboard is read, so a
 * process restart is simply a new unarmed instance. Every method is synchronized because the
 * adb receiver's IO coroutine and the session handlers both call in.
 */
internal class GateBFixture(
    private val localInstallationId: ByteArray,
    private val elapsed: ElapsedClock,
    private val wallMillis: () -> Long,
    private val random: (Int) -> ByteArray,
    private val log: (String) -> Unit,
) {
    private class Record(
        /** The entropy block that fixes this record's event id and marker. */
        val entropy: ByteArray,
        val eventKey: String,
        val code: String,
        val receiptEpochSeconds: Long,
        val expiryElapsedMillis: Long,
    )

    private var records: List<Record> = emptyList()

    /** Only the ids of recently pushed events are remembered, so a push never joins the history. */
    private val pushedEventKeys = ArrayDeque<String>()

    /** The single source of armed state; the pull router reads it once per pull. */
    @Volatile
    var armed: Boolean = false
        private set

    /**
     * Replaces the history with exactly five records armed from [lifetimes] (each 1..600 s) and
     * returns true; a list that is not five in-range values is refused, changes nothing and is
     * logged.
     */
    @Synchronized
    fun seed(lifetimes: List<Int>): Boolean {
        if (lifetimes.size != HISTORY_SIZE || lifetimes.any { it !in MIN_LIFETIME_SECONDS..MAX_LIFETIME_SECONDS }) {
            val reason = if (lifetimes.size != HISTORY_SIZE) "count" else "lifetime"
            log("elapsed=${elapsed.elapsedMillis()} refused seed reason=$reason")
            return false
        }
        val seedElapsed = elapsed.elapsedMillis()
        // A single wall-clock read fixes every receipt epoch to the seed's own second.
        val receiptEpochSeconds = wallMillis() / MILLIS_PER_SECOND
        val seeded = lifetimes.map { lifetime ->
            val entropy = random(SYNTHETIC_ENTROPY_BYTES)
            val event = reencode(entropy, receiptEpochSeconds, lifetime.toLong())
            Record(
                entropy = entropy,
                eventKey = hex(event.eventId),
                code = event.code,
                receiptEpochSeconds = receiptEpochSeconds,
                expiryElapsedMillis = seedElapsed + lifetime * MILLIS_PER_SECOND,
            )
        }
        records = seeded
        armed = true
        log(
            "elapsed=$seedElapsed seeded count=${seeded.size} " +
                "markers=${seeded.joinToString(",") { it.code }} " +
                "events=${seeded.joinToString(",") { it.eventKey }}",
        )
        return true
    }

    @Synchronized
    fun disarm() {
        if (!armed) return
        armed = false
        log("elapsed=${elapsed.elapsedMillis()} disarmed")
    }

    /**
     * The `pull_events` body: a count byte and the unexpired records re-encoded in seed order
     * with their fixed event id, original receipt epoch and floor-of-remaining-seconds lifetime.
     */
    @Synchronized
    fun pullBody(): ByteArray {
        val now = elapsed.elapsedMillis()
        val retained = records.mapNotNull { record ->
            val remainingMillis = record.expiryElapsedMillis - now
            if (remainingMillis < MILLIS_PER_SECOND) null else record to remainingMillis / MILLIS_PER_SECOND
        }
        val events = retained.map { (record, lifetime) ->
            reencode(record.entropy, record.receiptEpochSeconds, lifetime)
        }
        val body = ByteArray(1 + events.sumOf { it.body.size })
        body[0] = events.size.toByte()
        var offset = 1
        events.forEach { event ->
            event.body.copyInto(body, offset)
            offset += event.body.size
        }
        log(
            "elapsed=$now pull count=${events.size} expired-omitted count=${records.size - events.size} " +
                "markers=${events.joinToString(",") { it.code }} " +
                "events=${events.joinToString(",") { hex(it.eventId) }}",
        )
        return body
    }

    /**
     * §8.1/event_ack: true when this fixture issued the id (a history record or a recent push).
     * The acknowledgement is logged and never removes a record.
     */
    @Synchronized
    fun acknowledge(eventId: ByteArray): Boolean {
        val key = hex(eventId)
        val owned = records.any { it.eventKey == key } || pushedEventKeys.contains(key)
        if (owned) log("elapsed=${elapsed.elapsedMillis()} ack event=$key")
        return owned
    }

    /** One fresh event for a scheduled push; only its id is remembered, never the record. */
    @Synchronized
    fun nextPush(): SyntheticEvent {
        val event = createSyntheticEvent(
            localInstallationId = localInstallationId,
            receiptEpochSeconds = wallMillis() / MILLIS_PER_SECOND,
            random = random,
        )
        if (pushedEventKeys.size >= PUSHED_ID_MEMORY) pushedEventKeys.removeFirst()
        pushedEventKeys.addLast(hex(event.eventId))
        return event
    }

    /** Re-encodes a record from its stored entropy, so the event id and marker never change. */
    private fun reencode(entropy: ByteArray, receiptEpochSeconds: Long, lifetimeSeconds: Long): SyntheticEvent = createSyntheticEvent(
        localInstallationId = localInstallationId,
        receiptEpochSeconds = receiptEpochSeconds,
        random = { entropy },
        lifetimeSeconds = lifetimeSeconds,
    )

    private companion object {
        private const val HISTORY_SIZE = 5
        private const val MIN_LIFETIME_SECONDS = 1
        private const val MAX_LIFETIME_SECONDS = 600
        private const val MILLIS_PER_SECOND = 1_000L
        private const val PUSHED_ID_MEMORY = 16
    }
}
