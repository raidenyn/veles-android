// OTP-01 sub-project 1c — Tauri build script.
//
// Only runs when the `tauri-runtime` feature is enabled (Windows/macOS CI).
// On Linux, the protocol library and its tests build without Tauri's
// webview dependencies.

fn main() {
    #[cfg(feature = "tauri-runtime")]
    tauri_build::build()
}
