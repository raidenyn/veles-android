package me.nagaev.veles.session

/**
 * Named reconnect session limits.
 *
 * The timing values mirror `protocol/v1/limits.json`; [HEARTBEAT_TERMINATE_THRESHOLD] is the
 * OTP2-REC-034 rollover threshold (4 prior heartbeats in [HEARTBEAT_WINDOW_MS], so the fifth
 * heartbeat in a rolling 60 s window ends the session).
 */
object SessionLimits {
    /** §11.3 handshake wait: 30 s per wait state. */
    const val HANDSHAKE_TIMEOUT_MS = 30_000L

    /** `reconnect_idle_timeout_seconds`: no valid record in either direction. */
    const val IDLE_TIMEOUT_MS = 180_000L

    /** `heartbeat_min_interval_seconds`: the minimum spacing of heartbeats per direction. */
    const val HEARTBEAT_MIN_INTERVAL_MS = 30_000L

    /** Prior heartbeats allowed in [HEARTBEAT_WINDOW_MS]; the next one terminates. */
    const val HEARTBEAT_TERMINATE_THRESHOLD = 4

    /** OTP2-REC-034 rolling window for received heartbeats, per direction. */
    const val HEARTBEAT_WINDOW_MS = 60_000L

    /** §7.2 u64 counter ceiling; a counter never wraps past this value. */
    val SEQUENCE_MAX: ULong = ULong.MAX_VALUE
}
