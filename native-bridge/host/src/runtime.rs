//! Headless process runtime (OTP-24 Task 5): the concurrent wiring around the deterministic
//! core (`crate::core::Core`). One stdin reader and one stdout writer each own one end of the
//! Chrome pipe; the calling thread runs the core loop, feeding it parsed inbound frames,
//! drained backend events and bounded clock ticks. Every handoff flows through the bounded
//! seams built for this design — the reader-frame queue (`crate::ingress::InputFrames`), the
//! backend ingress (`crate::ingress::BackendEvents`/`BackendReceiver`) and the writer-aware
//! output queue (`crate::outqueue::OutQueue`) — so memory stays bounded and erasing end to end
//! and the terminal signals (EOF, framing violation, I/O failure, output blockage, explicit
//! shutdown) can never be starved behind payload traffic. Nothing here logs, `Debug`-prints or
//! traces payload bytes.
//!
//! Exit contract ("allow five seconds from terminal input to process exit"): the five-second
//! window begins at **terminal detection**, on the runtime thread, synchronously before
//! [`Core::handle`] runs the terminal input — so the deadline also covers any time the
//! terminal handling itself spends inside a (potentially blocking, adapter-regressed)
//! [`crate::backend::Backend`] call such as [`Backend::shutdown`]. Two layers enforce it:
//!
//! 1. The runtime's own bounded exit (the cooperative layer): the terminal deadline instant
//!    (`terminal detection + [`EXIT_DEADLINE`]`) drives the whole exit — at most that many
//!    seconds (inclusive) are spent observing the *real* backend release (the backend's own
//!    [`BackendEvent::ShutdownComplete`], drained post-exit into the core's
//!    [`Core::shutdown_complete_received`] observation, per the Task 5 prerequisite cleanup)
//!    and, inside the same deadline, at most [`DRAIN_MAX`] — two seconds — draining the
//!    output queue through the writer. If the release is not observed by the deadline, the
//!    host fails closed with exit 1 regardless of the core's code; otherwise the core's
//!    protocol exit (0/2/3/4) stands.
//! 2. An independent process-level [`HangWatchdog`] (the enforcement layer): it is armed —
//!    with exactly the same terminal deadline — at terminal detection, *before* the first
//!    [`Core::handle`] that can touch the backend, and disarmed when [`run`] returns.
//!    If the cooperating runtime cannot come back (an adapter blocked forever inside a
//!    submission), the watchdog still terminates the process with [`EXIT_HOST_FAILURE`] from
//!    outside — no host thread's strength is required, and no protocol code is fabricated:
//!    the enforced exit is the failure code. Production entries construct the runtime
//!    config through [`Config::production`], whose watchdog really calls
//!    `std::process::exit`; in-process tests inject a non-exiting [`HangWatchdog`] via
//!    `Config` instead (a real `process::exit` would kill the test process, not the child).
//!
//! Neither stdio thread is ever joined: a reader parked on a full queue or a writer blocked
//! inside a frozen pipe cannot stall the return. Returning from `run` therefore does not
//! terminate blocked threads — the production binary (Task 5b) exits the process explicitly
//! once `run` returns.
//!
//! The writer is the only code that frames output. A failed write is reported to the core as
//! `Input::StdoutClosed` through a sticky atomic notice (the bounded control path); the writer
//! cannot enqueue into the ingress itself. The backend producers keep their non-blocking
//! contract: the core loop polls the sticky process-level ingress latches
//! ([`BackendReceiver::emit_lock_failure`]/[`BackendReceiver::control_overflow`]) on every
//! iteration, so a genuine unrecoverable loss (a poisoned ingress lock, an exhausted control
//! lane — never routine lock contention, see `crate::ingress`) fails the host closed exactly
//! as the design requires.
//!
//! **Idle wake and deadlines** (OTP-24 whole-change review I1/I2). The core thread parks
//! (`std::thread::park_timeout`) when nothing is pending and is woken by a level-triggered
//! unpark token ([`crate::ingress::CoreWaker`]) from *every* seam it serves — a frame or the
//! reader terminal pushed by the stdin reader, any backend event that becomes deliverable, and
//! the writer's failure notice — so backend events (write completions, inbound fragments of a
//! multi-fragment record) are never delayed behind a stdin-only wait, and an idle host reacts
//! in microseconds rather than up to [`POLL_MAX`]. Monotonic deadlines (request timeouts, the
//! scan cap, reassembly expiry) are compared with the clock on **every** loop iteration and
//! `Input::Tick` is injected as soon as one is due, whether or not frames or backend events
//! keep arriving: continuous traffic or garbage on one link can no longer starve another
//! link's timeout.

use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crate::backend::Backend;
use crate::core::{
    Config as CoreConfig, Core, EXIT_HOST_FAILURE, EXIT_OK, EXIT_OUTPUT_OVERFLOW, Input,
};
use crate::framing::{FrameError, read_frame, write_frame};
use crate::ingress::{
    BackendEvents, BackendPop, BackendReceiver, CoreWaker, InputFrames, InputPop, InputTerminal,
};
use crate::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop as OutPop};

/// Upper bound of one idle park: a safety net only (every seam wakes the core explicitly), so
/// a missed wake of any kind is noticed at most one cycle late.
const POLL_MAX: Duration = Duration::from_millis(100);

/// Floor of one idle park: a due-but-unconsumed deadline can never turn the loop into a hot
/// spin.
const IDLE_MIN: Duration = Duration::from_millis(1);

/// Whole bounded exit: five seconds from the terminal input to `run` returning, inclusive —
/// the release observation and the stdout drain both live inside this window.
const EXIT_DEADLINE: Duration = Duration::from_secs(5);

/// At most this much of [`EXIT_DEADLINE`] is spent waiting for the (concurrently draining)
/// writer to finish the queued output.
const DRAIN_MAX: Duration = Duration::from_secs(2);

/// The independent watchdog's safety margin after the terminal deadline: an exactly-on-time
/// bounded exit performs its final disarm within microseconds of the deadline, so the
/// enforcing thread re-checks after this small grace before terminating the process. It only
/// delays the enforced kill of a genuinely wedged host by this much (well inside any
/// scheduling tolerance); the cooperative deadlines stay exact.
const WATCHDOG_GRACE: Duration = Duration::from_millis(100);

// The kill of a genuinely wedged host may lag the 5 s contract by at most this grace; the
// process tests' parent ceiling is derived from it. Raising either constant fails the build.
const _: () = assert!(EXIT_DEADLINE.as_millis() == 5_000);
const _: () = assert!(WATCHDOG_GRACE.as_millis() <= 100);

/// The independent process-level watchdog seam (review fix: the exit deadline must hold even
/// when its clock starts before terminal handling can call a blocking adapter). The core
/// only promises fire-and-forget backend submissions; a regressed adapter can block the
/// runtime thread forever inside a terminal release submission, where no cooperative
/// mechanism can help. The watchdog is armed with the terminal deadline *before* terminal
/// handling starts ([`Core::handle`] invoking any backend call) and must, by the deadline
/// and without any help from a running host thread, terminate the process with
/// [`EXIT_HOST_FAILURE`].
///
/// In-process tests inject a non-exiting implementation (a real `process::exit` would kill
/// the test process, not a child); production code uses [`process_exit_watchdog`].
pub trait HangWatchdog: Send + Sync + 'static {
    /// Arms the enforcement: if [`Self::disarm`] does not happen before `deadline`, the
    /// watchdog terminates the process with [`EXIT_HOST_FAILURE`] — from outside, even when
    /// every host thread is blocked forever inside a backend call.
    fn arm(&self, deadline: Instant);

    /// Disarms a pending enforcement. Late but harmless: a disarmed watchdog never kills.
    fn disarm(&self);
}

/// What the watchdog thread is doing while it is not holding the state lock. It is only ever
/// written by the thread itself, under the lock, immediately before it releases the lock into a
/// condvar wait (`Idle` = indefinite wait because nothing is armed, `Until(t)` = timed wait that
/// ends no later than `t`) and reset to `Awake` as soon as it re-holds the lock. `arm`/`disarm`
/// run under the same lock, so they always see an accurate picture of when the thread will next
/// look at the armed deadline by itself.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Sleep {
    Awake,
    Idle,
    Until(Instant),
}

struct WatchdogInner {
    armed: Option<Instant>,
    sleep: Sleep,
}

/// Shared state of the production watchdog: the single armed deadline.
struct WatchdogState {
    inner: Mutex<WatchdogInner>,
    wake: Condvar,
    /// Test census: how many `arm`/`disarm` calls actually woke the watchdog thread.
    #[cfg(test)]
    notifications: std::sync::atomic::AtomicUsize,
}

/// The production [`HangWatchdog`]: one independent thread parks until a deadline is armed
/// (or re-armed), then — after a small grace that lets an on-time exit disarm first —
/// terminates the process with [`EXIT_HOST_FAILURE`]. The thread needs nothing from the
/// host: a runtime stuck inside a blocking [`crate::backend::Backend`] call cannot prevent
/// or defer the kill.
///
/// **Per-input cost.** The runtime arms before and disarms after *every* handled input, so the
/// calls are on the hot path. A notification is only needed when the thread would otherwise
/// look at the armed deadline *later than it must*: [`HangWatchdog::arm`] wakes it only when it
/// sleeps indefinitely (`Sleep::Idle`) or the new deadline is earlier than the instant its timed
/// wait ends (`Sleep::Until`); a later or equal deadline is picked up when the thread wakes by
/// itself at that earlier instant (it re-reads the armed deadline under the lock and recomputes
/// its wait), once it has notified the thread it marks it `Awake` (it re-reads the armed deadline itself), so further arms before it runs add no notifications, and [`HangWatchdog::disarm`] never needs to wake it (a stale wake-up finds nothing
/// armed and parks again; the grace re-check compares against the *current* armed value). The
/// hard deadline is therefore unchanged: the thread is always awake no later than the earliest
/// armed deadline, and the kill still happens only after `deadline + grace` with the armed value
/// unchanged.
pub struct ProcessExitWatchdog {
    state: Arc<WatchdogState>,
}

impl ProcessExitWatchdog {
    /// Spawns the watchdog thread. One per process runtime is plenty; spawning it with the
    /// runtime config keeps the enforcement overhead off the exit path entirely.
    pub fn spawn() -> Arc<Self> {
        Self::spawn_with(WATCHDOG_GRACE, || std::process::exit(EXIT_HOST_FAILURE))
    }

    /// The grace and the enforcement action are parameters only so the expiry/grace/disarm semantics are
    /// testable deterministically in-process; the sole production caller is [`Self::spawn`],
    /// whose action is the real `std::process::exit(EXIT_HOST_FAILURE)`.
    fn spawn_with(grace: Duration, on_expiry: impl Fn() + Send + 'static) -> Arc<Self> {
        let state = Arc::new(WatchdogState {
            inner: Mutex::new(WatchdogInner {
                armed: None,
                sleep: Sleep::Awake,
            }),
            wake: Condvar::new(),
            #[cfg(test)]
            notifications: std::sync::atomic::AtomicUsize::new(0),
        });
        let shared = Arc::clone(&state);
        thread::Builder::new()
            .name("veles-exit-watchdog".into())
            .spawn(move || watchdog_thread(&shared, grace, on_expiry))
            .expect("spawn exit watchdog");
        Arc::new(Self { state })
    }
}

impl ProcessExitWatchdog {
    /// `arm` with the state lock already held (a seam so the wake bookkeeping is testable
    /// deterministically while the thread is kept off the lock).
    fn arm_locked(&self, inner: &mut WatchdogInner, deadline: Instant) {
        inner.armed = Some(deadline);
        let must_wake = match inner.sleep {
            Sleep::Awake => false, // cannot be observed under the lock; re-reads on its own
            Sleep::Idle => true,
            Sleep::Until(until) => deadline < until,
        };
        if must_wake {
            #[cfg(test)]
            self.state
                .notifications
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            self.state.wake.notify_all();
            // The thread is now committed to re-reading `armed` under the lock; until it does,
            // further arms need no notification of their own (it will see the latest value).
            inner.sleep = Sleep::Awake;
        }
    }
}

impl HangWatchdog for ProcessExitWatchdog {
    fn arm(&self, deadline: Instant) {
        let mut inner = self.state.inner.lock().expect("exit watchdog lock");
        self.arm_locked(&mut inner, deadline);
    }

    fn disarm(&self) {
        // No wake needed: a stale wake-up finds nothing armed (or a different value) and the
        // thread recomputes; waking it here would only cost a context switch per input.
        self.state.inner.lock().expect("exit watchdog lock").armed = None;
    }
}

/// The watchdog thread body: recompute the target on every state change; on deadline expiry
/// re-check once after [`WATCHDOG_GRACE`] so an on-time exit can disarm first — an unchanged,
/// still-armed deadline then terminates the process with the failure code, whatever the host
/// threads are doing.
fn watchdog_thread(state: &WatchdogState, grace: Duration, on_expiry: impl Fn()) {
    let mut inner = state.inner.lock().expect("exit watchdog lock");
    loop {
        let waking_deadline = loop {
            match inner.armed {
                None => {
                    inner.sleep = Sleep::Idle;
                    inner = state.wake.wait(inner).expect("exit watchdog lock");
                    inner.sleep = Sleep::Awake;
                }
                Some(deadline) => {
                    let now = Instant::now();
                    if now < deadline {
                        inner.sleep = Sleep::Until(deadline);
                        let (guard, _) = state
                            .wake
                            .wait_timeout(inner, deadline - now)
                            .expect("exit watchdog lock");
                        inner = guard;
                        inner.sleep = Sleep::Awake;
                    }
                    break inner.armed;
                }
            }
        };
        let Some(deadline) = waking_deadline else {
            // Disarmed while parked: back to waiting.
            continue;
        };
        if Instant::now() < deadline {
            // Re-armed with a later deadline (or a spurious wake): recompute.
            continue;
        }
        // The deadline has passed while still armed. One full grace period (a spurious or
        // unrelated wake does not shorten it) lets an exactly-on-time bounded exit perform its
        // final disarm without losing the kill race; the deadline must be unchanged for the
        // check to mean anything.
        let grace_end = Instant::now() + grace;
        while inner.armed == Some(deadline) && Instant::now() < grace_end {
            let remaining = grace_end.saturating_duration_since(Instant::now());
            inner.sleep = Sleep::Until(grace_end);
            let (guard, _) = state
                .wake
                .wait_timeout(inner, remaining)
                .expect("exit watchdog lock");
            inner = guard;
            inner.sleep = Sleep::Awake;
        }
        if inner.armed == Some(deadline) {
            // A still-latched unchanged deadline means the cooperative layer failed:
            // fail closed. No host thread made it back in time to finish (and disarm) the
            // exit, so no protocol code can be trusted and none is fabricated.
            on_expiry();
            // Production's action never returns. A test action returns: wait for the next
            // arm instead of firing again for the same deadline.
            inner.armed = None;
        }
    }
}

/// The production watchdog construction ([`Config::production`] uses it): an independent
/// thread enforcing the terminal deadline with a real `std::process::exit`, from outside the
/// host's own control flow.
pub fn process_exit_watchdog() -> Arc<dyn HangWatchdog> {
    ProcessExitWatchdog::spawn()
}

/// The process stdio owns. The reader thread owns `stdin` exclusively; the writer thread owns
/// `stdout` exclusively and is the only code that writes it.
pub struct Io {
    pub stdin: Box<dyn Read + Send>,
    pub stdout: Box<dyn Write + Send>,
}

/// The caller-supplied runtime configuration. `run` builds the core's [`CoreConfig`] itself:
/// its `ingress` is the receiver of the very same [`BackendEvents`] pair whose producer is
/// handed to `make_backend`, so the core's admission seams and the runtime's drain/poll seam
/// use the one shared receiver state (see `crate::ingress::BackendReceiver`).
pub struct Config {
    pub bridge_version: String,
    pub platform: &'static str,
    /// The independent process-level watchdog enforcing the terminal deadline from outside
    /// (see [`HangWatchdog`]).
    pub watchdog: Arc<dyn HangWatchdog>,
}

impl Config {
    /// The production configuration: the same fields plus the real process-exit watchdog.
    /// Production entries (the platform adapters of Tasks 6/7, wired into the
    /// veles-native-host entry) must construct it this way — a runtime without a real
    /// process-exit watchdog only *promises* the five-second exit contract, never enforces
    /// it against an adapter blocked inside a release submission.
    pub fn production(bridge_version: String, platform: &'static str) -> Self {
        Self {
            bridge_version,
            platform,
            watchdog: process_exit_watchdog(),
        }
    }
}

/// Sticky notice the writer thread signals on a failed frame delivery (closed Chrome pipe);
/// the core loop reads it through a bounded poll and feeds `Input::StdoutClosed`.
struct StdoutNotice {
    closed: AtomicBool,
}

impl StdoutNotice {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            closed: AtomicBool::new(false),
        })
    }
}

fn spawn_reader(mut stdin: Box<dyn Read + Send>, input: Arc<InputFrames>) {
    thread::Builder::new()
        .name("veles-stdin-reader".into())
        .spawn(move || {
            loop {
                match read_frame(&mut stdin) {
                    Ok(Some(frame)) => {
                        // Backpressure push: the reader parks until the core drains a frame
                        // instead of staging ahead; a terminal set by the runtime's bounded exit
                        // releases it (and its held frame) instead of parking it forever.
                        if input.push_frame(frame).is_err() {
                            return;
                        }
                    }
                    Ok(None) => {
                        input.set_terminal(InputTerminal::Eof);
                        return;
                    }
                    Err(FrameError::Io(_)) => {
                        // Only an OS read failure is I/O; every strict framing rejection
                        // (truncated frame, zero length, oversize) reads as the framing terminal.
                        input.set_terminal(InputTerminal::IoError);
                        return;
                    }
                    Err(_) => {
                        input.set_terminal(InputTerminal::TruncatedFrame);
                        return;
                    }
                }
            }
        })
        .expect("spawn stdin reader");
}

fn spawn_writer(
    mut stdout: Box<dyn Write + Send>,
    out: Arc<OutQueue>,
    notice: Arc<StdoutNotice>,
    waker: Arc<CoreWaker>,
) {
    thread::Builder::new()
        .name("veles-stdout-writer".into())
        .spawn(move || {
            loop {
                match out.pop(POLL_MAX) {
                    OutPop::Frame(payload) => {
                        let result = write_frame(&mut stdout, payload.as_slice());
                        if result.is_err() {
                            // One atomic transition under the queue lock (fail + close +
                            // release accounting); only then the sticky notice.
                            out.fail_writer();
                            notice.closed.store(true, Ordering::Release);
                            waker.wake();
                            return;
                        }
                        out.finish_write();
                    }
                    OutPop::Timeout => {}
                    OutPop::Closed => return,
                }
                // `stdout` is dropped here on clean exits, closing the pipe for Chrome; a writer
                // blocked inside a frozen pipe is never joined (the process exits instead).
            }
        })
        .expect("spawn stdout writer");
}

/// Runs the host until it has fully released every resource or the bounded exit expired;
/// returns the process exit code. See the module documentation for the exit contract.
pub fn run<B, F>(make_backend: F, io: Io, config: Config) -> i32
where
    B: Backend + 'static,
    F: FnOnce(BackendEvents) -> B,
{
    let (events, receiver) = BackendEvents::new();
    let core_config = CoreConfig {
        bridge_version: config.bridge_version,
        platform: config.platform,
        ingress: receiver.clone(),
    };
    let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
    // The core thread is the one this function runs on: bind it before any producer exists
    // (the backend is built below) so no wake can be missed.
    let waker = receiver.core_waker();
    waker.bind_current();
    let input = InputFrames::with_waker(Arc::clone(&waker));
    let notice = StdoutNotice::new();
    let mut core = Core::new(make_backend(events), out.clone(), core_config);
    spawn_reader(io.stdin, Arc::clone(&input));
    spawn_writer(
        io.stdout,
        out.clone(),
        Arc::clone(&notice),
        Arc::clone(&waker),
    );
    let watchdog = config.watchdog.as_ref();
    // The absolute terminal deadline of the input that decides the exit: set on every
    // handled input, so it always equals the winning (exit-deciding) handle's deadline —
    // armed before `Core::handle` can touch the backend at all.
    let mut exit_deadline: Option<Instant> = None;
    // Set once a valid explicit `shutdown` is accepted and awaits the backend's release:
    // the absolute deadline armed BEFORE that request's teardown submissions. Stdin may stay
    // open, so no terminal input would ever bound the wait otherwise.
    let mut shutdown_deadline: Option<Instant> = None;

    let code = loop {
        if let Some(deadline) = shutdown_deadline
            && Instant::now() >= deadline
            && let Some(code) = core.abort_pending_shutdown()
        {
            // The release was not confirmed within five seconds of the accepted request:
            // fail closed, never a `shutdown_complete`.
            exit_deadline = Some(deadline);
            break code;
        }
        // Process-level failure latches first: the sticky ingress signals dominate every
        // other input of the cycle (the core re-checks them on every input as well).
        if (receiver.emit_lock_failure() || receiver.control_overflow())
            && let Some(code) = handle_input(
                &mut core,
                Input::BackendOverflow,
                watchdog,
                &mut exit_deadline,
                &mut shutdown_deadline,
            )
        {
            break code;
        }
        if notice.closed.swap(false, Ordering::Acquire)
            && let Some(code) = handle_input(
                &mut core,
                Input::StdoutClosed,
                watchdog,
                &mut exit_deadline,
                &mut shutdown_deadline,
            )
        {
            break code;
        }
        // Monotonic deadlines are serviced on every cycle, however busy the inputs are
        // (review I1): a due request timeout / scan cap / reassembly expiry never waits for
        // an idle moment that continuous traffic would never provide.
        if core
            .next_deadline()
            .is_some_and(|deadline| Instant::now() >= deadline)
            && let Some(code) = handle_input(
                &mut core,
                Input::Tick,
                watchdog,
                &mut exit_deadline,
                &mut shutdown_deadline,
            )
        {
            break code;
        }
        // One input frame and one backend event per cycle: strictly paired consumption keeps
        // a flood of either side from starving the other beyond one bounded cycle.
        let mut pumped = false;
        match input.pop_frame(Some(Duration::ZERO)) {
            InputPop::Frame(frame) => {
                pumped = true;
                if let Some(code) = handle_input(
                    &mut core,
                    Input::Frame(frame),
                    watchdog,
                    &mut exit_deadline,
                    &mut shutdown_deadline,
                ) {
                    break code;
                }
            }
            InputPop::Terminal(kind) => {
                pumped = true;
                // Terminal detection happens here, on the runtime thread, before the core
                // handles the terminal input: the watchdog deadline (and the bounded exit's
                // own deadline below) start now, covering every potentially blocking
                // submission the terminal handling invokes.
                if let Some(code) = handle_input(
                    &mut core,
                    terminal_input(kind),
                    watchdog,
                    &mut exit_deadline,
                    &mut shutdown_deadline,
                ) {
                    break code;
                }
            }
            InputPop::Timeout => {}
        }
        if let BackendPop::Event(event) = receiver.pop_event(Some(Duration::ZERO)) {
            pumped = true;
            if let Some(code) = handle_input(
                &mut core,
                Input::Backend(event),
                watchdog,
                &mut exit_deadline,
                &mut shutdown_deadline,
            ) {
                break code;
            }
        }
        if pumped {
            continue;
        }
        // Idle: park until a seam wakes the core (frame, reader terminal, deliverable backend
        // event, writer failure) or the earliest monotonic deadline — the pending-shutdown
        // deadline included — is due. The unpark token is level-triggered, so a wake that
        // raced ahead of this park makes it return at once.
        let now = Instant::now();
        let earliest = [core.next_deadline(), shutdown_deadline]
            .into_iter()
            .flatten()
            .min();
        let wait = earliest
            .map_or(POLL_MAX, |deadline| deadline.saturating_duration_since(now))
            .clamp(IDLE_MIN, POLL_MAX);
        thread::park_timeout(wait);
    };

    // ---- bounded exit: observe the real release, drain the output, never join threads ----
    let deadline = exit_deadline.unwrap_or_else(|| Instant::now() + EXIT_DEADLINE);
    let code = bounded_exit(&mut core, &receiver, &out, &input, code, deadline);
    // Dropping the core drops the backend: a blocking adapter `Drop` is therefore still
    // inside the armed deadline. `run` only completes once that is done; then the watchdog
    // is disarmed so it can never fire into the caller's (already computed) result.
    drop(core);
    watchdog.disarm();
    code
}

/// One `Core::handle` call guarded by the process-level watchdog: armed with a fresh
/// terminal deadline before the core starts (so a blocking backend call inside the handling
/// is bounded from the moment the input was observed), disarmed when the core reports
/// "handled, keep going". A returned exit code leaves the watchdog armed — the bounded exit
/// and the process are then still inside that deadline.
fn handle_input<B: Backend>(
    core: &mut Core<B>,
    input: Input,
    watchdog: &dyn HangWatchdog,
    exit_deadline: &mut Option<Instant>,
    shutdown_deadline: &mut Option<Instant>,
) -> Option<i32> {
    // A pending explicit shutdown keeps its original deadline (armed when the request was
    // accepted); every other input arms a fresh one before the core can touch the backend.
    if let Some(deadline) = *shutdown_deadline {
        // The pending handshake's deadline is checked BEFORE the input can reach the core:
        // once it expired no input — least of all a late attributed `ShutdownComplete` — may
        // complete the handshake or be accepted. Fail closed, nothing emitted.
        if Instant::now() >= deadline
            && let Some(code) = core.abort_pending_shutdown()
        {
            *exit_deadline = Some(deadline);
            return Some(code);
        }
    }
    let deadline = shutdown_deadline.unwrap_or_else(|| Instant::now() + EXIT_DEADLINE);
    if shutdown_deadline.is_none() {
        watchdog.arm(deadline);
    }
    *exit_deadline = Some(deadline);
    let code = core.handle(input, Instant::now());
    if code.is_none() {
        if core.shutdown_pending() {
            // Post-handle consistency on the actual monotonic clock: a backend call that
            // blocked across the deadline inside this very `handle` (even one that
            // synchronously reported its release meanwhile) fails the handshake right here.
            // The core itself can never turn that release into `shutdown_complete` inside
            // the call: a backend only stages events into the ingress, and the success frame
            // is emitted solely when the runtime later dispatches the drained terminal
            // through this function, whose pre-dispatch gate above rejects it once expired.
            if Instant::now() >= deadline
                && let Some(code) = core.abort_pending_shutdown()
            {
                *shutdown_deadline = Some(deadline);
                *exit_deadline = Some(deadline);
                return Some(code);
            }
            // Handshake accepted, release outstanding: stay armed on this deadline.
            *shutdown_deadline = Some(deadline);
        } else {
            watchdog.disarm();
        }
    }
    code
}

/// Maps the reader terminal kinds to the core's dedicated inputs.
fn terminal_input(kind: InputTerminal) -> Input {
    match kind {
        InputTerminal::Eof => Input::StdinEof,
        InputTerminal::TruncatedFrame => Input::StdinFraming,
        InputTerminal::IoError => Input::StdinIoError,
    }
}

/// Bounded exit sequence. The one shared terminal deadline — armed at terminal detection,
/// before any terminal handling could touch the backend — drives everything: watch the
/// backend's real `ShutdownComplete` (post-exit drained into the core) until it, and — inside
/// the same window — at most two seconds of stdout drain. Missing cleanup fails closed
/// (exit 1); observed cleanup preserves the core's protocol exit. No thread is joined.
fn bounded_exit<B: Backend>(
    core: &mut Core<B>,
    receiver: &BackendReceiver,
    out: &Arc<OutQueue>,
    input: &Arc<InputFrames>,
    code: i32,
    total_deadline: Instant,
) -> i32 {
    out.close();
    // The queue state is dead past the core's exit; setting the terminal releases a producer
    // parked on a full queue (and its held, still-plain-text frame) instead of leaking it.
    // First-kind-wins keeps any already recorded reader terminal intact.
    input.set_terminal(InputTerminal::Eof);

    let mut cleanup_seen = core.shutdown_complete_received();
    while !cleanup_seen {
        let now = Instant::now();
        if now >= total_deadline {
            break;
        }
        let slice = (total_deadline - now).min(POLL_MAX);
        match receiver.pop_event(Some(slice)) {
            BackendPop::Event(event) => {
                // Post-exit the core only records the process-wide terminal; every other
                // event is returned unchanged, dropping its payload with it.
                let _ = core.handle(Input::Backend(event), Instant::now());
                cleanup_seen = core.shutdown_complete_received();
            }
            BackendPop::Timeout => {}
        }
    }
    if !cleanup_seen {
        return EXIT_HOST_FAILURE;
    }
    // The writer has been draining concurrently all along; the bound only matters when it is
    // slow or blocked. Drain ≤2 s, inside the total deadline.
    let drain_deadline = Instant::now() + DRAIN_MAX;
    let drain_deadline = drain_deadline.min(total_deadline);
    let _drained = out.wait_drained(drain_deadline);
    stdout_failure_precedence(code, out)
}

/// Precedence when both are latched: a failed stdout delivery dominates an output overflow.
/// With the Chrome pipe gone nobody can receive an overflow report, and the failed write is
/// the same clean termination as `Input::StdoutClosed`. Evaluated after cleanup and the
/// bounded drain, so a failure the writer records inside that window is honoured; a failure
/// recorded after the queue lock saw the breach, with the writer healthy until then, stays a
/// genuine overflow (exit 4). Every other code is returned unchanged.
fn stdout_failure_precedence(code: i32, out: &OutQueue) -> i32 {
    if code == EXIT_OUTPUT_OVERFLOW && out.writer_failed() {
        EXIT_OK
    } else {
        code
    }
}

#[cfg(test)]
mod tests {
    //! Deterministic boundary tests of the pending-shutdown deadline gate in `handle_input`:
    //! the stored deadline is moved relative to "now" instead of racing the real clock.
    use super::*;
    use crate::backend::{BackendEvent, NormalizedTarget, Target, Token};
    use crate::buffer::Payload;
    use crate::ingress::BackendEvents;

    struct NullBackend;

    impl Backend for NullBackend {
        fn normalize_target(&self, _hint: &str) -> Option<NormalizedTarget> {
            None
        }
        fn start_scan(&mut self, _scan: Token, _service_uuid: &str) {}
        fn stop_scan(&mut self, _scan: Token) {}
        fn connect(&mut self, _conn: Token, _target: &Target) {}
        fn write_fragments(&mut self, _conn: Token, _send: Token, _frames: Vec<Payload>) {}
        fn disconnect(&mut self, _conn: Token) {}
        fn shutdown(&mut self) {}
    }

    struct NoWatchdog;

    impl HangWatchdog for NoWatchdog {
        fn arm(&self, _deadline: Instant) {}
        fn disarm(&self) {}
    }

    struct Fixture {
        core: Core<NullBackend>,
        events: BackendEvents,
        receiver: BackendReceiver,
        out: Arc<OutQueue>,
        exit_deadline: Option<Instant>,
        shutdown_deadline: Option<Instant>,
    }

    fn frame(json: &str) -> Input {
        Input::Frame(Payload::new(json.as_bytes().to_vec()))
    }

    impl Fixture {
        fn input(&mut self, input: Input) -> Option<i32> {
            handle_input(
                &mut self.core,
                input,
                &NoWatchdog,
                &mut self.exit_deadline,
                &mut self.shutdown_deadline,
            )
        }

        /// Hello + accepted shutdown: handshake pending, deadline armed by `handle_input`.
        fn pending() -> Self {
            let (events, receiver) = BackendEvents::new();
            let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
            let core = Core::new(
                NullBackend,
                out.clone(),
                CoreConfig {
                    bridge_version: "runtime-unit/0".into(),
                    platform: "test",
                    ingress: receiver.clone(),
                },
            );
            let mut fixture = Self {
                core,
                events,
                receiver,
                out,
                exit_deadline: None,
                shutdown_deadline: None,
            };
            let hello =
                r#"{"envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]}"#;
            assert_eq!(fixture.input(frame(hello)), None);
            let bye =
                r#"{"envelope_version":1,"api_version":1,"type":"shutdown","request_id":"bye"}"#;
            assert_eq!(fixture.input(frame(bye)), None);
            assert!(fixture.shutdown_deadline.is_some(), "handshake pending");
            fixture
        }

        /// The attributed release (emitted after the claim), as the runtime would pop it.
        fn release(&mut self) -> Input {
            self.events.emit(BackendEvent::ShutdownComplete);
            match self.receiver.pop_event(Some(Duration::ZERO)) {
                BackendPop::Event(event) => Input::Backend(event),
                BackendPop::Timeout => panic!("release was not delivered"),
            }
        }

        fn output_types(&self) -> Vec<String> {
            let mut types = Vec::new();
            while let OutPop::Frame(payload) = self.out.pop(Duration::ZERO) {
                let value: serde_json::Value = serde_json::from_slice(payload.as_slice()).unwrap();
                types.push(value["type"].as_str().unwrap().to_string());
                self.out.finish_write();
            }
            types
        }
    }

    fn frame_of(len: usize) -> Payload {
        Payload::new(vec![0x41; len])
    }

    #[test]
    fn overflow_then_writer_failure_resolves_to_clean_stdout_closed() {
        // Deterministic interleaving: the writer holds an in-flight frame, a capacity breach is
        // observed on the still-open queue, THEN the failed write is recorded.
        let out = OutQueue::new(2, MAX_QUEUED_BYTES);
        out.push(frame_of(4), None).unwrap();
        let in_flight = out.pop(Duration::ZERO);
        assert!(matches!(in_flight, OutPop::Frame(_)));
        out.push(frame_of(4), None).unwrap();
        assert_eq!(
            out.push(frame_of(4), None),
            Err(crate::outqueue::PushError::Overflow),
            "full open queue: a genuine breach"
        );
        assert_eq!(stdout_failure_precedence(EXIT_OUTPUT_OVERFLOW, &out), 4);
        out.fail_writer();
        assert!(out.writer_failed());
        assert_eq!(
            out.len(),
            0,
            "failure releases in-flight and queued accounting"
        );
        assert_eq!(
            out.push(frame_of(4), None),
            Err(crate::outqueue::PushError::Closed)
        );
        assert_eq!(
            stdout_failure_precedence(EXIT_OUTPUT_OVERFLOW, &out),
            EXIT_OK
        );
        for other in [1, 2, 3] {
            assert_eq!(stdout_failure_precedence(other, &out), other);
        }
    }

    #[test]
    fn genuine_overflow_with_a_healthy_writer_stays_exit_four() {
        let out = OutQueue::new(1, MAX_QUEUED_BYTES);
        out.push(frame_of(4), None).unwrap();
        assert!(out.push(frame_of(4), None).is_err());
        out.close(); // the runtime's own bounded exit closes the queue; that is not a writer failure
        assert!(!out.writer_failed());
        assert_eq!(stdout_failure_precedence(EXIT_OUTPUT_OVERFLOW, &out), 4);
    }

    #[test]
    fn bounded_exit_honours_a_writer_failure_recorded_during_the_drain_window() {
        let mut fixture = Fixture::pending();
        let release = fixture.release();
        assert_eq!(fixture.input(release), Some(0));
        let out = fixture.out.clone();
        // Overflow decided the core's code; the writer's failure lands before the drain ends.
        let writer = {
            let out = out.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(50));
                out.fail_writer();
            })
        };
        let input = InputFrames::new();
        let code = bounded_exit(
            &mut fixture.core,
            &fixture.receiver,
            &out,
            &input,
            EXIT_OUTPUT_OVERFLOW,
            Instant::now() + Duration::from_secs(5),
        );
        writer.join().unwrap();
        assert_eq!(code, EXIT_OK);
    }

    #[test]
    fn release_well_before_the_pending_deadline_still_succeeds() {
        let mut fixture = Fixture::pending();
        let release = fixture.release();
        fixture.shutdown_deadline = Some(Instant::now() + Duration::from_secs(30));
        assert_eq!(fixture.input(release), Some(0));
        assert_eq!(fixture.output_types(), vec!["ready", "shutdown_complete"]);
    }

    #[test]
    fn release_just_before_the_pending_deadline_still_succeeds() {
        let mut fixture = Fixture::pending();
        let release = fixture.release();
        // A tight boundary: 250 ms of margin is orders of magnitude above the dispatch time.
        fixture.shutdown_deadline = Some(Instant::now() + Duration::from_millis(250));
        assert_eq!(fixture.input(release), Some(0));
        assert_eq!(fixture.output_types(), vec!["ready", "shutdown_complete"]);
    }

    #[test]
    fn attributed_release_after_the_pending_deadline_fails_closed_without_success() {
        let mut fixture = Fixture::pending();
        let release = fixture.release();
        fixture.shutdown_deadline = Some(Instant::now() - Duration::from_millis(1));
        assert_eq!(fixture.input(release), Some(EXIT_HOST_FAILURE));
        assert_eq!(
            fixture.output_types(),
            vec!["ready"],
            "no shutdown_complete"
        );
        assert!(!fixture.core.shutdown_complete_received());
    }

    #[test]
    fn no_input_is_accepted_after_the_pending_deadline() {
        let mut fixture = Fixture::pending();
        fixture.shutdown_deadline = Some(Instant::now() - Duration::from_millis(1));
        let late = r#"{"envelope_version":1,"api_version":1,"type":"stop_scan","request_id":"x","scan_handle":"0000000000000001"}"#;
        assert_eq!(fixture.input(frame(late)), Some(EXIT_HOST_FAILURE));
        assert_eq!(fixture.output_types(), vec!["ready"], "no further output");
    }

    // ---- deterministic deadline semantics (no wall-clock scheduling thresholds) ----------

    /// Records every armed deadline together with the instant the arm call ran.
    #[derive(Default)]
    struct RecordingWatchdog {
        arms: Mutex<Vec<(Instant, Instant)>>,
    }

    impl HangWatchdog for RecordingWatchdog {
        fn arm(&self, deadline: Instant) {
            self.arms.lock().unwrap().push((Instant::now(), deadline));
        }
        fn disarm(&self) {}
    }

    #[test]
    fn terminal_deadline_is_exactly_five_seconds_from_detection() {
        // The deadline is `detection + 5 s`, and detection lies between the two reads that
        // bracket the handled input — an exact invariant on the process's own monotonic
        // clock, independent of how loaded the machine is.
        let (_events, receiver) = BackendEvents::new();
        let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
        let mut core = Core::new(
            NullBackend,
            out,
            CoreConfig {
                bridge_version: "runtime-unit/0".into(),
                platform: "test",
                ingress: receiver,
            },
        );
        let watchdog = RecordingWatchdog::default();
        let (mut exit_deadline, mut shutdown_deadline) = (None, None);
        let before = Instant::now();
        let code = handle_input(
            &mut core,
            Input::StdinEof,
            &watchdog,
            &mut exit_deadline,
            &mut shutdown_deadline,
        );
        let after = Instant::now();
        assert!(code.is_some(), "EOF is terminal");
        let arms = watchdog.arms.lock().unwrap();
        assert_eq!(
            arms.len(),
            1,
            "armed once, before the core handled the input"
        );
        let (_, deadline) = arms[0];
        assert!(deadline >= before + EXIT_DEADLINE, "never earlier than 5 s");
        assert!(deadline <= after + EXIT_DEADLINE, "never later than 5 s");
        assert_eq!(
            exit_deadline,
            Some(deadline),
            "the exit uses the armed deadline"
        );
    }

    #[test]
    fn accepted_shutdown_arms_its_deadline_once_and_keeps_it() {
        let mut fixture = Fixture::pending();
        let deadline = fixture.shutdown_deadline.expect("pending");
        assert_eq!(fixture.exit_deadline, Some(deadline));
        // An unrelated input while the release is outstanding must not re-arm or extend it.
        let before = fixture.shutdown_deadline;
        let _ = fixture.input(Input::Tick);
        assert_eq!(fixture.shutdown_deadline, before);
    }

    #[test]
    fn bounded_exit_without_release_fails_closed_at_the_given_deadline() {
        let mut fixture = Fixture::pending();
        let input = InputFrames::new();
        let deadline = Instant::now() + Duration::from_millis(200);
        let code = bounded_exit(
            &mut fixture.core,
            &fixture.receiver,
            &fixture.out,
            &input,
            EXIT_OK,
            deadline,
        );
        assert_eq!(code, EXIT_HOST_FAILURE);
        assert!(
            Instant::now() >= deadline,
            "the release was awaited until the shared deadline, not given up early"
        );
    }

    #[test]
    fn bounded_exit_with_an_expired_deadline_fails_closed_immediately() {
        // Deadline already past and no release observed: fail closed without any wait loop.
        let mut fixture = Fixture::pending();
        let input = InputFrames::new();
        let code = bounded_exit(
            &mut fixture.core,
            &fixture.receiver,
            &fixture.out,
            &input,
            EXIT_OK,
            Instant::now() - Duration::from_millis(1),
        );
        assert_eq!(code, EXIT_HOST_FAILURE);
    }

    /// Watchdog whose expiry action records *when* the watchdog thread fired.
    fn expiry_probe(
        grace: Duration,
    ) -> (Arc<ProcessExitWatchdog>, std::sync::mpsc::Receiver<Instant>) {
        let (tx, rx) = std::sync::mpsc::channel();
        let tx = Mutex::new(tx);
        let watchdog = ProcessExitWatchdog::spawn_with(grace, move || {
            let _ = tx.lock().unwrap().send(Instant::now());
        });
        (watchdog, rx)
    }

    /// Event-driven waits only: a generous `recv_timeout` bounds a hang, never asserts latency.
    const EVENT_WAIT: Duration = Duration::from_secs(30);

    /// Scheduling tolerance for the watchdog thread's wake-up after `deadline + grace`.
    const WAKE_TOL: Duration = Duration::from_secs(1);

    #[test]
    fn production_deadline_and_grace_are_pinned() {
        assert_eq!(EXIT_DEADLINE, Duration::from_secs(5));
        assert_eq!(WATCHDOG_GRACE, Duration::from_millis(100));
        // Worst-case enforced kill of a wedged host: deadline + grace (+ scheduling).
        assert_eq!(EXIT_DEADLINE + WATCHDOG_GRACE, Duration::from_millis(5_100));
    }

    #[test]
    fn watchdog_expires_in_the_window_deadline_plus_grace_and_scheduling_tolerance() {
        let grace = Duration::from_millis(150);
        let (watchdog, expired) = expiry_probe(grace);
        let deadline = Instant::now() + Duration::from_millis(100);
        watchdog.arm(deadline);
        let fired_at = expired
            .recv_timeout(EVENT_WAIT)
            .expect("watchdog must fire");
        assert!(
            fired_at >= deadline + grace,
            "never before the deadline plus the full grace"
        );
        assert!(
            fired_at <= deadline + grace + WAKE_TOL,
            "enforced within deadline + grace + scheduling tolerance"
        );
    }

    #[test]
    fn watchdog_disarm_before_the_deadline_prevents_enforcement() {
        let (watchdog, expired) = expiry_probe(Duration::from_millis(50));
        let deadline = Instant::now() + Duration::from_secs(2);
        watchdog.arm(deadline);
        watchdog.disarm();
        assert!(
            Instant::now() < deadline,
            "precondition: disarmed before the deadline"
        );
        // A no-op disarm would fire at deadline + 50 ms; wait well past that.
        assert!(
            expired.recv_timeout(Duration::from_millis(2_600)).is_err(),
            "a disarmed deadline never fires"
        );
    }

    #[test]
    fn watchdog_grace_holds_the_kill_and_a_disarm_inside_it_wins() {
        // Grace far longer than the observation windows makes the race deterministic: the
        // deadline passes, the watchdog is inside its grace, and nothing may fire.
        let (watchdog, expired) = expiry_probe(Duration::from_secs(30));
        let deadline = Instant::now() + Duration::from_millis(20);
        watchdog.arm(deadline);
        std::thread::sleep(Duration::from_millis(100));
        assert!(Instant::now() > deadline, "the deadline has passed");
        assert!(
            expired.recv_timeout(Duration::from_millis(300)).is_err(),
            "the grace holds the kill after the deadline"
        );
        watchdog.disarm();
        assert!(
            expired.recv_timeout(Duration::from_millis(300)).is_err(),
            "a disarm inside the grace window wins"
        );
    }

    // ---- hot-path wake elision: independent verification (hard deadlines are unchanged) ----

    fn notifications(watchdog: &ProcessExitWatchdog) -> usize {
        watchdog
            .state
            .notifications
            .load(std::sync::atomic::Ordering::SeqCst)
    }

    #[test]
    fn a_notified_but_not_yet_running_thread_is_not_notified_again() {
        // Root cause of the formerly flaky wake-count test: after `arm` notified an idle thread,
        // `sleep` stayed `Idle` until the thread re-took the lock, so every arm in that window
        // notified again (13-64 redundant wake-ups under scheduling pressure). The test holds
        // the state lock so the thread cannot run between the arms.
        let (watchdog, _expired) = expiry_probe(Duration::from_millis(50));
        let deadline = Instant::now() + Duration::from_secs(5);
        let deadline_wait = Instant::now() + EVENT_WAIT;
        while watchdog.state.inner.lock().unwrap().sleep != Sleep::Idle {
            assert!(Instant::now() < deadline_wait, "thread never parked");
            std::thread::yield_now();
        }
        let mut inner = watchdog.state.inner.lock().unwrap();
        for step in 0..100 {
            watchdog.arm_locked(&mut inner, deadline + Duration::from_millis(step));
            inner.armed = None; // the per-input disarm
        }
        assert_eq!(notifications(&watchdog), 1, "one wake for the idle thread");
    }

    #[test]
    fn per_input_arm_disarm_cycles_do_not_wake_the_watchdog_thread() {
        let (watchdog, expired) = expiry_probe(Duration::from_millis(50));
        // Park the thread on a timed wait first (condition-based, no sleeps): from then on a
        // later deadline never needs a wake and a disarm never wakes, so no cycle adds a wake. (Without this step the thread may legitimately observe
        // a disarmed state mid-loop, park idle and need one more wake per observation, which
        // made the bound scheduling-dependent.)
        watchdog.arm(Instant::now() + Duration::from_secs(5));
        let parked_by = Instant::now() + EVENT_WAIT;
        while !matches!(watchdog.state.inner.lock().unwrap().sleep, Sleep::Until(_)) {
            assert!(
                Instant::now() < parked_by,
                "thread never parked on the deadline"
            );
            std::thread::yield_now();
        }
        watchdog.disarm();
        let baseline = notifications(&watchdog);
        for _ in 0..20_000 {
            watchdog.arm(Instant::now() + Duration::from_secs(5));
            watchdog.disarm();
        }
        assert_eq!(
            notifications(&watchdog),
            baseline,
            "elided: {} extra wake-ups for 20,000 inputs",
            notifications(&watchdog) - baseline
        );
        assert!(expired.try_recv().is_err(), "nothing was enforced");
    }

    #[test]
    fn an_earlier_deadline_after_elided_cycles_still_fires_on_time() {
        // The thread is parked until an old, far deadline; a new deadline that is EARLIER than
        // that must wake it (the one case where a wake is required).
        for round in 0..20u64 {
            let grace = Duration::from_millis(40);
            let (watchdog, expired) = expiry_probe(grace);
            for _ in 0..(round * 50) {
                watchdog.arm(Instant::now() + Duration::from_secs(30));
                watchdog.disarm();
            }
            watchdog.arm(Instant::now() + Duration::from_secs(30));
            watchdog.disarm();
            let deadline = Instant::now() + Duration::from_millis(60);
            watchdog.arm(deadline);
            let fired_at = expired
                .recv_timeout(EVENT_WAIT)
                .expect("an earlier deadline must still be enforced");
            assert!(fired_at >= deadline + grace, "round {round}: never early");
            assert!(
                fired_at <= deadline + grace + WAKE_TOL,
                "round {round}: not late"
            );
        }
    }

    #[test]
    fn a_later_rearm_after_a_disarm_does_not_fire_at_the_stale_deadline() {
        let grace = Duration::from_millis(40);
        let (watchdog, expired) = expiry_probe(grace);
        let stale = Instant::now() + Duration::from_millis(150);
        watchdog.arm(stale);
        watchdog.disarm();
        let later = Instant::now() + Duration::from_millis(500);
        watchdog.arm(later);
        let fired_at = expired
            .recv_timeout(EVENT_WAIT)
            .expect("the later deadline fires");
        assert!(
            fired_at >= later + grace,
            "must not fire at the stale deadline or before the new one plus grace"
        );
        assert!(fired_at <= later + grace + WAKE_TOL);
    }

    #[test]
    fn concurrent_arm_disarm_churn_never_loses_the_final_enforcement() {
        // Several threads hammer arm/disarm (later deadlines only); then ONE final arm is left
        // standing: it must fire within deadline + grace + tolerance, every round.
        for round in 0..10 {
            let grace = Duration::from_millis(30);
            let (watchdog, expired) = expiry_probe(grace);
            let churners: Vec<_> = (0..3)
                .map(|_| {
                    let watchdog = Arc::clone(&watchdog);
                    std::thread::spawn(move || {
                        for _ in 0..3_000 {
                            watchdog.arm(Instant::now() + Duration::from_secs(20));
                            watchdog.disarm();
                        }
                    })
                })
                .collect();
            for churner in churners {
                churner.join().unwrap();
            }
            assert!(
                expired.try_recv().is_err(),
                "round {round}: churn never fires"
            );
            let deadline = Instant::now() + Duration::from_millis(50);
            watchdog.arm(deadline);
            let fired_at = expired
                .recv_timeout(EVENT_WAIT)
                .expect("final arm enforced");
            assert!(fired_at >= deadline + grace, "round {round}: never early");
            assert!(
                fired_at <= deadline + grace + WAKE_TOL,
                "round {round}: not late"
            );
        }
    }

    #[test]
    fn rearming_replaces_the_deadline() {
        let (watchdog, expired) = expiry_probe(Duration::from_millis(20));
        watchdog.arm(Instant::now() + Duration::from_millis(200));
        let later = Instant::now() + Duration::from_millis(900);
        watchdog.arm(later);
        let fired_at = expired.recv_timeout(EVENT_WAIT).expect("fires");
        assert!(
            fired_at >= later,
            "the replaced earlier deadline never fires"
        );
    }

    // ---- call chronology: armed before the backend can be touched ----------------------

    type Log = Arc<Mutex<Vec<&'static str>>>;

    struct LogBackend(Log);

    impl Backend for LogBackend {
        fn normalize_target(&self, _hint: &str) -> Option<NormalizedTarget> {
            None
        }
        fn start_scan(&mut self, _scan: Token, _service_uuid: &str) {}
        fn stop_scan(&mut self, _scan: Token) {}
        fn connect(&mut self, _conn: Token, _target: &Target) {}
        fn write_fragments(&mut self, _conn: Token, _send: Token, _frames: Vec<Payload>) {}
        fn disconnect(&mut self, _conn: Token) {}
        fn shutdown(&mut self) {
            self.0.lock().unwrap().push("backend.shutdown");
        }
    }

    struct LogWatchdog(Log);

    impl HangWatchdog for LogWatchdog {
        fn arm(&self, _deadline: Instant) {
            self.0.lock().unwrap().push("arm");
        }
        fn disarm(&self) {
            self.0.lock().unwrap().push("disarm");
        }
    }

    fn log_fixture() -> (Log, Core<LogBackend>) {
        let log: Log = Arc::default();
        let (_events, receiver) = BackendEvents::new();
        let core = Core::new(
            LogBackend(Arc::clone(&log)),
            OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES),
            CoreConfig {
                bridge_version: "runtime-unit/0".into(),
                platform: "test",
                ingress: receiver,
            },
        );
        (log, core)
    }

    fn chronology(inputs: Vec<Input>) -> Vec<&'static str> {
        let (log, mut core) = log_fixture();
        let watchdog = LogWatchdog(Arc::clone(&log));
        let (mut exit_deadline, mut shutdown_deadline) = (None, None);
        for input in inputs {
            let _ = handle_input(
                &mut core,
                input,
                &watchdog,
                &mut exit_deadline,
                &mut shutdown_deadline,
            );
        }
        log.lock().unwrap().clone()
    }

    const HELLO: &str =
        r#"{"envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]}"#;
    const BYE: &str =
        r#"{"envelope_version":1,"api_version":1,"type":"shutdown","request_id":"bye"}"#;

    #[test]
    fn terminal_eof_arms_before_the_backend_release_submission() {
        let log = chronology(vec![frame(HELLO), Input::StdinEof]);
        let arm = log.iter().rposition(|e| *e == "arm").expect("armed");
        let shutdown = log
            .iter()
            .position(|e| *e == "backend.shutdown")
            .expect("terminal handling submits the release");
        assert!(arm < shutdown, "arm precedes backend.shutdown: {log:?}");
    }

    #[test]
    fn accepted_shutdown_arms_before_its_backend_release_submission() {
        let log = chronology(vec![frame(HELLO), frame(BYE)]);
        let shutdown = log
            .iter()
            .position(|e| *e == "backend.shutdown")
            .expect("accepted shutdown submits the release");
        let arm = log[..shutdown]
            .iter()
            .rposition(|e| *e == "arm")
            .expect("armed before the submission");
        assert!(arm < shutdown, "{log:?}");
        assert!(
            !log[shutdown..].contains(&"disarm"),
            "a pending release stays armed: {log:?}"
        );
    }
}
