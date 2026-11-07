//! The installer engine's command-line entry point (OTP-25 Task 10).
//!
//! A thin shell over [`veles_bridge_installer::cli`]: it parses the closed
//! argument grammar, resolves the fixed per-user layout from OS APIs, builds the
//! platform adapter for the build target and prints one report. It reads no
//! environment variable at run time, accepts no root/origin/command/URL and
//! starts no shell.
//!
//! Two inputs are compiled in, never chosen at run time:
//!
//! * [`EMBEDDED_CARRIER`]: the authenticated release carrier release packaging
//!   supplies through the build-time channel (`VELES_EMBEDDED_CARRIER`, see
//!   `build.rs`). The engine parses the release descriptor out of those exact
//!   bytes and authenticates every package against it. This unreleased build
//!   embeds nothing, so install and repair answer `policy-denied` rather than
//!   trusting a package-supplied descriptor.
//! * [`BUILD_CHANNEL`]: the channel compiled into this binary
//!   (`veles_bridge_installer::COMPILED_CHANNEL`): production unless the producer
//!   built a fixture or development engine with its own `veles_channel` cfg. A
//!   binary cannot select another channel at runtime.

use std::ffi::OsString;
use std::io::{self, Write};

use veles_bridge_installer::cli::{self, Context, ParsedCommand};
use veles_bridge_installer::embedded::decode_embedded_carrier;
use veles_bridge_installer::host::host_platform;
use veles_bridge_installer::{COMPILED_CHANNEL, Channel};

/// The channel this binary installs. Compiled in; never a runtime choice.
const BUILD_CHANNEL: Channel = COMPILED_CHANNEL;

// Written by `build.rs`: `Some(exact carrier bytes)` when release packaging
// supplied the authenticated carrier, `None` in an unreleased build.
include!(concat!(env!("OUT_DIR"), "/embedded_carrier.rs"));

fn main() {
    let arguments: Vec<OsString> = std::env::args_os().skip(1).collect();
    let mut stdout = io::stdout().lock();
    let mut stderr = io::stderr().lock();
    let code = match cli::parse_args(&arguments) {
        Ok(ParsedCommand::CleanupLauncher) => veles_bridge_installer::run_cleanup_launcher(),
        Ok(ParsedCommand::Run(invocation)) => run(&invocation, &mut stdout, &mut stderr),
        Err(_) => cli::invalid_invocation(&mut stderr),
    };
    let _ = stdout.flush();
    let _ = stderr.flush();
    std::process::exit(code);
}

fn run(invocation: &cli::Invocation, stdout: &mut dyn Write, stderr: &mut dyn Write) -> i32 {
    let paths = match cli::resolve_user_paths(BUILD_CHANNEL) {
        Ok(paths) => paths,
        Err(failure) => {
            let report = cli::failure_report(invocation.operation, &failure);
            return cli::emit(&report, stdout, stderr);
        }
    };
    // A carrier was compiled in but does not parse: that is a refusal of the
    // build's own release identity, never a silent "no identity".
    let expected = match decode_embedded_carrier(EMBEDDED_CARRIER) {
        Ok(expected) => expected,
        Err(failure) => {
            let report = cli::failure_report(invocation.operation, &failure);
            return cli::emit(&report, stdout, stderr);
        }
    };
    // The adapter is the one this build target needs (Windows, macOS or the
    // portable reference host); never a fallback chosen at run time.
    let platform = host_platform();
    let context = Context {
        platform: &platform,
        paths: &paths,
        expected: expected.as_ref(),
        quiescence: None,
    };
    let report = cli::execute(&context, invocation);
    cli::emit(&report, stdout, stderr)
}
