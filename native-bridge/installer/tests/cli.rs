//! Engine CLI contract tests (OTP-25 Task 10).
//!
//! The CLI is the only surface a native frontend (NSIS today, the macOS app in
//! Task 11) drives. These tests pin its closed contract:
//!
//! * only `inspect|install|repair|rollback|uninstall` (plus `--package <dir>` for
//!   install and repair) and one internal launcher mode are accepted; a caller
//!   can select no install root, origin, command, URL or shell, and an unknown
//!   flag is exit 2 before any platform effect;
//! * every operation prints exactly one strict `InstallReport` of at most 64 KiB
//!   on stdout, and stderr carries a fixed status vocabulary only - never a
//!   payload, an environment value, a package path or an install path;
//! * exit 0 is a complete operation, 1 an operation failure (a pending cleanup and
//!   an unsupported-state refusal included), 2 an invalid invocation.
//!
//! The portable tests drive [`cli::run`] over [`FakePlatform`]; the process tests
//! drive the real `veles-bridge-maintenance` binary, which on this Linux host is a
//! non-product host and therefore reports `unsupported-target` for any operation
//! it would otherwise perform. Native Windows execution is NOT RUN here.

use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use veles_bridge_installer::cli::{
    self, CLEANUP_LAUNCHER_ARG, Context, EXIT_COMPLETE, EXIT_FAILURE, EXIT_INVALID, Invocation,
    MAX_REPORT_BYTES, ParsedCommand,
};
use veles_bridge_installer::{
    Channel, FailureCode, FakePlatform, Faults, HostState, InstallPaths, InstallReport, Operation,
    STATE_UNINSTALLED, Target, WarningCode,
};

static COUNTER: AtomicU64 = AtomicU64::new(0);

struct Temp(PathBuf);

impl Temp {
    fn new(tag: &str) -> Self {
        let unique = COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "veles-installer-task10-{tag}-{}-{unique}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).expect("temp dir");
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn args(items: &[&str]) -> Vec<OsString> {
    items.iter().map(OsString::from).collect()
}

fn windows_paths(temp: &Temp) -> InstallPaths {
    let home = temp.path().join("Test User Å/用户");
    let local = temp.path().join("Local AppData/用户 Å");
    InstallPaths::for_user(Target::WindowsX64, Channel::Production, &home, Some(&local))
        .expect("injected Windows roots")
}

fn quiescent() -> FakePlatform {
    FakePlatform::with_faults(Faults {
        host_state: HostState::Quiescent,
        ..Faults::default()
    })
}

/// Run the CLI in-process and return `(exit, stdout, stderr)`.
fn run_cli(
    arguments: &[OsString],
    platform: &FakePlatform,
    paths: &InstallPaths,
) -> (i32, Vec<u8>, Vec<u8>) {
    let context = Context {
        platform,
        paths,
        expected: None,
        quiescence: Some(Duration::from_millis(50)),
    };
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let exit = cli::run(arguments, &context, &mut stdout, &mut stderr);
    (exit, stdout, stderr)
}

fn parse_report(stdout: &[u8]) -> InstallReport {
    assert!(
        stdout.len() <= MAX_REPORT_BYTES,
        "the report is at most 64 KiB"
    );
    assert_eq!(
        stdout.iter().filter(|byte| **byte == b'\n').count(),
        1,
        "exactly one newline-terminated JSON document"
    );
    assert!(stdout.ends_with(b"\n"));
    serde_json::from_slice(stdout).expect("a strict InstallReport")
}

fn binary() -> Command {
    Command::new(env!("CARGO_BIN_EXE_veles-bridge-maintenance"))
}

fn run_binary(arguments: &[&str]) -> (i32, Vec<u8>, Vec<u8>) {
    let output = binary()
        .args(arguments)
        .stdin(Stdio::null())
        .env_clear()
        .output()
        .expect("the maintenance binary runs");
    (
        output.status.code().expect("a normal exit"),
        output.stdout,
        output.stderr,
    )
}

// ---------------------------------------------------------------------------
// reject_root_origin_shell_and_unknown_flags
// ---------------------------------------------------------------------------

#[test]
fn reject_root_origin_shell_and_unknown_flags() {
    let rejected: &[&[&str]] = &[
        // No operation, an unknown operation, a wrong-case operation.
        &[],
        &["frobnicate"],
        &["Install", "--package", "/p"],
        &["--help"],
        &["-h"],
        // A caller-selected install root, in every spelling.
        &["install", "--root", "/tmp/elsewhere"],
        &["install", "--root=/tmp/elsewhere"],
        &["uninstall", "--install-root", "/tmp/elsewhere"],
        &["inspect", "--dir", "/tmp/elsewhere"],
        // A caller-selected origin or extension id.
        &[
            "install",
            "--origin",
            "chrome-extension://abcdefghijklmnopabcdefghijklmnop/",
        ],
        &[
            "repair",
            "--extension-id",
            "abcdefghijklmnopabcdefghijklmnop",
        ],
        // An arbitrary command, shell string or URL.
        &["install", "--shell", "cmd /c calc"],
        &["install", "--command", "calc.exe"],
        &["install", "--exec", "calc.exe"],
        &["install", "--url", "https://example.invalid/payload"],
        &["install", "--package", "https://example.invalid/payload"],
        &["cmd", "/c", "calc"],
        &["inspect", "&&", "calc"],
        // `--package` misuse.
        &["install"],
        &["repair"],
        &["install", "--package"],
        &["install", "--package", "relative/dir"],
        &["install", "--package", ""],
        &["install", "--package", "/a", "--package", "/b"],
        &["install", "--package=/a"],
        &["install", "/a"],
        &["install", "--package", "--root"],
        &["install", "--package", "/a", "--extra"],
        // `--package` is not part of the other operations.
        &["inspect", "--package", "/a"],
        &["rollback", "--package", "/a"],
        &["uninstall", "--package", "/a"],
        // Extra positional arguments.
        &["inspect", "extra"],
        &["uninstall", "now"],
        // The launcher mode takes no operands and no operation.
        &[CLEANUP_LAUNCHER_ARG, "extra"],
        &[CLEANUP_LAUNCHER_ARG, "--root", "/x"],
        &["install", CLEANUP_LAUNCHER_ARG],
    ];

    let temp = Temp::new("reject");
    let paths = windows_paths(&temp);
    for case in rejected {
        let owned = args(case);
        assert!(
            cli::parse_args(&owned).is_err(),
            "{case:?} must be an invalid invocation"
        );
        let platform = quiescent();
        let (exit, stdout, stderr) = run_cli(&owned, &platform, &paths);
        assert_eq!(exit, EXIT_INVALID, "{case:?} exits 2");
        assert!(
            stdout.is_empty(),
            "{case:?} prints no report: nothing was run"
        );
        assert!(
            platform.calls().is_empty(),
            "{case:?} reaches the platform not at all"
        );
        let diagnostic = String::from_utf8(stderr).expect("stderr is ASCII status text");
        assert_eq!(
            diagnostic.trim_end(),
            cli::INVALID_INVOCATION_STATUS,
            "{case:?} prints the fixed status line only"
        );
        for operand in case.iter().skip(1) {
            if operand.len() > 3 {
                assert!(
                    !diagnostic.contains(operand),
                    "{case:?} never echoes the offending operand"
                );
            }
        }
    }

    // The real binary agrees, for a representative of each class, before it
    // touches the platform or the file system.
    for case in [
        &["install", "--root", "/tmp/elsewhere"][..],
        &["install", "--origin", "chrome-extension://x/"][..],
        &["install", "--shell", "calc"][..],
        &["install", "--unknown"][..],
        &["frobnicate"][..],
        &[][..],
    ] {
        let (exit, stdout, stderr) = run_binary(case);
        assert_eq!(exit, EXIT_INVALID, "{case:?} exits 2 as a process");
        assert!(stdout.is_empty(), "{case:?} prints no report");
        assert_eq!(
            String::from_utf8(stderr).expect("ASCII").trim_end(),
            cli::INVALID_INVOCATION_STATUS
        );
    }
}

#[cfg(unix)]
#[test]
fn a_non_utf8_operand_is_an_invalid_invocation() {
    use std::os::unix::ffi::OsStringExt;

    let owned = vec![
        OsString::from("install"),
        OsString::from("--package"),
        OsString::from_vec(vec![b'/', 0xff, 0xfe]),
    ];
    assert!(cli::parse_args(&owned).is_err());
}

#[test]
fn the_closed_command_set_parses() {
    for (name, operation) in [
        ("inspect", Operation::Inspect),
        ("rollback", Operation::Rollback),
        ("uninstall", Operation::Uninstall),
    ] {
        assert_eq!(
            cli::parse_args(&args(&[name])).expect("parses"),
            ParsedCommand::Run(Invocation {
                operation,
                package: None
            })
        );
    }
    // The package path may be Unicode and contain spaces: it is one operand.
    let package = if cfg!(windows) {
        r"C:\Users\Tëst Üser\用户\pkg"
    } else {
        "/tmp/Tëst Üser/用户/pkg"
    };
    for (name, operation) in [
        ("install", Operation::Install),
        ("repair", Operation::Repair),
    ] {
        assert_eq!(
            cli::parse_args(&args(&[name, "--package", package])).expect("parses"),
            ParsedCommand::Run(Invocation {
                operation,
                package: Some(PathBuf::from(package)),
            })
        );
    }
    assert_eq!(
        cli::parse_args(&args(&[CLEANUP_LAUNCHER_ARG])).expect("parses"),
        ParsedCommand::CleanupLauncher
    );
}

// ---------------------------------------------------------------------------
// report_no_payload_or_environment_leak
// ---------------------------------------------------------------------------

#[test]
fn report_no_payload_or_environment_leak() {
    let canary = "CANARY-5f3c9a-secret";
    let temp = Temp::new("leak");
    let package = temp.path().join(format!("pkg-{canary}-ünï"));
    fs::create_dir_all(&package).expect("package dir");
    // A payload byte string that must never be echoed anywhere.
    fs::write(package.join("payload-file"), format!("PAYLOAD-{canary}")).expect("payload");
    let package_text = package.to_str().expect("UTF-8 temp path").to_owned();

    // In-process over the Windows layout: install fails for want of an expected
    // descriptor, the report is the strict vocabulary and nothing else leaks.
    let paths = windows_paths(&temp);
    let platform = quiescent();
    let (exit, stdout, stderr) = run_cli(
        &args(&["install", "--package", &package_text]),
        &platform,
        &paths,
    );
    assert_eq!(exit, EXIT_FAILURE);
    let report = parse_report(&stdout);
    assert_eq!(report.operation, Operation::Install);
    assert!(report.failure.is_some());
    assert!(report.active_payload.is_none());
    assert_leak_free(&stdout, &stderr, canary, &package_text, temp.path());

    // An inspect over an empty layout is a complete operation with a report.
    let (exit, stdout, stderr) = run_cli(&args(&["inspect"]), &platform, &paths);
    assert_eq!(exit, EXIT_COMPLETE);
    assert_eq!(parse_report(&stdout).operation, Operation::Inspect);
    assert_leak_free(&stdout, &stderr, canary, &package_text, temp.path());

    // The real process, with a poisoned environment and a canary package path.
    let output = binary()
        .args(["install", "--package", &package_text])
        .env_clear()
        .env("VELES_ENVIRONMENT_CANARY", canary)
        .env("LOCALAPPDATA", temp.path().join(canary))
        .env("HOME", temp.path().join(canary))
        .stdin(Stdio::null())
        .output()
        .expect("the maintenance binary runs");
    assert_eq!(output.status.code(), Some(EXIT_FAILURE));
    let report = parse_report(&output.stdout);
    assert_eq!(report.operation, Operation::Install);
    // A non-product host refuses every operation by name; a Windows or macOS
    // host resolves its layout and then finds no embedded release descriptor in
    // this unreleased build. (The macOS arm is cfg-gated and NOT RUN here.)
    assert_eq!(
        report.failure,
        Some(if cfg!(windows) || cfg!(target_os = "macos") {
            FailureCode::PolicyDenied
        } else {
            FailureCode::UnsupportedTarget
        })
    );
    assert_leak_free(
        &output.stdout,
        &output.stderr,
        canary,
        &package_text,
        temp.path(),
    );
}

fn assert_leak_free(stdout: &[u8], stderr: &[u8], canary: &str, package: &str, temp: &Path) {
    let temp = temp.to_str().expect("UTF-8 temp path");
    for (name, stream) in [("stdout", stdout), ("stderr", stderr)] {
        let text = String::from_utf8_lossy(stream);
        for forbidden in [
            canary,
            "PAYLOAD-",
            package,
            temp,
            "Test User",
            "Local AppData",
        ] {
            assert!(
                !text.contains(forbidden),
                "{name} must not contain {forbidden:?}: {text}"
            );
        }
        assert!(text.is_ascii(), "{name} is the ASCII status vocabulary");
    }
    // The diagnostic is a fixed, key=value status line vocabulary.
    for line in String::from_utf8_lossy(stderr).lines() {
        assert!(
            line.starts_with("veles-bridge-maintenance: "),
            "unexpected diagnostic {line:?}"
        );
    }
}

// ---------------------------------------------------------------------------
// pending_cleanup_is_nonzero
// ---------------------------------------------------------------------------

fn report(state: &str, failure: Option<FailureCode>, operation: Operation) -> InstallReport {
    InstallReport {
        operation,
        state: state.to_owned(),
        prior_usable: false,
        active_payload: None,
        failure,
        warnings: Vec::new(),
    }
}

#[test]
fn pending_cleanup_is_nonzero() {
    // A "successful" uninstall that carries the cleanup-pending warning is not a
    // success: the frontend must not tell the user the product is gone.
    let pending = report(STATE_UNINSTALLED, None, Operation::Uninstall)
        .with_warnings([WarningCode::CleanupPending]);
    assert_eq!(cli::exit_code(&pending), EXIT_FAILURE);

    // Leftover files and a compatibility window are guidance on a completed
    // operation, not failures.
    let leftovers = report(STATE_UNINSTALLED, None, Operation::Uninstall)
        .with_warnings([WarningCode::LeftoverFiles, WarningCode::CompatibilityWindow]);
    assert_eq!(cli::exit_code(&leftovers), EXIT_COMPLETE);

    // An explicit cleanup-pending failure code, on any operation.
    for operation in [
        Operation::Inspect,
        Operation::Install,
        Operation::Repair,
        Operation::Rollback,
        Operation::Uninstall,
    ] {
        let failed = report("damaged", Some(FailureCode::CleanupPending), operation);
        assert_eq!(cli::exit_code(&failed), EXIT_FAILURE, "{operation:?}");
    }

    // The unsupported-state refusal is its own non-zero outcome, never collapsed
    // into a generic failure: the report keeps the code.
    let unsupported = report(
        "recovery-required",
        Some(FailureCode::UnsupportedState),
        Operation::Inspect,
    );
    assert_eq!(cli::exit_code(&unsupported), EXIT_FAILURE);
    let rendered = cli::render_report(&unsupported).expect("renders");
    assert!(
        String::from_utf8(rendered)
            .expect("UTF-8")
            .contains("\"failure\":\"unsupported-state\"")
    );

    // A plain complete inspect, whatever it found, is the answer: exit 0.
    assert_eq!(
        cli::exit_code(&report("absent", None, Operation::Inspect)),
        EXIT_COMPLETE
    );
    assert_eq!(
        cli::exit_code(&report(
            "damaged",
            Some(FailureCode::RepairPackageRequired),
            Operation::Inspect
        )),
        EXIT_COMPLETE,
        "inspect succeeded; the guidance is in the report"
    );
    // A mutating operation that ends damaged/foreign/recovery-required is not
    // complete.
    for state in ["damaged", "foreign", "recovery-required"] {
        assert_eq!(
            cli::exit_code(&report(state, None, Operation::Repair)),
            EXIT_FAILURE,
            "{state}"
        );
    }

    // End to end: an uninstall that cannot establish quiescence reports a
    // pending cleanup, and the process-level exit is 1.
    let temp = Temp::new("pending");
    let paths = windows_paths(&temp);
    let active = FakePlatform::with_faults(Faults {
        host_state: HostState::Active,
        ..Faults::default()
    });
    let (exit, stdout, _) = run_cli(&args(&["uninstall"]), &active, &paths);
    assert_eq!(exit, EXIT_FAILURE);
    let report = parse_report(&stdout);
    assert_eq!(report.operation, Operation::Uninstall);
    assert_eq!(report.failure, Some(FailureCode::CleanupPending));

    // And a quiescent uninstall of an empty layout is complete.
    let (exit, stdout, _) = run_cli(&args(&["uninstall"]), &quiescent(), &paths);
    assert_eq!(exit, EXIT_COMPLETE);
    let report = parse_report(&stdout);
    assert_eq!(report.state, STATE_UNINSTALLED);
    assert!(!report.warns(WarningCode::CleanupPending));
}

// ---------------------------------------------------------------------------
// Bounds and the launcher entry
// ---------------------------------------------------------------------------

#[test]
fn every_failure_is_a_bounded_strict_report_with_its_own_code() {
    use FailureCode::*;
    for code in [
        InvalidInput,
        OriginRejected,
        InvalidPackage,
        TrustDenied,
        TrustUnavailable,
        PolicyDenied,
        UnsupportedTarget,
        Busy,
        HostActive,
        InspectionDenied,
        ForeignState,
        UnsafePath,
        Io,
        RecoveryRequired,
        UnsupportedState,
        NoRollback,
        RepairPackageRequired,
        CleanupPending,
    ] {
        let failure = veles_bridge_installer::InstallFailure::new(code).with_prior_usable(true);
        let report = cli::failure_report(Operation::Install, &failure);
        assert_eq!(report.failure, Some(code), "the code is preserved");
        assert!(report.prior_usable);
        assert_eq!(cli::exit_code(&report), EXIT_FAILURE);
        let bytes = cli::render_report(&report).expect("renders");
        assert!(bytes.len() <= MAX_REPORT_BYTES);
        let again: InstallReport = serde_json::from_slice(&bytes).expect("strict round trip");
        assert_eq!(again, report);
    }
}

#[test]
fn the_launcher_mode_refuses_a_missing_or_malformed_frame() {
    // Standard input closed: no frame, nothing is spawned, nothing is printed.
    let output = binary()
        .arg(CLEANUP_LAUNCHER_ARG)
        .stdin(Stdio::null())
        .env_clear()
        .output()
        .expect("runs");
    assert_ne!(output.status.code(), Some(EXIT_COMPLETE));
    assert!(output.stdout.is_empty());

    // A malformed frame over a pipe is refused the same way.
    use std::io::Write;
    let mut child = binary()
        .arg(CLEANUP_LAUNCHER_ARG)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env_clear()
        .spawn()
        .expect("runs");
    child
        .stdin
        .take()
        .expect("stdin")
        .write_all(b"{\"not\":\"a launch frame\"}")
        .expect("write");
    let output = child.wait_with_output().expect("exits");
    assert_ne!(output.status.code(), Some(EXIT_COMPLETE));
    assert!(output.stdout.is_empty());
}

// ---------------------------------------------------------------------------
// Launcher frames (portable evidence; the Windows process plumbing is NOT RUN)
// ---------------------------------------------------------------------------

mod launcher_frames {
    use super::*;
    use veles_bridge_installer::Digest;
    use veles_bridge_installer::cleanup::{HelperRun, ParentIdentity, parse_request};
    use veles_bridge_installer::cleanup::{LaunchRequest, LaunchStatus, MAX_LAUNCH_FRAME_BYTES};

    const CAPABILITY: [u8; 32] = [0x42; 32];

    fn transaction() -> String {
        "ab".repeat(32)
    }

    fn run<'a>(
        helper: &'a Path,
        transaction: &'a str,
        root: &'a Path,
        timeout: Duration,
    ) -> HelperRun<'a> {
        HelperRun {
            helper_path: helper,
            helper_sha256: Digest::of(b"helper image"),
            publisher: "Publisher One",
            transaction,
            capability: &CAPABILITY,
            root,
            entries: Vec::new(),
            timeout,
        }
    }

    fn frame(temp: &Temp) -> Vec<u8> {
        let root = temp.path().join("Veles").join("NativeBridge");
        let helper = temp.path().join("private").join("veles-bridge-cleanup.exe");
        let transaction = transaction();
        LaunchRequest::for_run(
            &run(&helper, &transaction, &root, Duration::from_secs(5)),
            7,
        )
        .encode()
        .expect("encodes")
    }

    #[test]
    fn a_launch_frame_round_trips_and_the_launcher_becomes_the_recorded_parent() {
        let temp = Temp::new("launch-frame");
        let bytes = frame(&temp);
        let parsed = LaunchRequest::parse(&bytes).expect("a canonical frame parses");
        assert_eq!(parsed.completion_handle, 7);

        // The helper's request records the LAUNCHER (the process that will exit),
        // with the launcher's own handles - not the installer's.
        let launcher = ParentIdentity {
            pid: 4242,
            start: 99,
        };
        let helper_request = parsed
            .into_helper_request(launcher, 11, 12)
            .expect("the helper request validates");
        let request = parse_request(&helper_request).expect("strict request");
        assert_eq!(request.parent, launcher);
        assert_eq!(request.parent_handle, 11);
        assert_eq!(request.release_handle, 12);
        assert_eq!(request.publisher, "Publisher One");
        assert!(request.deadline_ms <= 10_000);
    }

    #[test]
    fn a_tampered_or_oversized_launch_frame_is_refused() {
        let temp = Temp::new("launch-tamper");
        let bytes = frame(&temp);
        let text = String::from_utf8(bytes.clone()).expect("JSON");

        let tampered = [
            // Wrong magic, schema, zero completion handle.
            text.replace("veles-cleanup-launch-v1", "veles-cleanup-launch-v0"),
            text.replace(
                "\"schema\":1,\"magic\":\"veles-cleanup-launch",
                "\"schema\":2,\"magic\":\"veles-cleanup-launch",
            ),
            text.replace("\"completion_handle\":7", "\"completion_handle\":0"),
            // A non-placeholder identity: the launcher alone decides who the
            // parent is.
            text.replace("\"parent_handle\":1", "\"parent_handle\":9"),
            text.replace("\"pid\":1,", "\"pid\":77,"),
            // An unknown field is a different protocol.
            text.replacen('{', "{\"command\":\"calc.exe\",", 1),
            // Not JSON at all.
            "not json".to_owned(),
            String::new(),
        ];
        for case in tampered {
            assert!(
                LaunchRequest::parse(case.as_bytes()).is_err(),
                "refused: {case}"
            );
        }
        assert!(LaunchRequest::parse(&vec![b' '; MAX_LAUNCH_FRAME_BYTES + 1]).is_err());

        // The helper must live outside the removable root, exactly as the helper
        // itself insists.
        let root = temp.path().join("Veles").join("NativeBridge");
        let inside = root.join("maintenance").join("veles-bridge-cleanup.exe");
        let transaction = transaction();
        let framed = LaunchRequest::for_run(
            &run(&inside, &transaction, &root, Duration::from_secs(5)),
            7,
        )
        .encode()
        .expect("encodes");
        assert!(LaunchRequest::parse(&framed).is_err());

        // A root that is not an installer-owned layout directory is refused.
        let other = temp.path().join("somewhere-else");
        let helper = temp.path().join("private").join("h.exe");
        let framed = LaunchRequest::for_run(
            &run(&helper, &transaction, &other, Duration::from_secs(5)),
            7,
        )
        .encode()
        .expect("encodes");
        assert!(LaunchRequest::parse(&framed).is_err());
    }

    #[test]
    fn the_deadline_is_clamped_to_the_cleanup_ceiling() {
        let temp = Temp::new("launch-deadline");
        let root = temp.path().join("Veles").join("NativeBridge");
        let helper = temp.path().join("private").join("h.exe");
        let transaction = transaction();
        let bytes = LaunchRequest::for_run(
            &run(&helper, &transaction, &root, Duration::from_secs(3600)),
            7,
        )
        .encode()
        .expect("encodes");
        let parsed = LaunchRequest::parse(&bytes).expect("clamped, not refused");
        assert_eq!(parsed.request.deadline_ms, 10_000);
    }

    #[test]
    fn the_status_frame_is_strict_and_bounded() {
        let status = LaunchStatus::new(1234, 5678);
        let bytes = status.encode().expect("encodes");
        assert_eq!(LaunchStatus::parse(&bytes).expect("parses"), status);
        for case in [
            &b""[..],
            &b"{}"[..],
            &br#"{"schema":1,"magic":"veles-cleanup-launched-v1","helper_pid":0,"helper_start":1}"#[..],
            &br#"{"schema":1,"magic":"x","helper_pid":4,"helper_start":1}"#[..],
            &br#"{"schema":1,"magic":"veles-cleanup-launched-v1","helper_pid":4,"helper_start":1,"extra":true}"#[..],
            &vec![b'x'; 4096][..],
        ] {
            assert!(LaunchStatus::parse(case).is_err());
        }
    }

    #[test]
    fn a_host_without_a_native_launcher_is_a_pending_cleanup_never_a_success() {
        // The portable host has no launcher plumbing: it must answer pending and
        // must not touch a staged image.
        let temp = Temp::new("launch-pending");
        let root = temp.path().join("Veles").join("NativeBridge");
        let helper = temp.path().join("private").join("helper.exe");
        fs::create_dir_all(helper.parent().expect("parent")).expect("dir");
        fs::write(&helper, b"helper image").expect("helper");
        let transaction = transaction();
        let (completion, verdict) = veles_bridge_installer::run_cleanup_helper_via_launcher(
            &run(&helper, &transaction, &root, Duration::from_secs(1)),
            Path::new("/nonexistent/launcher"),
        );
        if cfg!(windows) {
            // A missing launcher image is also pending on Windows.
            assert!(verdict.is_err());
        } else {
            assert!(completion.is_none());
            assert_eq!(
                verdict.expect_err("pending").code,
                FailureCode::CleanupPending
            );
        }
        assert!(
            helper.exists(),
            "an unproven cleanup never removes the image"
        );
    }
}
