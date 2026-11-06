//! The installer engine's command-line entry point (OTP-25 Task 10).
//!
//! A thin shell over [`veles_bridge_installer::cli`]: it parses the closed
//! argument grammar, resolves the fixed per-user layout from OS APIs, builds the
//! platform adapter for the build target and prints one report. It reads no
//! environment variable, accepts no root/origin/command/URL and starts no shell.
//!
//! Two inputs belong to later tasks and are deliberately seams here:
//!
//! * [`EMBEDDED_DESCRIPTOR`]: the release descriptor the engine authenticates a
//!   package against. Release packaging (Task 13) compiles the approved
//!   descriptor in; until then it is absent and install/repair answer
//!   `policy-denied` rather than trusting a package-supplied descriptor.
//! * [`BUILD_CHANNEL`]: production. The development workflow (Task 12) builds a
//!   separate binary with its own channel compiled in; a production binary
//!   cannot select another channel at runtime.

use std::ffi::OsString;
use std::io::{self, Write};

use veles_bridge_installer::cli::{self, Context, ParsedCommand};
use veles_bridge_installer::{Channel, ReleaseDescriptor, parse_descriptor};

/// The channel this binary installs. Compiled in; never a runtime choice.
const BUILD_CHANNEL: Channel = Channel::Production;

/// Raw bytes of the approved release descriptor, compiled in by release
/// packaging (Task 13). `None` in this unreleased build.
const EMBEDDED_DESCRIPTOR: Option<&[u8]> = None;

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
    let expected: Option<ReleaseDescriptor> =
        EMBEDDED_DESCRIPTOR.and_then(|bytes| parse_descriptor(bytes).ok());
    #[cfg(windows)]
    let platform = veles_bridge_installer::WindowsPlatform::new();
    #[cfg(not(windows))]
    let platform = {
        // Unreachable in practice: `resolve_user_paths` already refused a host
        // without a product target. The portable adapter keeps this a total
        // function and is never the one that runs a product operation.
        veles_bridge_installer::PortablePlatform::new()
    };
    let context = Context {
        platform: &platform,
        paths: &paths,
        expected: expected.as_ref(),
        quiescence: None,
    };
    let report = cli::execute(&context, invocation);
    cli::emit(&report, stdout, stderr)
}
