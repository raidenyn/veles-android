// OTP-01 sub-project 1c — Veles Native Messaging host runtime entry.
//
// The real Tauri app shell is gated behind the `tauri-runtime` feature and
// only builds on Windows/macOS CI (per the 1d environment strategy). This
// boundary-level skeleton runs the headless protocol loop directly; the
// Tauri runtime wires the same `run` entry point once full integration lands.

fn main() {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    if let Err(e) = veles_native_bridge::run(stdin.lock(), &mut out) {
        eprintln!("veles-native-bridge: {e:?}");
        std::process::exit(1);
    }
}
