//! Tauri-shell headless Native Messaging host: a windowless Tauri app (wry runtime, zero windows)
//! whose setup hook runs the same host on a worker thread.
//!
//! When the host is done it asks the Tauri event loop to exit. FINDING (see docs/otp-23-windows.md):
//! with Tauri 2.6.0 and zero windows, `AppHandle::exit` raises `RunEvent::ExitRequested` but the
//! event loop never reaches `RunEvent::Exit` and the process does not end. The host therefore waits
//! a short grace period for Tauri's own exit and then ends the process itself; the trace records
//! which path ran. `OTP23_TAURI_EXIT_PROBE=1` skips the host and exercises only that Tauri exit path.
#![cfg_attr(windows, windows_subsystem = "windows")]

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use veles_otp23_feasibility::host::{Flavour, run_process};
use veles_otp23_feasibility::trace;

const TAURI_EXIT_GRACE: Duration = Duration::from_millis(1500);
/// Exit code of the probe when Tauri's own exit path hung and had to be forced.
const PROBE_FORCED: i32 = 11;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    // Same fail-closed origin gate as the baseline, before any Tauri machinery starts.
    if !veles_otp23_feasibility::identity::origin_argument_ok(&args) {
        std::process::exit(veles_otp23_feasibility::core::EXIT_REJECTED);
    }
    trace::init_from_env();
    let probe = std::env::var_os("OTP23_TAURI_EXIT_PROBE").is_some();
    let tauri_exited = Arc::new(AtomicBool::new(false));
    let setup_flag = tauri_exited.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            trace!("tauri setup: starting host thread");
            let handle = app.handle().clone();
            std::thread::Builder::new().name("veles-host".into()).spawn(move || {
                let code = if probe {
                    std::thread::sleep(Duration::from_millis(200));
                    0
                } else {
                    run_process(&args, Flavour::Tauri)
                };
                trace!("host finished code={code}; asking the Tauri event loop to exit");
                handle.exit(code);
                let deadline = Instant::now() + TAURI_EXIT_GRACE;
                while Instant::now() < deadline && !setup_flag.load(Ordering::SeqCst) {
                    std::thread::sleep(Duration::from_millis(10));
                }
                if setup_flag.load(Ordering::SeqCst) {
                    trace!("tauri exited by itself");
                } else {
                    trace!("tauri did not reach RunEvent::Exit within {TAURI_EXIT_GRACE:?}; forcing process exit");
                    std::process::exit(if probe { PROBE_FORCED } else { code });
                }
            })?;
            Ok(())
        })
        .build(tauri::generate_context!());
    let Ok(app) = app else {
        std::process::exit(veles_otp23_feasibility::core::EXIT_IO);
    };
    app.run(move |_, event| match event {
        tauri::RunEvent::Ready => trace!("tauri RunEvent::Ready"),
        tauri::RunEvent::ExitRequested { code, .. } => {
            trace!("tauri RunEvent::ExitRequested code={code:?}");
        }
        tauri::RunEvent::Exit => {
            trace!("tauri RunEvent::Exit");
            tauri_exited.store(true, Ordering::SeqCst);
        }
        _ => {}
    });
}
