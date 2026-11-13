//! Named protocol ceilings of the portable parsing layer, mirroring `protocol/v1/limits.json`
//! (protocol/docs/veles-v1.md). Every value here is echoed by the shared fixture checks and the
//! host contract tests; nothing in this layer silently hardcodes an untracked limit.

/// One Native Messaging JSON message, both directions: byte length 1..=65,536
/// (OTP2-BRD-001, `bridge_message_max_bytes`).
pub const FRAME_MAX_BYTES: usize = 65_536;

/// Decoded `record_base64` bound at the bridge's Native Messaging boundary
/// (OTP2-BRD-031, `bridge_record_max_bytes`). This is the envelope admission ceiling.
pub const DECODED_RECORD_MAX_BYTES: usize = 5_120;

/// The GATT send ceiling (OTP2-CRY-016c, `record_max_bytes`): a `send` that decodes to
/// 4,097..=5,120 bytes is admitted by this parsing layer but refused by the host's
/// fragment/send path with a correlated `oversize_record` before any fragment is created
/// or written. Documented here; enforced by the host core (later task), not by the parser.
pub const GATT_SEND_RECORD_MAX_BYTES: usize = 4_096;

/// Highest representable API version in `hello` offers (OTP2-BRD-010a).
pub const MAX_API_VERSION: u64 = 1_048_576;
