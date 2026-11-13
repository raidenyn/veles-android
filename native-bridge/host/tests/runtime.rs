//! Task 5 (narrow runtime slice) contract tests: the concurrent headless runtime around the
//! deterministic core, driven through deterministic in-memory stdio mocks and a recording
//! backend. Real child-process acceptance stays with the full Task 5 process tests; every
//! test here bounds the whole `run` call with a watchdog.

use std::io::{self, Cursor, Read, Write};
use std::sync::mpsc;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::backend::{Backend, BackendEvent, NormalizedTarget, Token};
use veles_ble_host::core::EXIT_HOST_FAILURE;
use veles_ble_host::ingress::BackendEvents;
use veles_ble_host::runtime::{Config, HangWatchdog, Io, run};

const SCAN_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";
/// A run may take at most 5 s (terminal deadline); every test watches with 2x slack.
const WATCHDOG: Duration = Duration::from_secs(10);
/// Scheduling tolerance for the inclusive 5 s deadline assertions: process/thread
/// scheduling and the poll machinery land the run within half a second of the deadline.
const SCHED_TOL: Duration = Duration::from_millis(500);

// ---- injectable watchdog seam (never process::exit in-process) -----------------------------------

/// Records every arm/disarm (with the armed deadline / disarm instant) so tests
/// can pin the watchdog consultation and the shared deadline's clock, without
/// ever exiting the process.
struct RecordingWatchdog {
    events: Mutex<Vec<(&'static str, Instant)>>,
}

impl RecordingWatchdog {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            events: Mutex::new(Vec::new()),
        })
    }
}

impl HangWatchdog for RecordingWatchdog {
    fn arm(&self, deadline: Instant) {
        self.events.lock().unwrap().push(("arm", deadline));
    }

    fn disarm(&self) {
        self.events.lock().unwrap().push(("disarm", Instant::now()));
    }
}

// ---- deterministic stdio mocks -----------------------------------------------------------------

/// Serves framed bytes, then either reports a real EOF or parks until released
/// (exercising exit without waiting on a blocked reader).
struct ScriptRead {
    data: Mutex<Cursor<Vec<u8>>>,
    gate: Option<Arc<Gate>>,
}

struct Gate {
    released: Mutex<bool>,
    wake: Condvar,
}

impl Gate {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            released: Mutex::new(false),
            wake: Condvar::new(),
        })
    }

    fn open(&self) {
        *self.released.lock().unwrap() = true;
        self.wake.notify_all();
    }
}

impl Read for ScriptRead {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let mut cursor = self.data.lock().unwrap();
        if cursor.position() < cursor.get_ref().len() as u64 {
            return cursor.read(buf);
        }
        match &self.gate {
            None => Ok(0),
            Some(gate) => {
                let mut released = gate.released.lock().unwrap();
                while !*released {
                    released = gate.wake.wait(released).unwrap();
                }
                Ok(0)
            }
        }
    }
}

/// Blocks in the first write until released: a frozen Chrome pipe.
struct FrozenWrite {
    gate: Arc<Gate>,
}

impl Write for FrozenWrite {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut released = self.gate.released.lock().unwrap();
        while !*released {
            released = self.gate.wake.wait(released).unwrap();
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Collects everything written so the test can parse the emitted frames.
type Collected = Arc<Mutex<Vec<u8>>>;

struct CollectWrite {
    collected: Collected,
}

impl Write for CollectWrite {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.collected.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Fails the first write: a closed Chrome pipe.
struct FailWrite;

impl Write for FailWrite {
    fn write(&mut self, _buf: &[u8]) -> io::Result<usize> {
        Err(io::Error::from(io::ErrorKind::BrokenPipe))
    }

    fn flush(&mut self) -> io::Result<()> {
        Err(io::Error::from(io::ErrorKind::BrokenPipe))
    }
}

// ---- deterministic in-memory backend ------------------------------------------------------------

/// How the backend reports the process-wide release after `shutdown`.
#[derive(Clone, Copy)]
enum Release {
    /// Synchronously from inside the `shutdown()` submission.
    Sync,
    /// On a separate thread, after a fixed delay (an async OS release).
    Async(Duration),
    /// Synchronously from inside `shutdown()`, but only after blocking the calling (runtime)
    /// thread for this long: a blocking adapter call that returns its release late.
    SyncAfter(Duration),
    /// Never: the OS release is stalled beyond every deadline.
    Never,
}

struct StageBackend {
    events: BackendEvents,
    calls: Arc<Mutex<Vec<&'static str>>>,
    release: Release,
    /// Emits one stray, pre-release `ShutdownComplete` from inside `start_scan`
    /// (a bogus adapter terminal reported before any release was submitted).
    /// The review's stale-cleanup scenario at real-runtime seam level.
    stray_from_start_scan: bool,
}

impl StageBackend {
    fn new(
        events: BackendEvents,
        calls: Arc<Mutex<Vec<&'static str>>>,
        release: Release,
        stray_from_start_scan: bool,
    ) -> Self {
        Self {
            events,
            calls,
            release,
            stray_from_start_scan,
        }
    }
}

impl Backend for StageBackend {
    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        (!hint.is_empty()).then(|| NormalizedTarget::new(hint.to_string()))
    }

    fn start_scan(&mut self, scan: Token, _service_uuid: &str) {
        self.calls.lock().unwrap().push("start_scan");
        self.events.emit(BackendEvent::ScanStarted { scan });
        if self.stray_from_start_scan {
            self.events.emit(BackendEvent::ShutdownComplete);
        }
    }

    fn stop_scan(&mut self, _scan: Token) {
        self.calls.lock().unwrap().push("stop_scan");
    }

    fn connect(&mut self, _conn: Token, _target: &veles_ble_host::backend::Target) {
        self.calls.lock().unwrap().push("connect");
    }

    fn write_fragments(
        &mut self,
        _conn: Token,
        _send: Token,
        _frames: Vec<veles_ble_host::buffer::Payload>,
    ) {
        self.calls.lock().unwrap().push("write_fragments");
    }

    fn disconnect(&mut self, _conn: Token) {
        self.calls.lock().unwrap().push("disconnect");
    }

    fn shutdown(&mut self) {
        self.calls.lock().unwrap().push("shutdown");
        match self.release {
            Release::Sync => {
                self.events.emit(BackendEvent::ShutdownComplete);
            }
            Release::Async(delay) => {
                let events = self.events.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(delay);
                    events.emit(BackendEvent::ShutdownComplete);
                });
            }
            Release::SyncAfter(delay) => {
                std::thread::sleep(delay);
                self.events.emit(BackendEvent::ShutdownComplete);
            }
            Release::Never => {}
        }
    }
}

// ---- helpers ------------------------------------------------------------------------------------

fn framed(payload: &[u8]) -> Vec<u8> {
    let mut frame = (payload.len() as u32).to_le_bytes().to_vec();
    frame.extend_from_slice(payload);
    frame
}

fn frame_of(value: &Value) -> Vec<u8> {
    framed(&serde_json::to_vec(value).unwrap())
}

fn hello_frame() -> Vec<u8> {
    frame_of(&json!({
        "envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]
    }))
}

fn start_scan_frame(id: &str) -> Vec<u8> {
    frame_of(&json!({
        "envelope_version":1,"api_version":1,"type":"start_scan",
        "request_id":id,"service_uuid":SCAN_UUID
    }))
}

fn shutdown_frame(id: &str) -> Vec<u8> {
    frame_of(&json!({
        "envelope_version":1,"api_version":1,"type":"shutdown","request_id":id
    }))
}

fn script_io(frames: &[Vec<u8>], park_reader: bool) -> (Box<dyn Read + Send>, Arc<Gate>) {
    let mut bytes = Vec::new();
    for frame in frames {
        bytes.extend_from_slice(frame);
    }
    let gate = Gate::new();
    let gate_for_read = gate.clone();
    let read = ScriptRead {
        data: Mutex::new(Cursor::new(bytes)),
        gate: park_reader.then(|| Arc::clone(&gate_for_read)),
    };
    (Box::new(read), gate)
}

fn collect_stdout() -> (Box<dyn Write + Send>, Collected) {
    let collected: Collected = Arc::new(Mutex::new(Vec::new()));
    let write = CollectWrite {
        collected: Arc::clone(&collected),
    };
    (Box::new(write), collected)
}

fn stdout_frames(collected: &Collected) -> Vec<Value> {
    let bytes = collected.lock().unwrap().clone();
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

fn run_bounded(release: Release, io: Io) -> (Arc<Mutex<Vec<&'static str>>>, i32, Duration) {
    run_bounded_stray(release, false, io)
}

fn run_bounded_stray(
    release: Release,
    stray_from_start_scan: bool,
    io: Io,
) -> (Arc<Mutex<Vec<&'static str>>>, i32, Duration) {
    run_bounded_watch(release, stray_from_start_scan, io, RecordingWatchdog::new())
}

/// Runs bounded with an explicitly injected watchdog (the seam under test); the
/// recording watchdog must be consulted instead of any real process exit.
fn run_bounded_watch(
    release: Release,
    stray_from_start_scan: bool,
    io: Io,
    watchdog: Arc<RecordingWatchdog>,
) -> (Arc<Mutex<Vec<&'static str>>>, i32, Duration) {
    let calls = Arc::new(Mutex::new(Vec::new()));
    let backend_calls = Arc::clone(&calls);
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let started = Instant::now();
        let code = run(
            |events| StageBackend::new(events, backend_calls, release, stray_from_start_scan),
            io,
            Config {
                bridge_version: "runtime-test/0".into(),
                platform: "test",
                watchdog,
            },
        );
        let _ = tx.send((code, started.elapsed()));
    });
    let (code, elapsed) = rx
        .recv_timeout(WATCHDOG)
        .expect("run exceeded the bounded watchdog");
    (calls, code, elapsed)
}

// ---- clean EOF ----------------------------------------------------------------------------------

#[test]
fn clean_eof_exits_ok_after_observed_release() {
    let (stdin, reader_gate) = script_io(&[hello_frame()], false);
    let (stdout, collected) = collect_stdout();
    let (calls, code, elapsed) = run_bounded(Release::Sync, Io { stdin, stdout });
    reader_gate.open();
    assert_eq!(code, 0, "clean EOF with observed release");
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    let calls = calls.lock().unwrap().clone();
    assert!(
        calls.contains(&"shutdown"),
        "the EOF release reached the backend: {calls:?}"
    );
    let frames = stdout_frames(&collected);
    assert_eq!(frames.len(), 1, "exactly the ready frame: {frames:?}");
    assert_eq!(frames[0]["type"], "ready");
    // EOF is a silent fail-closed exit: no shutdown_complete follows.
    assert!(
        !frames
            .iter()
            .any(|frame| frame["type"] == "shutdown_complete"),
        "{frames:?}"
    );
}

// ---- async release ------------------------------------------------------------------------------

#[test]
fn async_release_is_observed_within_the_deadline() {
    let (stdin, reader_gate) = script_io(&[hello_frame()], false);
    let (stdout, collected) = collect_stdout();
    let (_, code, elapsed) = run_bounded(
        Release::Async(Duration::from_millis(150)),
        Io { stdin, stdout },
    );
    reader_gate.open();
    assert_eq!(code, 0, "the runtime observed the real async release");
    assert!(
        elapsed >= Duration::from_millis(100),
        "the runtime waited for the delayed release: {elapsed:?}"
    );
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    let frames = stdout_frames(&collected);
    assert_eq!(frames.len(), 1, "{frames:?}");
    assert_eq!(frames[0]["type"], "ready");
}

// ---- cleanup stall ------------------------------------------------------------------------------

#[test]
fn stalled_cleanup_returns_host_failure_within_five_seconds() {
    let (stdin, reader_gate) = script_io(&[], false);
    let (stdout, collected) = collect_stdout();
    let (calls, code, elapsed) = run_bounded(Release::Never, Io { stdin, stdout });
    reader_gate.open();
    assert_eq!(code, EXIT_HOST_FAILURE, "unobserved cleanup fails closed");
    assert!(
        elapsed >= Duration::from_millis(4_500) && elapsed < Duration::from_secs(5) + SCHED_TOL,
        "the inclusive 5 s deadline bounded the stall: {elapsed:?}"
    );
    let calls = calls.lock().unwrap().clone();
    assert_eq!(calls, vec!["shutdown"], "{calls:?}");
    assert!(
        collected.lock().unwrap().is_empty(),
        "no stdout diagnostic contamination"
    );
}

// ---- frozen output ------------------------------------------------------------------------------

#[test]
fn frozen_output_preserves_protocol_exit_with_bounded_drain() {
    let (stdin, reader_gate) = script_io(&[hello_frame(), shutdown_frame("bye")], false);
    let freeze_gate = Gate::new();
    let stdout_box: Box<dyn Write + Send> = Box::new(FrozenWrite {
        gate: Arc::clone(&freeze_gate),
    });
    let (calls, code, elapsed) = run_bounded(
        Release::Sync,
        Io {
            stdin,
            stdout: stdout_box,
        },
    );
    freeze_gate.open();
    reader_gate.open();
    assert_eq!(
        code, 0,
        "cleanup was observed: the protocol exit is preserved"
    );
    assert!(
        elapsed < Duration::from_millis(4_500),
        "the writer was never waited on beyond the drain bound: {elapsed:?}"
    );
    let calls = calls.lock().unwrap().clone();
    assert!(
        calls.contains(&"shutdown"),
        "the handshake release reached the backend: {calls:?}"
    );
}

// ---- full wiring with a blocked reader ----------------------------------------------------------

#[test]
fn handshake_roundtrip_returns_while_stdin_reader_stays_blocked() {
    let (stdin, reader_gate) = script_io(
        &[
            hello_frame(),
            start_scan_frame("scan1"),
            shutdown_frame("bye"),
        ],
        true,
    );
    let (stdout, collected) = collect_stdout();
    let (calls, code, elapsed) = run_bounded(Release::Sync, Io { stdin, stdout });
    reader_gate.open();
    assert_eq!(code, 0, "the handshake completed without stdin EOF");
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    assert!(
        !calls.lock().unwrap().is_empty(),
        "the backend served the requests"
    );
    let frames = stdout_frames(&collected);
    let types: Vec<&str> = frames
        .iter()
        .map(|frame| frame["type"].as_str().unwrap())
        .collect();
    // Deterministic: the ScanStarted staged during the start_scan submission is pumped in the
    // same cycle (one frame and one event per cycle), before the shutdown frame is consumed,
    // so the handshake reports the started scan's lifecycle stop — no scan error.
    assert_eq!(
        types,
        vec!["ready", "scan_state", "scan_state", "shutdown_complete"],
        "{frames:?}"
    );
    assert_eq!(frames[1]["status"], "started");
    assert_eq!(frames[1]["request_id"], "scan1");
    assert_eq!(frames[2]["status"], "stopped");
    assert_eq!(frames[2]["scan_handle"], frames[1]["scan_handle"]);
    assert_eq!(frames[3]["request_id"], "bye");
}

// ---- writer failure -----------------------------------------------------------------------------

#[test]
fn writer_failure_terminates_bounded() {
    let (stdin, reader_gate) = script_io(&[hello_frame()], false);
    let stdout_box: Box<dyn Write + Send> = Box::new(FailWrite);
    let (calls, code, elapsed) = run_bounded(
        Release::Sync,
        Io {
            stdin,
            stdout: stdout_box,
        },
    );
    reader_gate.open();
    assert_eq!(code, 0, "the writer notice failed the process clean");
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    let calls = calls.lock().unwrap().clone();
    assert!(
        calls.contains(&"shutdown"),
        "the release was submitted and observed: {calls:?}"
    );
}

// ---- stdin framing violation --------------------------------------------------------------------

#[test]
fn framing_violation_keeps_rejected_exit_after_cleanup() {
    // A zero-length declared payload: a strict framing rejection (truncated frame class).
    let (stdin, reader_gate) = script_io(&[vec![0u8, 0, 0, 0]], false);
    let (stdout, collected) = collect_stdout();
    let (calls, code, elapsed) = run_bounded(Release::Sync, Io { stdin, stdout });
    reader_gate.open();
    assert_eq!(code, 2, "the framing terminal keeps its protocol exit");
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    let calls = calls.lock().unwrap().clone();
    assert!(
        calls.contains(&"shutdown"),
        "the rejected exit still released and observed: {calls:?}"
    );
    assert!(
        collected.lock().unwrap().is_empty(),
        "the uncorrelatable terminal is silent"
    );
}

// ---- stale terminal provenance (review fix: generation attribution) --------------------------------

/// Drives hello + one start_scan (whose backend emits the stray pre-release
/// terminal after its ScanStarted) and lets stdin EOF follow: the stray is
/// staged pre-claim and drains post-exit, as pure bookkeeping, so the EOF
/// cleanup confirmation depends exclusively on the attributed terminal.
#[test]
fn staged_stale_terminal_confirmed_only_by_the_genuine_async_release() {
    let (stdin, reader_gate) = script_io(&[hello_frame(), start_scan_frame("scan1")], false);
    let (stdout, collected) = collect_stdout();
    let (calls, code, elapsed) = run_bounded_stray(
        Release::Async(Duration::from_millis(150)),
        true,
        Io { stdin, stdout },
    );
    reader_gate.open();
    assert_eq!(
        code, 0,
        "the EOF exit stands once the genuine release was observed"
    );
    assert!(
        elapsed >= Duration::from_millis(100),
        "the runtime waited for the genuine (delayed) release, not the \
         pre-existing stale terminal: {elapsed:?}"
    );
    assert!(elapsed < Duration::from_secs(3), "{elapsed:?}");
    let calls = calls.lock().unwrap().clone();
    assert!(
        calls.contains(&"shutdown"),
        "the EOF release reached the backend: {calls:?}"
    );
    let frames = stdout_frames(&collected);
    assert_eq!(frames.len(), 2, "{frames:?}");
    assert_eq!(frames[1]["type"], "scan_state");
    assert!(
        !frames
            .iter()
            .any(|frame| frame["type"] == "shutdown_complete"),
        "EOF never emits a completion: {frames:?}"
    );
}

#[test]
fn staged_stale_terminal_alone_never_confirms_eof_cleanup() {
    // The same staged stale terminal with a backend whose release is never
    // observed: the run must fail closed at the inclusive 5 s deadline —
    // proof that the stale terminal drained post-EOF cannot pass for the
    // genuine release.
    let (stdin, reader_gate) = script_io(&[hello_frame(), start_scan_frame("scan1")], false);
    let (stdout, collected) = collect_stdout();
    let (calls, code, elapsed) = run_bounded_stray(Release::Never, true, Io { stdin, stdout });
    reader_gate.open();
    assert_eq!(
        code, EXIT_HOST_FAILURE,
        "the unobserved (stale-only) release fails closed"
    );
    assert!(
        elapsed >= Duration::from_millis(4_500) && elapsed < Duration::from_millis(7_500),
        "the inclusive 5 s deadline bounded the stall: {elapsed:?}"
    );
    assert!(
        !calls.lock().unwrap().is_empty(),
        "the release was still submitted (fire-and-forget)"
    );
    let frames = stdout_frames(&collected);
    assert!(
        !frames
            .iter()
            .any(|frame| frame["type"] == "shutdown_complete"),
        "no shutdown_complete without an observed release: {frames:?}"
    );
}

#[test]
fn staged_stale_terminal_confirmed_by_the_synchronous_release() {
    // Sync path: the genuine terminal is stamped inside the `shutdown()`
    // submission right after the claim, so the stale staged one (drained
    // first, post-exit) never delays or preempts it.
    let (stdin, reader_gate) = script_io(&[hello_frame(), start_scan_frame("scan1")], false);
    let (stdout, collected) = collect_stdout();
    let (calls, code, elapsed) = run_bounded_stray(Release::Sync, true, Io { stdin, stdout });
    reader_gate.open();
    assert_eq!(code, 0, "the synchronous genuine release confirms cleanup");
    assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
    let calls = calls.lock().unwrap().clone();
    assert!(
        calls.contains(&"shutdown"),
        "the EOF release reached the backend: {calls:?}"
    );
    let frames = stdout_frames(&collected);
    assert!(
        !frames
            .iter()
            .any(|frame| frame["type"] == "shutdown_complete"),
        "EOF never emits a completion: {frames:?}"
    );
}

// ---- watchdog seam (review fix: the enforcement layer) --------------------------------------------

/// A stalled release driven with the recording watchdog: the run must end on the
/// deadline armed with the terminal input — the very instant the bounded exit's
/// own deadlines are measured against — and disarm afterwards, exactly when the
/// exit is complete. The arm/deadline pairing proves the runtime's cooperative
/// deadline and the watchdog deadline are one shared clock measured from terminal
/// detection, not two independent waits appended back to back.
#[test]
fn terminal_deadline_armed_before_handling_ends_the_run() {
    let (stdin, reader_gate) = script_io(&[], false);
    let (stdout, _collected) = collect_stdout();
    let watchdog = RecordingWatchdog::new();
    let (_, code, elapsed) = run_bounded_watch(
        Release::Never,
        false,
        Io { stdin, stdout },
        Arc::clone(&watchdog),
    );
    reader_gate.open();
    assert_eq!(code, EXIT_HOST_FAILURE, "unobserved cleanup fails closed");
    assert!(
        elapsed >= Duration::from_millis(4_500) && elapsed < Duration::from_secs(5) + SCHED_TOL,
        "the inclusive 5 s deadline bounded the stall: {elapsed:?}"
    );
    let events = watchdog.events.lock().unwrap().clone();
    let (kind, end_of_run) = *events.last().expect("the watchdog was consulted");
    assert_eq!(
        kind, "disarm",
        "the armed exit ends with the run: {events:?}"
    );
    let deadline = events
        .iter()
        .rev()
        .find(|(kind, _)| *kind == "arm")
        .map(|(_, deadline)| *deadline)
        .expect("at least one arm");
    // The run ends on (not appreciably beyond) the last armed deadline: the
    // bounded exit and the watchdog share the one clock. The tolerance covers
    // the drain-return path after the deadline (the output queue here is empty,
    // so the overrun is only wait-loop slack).
    let overrun = end_of_run
        .checked_duration_since(deadline)
        .expect("the run ended after its terminal deadline");
    assert!(
        overrun <= Duration::from_millis(500),
        "the run ended {overrun:?} after the armed deadline"
    );
}

// ---- explicit shutdown with live stdin and an unconfirmed release -------------------------------

#[test]
fn accepted_shutdown_with_live_stdin_and_stuck_release_fails_within_five_seconds() {
    // stdin stays open (reader parked) and the backend never reports the release: no terminal
    // input will ever arrive, so only the deadline armed at the accepted shutdown request —
    // before its teardown submissions — can end the run. Fail closed, no `shutdown_complete`.
    let (stdin, reader_gate) = script_io(&[hello_frame(), shutdown_frame("bye")], true);
    let (stdout, collected) = collect_stdout();
    let watchdog = RecordingWatchdog::new();
    let (calls, code, elapsed) = run_bounded_watch(
        Release::Never,
        false,
        Io { stdin, stdout },
        Arc::clone(&watchdog),
    );
    reader_gate.open();
    assert_eq!(code, 1, "unconfirmed release is a host failure");
    assert!(
        elapsed >= Duration::from_secs(5) && elapsed < Duration::from_secs(5) + SCHED_TOL,
        "{elapsed:?}"
    );
    assert!(calls.lock().unwrap().contains(&"shutdown"));
    let frames = stdout_frames(&collected);
    assert!(
        frames
            .iter()
            .all(|frame| frame["type"] != "shutdown_complete"),
        "false cleanup success: {frames:?}"
    );
    // The watchdog stayed armed on the request's deadline (never disarmed in between), so a
    // blocking submission in that handling is enforced from outside as well.
    let events = watchdog.events.lock().unwrap().clone();
    let last_arm = events.iter().rposition(|(kind, _)| *kind == "arm").unwrap();
    let after: Vec<&str> = events[last_arm + 1..]
        .iter()
        .map(|(kind, _)| *kind)
        .collect();
    assert!(
        after.is_empty() || after == ["disarm"],
        "only the final return-time disarm may follow the request's arm: {events:?}"
    );
}

#[test]
fn accepted_shutdown_with_live_stdin_and_async_release_after_the_deadline_fails_closed() {
    // End-to-end counterpart of the deterministic unit boundary tests in `src/runtime.rs`:
    // idle stdin, and the attributed release is only reported shortly after the five seconds.
    let (stdin, reader_gate) = script_io(&[hello_frame(), shutdown_frame("bye")], true);
    let (stdout, collected) = collect_stdout();
    let (_calls, code, elapsed) = run_bounded(
        Release::Async(Duration::from_millis(5_030)),
        Io { stdin, stdout },
    );
    reader_gate.open();
    assert_eq!(code, 1, "a release after the deadline never yields exit 0");
    assert!(elapsed < Duration::from_secs(5) + SCHED_TOL, "{elapsed:?}");
    let frames = stdout_frames(&collected);
    assert!(
        frames
            .iter()
            .all(|frame| frame["type"] != "shutdown_complete"),
        "{frames:?}"
    );
}

#[test]
fn blocking_release_returning_after_the_deadline_fails_closed_without_success() {
    // Accepted shutdown, idle stdin; `Backend::shutdown` blocks the runtime thread across the
    // 5 s deadline and synchronously reports its attributed release on return (5.05 s).
    let (stdin, reader_gate) = script_io(&[hello_frame(), shutdown_frame("bye")], true);
    let (stdout, collected) = collect_stdout();
    let (_calls, code, elapsed) = run_bounded(
        Release::SyncAfter(Duration::from_millis(5_050)),
        Io { stdin, stdout },
    );
    reader_gate.open();
    assert_eq!(code, 1, "a late synchronous release never yields exit 0");
    assert!(elapsed >= Duration::from_secs(5), "{elapsed:?}");
    assert!(elapsed < Duration::from_secs(5) + SCHED_TOL, "{elapsed:?}");
    let frames = stdout_frames(&collected);
    assert!(
        frames
            .iter()
            .all(|frame| frame["type"] != "shutdown_complete"),
        "{frames:?}"
    );
}

#[test]
fn blocking_release_returning_before_the_deadline_still_succeeds() {
    let (stdin, reader_gate) = script_io(&[hello_frame(), shutdown_frame("bye")], true);
    let (stdout, collected) = collect_stdout();
    let (_calls, code, elapsed) = run_bounded(
        Release::SyncAfter(Duration::from_millis(4_500)),
        Io { stdin, stdout },
    );
    reader_gate.open();
    assert_eq!(code, 0);
    assert!(elapsed >= Duration::from_millis(4_500) && elapsed < Duration::from_secs(5));
    let frames = stdout_frames(&collected);
    assert_eq!(
        frames.last().unwrap()["type"],
        "shutdown_complete",
        "{frames:?}"
    );
}
