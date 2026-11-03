//! Ordered callback admission for the platform adapters (OTP-24 Task 6 review fix).
//!
//! The design requires "scan start before discoveries" and "connected before messages". OS
//! callbacks (advertisement, early watcher `Stopped`, GATT notification, connection-status,
//! capacity) are registered *before* the adapter may announce `ScanStarted` / `Connected`, so
//! they can fire first. [`AdmissionGate`] serializes all of them with that one opening event:
//!
//! - **Pending**: callbacks are staged in a small bounded buffer (never emitted, so the core
//!   never sees an event for a scan/link it has not been told is live);
//! - **`open(first, on_overflow)`**: under the same lock the opening event is emitted first,
//!   then the staged callbacks in arrival order, then the gate is open and later callbacks emit
//!   directly (still under the lock, so a callback racing the flush lands behind it);
//! - **Overflow fails closed**: a stream fragment that does not fit the staging bound is never
//!   silently dropped and then followed by later bytes — the overflow is latched and `open`
//!   emits the opening event followed by `on_overflow` (a link failure) instead of the staged
//!   events. Advertisements beyond the bound are dropped (discovery is lossy by design);
//!   control events beyond their small bound latch the same overflow;
//! - **`abort()`**: the setup failed or was cancelled — staged events (and their payloads) are
//!   discarded and nothing further is emitted, with one exception: a *terminal* of an announced
//!   operation that a callback had already deferred into the lane (acknowledged `Accepted`)
//!   during the close is committed once, with its own failure classification, never
//!   discarded (`abort`/`terminate` hold the gate, so such a deferral is applied by the settle
//!   step after the phase is `Closed`).
//!
//! Emission under the lock is the non-blocking ingress `emit` (spin-hinted `try_lock`;
//! contended events go to its ordered lane), so a callback never waits on anything but this
//! gate's short critical section, which is bounded by the staging caps below. Pure Rust,
//! shared by `winrt.rs` (and the macOS adapter), tested on any host against the real ingress.
//!
//! **Contention is not a link failure** (OTP-24 review follow-up #2). WinRT may deliver GATT
//! notifications on several threadpool threads, and `open`/`terminate` legitimately hold the
//! gate while they emit into the ingress, so a callback regularly finds the gate busy. After
//! [`CALLBACK_LOCK_ATTEMPTS`] spin-hinted attempts it hands the event to the gate's own
//! **bounded, strictly ordered lane** (a fixed-capacity `sync_channel`, `try_send` only) and
//! returns [`Push::Accepted`]. Whoever next holds the gate commits the lane first, in order,
//! through the same staging/emission code a direct callback uses — and *every* holder, callback
//! or worker, settles after releasing: it re-checks a `dirty` flag (SeqCst fences on both
//! sides) and drains again if a deferral raced its release, so a deferred event can never be
//! stranded with nobody left to commit it. Per-callback-thread FIFO is exact (a thread's next
//! callback drains its own earlier deferral first); nothing is lost; memory is bounded by the
//! existing staging caps ([`STAGED_DATA_EVENTS_MAX`] fragments / [`STAGED_DATA_BYTES_MAX`]
//! bytes, [`STAGED_CONTROL_EVENTS_MAX`] controls), which are the lane's capacity. Only a lane
//! that is genuinely **full** (the holder is stuck while the radio keeps delivering) fails
//! closed with [`Push::Failed`] — backpressure on a bounded queue, not contention.
//! Advertisements stay lossy and are dropped when the gate is busy.
//!
//! **After a genuine lane overflow (open phase).** The dropped event leaves a hole in the
//! stream, so nothing of the link may follow it. The gate latches the gap, fails the link in
//! the sink *immediately* ([`EventSink::fail_link`] — for the real ingress a lock-free failure
//! marker, so a worker blocked inside an OS write cannot delay the report) and, while open,
//! erases every later fragment and non-terminal control. This is deliberately conservative: it
//! also drops events that were deferred *before* the gap but are only committed afterwards
//! (the lane has no sequence numbers to tell them apart); fail-closed beats a possibly
//! reordered prefix, and the ingress marker truncates pre-gap work the same way. The marker is
//! the link's loss, so the worker's later `terminate` adds no second `LinkLost`. A gap that
//! lands while `open` is still flushing (phase not yet `Open`) is not reported immediately; it
//! is still fenced here and ended by the worker's `Command::Fail`/terminate.
//!
//! **Coupling note.** A terminal committed from the lane counts as delivered when the ingress
//! accepts or defers it; if the deferred commit later hits `ControlOverflow`, the gate's
//! `process_failed` is not marked — the ingress' sticky `control_overflow` latch fails the
//! process first (`core.rs` checks it on every input and `ShutdownComplete` cannot overtake the
//! overflowed link's zone). Changing that latch policy requires revisiting this.

use std::ops::{Deref, DerefMut};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering, fence};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Mutex, MutexGuard, TryLockError};

use crate::backend::{BackendEvent, Failure, Token};
use crate::cleanup::CleanupOutcome;
use crate::ingress::{
    BackendEvents, DATA_BYTES_PER_LINK_MAX, DATA_EVENTS_PER_LINK_MAX, EmitStatus,
};

/// Staged data events (fragments + advertisements) before `open`: the per-link ingress event
/// ceiling (48), so a burst the ingress would accept is never refused earlier by the gate.
pub const STAGED_DATA_EVENTS_MAX: usize = DATA_EVENTS_PER_LINK_MAX;
/// Staged fragment bytes before `open`: the per-link ingress byte ceiling (16 KiB).
pub const STAGED_DATA_BYTES_MAX: usize = DATA_BYTES_PER_LINK_MAX;
/// Staged control events (`LinkLost`, `CapacityChanged`, `ScanEnded`, ...) before `open`.
pub const STAGED_CONTROL_EVENTS_MAX: usize = 8;

/// Bounded non-parking attempts a callback makes to take the gate before it defers to the lane.
pub const CALLBACK_LOCK_ATTEMPTS: usize = 64;

/// What a callback's [`AdmissionGate::push`] did with its event.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Push {
    /// Staged or emitted (or deliberately ignored after the gate closed).
    Accepted,
    /// A lossy advertisement was dropped (full staging or contended gate): discovery is lossy
    /// by design, nothing else is affected.
    Dropped,
    /// A non-droppable event (stream fragment, loss, capacity, scan end) could not be admitted
    /// in order because the gate's bounded lane is **full** (not mere contention): the event
    /// was dropped and the failure latched ([`AdmissionGate::contention_failed`]). The caller
    /// must wake its worker, which fails the link/scan closed instead of continuing with a
    /// gapped stream.
    Failed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase {
    Pending,
    Open,
    Closed,
}

struct State {
    /// The consuming end of the callback lane; only touched under the gate lock.
    lane_rx: Receiver<BackendEvent>,
    phase: Phase,
    staged: Vec<BackendEvent>,
    data_events: usize,
    data_bytes: usize,
    control_events: usize,
    overflowed: bool,
    /// The opening event has been emitted (the scan/link was announced).
    announced: bool,
    /// A terminal event (`ScanEnded`, `ScanFailed`, `ConnectFailed`, `LinkLost`) was ACCEPTED
    /// by the ingress (`EmitStatus::Accepted`, or `Deferred` = accepted into the ingress' ordered in-transit lane — a rejected emission is not delivery):
    /// a later panic/contention terminal is suppressed so the operation never ends twice.
    terminal_emitted: bool,
    /// The ingress rejected a terminal (the first rejection is kept). It is never retried by
    /// this gate: `StaleToken`/`Shutdown` mean the core no longer tracks the operation (nothing
    /// to deliver); any other status is a process-level failure (see `process_failed`).
    terminal_rejected: Option<EmitStatus>,
}

fn is_terminal(event: &BackendEvent) -> bool {
    matches!(
        event,
        BackendEvent::ScanEnded { .. }
            | BackendEvent::ScanFailed { .. }
            | BackendEvent::ConnectFailed { .. }
            | BackendEvent::LinkLost { .. }
    )
}

/// The link an event belongs to (scan events and advertisements have none).
fn link_of(event: &BackendEvent) -> Option<Token> {
    match event {
        BackendEvent::Fragment { conn, .. }
        | BackendEvent::WriteDone { conn, .. }
        | BackendEvent::WriteFailed { conn, .. }
        | BackendEvent::CapacityChanged { conn, .. }
        | BackendEvent::LinkLost { conn } => Some(*conn),
        _ => None,
    }
}

/// Where a gate sends its events: the real non-blocking ingress producer in production, a
/// scripted sink in the failure-injection tests (same gate code, forced `EmitStatus`).
pub trait EventSink: Send + Sync {
    fn emit(&self, event: BackendEvent) -> EmitStatus;

    /// The gate lost an in-order event of link `conn` (lane overflow): fail the link in the
    /// sink immediately instead of waiting for the adapter's worker. Default: nothing (a
    /// scripted sink that only records emissions).
    fn fail_link(&self, _conn: Token) {}
}

impl EventSink for BackendEvents {
    fn emit(&self, event: BackendEvent) -> EmitStatus {
        BackendEvents::emit(self, event)
    }

    fn fail_link(&self, conn: Token) {
        BackendEvents::fail_link(self, conn);
    }
}

pub struct AdmissionGate {
    sink: Arc<dyn EventSink>,
    /// The worker's sticky cleanup record: a terminal the ingress rejected for a process-level
    /// reason marks it failed, so the link/scan is never certified as released.
    outcome: Option<Arc<CleanupOutcome>>,
    /// A terminal was rejected by the ingress for a process-level reason (`LockBusy` = poisoned lock,
    /// `ControlOverflow`, ...): sticky, readable without any lock.
    process_failed: AtomicBool,
    state: Mutex<State>,
    /// The callback lane's producing end (`try_send` only).
    lane_tx: SyncSender<BackendEvent>,
    /// Packed (events, bytes) of fragments currently in the lane.
    lane_data: AtomicU64,
    /// Control events currently in the lane.
    lane_controls: AtomicUsize,
    /// A deferral may be waiting in the lane: re-checked by every holder after it releases.
    dirty: AtomicBool,
    /// The lane was full and a non-droppable event was dropped; sticky and readable without any
    /// lock.
    contended: AtomicBool,
    /// Lock-free mirror of `phase == Open`: the link is announced and not yet terminated, so an
    /// immediate sink-side failure marker is meaningful (Pending gates fail through `open`;
    /// Closed ones already ended).
    live: AtomicBool,
    /// A lane gap was reported to the sink as an immediate link failure; that marker *is* the
    /// link's loss, so [`AdmissionGate::terminate`] does not emit a second one.
    marker_sent: AtomicBool,
}

/// The worker-side guard: commits the callback lane before handing out the state and settles
/// (re-checks for a deferral that raced the release) when dropped.
struct GateGuard<'a> {
    gate: &'a AdmissionGate,
    guard: Option<MutexGuard<'a, State>>,
}

impl Deref for GateGuard<'_> {
    type Target = State;
    fn deref(&self) -> &State {
        self.guard.as_ref().expect("held")
    }
}

impl DerefMut for GateGuard<'_> {
    fn deref_mut(&mut self) -> &mut State {
        self.guard.as_mut().expect("held")
    }
}

impl Drop for GateGuard<'_> {
    fn drop(&mut self) {
        drop(self.guard.take());
        self.gate.settle();
    }
}

fn pack(events: usize, bytes: usize) -> u64 {
    ((events as u64) << 32) | bytes as u64
}

fn unpack(value: u64) -> (usize, usize) {
    ((value >> 32) as usize, (value & 0xFFFF_FFFF) as usize)
}

impl AdmissionGate {
    pub fn new(events: BackendEvents) -> Arc<Self> {
        Self::with_sink(Arc::new(events), None)
    }

    /// A gate whose process-level terminal rejections also mark the worker's cleanup outcome.
    pub fn with_outcome(events: BackendEvents, outcome: Arc<CleanupOutcome>) -> Arc<Self> {
        Self::with_sink(Arc::new(events), Some(outcome))
    }

    /// The general constructor (tests inject a scripted sink here).
    pub fn with_sink(sink: Arc<dyn EventSink>, outcome: Option<Arc<CleanupOutcome>>) -> Arc<Self> {
        let (lane_tx, lane_rx) =
            mpsc::sync_channel(STAGED_DATA_EVENTS_MAX + STAGED_CONTROL_EVENTS_MAX);
        Arc::new(Self {
            sink,
            outcome,
            process_failed: AtomicBool::new(false),
            lane_tx,
            lane_data: AtomicU64::new(0),
            lane_controls: AtomicUsize::new(0),
            dirty: AtomicBool::new(false),
            state: Mutex::new(State {
                lane_rx,
                phase: Phase::Pending,
                staged: Vec::new(),
                data_events: 0,
                data_bytes: 0,
                control_events: 0,
                overflowed: false,
                announced: false,
                terminal_emitted: false,
                terminal_rejected: None,
            }),
            contended: AtomicBool::new(false),
            live: AtomicBool::new(false),
            marker_sent: AtomicBool::new(false),
        })
    }

    /// Emits through the sink and accounts terminals by their actual status: only
    /// `Accepted` is delivery; `StaleToken`/`Shutdown` are recorded as moot (no retry); every
    /// other rejection is a process-level failure — latched here, and recorded in the worker's
    /// cleanup outcome so its release (and the process-wide one) is withheld. Never retried.
    fn emit_event(&self, state: &mut State, event: BackendEvent) -> EmitStatus {
        let terminal = is_terminal(&event);
        let status = self.sink.emit(event);
        if terminal {
            match status {
                EmitStatus::Accepted | EmitStatus::Deferred => state.terminal_emitted = true,
                EmitStatus::StaleToken | EmitStatus::Shutdown => {
                    state.terminal_rejected.get_or_insert(status);
                }
                _ => {
                    state.terminal_rejected.get_or_insert(status);
                    self.process_failed.store(true, Ordering::SeqCst);
                    if let Some(outcome) = &self.outcome {
                        outcome.mark_failed();
                    }
                }
            }
        }
        status
    }

    /// The ingress rejected a terminal for a process-level reason (sticky, lock-free).
    pub fn process_failed(&self) -> bool {
        self.process_failed.load(Ordering::SeqCst)
    }

    /// The first terminal rejection this gate saw, if any (takes the worker-side lock).
    pub fn terminal_rejection(&self) -> Option<EmitStatus> {
        self.lock().terminal_rejected
    }

    /// An accepted terminal was delivered through this gate.
    pub fn terminal_delivered(&self) -> bool {
        self.lock().terminal_emitted
    }

    fn lock(&self) -> GateGuard<'_> {
        // Worker-side (non-callback) paths may wait for the short critical section; a poisoned
        // lock must never panic. Anything deferred while it was busy commits first.
        let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        self.drain_lane(&mut guard);
        GateGuard {
            gate: self,
            guard: Some(guard),
        }
    }

    /// Commits everything callbacks deferred past a busy gate, in arrival order, through the
    /// ordinary staging/emission code. The dirty flag is cleared *before* draining, so a
    /// deferral landing after the drain re-raises it for the holder's settle step.
    fn drain_lane(&self, state: &mut State) {
        // `swap`, not `store`: an RMW reads the latest value in the modification order, so a
        // deferrer's `true` is either cleared *and* its lane item is visible to the drain below,
        // or it stays set for the holder's settle step (formally sound Dekker pairing).
        self.dirty.swap(false, Ordering::SeqCst);
        while let Ok(event) = state.lane_rx.try_recv() {
            match &event {
                BackendEvent::Fragment { bytes, .. } => {
                    self.lane_data
                        .fetch_sub(pack(1, bytes.len()), Ordering::AcqRel);
                }
                _ => {
                    self.lane_controls.fetch_sub(1, Ordering::AcqRel);
                }
            }
            if state.phase == Phase::Closed {
                self.closed_lane_event(state, event);
            } else {
                self.apply(state, event);
            }
        }
    }

    /// A lane item committed after the gate closed (`abort`/`terminate`/overflow close). The lane
    /// acknowledged it `Push::Accepted`, so it must not vanish silently: a **terminal of an
    /// announced operation** that nothing has ended yet is still delivered (once, with its own
    /// failure classification, in lane order). Everything else — stream bytes, controls,
    /// anything for a never-announced operation, a second terminal, an operation already ended
    /// by the immediate link-failure marker — stays erased.
    fn closed_lane_event(&self, state: &mut State, event: BackendEvent) {
        if is_terminal(&event)
            && state.announced
            && !state.terminal_emitted
            && state.terminal_rejected.is_none()
            && !self.marker_sent.load(Ordering::SeqCst)
        {
            self.emit_event(state, event);
        }
    }

    /// After a holder released the gate: if a deferral raced the release, take the gate again
    /// (never waiting — if someone else holds it, *they* settle on their release) and commit.
    /// Dekker-style: the deferring callback publishes `dirty` then fences then tries the lock;
    /// the releasing holder unlocks then fences then reads `dirty` — one of the two always
    /// sees the other, so an event is never left with no committer.
    fn settle(&self) {
        loop {
            fence(Ordering::SeqCst);
            if !self.dirty.load(Ordering::SeqCst) {
                return;
            }
            let mut guard = match self.state.try_lock() {
                Ok(guard) => guard,
                Err(TryLockError::Poisoned(poisoned)) => poisoned.into_inner(),
                Err(TryLockError::WouldBlock) => return,
            };
            self.drain_lane(&mut guard);
            drop(guard);
        }
    }

    /// The contended path: queue the event in the bounded lane (never waits). Advertisements
    /// are lossy; a full lane fails non-droppable events closed.
    fn defer(&self, event: BackendEvent) -> Push {
        let conn = link_of(&event);
        let admitted = match &event {
            BackendEvent::Advertisement { .. } => return Push::Dropped,
            BackendEvent::Fragment { bytes, .. } => {
                let len = bytes.len();
                self.lane_data
                    .fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| {
                        let (events, used) = unpack(value);
                        (events < STAGED_DATA_EVENTS_MAX && used + len <= STAGED_DATA_BYTES_MAX)
                            .then(|| pack(events + 1, used + len))
                    })
                    .is_ok()
            }
            _ => self
                .lane_controls
                .fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
                    (used < STAGED_CONTROL_EVENTS_MAX).then_some(used + 1)
                })
                .is_ok(),
        };
        if !admitted {
            return self.lane_gap(conn);
        }
        let fragment_len = match &event {
            BackendEvent::Fragment { bytes, .. } => Some(bytes.len()),
            _ => None,
        };
        if self.lane_tx.try_send(event).is_err() {
            // Unreachable by the capacity invariant; fail closed rather than lose silently.
            match fragment_len {
                Some(len) => {
                    self.lane_data.fetch_sub(pack(1, len), Ordering::AcqRel);
                }
                None => {
                    self.lane_controls.fetch_sub(1, Ordering::AcqRel);
                }
            }
            return self.lane_gap(conn);
        }
        self.dirty.store(true, Ordering::SeqCst);
        self.settle();
        Push::Accepted
    }

    /// A non-droppable event was lost (full lane). Latches the gap — [`Self::apply`] then fences
    /// every later same-link fragment and control while the gate is open — and fails the link
    /// in the sink right now, so the loss is reported without waiting for the adapter's worker
    /// (which may be blocked inside an OS call).
    fn lane_gap(&self, conn: Option<Token>) -> Push {
        self.contended.store(true, Ordering::SeqCst);
        if let Some(conn) = conn
            && self.live.load(Ordering::SeqCst)
            && !self.marker_sent.swap(true, Ordering::SeqCst)
        {
            self.sink.fail_link(conn);
        }
        Push::Failed
    }

    /// Callback-side acquisition: bounded `try_lock` retries with a spin hint, never parking.
    fn try_lock_bounded(&self) -> Option<MutexGuard<'_, State>> {
        for _ in 0..CALLBACK_LOCK_ATTEMPTS {
            match self.state.try_lock() {
                Ok(state) => return Some(state),
                Err(TryLockError::Poisoned(poisoned)) => return Some(poisoned.into_inner()),
                Err(TryLockError::WouldBlock) => std::hint::spin_loop(),
            }
        }
        None
    }

    /// A callback's event. Staged before `open`, emitted in order after it, dropped once the
    /// gate is closed. **Never blocks**: the gate is taken with bounded `try_lock` attempts; if
    /// it stays busy the event goes to the gate's bounded ordered lane and is committed by
    /// the next holder ([`Push::Accepted`]). Only a full lane fails a non-droppable event
    /// ([`Push::Failed`]), so the worker fails the link/scan closed rather than continuing past
    /// a gap or reordering.
    pub fn push(&self, event: BackendEvent) -> Push {
        let Some(mut state) = self.try_lock_bounded() else {
            return self.defer(event);
        };
        self.drain_lane(&mut state);
        let result = self.apply(&mut state, event);
        drop(state);
        self.settle();
        result
    }

    /// The staging/emission rules for one callback event, under the gate lock.
    fn apply(&self, state: &mut State, event: BackendEvent) -> Push {
        match state.phase {
            Phase::Closed => Push::Accepted,
            Phase::Open => {
                // After a genuine lane overflow the stream has a hole: nothing of the link may
                // follow it. Fragments and non-terminal controls are erased (conservative: this
                // also drops the few events that were deferred *before* the gap but are only
                // committed afterwards — fail-closed beats a possibly reordered prefix). A
                // terminal still passes so the operation ends with its true classification.
                if self.contended.load(Ordering::SeqCst) && !is_terminal(&event) {
                    return Push::Accepted;
                }
                self.emit_event(state, event);
                Push::Accepted
            }
            Phase::Pending => match &event {
                BackendEvent::Fragment { bytes, .. } => {
                    if state.data_events >= STAGED_DATA_EVENTS_MAX
                        || state.data_bytes + bytes.len() > STAGED_DATA_BYTES_MAX
                    {
                        // Stream bytes cannot be dropped and followed by later bytes.
                        state.overflowed = true;
                        return Push::Failed;
                    }
                    state.data_events += 1;
                    state.data_bytes += bytes.len();
                    state.staged.push(event);
                    Push::Accepted
                }
                BackendEvent::Advertisement { .. } => {
                    if state.data_events < STAGED_DATA_EVENTS_MAX {
                        state.data_events += 1;
                        state.staged.push(event);
                        Push::Accepted
                    } else {
                        Push::Dropped
                    }
                }
                _ => {
                    if state.control_events >= STAGED_CONTROL_EVENTS_MAX {
                        state.overflowed = true;
                        return Push::Failed;
                    }
                    state.control_events += 1;
                    state.staged.push(event);
                    Push::Accepted
                }
            },
        }
    }

    /// A callback dropped a non-droppable event because the gate was busy.
    pub fn contention_failed(&self) -> bool {
        self.contended.load(Ordering::SeqCst)
    }

    /// Emits `first`, then the staged callbacks in arrival order, then opens the gate. On a
    /// latched overflow the staged events are discarded and `on_overflow` is emitted after
    /// `first` instead, and the gate closes. Returns `false` (emitting nothing) if the gate was
    /// already aborted.
    pub fn open(&self, first: BackendEvent, on_overflow: Option<BackendEvent>) -> bool {
        self.open_with_hook(first, on_overflow, |_| {})
    }

    /// [`AdmissionGate::open`] with a hook run after the opening event (index 0) and after each
    /// flushed staged event (index 1..): a deterministic injection point for a panic during
    /// the open, which the worker's terminal bookkeeping must survive. Production passes a
    /// no-op.
    #[doc(hidden)]
    pub fn open_with_hook(
        &self,
        first: BackendEvent,
        on_overflow: Option<BackendEvent>,
        mut hook: impl FnMut(usize),
    ) -> bool {
        let mut state = self.lock();
        if state.phase != Phase::Pending {
            return false;
        }
        self.emit_event(&mut state, first);
        state.announced = true;
        hook(0);
        let staged = std::mem::take(&mut state.staged);
        if state.overflowed || self.contended.load(Ordering::SeqCst) {
            drop(staged); // payloads erase themselves
            state.phase = Phase::Closed;
            self.live.store(false, Ordering::SeqCst);
            if let Some(failure) = on_overflow {
                self.emit_event(&mut state, failure);
            }
            return true;
        }
        for (index, event) in staged.into_iter().enumerate() {
            // Accounted before the hook/any later panic: acceptance is what counts.
            self.emit_event(&mut state, event);
            hook(index + 1);
        }
        state.phase = Phase::Open;
        self.live.store(true, Ordering::SeqCst);
        true
    }

    /// Exactly-once terminal for a worker that cannot continue (setup failure, panic — before
    /// or during `open` — or a latched callback failure). `decide(announced)` picks the event:
    /// before the opening event was emitted it should be the pending-operation failure
    /// (`ConnectFailed`/`ScanFailed`), afterwards a safe loss (`LinkLost`/`ScanEnded`); `None`
    /// emits nothing. Staged events are discarded and the gate closes, so a gate that already
    /// terminated (overflow failure, abort) makes this a no-op returning `false`.
    pub fn terminate(&self, decide: impl FnOnce(bool) -> Option<BackendEvent>) -> bool {
        self.terminate_with_hook(decide, || {})
    }

    /// [`AdmissionGate::terminate`] with a hook run while the gate is held and already closed:
    /// the deterministic injection point for a callback that defers an event into the lane
    /// during the close. Production passes a no-op.
    #[doc(hidden)]
    pub fn terminate_with_hook(
        &self,
        decide: impl FnOnce(bool) -> Option<BackendEvent>,
        hook: impl FnOnce(),
    ) -> bool {
        let mut state = self.lock();
        if state.phase == Phase::Closed {
            return false;
        }
        // An operation that already ended through this gate is never ended a second time.
        let event = if state.terminal_emitted
            || state.terminal_rejected.is_some()
            || self.marker_sent.load(Ordering::SeqCst)
        {
            None
        } else {
            decide(state.announced)
        };
        state.phase = Phase::Closed;
        self.live.store(false, Ordering::SeqCst);
        state.staged.clear();
        if let Some(event) = event {
            self.emit_event(&mut state, event);
        }
        hook();
        true
    }

    /// Setup failed or was cancelled: discard everything staged and emit nothing further.
    pub fn abort(&self) {
        self.abort_with_hook(|| {});
    }

    /// [`AdmissionGate::abort`] with a hook run while the gate is held and already closed (see
    /// [`AdmissionGate::terminate_with_hook`]).
    #[doc(hidden)]
    pub fn abort_with_hook(&self, hook: impl FnOnce()) {
        let mut state = self.lock();
        state.phase = Phase::Closed;
        self.live.store(false, Ordering::SeqCst);
        state.staged.clear();
        hook();
    }

    /// Runs `f` while holding the gate (test seam: lets a test park a "slow holder" and observe
    /// that callbacks fail closed instead of waiting).
    #[doc(hidden)]
    pub fn with_gate_held<R>(&self, f: impl FnOnce() -> R) -> R {
        let _held = self.lock();
        f()
    }

    pub fn is_open(&self) -> bool {
        self.lock().phase == Phase::Open
    }
}

// ---- shared terminal decisions (used by the adapters' worker bookkeeping) -------------------

/// The terminal of a link worker that panicked: `ConnectFailed` when the link was never
/// announced (the panic hit before or inside `open`, before `Connected` was emitted), a safe
/// `LinkLost` afterwards. Whether a loss was already *delivered* is tracked by the gate itself
/// (`terminate` suppresses the event then), not by a flag that may only record a staged loss.
pub fn link_panic_terminal(conn: Token, announced: bool) -> Option<BackendEvent> {
    if announced {
        Some(BackendEvent::LinkLost { conn })
    } else {
        Some(BackendEvent::ConnectFailed {
            conn,
            failure: Failure::new("setup_panic", true),
        })
    }
}

/// The terminal of a link whose callback lost a non-droppable event to gate contention (only
/// meaningful once announced; before that `open` already fails the link closed).
pub fn link_contention_terminal(conn: Token, announced: bool) -> Option<BackendEvent> {
    announced.then_some(BackendEvent::LinkLost { conn })
}

/// The one terminal event of a scan worker that panicked: `ScanFailed` while pending, a safe
/// `ScanEnded` failure once started.
pub fn scan_panic_terminal(scan: Token, announced: bool) -> BackendEvent {
    if announced {
        BackendEvent::ScanEnded {
            scan,
            failure: Some(Failure::new("scan_panic", false)),
        }
    } else {
        BackendEvent::ScanFailed {
            scan,
            failure: Failure::new("scan_start", false),
        }
    }
}
