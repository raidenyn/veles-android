//! Fixed development identity of the OTP-23 harness. The extension key lives in
//! `extension/manifest.json`; its derived id is pinned here so the host can refuse any other
//! caller even though Chrome already enforces `allowed_origins`.

/// Derived from the public `key` in `extension/manifest.json` (SHA-256 of the DER SPKI, first
/// 16 bytes, hex digits mapped to `a..p`). `scripts/verify-identity.py` re-derives and compares it.
pub const EXTENSION_ID: &str = "gmekhgkipjkikmaadaajljinkgfielei";

/// The one origin Chrome passes as the first host argument and lists in `allowed_origins`.
pub const EXPECTED_ORIGIN: &str = "chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/";

pub const HOST_NAME_PLAIN: &str = "me.nagaev.veles.otp23.plain";
pub const HOST_NAME_TAURI: &str = "me.nagaev.veles.otp23.tauri";

/// Chrome appends `--parent-window=<hwnd>` on Windows; nothing else is accepted.
pub fn origin_argument_ok(args: &[String]) -> bool {
    args.get(1).is_some_and(|origin| origin == EXPECTED_ORIGIN)
}
