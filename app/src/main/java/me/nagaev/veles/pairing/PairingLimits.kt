package me.nagaev.veles.pairing

/**
 * Phone-side pairing ceilings, one field per `protocol/v1/limits.json` entry.
 *
 * The names and units mirror the JSON so `limits_match_limits_json` can map them
 * one-to-one; a change to either side must land in both. Durations are held in
 * seconds exactly as the protocol document states them, and the millisecond
 * getters are the only conversion point.
 */
data class PairingLimits(
    val pairingWindowSeconds: Long = 300L,
    val attemptsPerPairingId: Int = 5,
    val rollingAttemptsIntervalSeconds: Long = 300L,
    val rollingAttemptsMax: Int = 5,
    val spakeOperationsInFlight: Int = 1,
    val freshPaPerConnection: Int = 1,
    val freshPaRollingIntervalSeconds: Long = 60L,
    val freshPaRollingMax: Int = 5,
    val malformedConnectionsRollingMax: Int = 20,
    val malformedRollingIntervalSeconds: Long = 60L,
    val requestRollingMax: Int = 10,
    val requestRollingIntervalSeconds: Long = 60L,
    val requestInactivityTimeoutSeconds: Long = 30L,
) {
    val rollingAttemptsIntervalMillis: Long get() = rollingAttemptsIntervalSeconds.millis()

    val freshPaRollingIntervalMillis: Long get() = freshPaRollingIntervalSeconds.millis()

    val malformedRollingIntervalMillis: Long get() = malformedRollingIntervalSeconds.millis()

    val requestRollingIntervalMillis: Long get() = requestRollingIntervalSeconds.millis()

    val requestInactivityTimeoutMillis: Long get() = requestInactivityTimeoutSeconds.millis()

    private fun Long.millis(): Long = this * MILLIS_PER_SECOND

    private companion object {
        const val MILLIS_PER_SECOND = 1_000L
    }
}
