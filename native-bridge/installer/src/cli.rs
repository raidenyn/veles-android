//! The closed command-line surface of the installer engine (OTP-25 Task 10).
//!
//! Native frontends (NSIS today, the macOS app in Task 11) start the engine as a
//! process with structured arguments and read one report back. Everything they
//! may say is in this module:
//!
//! ```text
//! veles-bridge-maintenance inspect
//! veles-bridge-maintenance install   --package <absolute directory>
//! veles-bridge-maintenance repair    --package <absolute directory>
//! veles-bridge-maintenance rollback
//! veles-bridge-maintenance uninstall
//! veles-bridge-maintenance --internal-cleanup-launcher        (no operands)
//! ```
//!
//! There is no install-root, origin, extension-id, command, URL or shell
//! argument: the root is the fixed per-user layout resolved by the OS, the origin
//! comes from the authenticated descriptor, and `--package` names a directory that
//! the engine authenticates ([`authenticate_package`]) against the release
//! descriptor **compiled into the engine** before anything is trusted. Any other
//! argument is exit 2 before the platform is touched, and the diagnostic never
//! echoes the offending operand.
//!
//! Output contract:
//!
//! * **stdout** - exactly one newline-terminated [`InstallReport`] of at most
//!   [`MAX_REPORT_BYTES`], for every accepted invocation (also on failure). An
//!   invalid invocation runs nothing and so prints no report.
//! * **stderr** - one fixed status line of enumerated words. It carries no
//!   payload byte, environment value, package path or install path.
//! * **exit** - [`EXIT_COMPLETE`] `0`, [`EXIT_FAILURE`] `1`, [`EXIT_INVALID`] `2`.
//!   A report whose warnings include `cleanup-pending`, or whose failure is
//!   `cleanup-pending` or `unsupported-state`, is never a `0`.
//!
//! This module dispatches nothing itself: it parses, authenticates the package
//! and calls [`run_operation`] (or its bounded form [`run_operation_within`]), the
//! engine's one operation dispatcher.

use std::ffi::OsString;
use std::io::Write;
use std::path::PathBuf;
use std::time::Duration;

pub use crate::cleanup::CLEANUP_LAUNCHER_ARG;
use crate::model::{
    Channel, FailureCode, InstallFailure, InstallReport, InstallResult, Operation,
    ReleaseDescriptor, Target, WarningCode, canonical_json,
};
use crate::operations::{
    STATE_DAMAGED, STATE_FOREIGN, STATE_RECOVERY_REQUIRED, run_operation, run_operation_within,
};
use crate::paths::InstallPaths;
use crate::platform::{Platform, authenticate_package};

/// The operation completed.
pub const EXIT_COMPLETE: i32 = 0;
/// The operation failed, was refused, or ended with work pending.
pub const EXIT_FAILURE: i32 = 1;
/// The invocation was invalid; nothing was run.
pub const EXIT_INVALID: i32 = 2;
/// Hard bound on the report document.
pub const MAX_REPORT_BYTES: usize = 64 * 1024;
/// The report state of an operation that failed before reaching a state.
pub const STATE_FAILED: &str = "failed";
/// The only diagnostic an invalid invocation prints.
pub const INVALID_INVOCATION_STATUS: &str = "veles-bridge-maintenance: status=invalid-invocation";

const PACKAGE_FLAG: &str = "--package";
/// Prefix of every status line; the frontend and the tests rely on it.
const STATUS_PREFIX: &str = "veles-bridge-maintenance: ";

/// One accepted operation request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Invocation {
    pub operation: Operation,
    /// The absolute package directory; present exactly for install and repair.
    pub package: Option<PathBuf>,
}

/// What an accepted argument vector asks for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParsedCommand {
    /// One of the five operations.
    Run(Invocation),
    /// The internal short-lived cleanup launcher mode (no operands).
    CleanupLauncher,
}

/// An argument vector outside the closed grammar. Carries nothing on purpose:
/// the offending operand is never retained, let alone printed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidInvocation;

/// Parse an argument vector (without the program name).
///
/// The grammar is closed and positional: an operation word, then for install and
/// repair exactly `--package <absolute directory>`. Every operand must be valid
/// UTF-8 and a package must be an absolute path that is not flag-shaped.
pub fn parse_args(args: &[OsString]) -> Result<ParsedCommand, InvalidInvocation> {
    let mut texts = Vec::with_capacity(args.len());
    for argument in args {
        texts.push(argument.to_str().ok_or(InvalidInvocation)?);
    }
    let (word, rest) = texts.split_first().ok_or(InvalidInvocation)?;
    if *word == CLEANUP_LAUNCHER_ARG {
        return if rest.is_empty() {
            Ok(ParsedCommand::CleanupLauncher)
        } else {
            Err(InvalidInvocation)
        };
    }
    let operation = match *word {
        "inspect" => Operation::Inspect,
        "install" => Operation::Install,
        "repair" => Operation::Repair,
        "rollback" => Operation::Rollback,
        "uninstall" => Operation::Uninstall,
        _ => return Err(InvalidInvocation),
    };
    let takes_package = matches!(operation, Operation::Install | Operation::Repair);
    let package = match (takes_package, rest) {
        (false, []) => None,
        (true, [flag, value]) if *flag == PACKAGE_FLAG => {
            let path = PathBuf::from(value);
            if value.is_empty() || value.starts_with('-') || !path.is_absolute() {
                return Err(InvalidInvocation);
            }
            Some(path)
        }
        _ => return Err(InvalidInvocation),
    };
    Ok(ParsedCommand::Run(Invocation { operation, package }))
}

/// Everything an operation needs from the process it runs in.
///
/// The platform adapter and the fixed layout are supplied by the binary (from OS
/// APIs, never from arguments); `expected` is the release descriptor compiled
/// into the engine, which is the only thing a package may be authenticated
/// against. `None` means this build embeds no release descriptor and so cannot
/// install or repair.
pub struct Context<'a, P: Platform> {
    pub platform: &'a P,
    pub paths: &'a InstallPaths,
    pub expected: Option<&'a ReleaseDescriptor>,
    /// A shorter host-quiescence wait than the engine default, for tests. The
    /// engine clamps it to its fixed ceiling, so it can never lengthen the wait;
    /// the binary passes `None`.
    pub quiescence: Option<Duration>,
}

/// The process exit status a finished report maps to.
///
/// Exit `0` means the operation completed. A pending cleanup (warning or failure)
/// and an unsupported-state refusal are always `1`. An inspect that produced a
/// report is complete whatever it found - the state and guidance are the answer;
/// every other operation must end without failure and in a settled state.
pub fn exit_code(report: &InstallReport) -> i32 {
    if report.warns(WarningCode::CleanupPending)
        || matches!(
            report.failure,
            Some(FailureCode::CleanupPending | FailureCode::UnsupportedState)
        )
    {
        return EXIT_FAILURE;
    }
    if report.operation == Operation::Inspect {
        return EXIT_COMPLETE;
    }
    let unsettled = matches!(
        report.state.as_str(),
        STATE_DAMAGED | STATE_FOREIGN | STATE_RECOVERY_REQUIRED | STATE_FAILED
    );
    if report.failure.is_some() || unsettled {
        EXIT_FAILURE
    } else {
        EXIT_COMPLETE
    }
}

/// The report of an operation that failed.
///
/// The failure code is kept verbatim - in particular `unsupported-state` and
/// `cleanup-pending` are never folded into a generic failure.
pub fn failure_report(operation: Operation, failure: &InstallFailure) -> InstallReport {
    InstallReport {
        operation,
        state: STATE_FAILED.to_owned(),
        prior_usable: failure.prior_usable,
        active_payload: None,
        failure: Some(failure.code),
        warnings: Vec::new(),
    }
}

/// Render the one stdout document: canonical JSON plus a newline, bounded.
pub fn render_report(report: &InstallReport) -> InstallResult<Vec<u8>> {
    let bytes = canonical_json(report)?;
    if bytes.len() > MAX_REPORT_BYTES {
        return Err(InstallFailure::new(FailureCode::InvalidInput));
    }
    Ok(bytes)
}

/// Run one accepted operation to a report. Never panics on a refusal.
pub fn execute<P: Platform>(context: &Context<'_, P>, invocation: &Invocation) -> InstallReport {
    let operation = invocation.operation;
    let outcome =
        authenticated_package(context, invocation).and_then(|package| match context.quiescence {
            None => run_operation(context.platform, context.paths, operation, package),
            Some(bound) => {
                run_operation_within(context.platform, context.paths, operation, package, bound)
            }
        });
    match outcome {
        Ok(report) => report,
        Err(failure) => {
            // A refusal before the operation ran (a package that did not
            // authenticate) still owes the frontend the live truth about the
            // prior installation, read read-only.
            let failure = if failure.prior_usable {
                failure
            } else {
                failure.with_prior_usable(prior_is_usable(context))
            };
            failure_report(operation, &failure)
        }
    }
}

fn authenticated_package<P: Platform>(
    context: &Context<'_, P>,
    invocation: &Invocation,
) -> InstallResult<Option<crate::platform::AuthenticatedPackage>> {
    let Some(package) = &invocation.package else {
        return Ok(None);
    };
    // A build without an embedded release descriptor has no authority to
    // authenticate any package against.
    let expected = context
        .expected
        .ok_or_else(|| InstallFailure::new(FailureCode::PolicyDenied))?;
    authenticate_package(context.platform, package, expected).map(Some)
}

fn prior_is_usable<P: Platform>(context: &Context<'_, P>) -> bool {
    run_operation(context.platform, context.paths, Operation::Inspect, None)
        .map(|report| report.prior_usable)
        .unwrap_or(false)
}

/// Parse `args`, run the operation, print the report and the status line, and
/// return the process exit status.
///
/// The launcher mode is not an operation: the binary dispatches it from
/// [`parse_args`] before it builds a [`Context`], so here it is refused.
pub fn run<P: Platform>(
    args: &[OsString],
    context: &Context<'_, P>,
    stdout: &mut dyn Write,
    stderr: &mut dyn Write,
) -> i32 {
    let invocation = match parse_args(args) {
        Ok(ParsedCommand::Run(invocation)) => invocation,
        Ok(ParsedCommand::CleanupLauncher) | Err(InvalidInvocation) => {
            return invalid_invocation(stderr);
        }
    };
    let report = execute(context, &invocation);
    emit(&report, stdout, stderr)
}

/// Print the report and the status line for an already finished operation.
pub fn emit(report: &InstallReport, stdout: &mut dyn Write, stderr: &mut dyn Write) -> i32 {
    let code = exit_code(report);
    let rendered = match render_report(report) {
        Ok(rendered) => rendered,
        Err(_) => {
            let _ = writeln!(stderr, "{STATUS_PREFIX}status=report-unavailable");
            return EXIT_FAILURE;
        }
    };
    if stdout
        .write_all(&rendered)
        .and_then(|()| stdout.flush())
        .is_err()
    {
        let _ = writeln!(stderr, "{STATUS_PREFIX}status=report-unwritable");
        return EXIT_FAILURE;
    }
    let _ = writeln!(stderr, "{}", status_line(report, code));
    code
}

/// Print the invalid-invocation status line and return [`EXIT_INVALID`].
pub fn invalid_invocation(stderr: &mut dyn Write) -> i32 {
    let _ = writeln!(stderr, "{INVALID_INVOCATION_STATUS}");
    EXIT_INVALID
}

/// The fixed-vocabulary status line of one report: enumerated words only.
fn status_line(report: &InstallReport, code: i32) -> String {
    let mut line = format!(
        "{STATUS_PREFIX}operation={} result={} state={} code={}",
        operation_word(report.operation),
        if code == EXIT_COMPLETE {
            "complete"
        } else {
            "failure"
        },
        sanitized_state(&report.state),
        report.failure.map_or("none", |failure| failure.as_str()),
    );
    if !report.warnings.is_empty() {
        let words: Vec<&str> = report.warnings.iter().map(|w| w.as_str()).collect();
        line.push_str(" warnings=");
        line.push_str(&words.join(","));
    }
    line
}

fn operation_word(operation: Operation) -> &'static str {
    match operation {
        Operation::Inspect => "inspect",
        Operation::Install => "install",
        Operation::Repair => "repair",
        Operation::Rollback => "rollback",
        Operation::Uninstall => "uninstall",
    }
}

/// The engine's state words are lowercase kebab-case; anything else is masked so
/// a diagnostic can never carry free text.
fn sanitized_state(state: &str) -> &str {
    if !state.is_empty()
        && state.len() <= 32
        && state
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'-')
    {
        state
    } else {
        "unknown"
    }
}

/// The report of an operation on a host that has no product target.
pub fn unsupported_target_report(operation: Operation) -> InstallReport {
    failure_report(
        operation,
        &InstallFailure::new(FailureCode::UnsupportedTarget),
    )
}

/// The fixed per-user layout for this process, resolved from OS APIs.
///
/// There is no override: on Windows the user profile and `%LOCALAPPDATA%` come
/// from the Known Folders API (`SHGetKnownFolderPath`), never from environment
/// variables or arguments. A host without a product target (and macOS, whose
/// known-folder resolution belongs to the Task 11 frontend) is
/// `UnsupportedTarget`.
pub fn resolve_user_paths(channel: Channel) -> InstallResult<InstallPaths> {
    #[cfg(windows)]
    {
        let profile = windows_known_folder(&windows::Win32::UI::Shell::FOLDERID_Profile)?;
        let local = windows_known_folder(&windows::Win32::UI::Shell::FOLDERID_LocalAppData)?;
        InstallPaths::for_user(Target::WindowsX64, channel, &profile, Some(&local))
    }
    #[cfg(not(windows))]
    {
        let _ = (channel, Target::WindowsX64);
        Err(InstallFailure::new(FailureCode::UnsupportedTarget))
    }
}

#[cfg(windows)]
fn windows_known_folder(id: &windows::core::GUID) -> InstallResult<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    use windows::Win32::System::Com::CoTaskMemFree;
    use windows::Win32::UI::Shell::{KF_FLAG_DEFAULT, SHGetKnownFolderPath};

    // SAFETY: `id` is a live GUID; the returned buffer is NUL-terminated, owned by
    // the caller and freed exactly once below.
    let path = unsafe { SHGetKnownFolderPath(id, KF_FLAG_DEFAULT, None) }
        .map_err(|_| InstallFailure::new(FailureCode::Io))?;
    // SAFETY: `path` is a valid NUL-terminated wide string until it is freed.
    let text = OsString::from_wide(unsafe { path.as_wide() });
    // SAFETY: the buffer came from `SHGetKnownFolderPath` and is freed once.
    unsafe { CoTaskMemFree(Some(path.0.cast_const().cast())) };
    Ok(PathBuf::from(text))
}
