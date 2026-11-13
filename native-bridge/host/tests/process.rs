//! OTP-24 Task 5b process-contract tests: the host entry and its binaries driven as real
//! child processes over real pipes, every child bounded by a parent watchdog that measures
//! the exit independently (`try_wait` deadline loop, `kill` on overrun). The production
//! `veles-native-host` binary is exercised through its actual launcher contract
//! (platform-gated refusal); every portable protocol scenario runs through the explicit
//! test-driver binary (`examples/host_test_driver.rs`, feature `test-driver`) — never through a production simulator
//! switch (none exists).
//!
//! Stdout assertions are strict: the collected bytes must parse as a whole number of
//! Chrome-framed JSON messages, so any diagnostic contamination breaks the framing
//! assertions. Stderr carries at most the bounded typed refusal diagnostic line.

use std::io::Read;
use std::path::PathBuf;
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::core::{
    EXIT_HOST_FAILURE, EXIT_INCOMPATIBLE, EXIT_OUTPUT_OVERFLOW, EXIT_REJECTED,
};
use veles_ble_host::host::DEVELOPMENT_ORIGIN;

/// Every bounded host exit is at most 5 s plus the protocol drain; 2x slack for the parent
/// watchdog (spawn, page cache) keeps runs deterministic while still exiting hard.
const WATCHDOG: Duration = Duration::from_secs(10);

/// Scheduling tolerance of the child's timing-contract assertions: the deadline
/// armed inside the child is exact, but the parent observes the write of the
/// terminal input, the child's pipe delivery (≤ one poll cycle) and process
/// teardown, so the advertised 5 s total is checked with half a second of slack.
/// The margin stays far below the old unbounded/summed waits these tests now pin.
const SCHED_TOL: Duration = Duration::from_millis(500);

/// Upper bound of the *real-child* observation of the 5 s window: the 5 s deadline plus the
/// production kill grace (<= 100 ms, pinned at compile time in `src/runtime.rs`) plus 1.4 s of
/// parent-side scheduling/teardown tolerance. The strict product deadline is proven
/// deterministically, without wall-clock thresholds on the parent, by the in-process tests in
/// `src/runtime.rs` (armed deadline == detection + 5 s bracket, call chronology, watchdog
/// expiry window / grace / disarm semantics, fail-closed `bounded_exit`, late-release
/// rejection). The parent only sees the child's exit through process scheduling and pipe
/// teardown, which measured up to 5.77 s on a saturated macOS host, so the old 5.5 s bound
/// tested the machine. The lower bound stays exact (the child can never exit early); exit
/// code and no-success semantics stay exact; this ceiling still fails a watchdog kill or
/// bounded exit that lags beyond grace + tolerance (e.g. a summed 5 s + 2 s drain = 7 s, or
/// an unbounded child hitting the 10 s parent watchdog).
const CHILD_WINDOW_CEILING: Duration = Duration::from_millis(6_500);

/// The 5 s window as observed from a parent-controlled terminal point: never early (exact),
/// never past the load-tolerant ceiling (see [`CHILD_WINDOW_CEILING`]).
#[track_caller]
fn assert_five_second_window(elapsed: Duration) {
    assert!(
        elapsed >= Duration::from_secs(5),
        "the child exited before the 5 s deadline: {elapsed:?}"
    );
    assert!(
        elapsed < CHILD_WINDOW_CEILING,
        "the bounded exit overran the load-tolerant ceiling {CHILD_WINDOW_CEILING:?}: {elapsed:?}"
    );
}

/// A wrong but well-shaped origin: same scheme and alphabet as the development
/// placeholder, listed in no allowlist.
const FOREIGN_ORIGIN: &str = "chrome-extension://ponmlkjihgfedcbponmlkjihgfedcbp/";

// ---- Chrome framing -----------------------------------------------------------------------------

fn framed(payload: &[u8]) -> Vec<u8> {
    let mut frame = (payload.len() as u32).to_le_bytes().to_vec();
    frame.extend_from_slice(payload);
    frame
}

fn frame_of(value: &Value) -> Vec<u8> {
    framed(&serde_json::to_vec(value).unwrap())
}

fn hello_frame(api_versions: &[u64]) -> Vec<u8> {
    frame_of(&json!({
        "envelope_version":1,"type":"hello","request_id":"hello","api_versions":api_versions
    }))
}

/// A schema-valid `connect` to a device handle the driver's backend never issued: one
/// correlated error frame per request, no backend reservation, no side effect.
fn unknown_handle_frame(id: &str) -> Vec<u8> {
    frame_of(&json!({
        "envelope_version":1,"api_version":1,"type":"connect","request_id":id,
        "device_handle":"0000000000000001"
    }))
}

fn shutdown_frame(id: &str) -> Vec<u8> {
    frame_of(&json!({
        "envelope_version":1,"api_version":1,"type":"shutdown","request_id":id
    }))
}

// ---- child harness ------------------------------------------------------------------------------

type Collected = Arc<Mutex<Vec<u8>>>;

struct ReaderOutput {
    handle: JoinHandle<()>,
    buffer: Collected,
}

struct ChildRun {
    child: Child,
    /// Raw, unread stdout end. Normal runs hand it to a reader thread; the broken-stdout
    /// scenario drops it instead, which breaks the child's next stdout delivery.
    kept_stdout: Option<ChildStdout>,
    stdout: Option<ReaderOutput>,
    stderr: ReaderOutput,
    started: Instant,
}

fn read_loop(pipe: impl Read + Send + 'static, into: Collected) -> ReaderOutput {
    let read_into = Arc::clone(&into);
    let handle = std::thread::spawn(move || {
        let mut pipe = pipe;
        let mut buffer = [0u8; 4096];
        loop {
            match pipe.read(&mut buffer) {
                Ok(0) => return,
                Ok(n) => read_into.lock().unwrap().extend_from_slice(&buffer[..n]),
                Err(_) => return,
            }
        }
    });
    ReaderOutput {
        handle,
        buffer: into,
    }
}

fn base_child(mut command: Command) -> ChildRun {
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().expect("spawn host child");
    let stdout = child.stdout.take().expect("child stdout piped");
    let stderr = child.stderr.take().expect("child stderr piped");
    ChildRun {
        stdout: None,
        stderr: read_loop(stderr, Arc::new(Mutex::new(Vec::new()))),
        kept_stdout: Some(stdout),
        child,
        started: Instant::now(),
    }
}

/// The test-only simulator driver, built by `cargo test` through the self dev-dependency that
/// enables the non-default `test-driver` feature (Cargo.toml). It is never part of a
/// production build; the exclusion is pinned by `driver_is_excluded_from_production_builds`.
fn driver_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_host-test-driver"))
}

fn production_path() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_veles-native-host"))
}

fn spawn_driver(modes: &[&str], args: &[&str]) -> ChildRun {
    spawn_driver_ex(modes, args, true)
}

/// `read_stdout = false` leaves the raw stdout end in `kept_stdout` for the
/// broken-stdout scenario, where the test drops it itself.
fn spawn_driver_ex(modes: &[&str], args: &[&str], read_stdout: bool) -> ChildRun {
    let mut command = Command::new(driver_path());
    for mode in modes {
        command.arg(mode);
    }
    // The driver strips its test-only flags before the strict production-style validation;
    // these launch arguments are exactly what a Chrome launch looks like.
    for arg in args {
        command.arg(arg);
    }
    let mut run = base_child(command);
    if read_stdout {
        let stdout = run.kept_stdout.take().expect("raw child stdout");
        run.stdout = Some(read_loop(stdout, Arc::new(Mutex::new(Vec::new()))));
    }
    run
}

fn spawn_production(args: &[&str]) -> ChildRun {
    let mut command = Command::new(production_path());
    for arg in args {
        command.arg(arg);
    }
    let mut run = base_child(command);
    let stdout = run.kept_stdout.take().expect("raw child stdout");
    run.stdout = Some(read_loop(stdout, Arc::new(Mutex::new(Vec::new()))));
    run
}

/// Writes every frame (tolerating a child that exits mid-write). `close` drops stdin for a
/// clean EOF at a frame boundary; without it the descriptor stays open for the child
/// (leaked in the parent: these tests never reuse it).
fn write_frames(run: &mut ChildRun, frames: &[Vec<u8>], close: bool) {
    if frames.is_empty() && !close {
        return;
    }
    let Some(stdin) = run.child.stdin.take() else {
        panic!("stdin already closed");
    };
    let mut bytes = Vec::new();
    for frame in frames {
        bytes.extend_from_slice(frame);
    }
    std::thread::spawn(move || {
        use std::io::Write;
        let mut stdin = stdin;
        let _ = stdin.write_all(&bytes);
        let _ = stdin.flush();
        if !close {
            std::mem::forget(stdin);
        }
    });
}

/// Parent watchdog: polls `try_wait` against the independent deadline, kills on overrun,
/// joins the readers after the exit and returns `(code, elapsed, stdout frames, stderr)`.
/// `elapsed` is measured from the child's spawn.
fn finish(run: ChildRun, limit: Duration) -> (i32, Duration, Vec<Value>, String) {
    let since = run.started;
    await_child(run, since, limit)
}

/// Like [`finish`], but `elapsed` is measured from the caller-provided
/// parent-controlled terminal point (the instant just before the terminal-
/// triggering input is written): the timing contract is a *terminal to exit*
/// bound, so process spawn and pre-terminal setup overhead stay out of it.
/// A wrong measurement point here would make the same assertions pass against
/// delayed terminals the contract does not cover.
fn finish_after_terminal(
    run: ChildRun,
    terminal: Instant,
    limit: Duration,
) -> (i32, Duration, Vec<Value>, String) {
    await_child(run, terminal, limit)
}

fn await_child(
    run: ChildRun,
    since: Instant,
    limit: Duration,
) -> (i32, Duration, Vec<Value>, String) {
    let ChildRun {
        mut child,
        kept_stdout,
        stdout,
        stderr,
        started,
    } = run;
    let deadline = started + limit;
    let code = loop {
        match child.try_wait().expect("poll child status") {
            Some(status) => {
                assert!(
                    status.code().is_some(),
                    "child was killed by a signal: {status:?}"
                );
                break status.code().expect("exit code");
            }
            None if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                panic!(
                    "child outlived the {limit:?} parent watchdog: the child never exited boundedly"
                );
            }
            None => std::thread::sleep(Duration::from_millis(5)),
        }
    };
    // Any held-but-unread stdout end stays unread for exactly the child's lifetime (the
    // unread-output scenarios depend on it filling).
    drop(kept_stdout);
    let elapsed = since.elapsed();
    let stdout = stdout
        .map(|reader| {
            reader.handle.join().expect("stdout reader");
            let buffer = reader.buffer.lock().unwrap().clone();
            parse_stdout(&buffer)
        })
        .unwrap_or_default();
    // Join before reading: the reader thread delivers the last bytes between the child's
    // exit and its own EOF return.
    stderr.handle.join().expect("stderr reader");
    let stderr_bytes = stderr.buffer.lock().unwrap().clone();
    let stderr = String::from_utf8_lossy(&stderr_bytes).into_owned();
    (code, elapsed, stdout, stderr)
}

/// Strict frame parsing: the whole stdout must be a whole number of framed JSON messages.
fn parse_stdout(bytes: &[u8]) -> Vec<Value> {
    let mut offset = 0usize;
    let mut parsed = Vec::new();
    while offset + 4 <= bytes.len() {
        let len = u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        offset += 4;
        assert!(
            offset + len <= bytes.len(),
            "truncated stdout frame at {offset}+{len}"
        );
        parsed.push(serde_json::from_slice::<Value>(&bytes[offset..offset + len]).unwrap());
        offset += len;
    }
    assert_eq!(
        offset,
        bytes.len(),
        "leftover stdout bytes: {}",
        bytes.len()
    );
    parsed
}

fn diagnostic_line(stage: &str) -> String {
    format!("veles-native-host: stage={stage}\n")
}

fn types(frames: &[Value]) -> Vec<&str> {
    frames
        .iter()
        .map(|frame| frame["type"].as_str().expect("envelope type"))
        .collect()
}

// ---- clean EOF vs truncated / malformed input ----------------------------------------------------

#[test]
fn clean_eof_after_hello_exits_ok_silently() {
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN]);
    write_frames(&mut run, &[hello_frame(&[1])], true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0, "clean EOF with the release observed");
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert_eq!(
        types(&stdout),
        vec!["ready"],
        "EOF is a silent exit: {stdout:?}"
    );
    assert_eq!(stderr, "", "no stderr contamination on a protocol exit");
}

#[test]
fn truncated_header_fails_closed_rejected() {
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN]);
    let hello = hello_frame(&[1]);
    // Deliver only 3 of the 4 header bytes, then EOF.
    let mut bytes = hello[0..4].to_vec();
    bytes.truncate(3);
    write_frames(&mut run, &[bytes], true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, EXIT_REJECTED);
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert!(
        stdout.is_empty(),
        "uncorrelatable terminal is silent: {stdout:?}"
    );
    assert_eq!(stderr, "");
}

#[test]
fn truncated_payload_fails_closed_rejected() {
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN]);
    let hello = hello_frame(&[1]);
    let mut bytes = hello[0..4].to_vec();
    bytes.extend_from_slice(&hello[4..hello.len() - 2]);
    write_frames(&mut run, &[bytes], true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, EXIT_REJECTED);
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert!(stdout.is_empty(), "{stdout:?}");
    assert_eq!(stderr, "");
}

#[test]
fn zero_length_frame_fails_closed_rejected() {
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN]);
    write_frames(&mut run, &[vec![0u8, 0, 0, 0]], true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, EXIT_REJECTED);
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert!(stdout.is_empty(), "{stdout:?}");
    assert_eq!(stderr, "");
}

#[test]
fn oversize_frame_fails_closed_rejected() {
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN]);
    // u32le 65537, one byte above the frame ceiling, with no payload ever sent.
    write_frames(&mut run, &[vec![0x01, 0x00, 0x01, 0x00]], true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, EXIT_REJECTED);
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert!(stdout.is_empty(), "{stdout:?}");
    assert_eq!(stderr, "");
}

// ---- negotiation ---------------------------------------------------------------------------------

#[test]
fn negotiation_without_overlap_exits_3() {
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN]);
    write_frames(&mut run, &[hello_frame(&[2, 3])], true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, EXIT_INCOMPATIBLE);
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert_eq!(types(&stdout), vec!["error"], "{stdout:?}");
    assert_eq!(stdout[0]["code"], "incompatible_api");
    assert_eq!(stdout[0]["request_id"], "hello");
    assert_eq!(stderr, "");
}

// ---- launch-argument refusals --------------------------------------------------------------------

#[test]
fn wrong_origin_refused_before_runtime() {
    let run = spawn_driver(&[], &[FOREIGN_ORIGIN]);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(
        code, EXIT_REJECTED,
        "an origin off the build-time allowlist is refused"
    );
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    assert!(stdout.is_empty(), "a refusal writes nothing to stdout");
    assert_eq!(stderr, diagnostic_line("launch_origin"));
}

#[test]
fn missing_origin_refused() {
    let run = spawn_driver(&[], &[]);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, EXIT_REJECTED);
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    assert!(stdout.is_empty());
    assert_eq!(stderr, diagnostic_line("launch_origin"));
}

#[test]
fn unknown_and_simulator_launch_flags_refused() {
    for extra in [
        vec![DEVELOPMENT_ORIGIN, "loose-argument"],
        // No simulator switch of any shape may ever reach a runtime in production; the
        // driver shares the production validator and refuses these exactly like the
        // production binary will.
        vec![DEVELOPMENT_ORIGIN, "--backend", "sim"],
        vec![DEVELOPMENT_ORIGIN, "--backend=sim"],
    ] {
        let run = spawn_driver(&[], &extra);
        let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
        assert_eq!(code, EXIT_REJECTED, "argument {extra:?}");
        assert!(elapsed < Duration::from_secs(2), "{extra:?}: {elapsed:?}");
        assert!(stdout.is_empty(), "{extra:?}");
        assert_eq!(stderr, diagnostic_line("launch_arguments"), "{extra:?}");
    }
}

#[test]
fn production_binary_refuses_simulator_and_test_flags() {
    // validate_launch precedes the platform gate on every platform, so the production
    // argument policy (no simulator, no test-driver flags) is assertable here.
    for extra in [
        vec![DEVELOPMENT_ORIGIN, "--backend", "sim"],
        vec![DEVELOPMENT_ORIGIN, "--backend=sim"],
        vec![DEVELOPMENT_ORIGIN, "--test-stuck-cleanup"],
        vec![DEVELOPMENT_ORIGIN, "--test-block-shutdown"],
        vec![DEVELOPMENT_ORIGIN, "--test-freeze-stdout"],
        vec![DEVELOPMENT_ORIGIN, "--test-delayed-release=100"],
    ] {
        let run = spawn_production(&extra);
        let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
        assert_eq!(
            code, EXIT_REJECTED,
            "argument {extra:?} must never reach a runtime in production"
        );
        assert!(elapsed < Duration::from_secs(2), "{extra:?}: {elapsed:?}");
        assert!(stdout.is_empty(), "{extra:?}");
        assert_eq!(stderr, diagnostic_line("launch_arguments"), "{extra:?}");
    }
}

/// The simulator driver must never ship: a default (production-style) build produces the
/// production host only, and cargo refuses to build the driver without the non-default
/// `test-driver` feature. Both builds run in an isolated target directory so they cannot
/// contend with the enclosing `cargo test` build lock or disturb its artifacts.
#[test]
fn driver_is_excluded_from_production_builds() {
    let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".to_string());
    let manifest = concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml");
    let target_dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("production-exclusion");
    let build = |extra: &[&str]| {
        Command::new(&cargo)
            .args([
                "build",
                "--locked",
                "--offline",
                "--manifest-path",
                manifest,
            ])
            .arg("--target-dir")
            .arg(&target_dir)
            .args(extra)
            .output()
            .expect("run cargo build")
    };

    let default = build(&[]);
    assert!(
        default.status.success(),
        "{}",
        String::from_utf8_lossy(&default.stderr)
    );
    let suffix = std::env::consts::EXE_SUFFIX;
    assert!(
        target_dir
            .join("debug")
            .join(format!("veles-native-host{suffix}"))
            .exists(),
        "the default build must produce the production host"
    );
    assert!(
        !target_dir
            .join("debug")
            .join(format!("host-test-driver{suffix}"))
            .exists(),
        "the default build must not produce the simulator driver"
    );

    let explicit = build(&["--bin", "host-test-driver"]);
    assert!(
        !explicit.status.success(),
        "building the driver without the test-driver feature must be refused"
    );
    let stderr = String::from_utf8_lossy(&explicit.stderr);
    assert!(stderr.contains("test-driver"), "{stderr}");
}

#[test]
fn parent_window_argument_accepted_and_malformed_refused() {
    // The documented platform launcher argument with an immediate EOF: a plain clean
    // exit 0 with no traffic at all.
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN, "--parent-window=123"]);
    write_frames(&mut run, &[], true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0, "{stdout:?}");
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert!(stdout.is_empty(), "{stdout:?}");
    assert_eq!(stderr, "");
    for extra in [
        vec![
            DEVELOPMENT_ORIGIN,
            "--parent-window=123",
            "--parent-window=124",
        ],
        vec![DEVELOPMENT_ORIGIN, "--parent-window=not-a-number"],
        vec![DEVELOPMENT_ORIGIN, "--parent-window="],
        vec![DEVELOPMENT_ORIGIN, "--parent-window=123", "x"],
    ] {
        let run = spawn_driver(&[], &extra);
        let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
        assert_eq!(code, EXIT_REJECTED, "{extra:?}");
        assert!(elapsed < Duration::from_secs(2), "{extra:?}: {elapsed:?}");
        assert!(stdout.is_empty());
        assert_eq!(stderr, diagnostic_line("launch_arguments"), "{extra:?}");
    }
}

// ---- platform gate of the production binary -------------------------------------------------------

/// Linux (and every other non-Windows/macOS target) refuses before any runtime starts and
/// never reports itself as a platform it is not. Asserted here on Linux itself; the
/// Windows/macOS counterpart runs on those runners, never cross-compiled from here.
#[cfg(not(any(windows, target_os = "macos")))]
#[test]
fn production_binary_refuses_unsupported_platform() {
    // The refusal never reads stdin; the piped descriptor going out of scope closes it.
    let run = spawn_production(&[DEVELOPMENT_ORIGIN]);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(
        code, EXIT_REJECTED,
        "the unsupported platform fails closed without a protocol run"
    );
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    assert!(stdout.is_empty());
    assert_eq!(stderr, diagnostic_line("platform_unsupported"));
}

/// Windows and macOS run the production adapter. `MacosBackend::new` (and the WinRT
/// equivalent) perform no BLE work: CoreBluetooth's central manager is created lazily, only
/// when a scan or connect worker first needs it, so hello + EOF never touches Bluetooth (no
/// authorization prompt, no adapter assumption). The documented clean EOF exit answers
/// `ready` for the true platform, is silent on stderr and is never the watchdog's exit 1.
/// (Compiled for those targets; it only runs on a matching host.)
#[cfg(windows)]
const EXPECTED_PLATFORM: &str = "windows";
#[cfg(target_os = "macos")]
const EXPECTED_PLATFORM: &str = "macos";

#[cfg(any(windows, target_os = "macos"))]
#[test]
fn production_binary_runs_the_platform_runtime_and_exits_cleanly_on_eof() {
    let mut run = spawn_production(&[DEVELOPMENT_ORIGIN]);
    write_frames(&mut run, &[hello_frame(&[1])], true);
    // Exit code 0 (not the watchdog's 1) proves the release was observed inside the 5 s
    // deadline; the parent watchdog alone bounds the run, no wall-clock threshold.
    let (code, _elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0, "clean EOF with the release observed");
    assert_eq!(types(&stdout), vec!["ready"], "{stdout:?}");
    assert_eq!(stdout[0]["platform"], EXPECTED_PLATFORM);
    assert_eq!(stderr, "", "no stderr contamination on a protocol exit");
}

/// Startup without any traffic: immediate EOF is the silent clean exit on the real adapter.
#[cfg(any(windows, target_os = "macos"))]
#[test]
fn production_binary_exits_cleanly_on_immediate_eof_without_traffic() {
    let mut run = spawn_production(&[DEVELOPMENT_ORIGIN]);
    write_frames(&mut run, &[], true);
    let (code, _elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0, "{stdout:?}");
    assert!(stdout.is_empty(), "no hello, no output: {stdout:?}");
    assert_eq!(stderr, "");
}

/// Held-open stdin (what Chrome does between messages): the host idles instead of exiting
/// or failing, and a later explicit shutdown ends it cleanly. Proves the macOS launcher
/// neither exits at startup nor needs an adapter to answer `hello`/`shutdown`.
#[cfg(any(windows, target_os = "macos"))]
#[test]
fn production_binary_idles_on_open_stdin_then_shuts_down() {
    let mut run = spawn_production(&[DEVELOPMENT_ORIGIN]);
    write_frames(&mut run, &[hello_frame(&[1]), shutdown_frame("bye")], false);
    let (code, _elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0, "{stdout:?}");
    assert_eq!(types(&stdout), vec!["ready", "shutdown_complete"]);
    assert_eq!(stderr, "");
}

// ---- bounded exits ---------------------------------------------------------------------------------

#[test]
fn stuck_cleanup_exits_with_failure_within_five_seconds() {
    let mut run = spawn_driver(&["--test-stuck-cleanup"], &[DEVELOPMENT_ORIGIN]);
    // Parent-controlled terminal point: the five-second contract is measured
    // from the terminal input's write, not from the (already elapsed) spawn.
    let terminal = Instant::now();
    write_frames(&mut run, &[], true);
    let (code, elapsed, stdout, stderr) = finish_after_terminal(run, terminal, WATCHDOG);
    assert_eq!(
        code, EXIT_HOST_FAILURE,
        "unobserved cleanup fails closed (this is a host failure, not a protocol exit)"
    );
    assert_five_second_window(elapsed);
    assert!(
        !stdout
            .iter()
            .any(|frame| frame["type"] == "shutdown_complete"),
        "no shutdown_complete without an observed release: {stdout:?}"
    );
    assert!(stdout.is_empty(), "silent bounded failure: {stdout:?}");
    assert_eq!(stderr, "");
}

#[test]
fn blocked_shutdown_submission_is_killed_by_the_shared_deadline() {
    // A regressed adapter blocks forever inside `Backend::shutdown`, called
    // synchronously by the terminal handling: the cooperative runtime can never
    // return, so only the independent process-exit watchdog — armed at terminal
    // detection, before `Core::handle` runs the release submissions — can bound
    // the process. The enforced exit is the failure code (no fabricated 0), and
    // the schedule is the same 5 s the cooperative deadline uses.
    let mut run = spawn_driver(&["--test-block-shutdown"], &[DEVELOPMENT_ORIGIN]);
    // A zero-length declared payload is a framing terminal that needs no
    // readiness round trip: deterministic, parent-controlled, and it drives the
    // same release path (all teardowns plus `shutdown`) as every other terminal.
    let terminal = Instant::now();
    write_frames(&mut run, &[vec![0u8, 0, 0, 0]], true);
    let (code, elapsed, stdout, stderr) = finish_after_terminal(run, terminal, WATCHDOG);
    assert_eq!(
        code, EXIT_HOST_FAILURE,
        "the watchdog enforces the deadline with the failure code"
    );
    assert_five_second_window(elapsed);
    assert!(
        stdout.is_empty(),
        "a blocked host wrote nothing to stdout: {stdout:?}"
    );
    assert_eq!(stderr, "", "silent enforced failure");
}

#[test]
fn accepted_shutdown_with_idle_stdin_and_stuck_release_fails_within_five_seconds() {
    // stdin stays OPEN and idle and the backend never reports the release: the deadline is
    // armed when the valid shutdown request is accepted, so the child fails closed (exit 1)
    // within 5 s of that request, with no `shutdown_complete`.
    let mut run = spawn_driver(&["--test-stuck-cleanup"], &[DEVELOPMENT_ORIGIN]);
    let terminal = Instant::now();
    write_frames(&mut run, &[hello_frame(&[1]), shutdown_frame("bye")], false);
    let (code, elapsed, stdout, stderr) = finish_after_terminal(run, terminal, WATCHDOG);
    assert_eq!(code, EXIT_HOST_FAILURE, "{stdout:?}");
    assert_five_second_window(elapsed);
    assert_eq!(
        types(&stdout),
        vec!["ready"],
        "no shutdown_complete: {stdout:?}"
    );
    assert_eq!(stderr, "");
}

#[test]
fn accepted_shutdown_with_blocking_backend_shutdown_is_killed_within_five_seconds() {
    // stdin open and `Backend::shutdown` blocks forever inside the request's own teardown:
    // the watchdog armed at acceptance, before that submission, ends the process.
    let mut run = spawn_driver(&["--test-block-shutdown"], &[DEVELOPMENT_ORIGIN]);
    let terminal = Instant::now();
    write_frames(&mut run, &[hello_frame(&[1]), shutdown_frame("bye")], false);
    let (code, elapsed, stdout, stderr) = finish_after_terminal(run, terminal, WATCHDOG);
    assert_eq!(code, EXIT_HOST_FAILURE, "{stdout:?}");
    assert_five_second_window(elapsed);
    assert!(
        stdout
            .iter()
            .all(|frame| frame["type"] != "shutdown_complete"),
        "{stdout:?}"
    );
    assert_eq!(stderr, "");
}

#[test]
fn blocking_release_returning_across_the_deadline_fails_closed_without_success() {
    // Real child, idle stdin: `Backend::shutdown` blocks across the 5 s deadline and then
    // synchronously reports its release (5.05 s, inside the watchdog's 100 ms grace). The
    // late success must be neither emitted nor returned: exit 1, no `shutdown_complete`.
    let mut run = spawn_driver(&["--test-block-then-release=5050"], &[DEVELOPMENT_ORIGIN]);
    let terminal = Instant::now();
    write_frames(&mut run, &[hello_frame(&[1]), shutdown_frame("bye")], false);
    let (code, elapsed, stdout, stderr) = finish_after_terminal(run, terminal, WATCHDOG);
    assert_eq!(code, EXIT_HOST_FAILURE, "{stdout:?}");
    assert_five_second_window(elapsed);
    assert_eq!(types(&stdout), vec!["ready"], "{stdout:?}");
    assert_eq!(stderr, "");
}

#[test]
fn blocking_release_returning_before_the_deadline_succeeds() {
    let mut run = spawn_driver(&["--test-block-then-release=4500"], &[DEVELOPMENT_ORIGIN]);
    write_frames(&mut run, &[hello_frame(&[1]), shutdown_frame("bye")], false);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0, "{stdout:?}");
    assert!(elapsed < CHILD_WINDOW_CEILING, "{elapsed:?}");
    assert_eq!(types(&stdout), vec!["ready", "shutdown_complete"]);
    assert_eq!(stderr, "");
}

#[test]
fn broken_stdout_terminates_clean_bounded() {
    // No stdout reader: the parent holds the raw read end, writes hello plus one follow-up
    // request, then drops the read end so the child's next stdout delivery fails and the
    // writer notice terminates the process clean.
    let mut run = spawn_driver_ex(&[], &[DEVELOPMENT_ORIGIN], false);
    write_frames(
        &mut run,
        &[hello_frame(&[1]), unknown_handle_frame("probe")],
        false,
    );
    drop(run.kept_stdout.take());
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0, "the writer notice terminates the process clean");
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert!(stdout.is_empty(), "nothing is readable after the break");
    assert_eq!(stderr, "");
}

#[test]
fn frozen_stdout_shutdown_preserved_within_bounds() {
    // The writer parks inside its first write; cleanup is observed and the protocol exit
    // stands, via the ≤2 s drain inside the shared ≤5 s total.
    let mut run = spawn_driver(&["--test-freeze-stdout"], &[DEVELOPMENT_ORIGIN]);
    let terminal = Instant::now();
    write_frames(&mut run, &[hello_frame(&[1]), shutdown_frame("bye")], true);
    let (code, elapsed, stdout, stderr) = finish_after_terminal(run, terminal, WATCHDOG);
    assert_eq!(code, 0, "the observed release preserves the protocol exit");
    // The release is observed immediately (synchronous); the writer is frozen, so the
    // exit is the drain bound (≤2 s) plus scheduling tolerance — and never the 5 s total.
    assert!(
        elapsed < Duration::from_secs(2) + SCHED_TOL,
        "the exit is the ≤2 s drain bound inside the shared deadline: {elapsed:?}"
    );
    assert!(
        stdout.is_empty(),
        "the frozen writer never delivered: {stdout:?}"
    );
    assert_eq!(stderr, "");
}

#[test]
fn frozen_stdout_duplicate_request_id_rejected_bounded() {
    // The writer parks inside the `ready` write, so the first `connect`'s error frame
    // stays queued; the second same-id `connect` is then a live duplicate
    // (OTP2-BRD-003) and the bounded exit never waits on the blocked writer.
    let mut run = spawn_driver(&["--test-freeze-stdout"], &[DEVELOPMENT_ORIGIN]);
    let terminal = Instant::now();
    write_frames(
        &mut run,
        &[
            hello_frame(&[1]),
            unknown_handle_frame("dup"),
            unknown_handle_frame("dup"),
        ],
        true,
    );
    let (code, elapsed, stdout, stderr) = finish_after_terminal(run, terminal, WATCHDOG);
    assert_eq!(code, EXIT_REJECTED, "a live duplicate id fails the process");
    assert!(
        elapsed < Duration::from_secs(2) + SCHED_TOL,
        "the frozen-writer exit is the ≤2 s drain bound: {elapsed:?}"
    );
    assert!(stdout.is_empty(), "frozen writer: {stdout:?}");
    assert_eq!(stderr, "");
}

#[test]
fn output_overflow_exits_4_with_unread_stdout() {
    // No stdout reader and the raw read end held unread: the writer fills the 64 KiB pipe,
    // parks, and the output queue overflows its 128-message ceiling → exit 4, bounded,
    // silent stderr.
    let mut run = spawn_driver_ex(&[], &[DEVELOPMENT_ORIGIN], false);
    let mut frames = vec![hello_frame(&[1])];
    for n in 0..700u32 {
        frames.push(unknown_handle_frame(&format!("flood-{n:05}")));
    }
    write_frames(&mut run, &frames, true);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, EXIT_OUTPUT_OVERFLOW, "the queue ceiling fails closed");
    assert!(elapsed < Duration::from_secs(8), "{elapsed:?}");
    assert!(
        stdout.is_empty(),
        "an unread pipe yields no collected frames"
    );
    assert_eq!(stderr, "");
}

// ---- explicit shutdown, last output and closure ------------------------------------------------------

#[test]
fn explicit_shutdown_emits_last_output_and_exits() {
    let mut run = spawn_driver(&[], &[DEVELOPMENT_ORIGIN]);
    // No EOF: the handshake alone must terminate the process.
    write_frames(&mut run, &[hello_frame(&[1]), shutdown_frame("bye")], false);
    let (code, elapsed, stdout, stderr) = finish(run, WATCHDOG);
    assert_eq!(code, 0);
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    assert_eq!(
        types(&stdout),
        vec!["ready", "shutdown_complete"],
        "last output and closure: {stdout:?}"
    );
    assert_eq!(stdout[1]["request_id"], "bye");
    assert_eq!(stderr, "");
}
