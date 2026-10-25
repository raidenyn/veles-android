//! Process entry shared by the plain-Rust and Tauri-linked binaries.

use crate::core::Config;
use crate::runtime::{self, Io};
use crate::{identity, sim};

/// Which executable flavour is running; reported in `ready.bridge_version`.
#[derive(Clone, Copy)]
pub enum Flavour {
    Plain,
    Tauri,
}

impl Flavour {
    fn label(self) -> &'static str {
        match self {
            Flavour::Plain => "plain",
            Flavour::Tauri => "tauri",
        }
    }
}

// Linux runs the portable Windows harness tests, never a native BLE backend.
#[cfg(not(target_os = "macos"))]
const PLATFORM: &str = "windows";
#[cfg(target_os = "macos")]
const PLATFORM: &str = "macos";

/// Stdio as raw duplicated handles: no std-level locks or line buffering can block process exit.
pub fn process_io() -> std::io::Result<Io> {
    use std::fs::File;
    #[cfg(unix)]
    use std::os::fd::AsFd;
    #[cfg(windows)]
    use std::os::windows::io::AsHandle;
    #[cfg(windows)]
    let (stdin, stdout) = (
        File::from(std::io::stdin().as_handle().try_clone_to_owned()?),
        File::from(std::io::stdout().as_handle().try_clone_to_owned()?),
    );
    #[cfg(unix)]
    let (stdin, stdout) = (
        File::from(std::io::stdin().as_fd().try_clone_to_owned()?),
        File::from(std::io::stdout().as_fd().try_clone_to_owned()?),
    );
    Ok(Io {
        stdin: Box::new(stdin),
        stdout: Box::new(stdout),
    })
}

/// Runs one host process and returns its exit code. `args` is the full argv.
pub fn run_process(args: &[String], flavour: Flavour) -> i32 {
    if !identity::origin_argument_ok(args) {
        return crate::core::EXIT_REJECTED;
    }
    let Ok(io) = process_io() else {
        return crate::core::EXIT_IO;
    };
    let config = Config {
        bridge_version: format!(
            "otp23-feasibility/{} {}",
            env!("CARGO_PKG_VERSION"),
            flavour.label()
        ),
        platform: PLATFORM,
    };
    let simulated = args
        .windows(2)
        .any(|pair| pair[0] == "--backend" && pair[1] == "sim");
    if simulated {
        return runtime::run(sim::SimBackend::new, io, config);
    }
    #[cfg(windows)]
    {
        runtime::run(crate::winrt::WinrtBackend::new, io, config)
    }
    #[cfg(not(windows))]
    {
        runtime::run(crate::unsupported::UnsupportedBackend::new, io, config)
    }
}
