//! OTP-24 production native-messaging host entry point. Reads the process arguments, runs
//! the host to its bounded process exit and exits the process exactly once — returning from
//! the runtime never terminates blocked stdio threads, so the explicit `process::exit` is
//! the only way those threads cannot outlive the process.
//!
//! Platform dispatch lives in `veles_ble_host::host::run_process`: this entry adds no
//! behavior of its own, carries no simulator switch, and never serves a platform it was
//! not built for.
#![cfg_attr(windows, windows_subsystem = "windows")]

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let code = veles_ble_host::host::run_process(&args);
    std::process::exit(code);
}
