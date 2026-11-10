// Named reconnect session limits, mirroring the Kotlin `SessionLimits`.
//
// The timing values mirror `protocol/v1/limits.json`; the handshake wait is the
// §11.3 value and `HEARTBEAT_SILENCE_MS` is the plan's Chrome heartbeat driver.
// `HEARTBEAT_TERMINATE_THRESHOLD` is the OTP2-REC-034 rollover threshold: four
// prior heartbeats in `HEARTBEAT_WINDOW_MS`, so the fifth heartbeat in a rolling
// 60 s window ends the session.

/** §11.3 handshake wait: 30 s per wait state. */
export const HANDSHAKE_TIMEOUT_MS = 30_000;

/** `reconnect_idle_timeout_seconds`: no valid record in either direction. */
export const IDLE_TIMEOUT_MS = 180_000;

/** `heartbeat_min_interval_seconds`: the minimum spacing of heartbeats per direction. */
export const HEARTBEAT_MIN_INTERVAL_MS = 30_000;

/** Prior heartbeats allowed in `HEARTBEAT_WINDOW_MS`; the next one terminates. */
export const HEARTBEAT_TERMINATE_THRESHOLD = 4;

/** OTP2-REC-034 rolling window for received heartbeats. */
export const HEARTBEAT_WINDOW_MS = 60_000;

/** Plan heartbeat driver: emit `app_heartbeat` after 60 s with no outbound record. */
export const HEARTBEAT_SILENCE_MS = 60_000;

/** §7.2 u64 counter ceiling; a counter never wraps past this value. */
export const SEQUENCE_MAX = 0xffffffffffffffffn;
