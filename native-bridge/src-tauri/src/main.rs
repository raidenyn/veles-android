// OTP-01 sub-project 1c — Veles Native Messaging host entry (scaffold).
//
// Boundary-level skeleton: it runs the headless protocol loop directly. The Tauri runtime
// feature (`tauri-runtime`) is retained only as packaging/build TOOLING scaffolding for the
// OTP-01 toolchain; it is NOT the approved production architecture. The production host is a
// plain Rust executable (RFC amendment of 2026-10-26, docs/otp-23-contract-alignment.md): the
// Tauri event loop is never started by the host, and whether Tauri bundling is kept for
// packaging at all is the open OTP-23 matrix row D-01. This comment changes no behavior and is
// not OTP-24 work.

fn main() {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    if let Err(e) = veles_native_bridge::run(stdin.lock(), &mut out) {
        eprintln!("veles-native-bridge: {e:?}");
        std::process::exit(1);
    }
}
