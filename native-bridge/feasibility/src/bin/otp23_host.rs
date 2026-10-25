//! Plain-Rust headless Native Messaging host (baseline). No window, no console, no Tauri.
#![cfg_attr(windows, windows_subsystem = "windows")]

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let code = veles_otp23_feasibility::host::run_process(
        &args,
        veles_otp23_feasibility::host::Flavour::Plain,
    );
    std::process::exit(code);
}
