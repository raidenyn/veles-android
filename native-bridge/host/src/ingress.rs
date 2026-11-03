//! Bounded runtime ingress (OTP-24 Task 3): the non-blocking staging between the process's
//! producers — the raw-stdio reader, the writer and the OS BLE backend callbacks — and the
//! deterministic core (later task), plus the reader-side terminal path.
//!
//! Three independent staging zones, each bounded by construction (design: "Numeric bounds and
//! overload"; brief ceilings):
//!
//! - **Input frames** ([`InputFrames`]): at most 64 frames / 256 KiB — the backpressure queue
//!   the reader pushes serialised JSON frames into. The reader holds at most one further
//!   explicitly budgeted local frame (the one being parsed or parked on a full queue); pushes
//!   block instead of staging ahead. EOF/parse/I/O terminals are a separate always-deliverable
//!   flag, never a queue entry, so a full queue can never starve them.
//! - **Backend data** (`Fragment` events): at most 1,536 events / 512 KiB globally and 48 events /
//!   16 KiB per link, each fragment at most 512 bytes (the physical attribute maximum) so no
//!   copy into the queue exceeds the budget. Link data-budget overflow fails that link: the
//!   ingress retains exactly one failure marker (delivered as a synthesized
//!   [`crate::backend::BackendEvent::LinkLost`] at its per-link FIFO position — after the
//!   link's retained data, before controls emitted after the failure; the commit fence is
//!   conservative, so work emitted just before the failure may be truncated or a control
//!   committed behind the marker, never data after the gap) and rejects all subsequent stream
//!   data of that link; other links continue to progress.
//! - **Reserved controls**: every accepted token (scan ≤ 2, connection ≤ 32, request ≤ 64)
//!   reserves its bounded control bookkeeping at admission, before any side effect; controls
//!   are queued in per-token bounded slots, separate from the data budget, so floods of
//!   fragments/advertisements can never displace EOF, cancel, release or completion events.
//!   Send completions reserve their own guaranteed per-link region of that link's FIFO
//!   ([`COMPLETIONS_PER_LINK_MAX`], the global request bound), so every admitted request's
//!   completion is deliverable on any single link regardless of ordinary control traffic,
//!   and request tokens are monotonic and never reused — reservations must be strictly
//!   increasing (a released token's value is retired at the process high-water mark), so a
//!   late stale completion can never be delivered to a same-valued reused reservation.
//!   A control that does not fit its slot is dropped with a process-level control-overflow
//!   latch ([`BackendReceiver::control_overflow`]) — host failure, never a silent loss.
//!   Advertisements coalesce per scan into a bounded latest-wins staging set whose opaque
//!   routing hint (the per-scan dedupe and connect key) is preserved verbatim — the staged
//!   device is rejected rather than rewritten when the hint is unrepresentable at the
//!   protocol ceiling (non-empty, ≤ 256 bytes, no control characters); display labels and
//!   advertisement data (≤ 256 bytes) are safe bounded copies, sanitized separately.
//!
//! Delivery is fair: [`BackendReceiver::pop_event`] serves one event per call in a strict
//! round-robin over the combined delivery zones — every scan's FIFO (its control events
//! and staged discoveries interleaved in exact emission order, so a staged advertisement
//! never trails the scan's terminal) and every connection stream (per-link FIFO; strict
//! round-robin rotation) — with each non-empty zone served within one bounded sweep, so
//! continuously refillable scan staging can never starve connection controls, data, or
//! any other zone (and vice versa). Per-zone FIFO order is preserved exactly (emission
//! order; connected before messages, scan start before discoveries, the failure marker at
//! its failure-time FIFO position), and a pending
//! [`crate::backend::BackendEvent::ShutdownComplete`] delivered only once everything else
//! has been observed. Once shutdown is staged (or delivered), admission is closed:
//! further emissions are rejected as [`EmitStatus::Shutdown`] and nothing new is staged,
//! so the retained work drains in a finite number of pops and the terminal is guaranteed
//! delivery. Terminals are **generation-attributed** (OTP-24 Task 5 review fix): each is
//! stamped with the release generation live at its emission — the coordinator's
//! [`BackendReceiver::claim_release`] (called immediately before each real `Backend::shutdown`
//! submission, on every terminal path: EOF, rejection, exit overflow and the explicit
//! shutdown handshake alike) mints the claimed generation, so only a terminal emitted at
//! the claimed generation can ever confirm that release's cleanup; a stale/pre-existing or
//! already-delivered terminal is delivered to the core but never attributed to a later
//! claim, and no false cleanup confirmation is possible. (The coordinator claims at most
//! once per process; a superseding second release submission reuses the claimed generation,
//! which every post-claim terminal still stamps.)
//!
//! All producers are non-blocking: OS callback context never waits, on queues or on the
//! state mutex — [`BackendEvents::emit`] only ever `try_lock`s it (a handful of spin-hinted
//! attempts, never parking). **Routine contention is not a failure** (OTP-24 whole-change
//! review C1): the core thread and every link/scan producer legitimately share that one
//! mutex, so an emission that cannot take it is handed to a bounded, strictly ordered
//! in-transit lane (a fixed-capacity `std::sync::mpsc::sync_channel`, `try_send` only) and
//! returns the typed [`EmitStatus::Deferred`] (= accepted). Every later lock holder — every
//! producer that wins the mutex and every receiver seam ([`BackendReceiver::pop_event`] and
//! the admission seams) — drains the lane *first*, in emission order, through the same
//! admission code a direct emission uses, so per-link FIFO order is exact (a link's own
//! callbacks are serialised, so an earlier deferred event always precedes its successor) and
//! nothing is lost. Every deferral also wakes the consumer (condvar and the core's park
//! token, see [`CoreWaker`]), and receiver waits are sliced ([`WAIT_SLICE`]) so a deferral
//! can never sleep unnoticed. The lane has an explicit overflow policy and is part of the
//! documented budgets, not extra memory:
//!
//! - **Fragments** reserve the one *global* data budget (1,536 events / 512 KiB — a lock-free
//!   counter shared by committed and in-transit fragments, so the ceiling is exact across
//!   both paths) before entering the lane; per-link budgets are applied when the fragment
//!   commits. A fragment that cannot be reserved (budget exhausted) or is oversize fails *its
//!   link* — recorded in a lock-free failure register and surfaced as the usual single
//!   `LinkLost` marker — never the process.
//! - **Controls and terminals** use [`DEFERRED_CONTROLS_MAX`] dedicated slots of the lane
//!   that data can never occupy, so a fragment flood cannot displace them. Running out of
//!   those is a genuine unrecoverable control failure (not contention): it latches the
//!   process-level [`BackendReceiver::control_overflow`], exactly like a control that
//!   misses its reserved slot.
//! - **Advertisements** are lossy by contract: under contention they are dropped
//!   ([`EmitStatus::AdvertisementDropped`]), never latched.
//! - Only a **poisoned** state mutex (a panicked holder — memory the ingress can no longer
//!   trust) latches the sticky process failure ([`BackendReceiver::emit_lock_failure`],
//!   an atomic outside the mutex) and rejects as [`EmitStatus::LockBusy`].
//!
//! Terminal generation attribution stays exact: the `release_arms` counter is an atomic read
//! at *emission* time (not at commit time), and `claim_release` increments it under the lock.
//! Parked drains are woken by every event that becomes deliverable (`notify_all`) —
//! accepted emissions and the per-link failure-marker staging alike, so a marker on an
//! otherwise empty link wakes a core parked on `pop_event(None)`. Erasure: fragment bytes
//! handed over, rejected on overflow/oversize, or released from a dropped reservation are
//! owned [`crate::buffer::Payload`] buffers whose `Drop` erases them — overflow ownership
//! always returns to a dropping/erasing path by construction.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Condvar, Mutex, OnceLock, TryLockError};
use std::thread::{self, Thread};
use std::time::{Duration, Instant};

use crate::backend::{BackendEvent, Discovered, Token};
use crate::buffer::Payload;

/// Input frame queue ceilings: at most 64 frames in 256 KiB.
pub const INPUT_FRAMES_MAX: usize = 64;
pub const INPUT_BYTES_MAX: usize = 256 * 1024;
/// Global backend data-event ceilings: at most 1,536 events in 512 KiB — exactly
/// [`MAX_RESERVED_CONNECTIONS`] (32) times the per-link ceilings (48 events, 16 KiB), so all
/// 32 links can sit at their own cap simultaneously. These are **ingress staging** ceilings
/// (unconsumed OS notifications), unrelated to the frozen reassembly caps (2 transactions /
/// 8 KiB per peer, 16 / 64 KiB global) and the delivery/send caps. The two ceilings are
/// independent: 512-byte fragments reach the byte ceiling at 1,024 events; small ones reach the
/// event ceiling first (48 * 512 = 24,576 > 16,384, so the per-link byte cap binds first at
/// maximum size too).
pub const DATA_EVENTS_GLOBAL_MAX: usize = 1536;
pub const DATA_BYTES_GLOBAL_MAX: usize = 512 * 1024;
/// Per-link data-event ceilings: at most 48 events in 16 KiB. 48 events is exactly two maximum
/// legal 4,096-byte records at the minimum 185-byte MTU (2 * 24 fragments of 182 bytes ->
/// 8,736 bytes); the byte cap is independent (32 fragments of 512 bytes).
pub const DATA_EVENTS_PER_LINK_MAX: usize = 48;
pub const DATA_BYTES_PER_LINK_MAX: usize = 16 * 1024;
/// Physical fragment ceiling: no fragment frame is copied into the queues above 512 bytes
/// (the ATT attribute maximum of the shared fragment budget).
pub const FRAGMENT_FRAME_MAX: usize = 512;
/// Reserved control bookkeeping ceilings, enforced before any side effect.
pub const MAX_RESERVED_SCANS: usize = 2;
pub const MAX_RESERVED_CONNECTIONS: usize = 32;
pub const MAX_RESERVED_REQUESTS: usize = 64;
/// Per-slot control FIFO ceilings (scan slot, connection stream ordinary-control tail; send
/// completions are counted in their own guaranteed region instead —
/// [`COMPLETIONS_PER_LINK_MAX`]).
pub const CONTROL_EVENTS_PER_SCAN_MAX: usize = 8;
pub const CONTROL_EVENTS_PER_LINK_MAX: usize = 32;
/// Per-link send-completion capacity, guaranteed separately from
/// [`CONTROL_EVENTS_PER_LINK_MAX`]: send completions ([`BackendEvent::WriteDone`] /
/// [`BackendEvent::WriteFailed`]) stage in their own bounded region of the per-link FIFO,
/// sized to the global outstanding-request bound — at most 64 request tokens are admitted
/// process-wide, so a legitimate one-completion-per-token burst on any single link always
/// fits and every admitted request's completion has guaranteed delivery, regardless of how
/// many ordinary link controls are queued.
pub const COMPLETIONS_PER_LINK_MAX: usize = MAX_RESERVED_REQUESTS;
/// Discovery staging: at most 64 distinct devices per scan (latest observation wins, new
/// devices beyond the bound are dropped) — aligned with the core's 64-discoveries-per-second
/// ceiling; the core keeps its own bounded cache.
pub const DISCOVERY_STAGING_MAX: usize = 64;
/// Safe bounded metadata: the protocol hint ceiling (opaque hints are preserved verbatim or
/// the device is rejected, never rewritten), and display labels cap at this byte bound with
/// control characters removed; advertisement data is bounded at the binding protocol maximum
/// of 256 bytes.
pub const META_STRING_MAX: usize = 256;
/// Advertisements keep the protocol's binding maximum: up to 256 bytes per staged device.
pub const META_ADVERTISEMENT_MAX: usize = 256;
/// Hard cap on simultaneously staged process-wide terminals: one pre-claim stray (all
/// generation-0 strays collapse into a single entry) plus at most one per release
/// submission (at most two per process: the handshake's release, and at most one
/// superseding terminal release — `finish` is exit-sticky, `on_shutdown_complete` never
/// resubmits). Exceeding it latches [`BackendReceiver::control_overflow`] — fail closed,
/// never a silent loss that could mask stale attribution.
pub const SHUTDOWN_TERMINALS_MAX: usize = 3;
/// In-transit lane slots reserved for controls/terminals emitted while the state mutex is
/// contended (see the module docs); data can never occupy them. Far above any plausible
/// number of events a producer can emit during one lock hold, yet tiny and fixed.
pub const DEFERRED_CONTROLS_MAX: usize = 256;
/// Total lane capacity: every in-transit fragment (bounded by the global data-event ceiling)
/// plus the dedicated control slots. `try_send` therefore never sees a full lane unless an
/// accounting invariant is broken (which is handled as an overflow, not a panic).
const LANE_CAPACITY: usize = DATA_EVENTS_GLOBAL_MAX + DEFERRED_CONTROLS_MAX;
/// Lock-free link-failure register size: distinct links that may be failed without the state
/// lock before it is drained (live links are at most [`MAX_RESERVED_CONNECTIONS`]; the rest
/// is slack for released-but-undrained tokens).
const FAILURE_SLOTS: usize = 2 * MAX_RESERVED_CONNECTIONS;
/// Spin-hinted `try_lock` attempts of one emission before it is deferred to the lane.
const EMIT_LOCK_ATTEMPTS: usize = 16;
/// Receiver condvar waits are sliced to this bound so a deferral can never sleep unnoticed.
pub const WAIT_SLICE: Duration = Duration::from_millis(10);

/// Wakes the one core thread out of its idle park from every seam it must react to — the
/// stdin reader, the backend ingress and the stdout writer's failure notice. It is an unpark
/// token (`std::thread::Thread::unpark`): non-blocking, safe from any OS callback, and
/// level-triggered (an unpark that races ahead of the park makes the park return at once), so
/// no wake is ever lost and the core's idle wait is never stdin-only.
#[derive(Default)]
pub struct CoreWaker {
    thread: OnceLock<Thread>,
}

impl CoreWaker {
    /// Binds the calling thread as the one to wake. First bind wins.
    pub fn bind_current(&self) {
        let _ = self.thread.set(thread::current());
    }

    /// Wakes the bound thread (no-op before a bind). Never blocks.
    pub fn wake(&self) {
        if let Some(thread) = self.thread.get() {
            thread.unpark();
        }
    }
}

/// How the raw-stdio reader ended. Distinct terminal kinds per the design ("Keep EOF,
/// truncated frame, I/O failure, output blockage and explicit shutdown distinct"): every strict
/// framing rejection before/inside a payload reads as [`InputTerminal::TruncatedFrame`]; only
/// an OS read failure is [`InputTerminal::IoError`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputTerminal {
    Eof,
    TruncatedFrame,
    IoError,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputPushError {
    /// The frame does not fit the bounded queue (or no capacity freed before the timeout).
    /// The pushed payload is consumed by the push call and dropped here (its bytes are
    /// erased): the caller does NOT retain ownership and must re-parse its frame source for
    /// a retry — the queue itself is left intact.
    Overflow,
    /// The reader has already reported its terminal; no frame may follow it.
    TerminalSet,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum InputPop {
    Frame(Payload),
    /// Input is done: a kind is delivered after every queued frame has drained, repeatedly
    /// (the flag persists until the core consumes it and exits).
    Terminal(InputTerminal),
    Timeout,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EmitStatus {
    Accepted,
    /// A fragment was rejected on a data-budget overflow (per-link or global); the emitting
    /// link retains exactly one failure marker. Ownership returned to the dropping path.
    DataOverflow,
    /// A fragment above [`FRAGMENT_FRAME_MAX`] was rejected before any copy (its bytes are
    /// erased); the link is deterministically failed with exactly one retained marker, so
    /// the core sees a guaranteed terminal signal and no later data of that link is admitted
    /// as though the stream were intact.
    FragmentOversize,
    /// A control did not fit its bounded slot and was dropped; the process-level control
    /// overflow was latched ([`BackendReceiver::control_overflow`]).
    ControlOverflow,
    /// An event for a token with no reserved bookkeeping (never accepted, released, or
    /// cancelled): dropped by design, no allocation, no marker.
    StaleToken,
    /// An advertisement did not fit the bounded per-scan staging set (full or unreserved scan).
    AdvertisementDropped,
    /// The backend has already reported process shutdown (terminal pending or delivered),
    /// so admission is closed: the event is consumed and dropped unstaged, and the
    /// terminal is never postponed by further traffic.
    Shutdown,
    /// The ingress state lock is **poisoned** (a holder panicked): the event is consumed and
    /// dropped unstaged — its payload erases itself — and a sticky process-level failure is
    /// latched outside the mutex ([`BackendReceiver::emit_lock_failure`], observable without
    /// ever taking the lock). Ordinary contention is never this status: it is
    /// [`EmitStatus::Deferred`].
    LockBusy,
    /// The state lock was busy (routine contention), so the event was accepted into the
    /// bounded in-order lane and will be committed by the next lock holder, in emission
    /// order, through the normal admission rules. Counts as accepted for the caller; a commit
    /// that later finds the token released simply drops it as stale.
    Deferred,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReserveError {
    /// The bounded reservation set for that kind is full (2 scans / 32 connections / 64
    /// outstanding requests).
    Bounded,
    /// The request token is not strictly newer than the process request-token high-water
    /// mark: the value was already retired (reserved before and since released, superseded
    /// by a later allocation, or passed to a release) or is out of the required monotonic
    /// order. Request tokens are never reused, so a retired value can never collide with
    /// its own late stale completions.
    Reused,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BackendPop {
    Event(BackendEvent),
    Timeout,
}

/// One staged entry of a scan's FIFO: a scan-scoped control event, or a staged discovery
/// awaiting emission as an [`crate::backend::BackendEvent::Advertisement`]. A single FIFO, so
/// per-scan emission order is exact (a discovery staged before the scan's terminal never
/// delivers after it).
enum ScanEntry {
    Control(BackendEvent),
    Discover(Discovered),
}

/// One staged entry of a link's FIFO: a queued fragment or link-scoped control, or the
/// failure marker occupying its real FIFO position (the stream data admitted before the
/// failure stays ahead of it; controls emitted after it stay behind). The marker is not a
/// control: it consumes no reserved [`CONTROL_EVENTS_PER_LINK_MAX`] slot.
enum StreamEntry {
    Event(BackendEvent),
    FailureMarker,
}

struct ScanSlot {
    /// Scan-scoped FIFO in emission order: control events (start / failed / ended) and
    /// staged discoveries in the order they were created/replaced.
    stream: VecDeque<ScanEntry>,
    /// Bound for [`ScanEntry::Control`] entries ([`CONTROL_EVENTS_PER_SCAN_MAX`]); the
    /// marker-free scan data (discoveries) is bounded separately.
    ctl_events: usize,
    discoveries: usize,
}

struct ConnSlot {
    /// Per-link FIFO in emission order: fragment data plus link-scoped controls, plus the
    /// failure marker at its failure-time position.
    stream: VecDeque<StreamEntry>,
    data_events: usize,
    data_bytes: usize,
    ctl_events: usize,
    /// Queued send completions ([`StreamEntry::Event`] entries carrying
    /// `BackendEvent::WriteDone`/`WriteFailed`), accounted separately from `ctl_events` in
    /// their own guaranteed bounded region — every admitted request's completion fits the
    /// link ([`COMPLETIONS_PER_LINK_MAX`]).
    completions: usize,
    /// Set on the first data-budget overflow; persists until the reservation is released.
    data_failed: bool,
    /// Exactly one pending failure marker, held as its real FIFO position in `stream`.
    marker_pending: bool,
}

struct InnerState {
    scans: Vec<(Token, ScanSlot)>,
    conns: Vec<(Token, ConnSlot)>,
    /// Reserved outstanding request tokens (send completions); bounded linear set.
    requests: Vec<Token>,
    /// The request-token high-water mark: every token value the coordinator has sent to
    /// `reserve_request`/`release_request` is retired at this monotonic mark. Quarantine
    /// with one bounded value — no retired-set is kept.
    request_high_water: Option<Token>,
    /// The one global data counter (packed events/bytes of committed AND in-transit
    /// fragments), shared with [`Shared`] so a pop releases it without the producer side.
    global_data: Arc<AtomicU64>,
    /// Staged, undelivered process-wide terminals with their release generation (see
    /// [`BackendReceiver::claim_release`]): non-decreasing over time (generations only
    /// grow), same-generation strays collapse into one entry, and delivery (last, when
    /// every other zone drained) pops one entry per terminal. Bounded by one pre-claim
    /// stray (all generation-0 strays collapse) plus one entry per release submission —
    /// at most two submissions in a process (the handshake's release and at most one
    /// superseding terminal release) — hard-capped at [`SHUTDOWN_TERMINALS_MAX`].
    shutdown_queue: VecDeque<u64>,
    /// Sticky: a process-wide terminal has been delivered (admission is closed for good).
    shutdown_delivered: bool,
    /// The release generation of the most recently delivered terminal: the attribution
    /// key a draining consumer reads (immediately upon handling the delivered terminal,
    /// see [`BackendReceiver::delivered_terminal_gen`]) to distinguish the terminal of
    /// the claimed release it waits for from a stale one.
    delivered_terminal: Option<u64>,
    /// Round-robin cursor over the combined delivery zones (scans in registration order,
    /// then their connection slots) for fair consumption.
    cursor: usize,
    /// The consuming end of the in-transit lane; only ever touched under the state lock.
    lane_rx: Receiver<Deferred>,
}

/// One emission deferred past a contended state lock (see the module docs).
struct Deferred {
    event: BackendEvent,
    /// The release generation live at *emission* (terminals only).
    generation: u64,
    /// A fragment holding a reservation of the global data budget.
    reserved: bool,
    /// Occupies one of the [`DEFERRED_CONTROLS_MAX`] control slots.
    control: bool,
}

/// Packed global data usage: events in the high 32 bits, bytes in the low 32 bits, so one
/// compare-exchange reserves both ceilings atomically and exactly.
fn pack(events: usize, bytes: usize) -> u64 {
    ((events as u64) << 32) | bytes as u64
}

fn unpack(value: u64) -> (usize, usize) {
    ((value >> 32) as usize, (value & 0xFFFF_FFFF) as usize)
}

/// A held reservation of the global data budget; released on drop unless [`Self::keep`]
/// transfers it into the committed accounting.
struct Reservation<'a> {
    counter: &'a AtomicU64,
    bytes: usize,
}

impl Reservation<'_> {
    fn keep(self) {
        std::mem::forget(self);
    }
}

impl Drop for Reservation<'_> {
    fn drop(&mut self) {
        self.counter
            .fetch_sub(pack(1, self.bytes), Ordering::AcqRel);
    }
}

fn reserve_global(counter: &AtomicU64, bytes: usize) -> Option<Reservation<'_>> {
    counter
        .fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| {
            let (events, used) = unpack(value);
            (events < DATA_EVENTS_GLOBAL_MAX && used + bytes <= DATA_BYTES_GLOBAL_MAX)
                .then(|| pack(events + 1, used + bytes))
        })
        .ok()
        .map(|_| Reservation { counter, bytes })
}

struct Shared {
    state: Mutex<InnerState>,
    activity: Condvar,
    /// Sticky process-level failure signal for a poisoned state lock. Deliberately outside
    /// the mutex: independently observable without ever blocking on
    /// ([`BackendReceiver::emit_lock_failure`]).
    lock_failure: AtomicBool,
    /// Sticky process-level failure latch for dropped controls; an atomic so it can be set
    /// (and polled by the core every cycle) without the state lock.
    control_overflow: AtomicBool,
    /// Count of claimed release generations (see [`BackendReceiver::claim_release`]):
    /// incremented under the state lock, read at emission time.
    release_arms: AtomicU64,
    /// Packed (events, bytes) of every committed or in-transit fragment: the exact global
    /// 1,536-event / 512 KiB data ceiling across both paths (`DATA_EVENTS_GLOBAL_MAX` /
    /// `DATA_BYTES_GLOBAL_MAX`).
    global_data: Arc<AtomicU64>,
    /// The producing end of the in-transit lane (non-blocking `try_send` only).
    lane_tx: SyncSender<Deferred>,
    /// Control slots currently occupied in the lane.
    lane_controls: AtomicUsize,
    /// Lock-free register of links to fail at the next drain (0 = empty slot).
    link_failures: [AtomicU64; FAILURE_SLOTS],
    /// Occupied register slots: lets the fragment fences skip the scan when it is empty.
    failures_pending: AtomicUsize,
    /// Wakes the core thread's idle park.
    waker: Arc<CoreWaker>,
    /// Test-only census (`#[cfg(test)]` builds only, never part of the production API):
    /// how many consumers are currently parked in [`BackendReceiver::pop_event`]'s
    /// indefinite wait. The internal wake tests spin on it to prove the drain reached its
    /// park before anything is staged — a deterministic rendezvous, never sleeps.
    #[cfg(test)]
    parked_drains: std::sync::atomic::AtomicUsize,
}

/// Cloneable non-blocking backend event producer: one handle per OS adapter/callback source.
#[derive(Clone)]
pub struct BackendEvents {
    shared: Arc<Shared>,
}

/// The core-side receiver of [`BackendEvents`]: admission reservations, drains and the
/// overflow/terminal observations.
///
/// Both handles of a `Clone`d pair share the one receiver state (`Arc<Shared>`): the
/// coordinator (`crate::core`) keeps one for its **admission seams** (`reserve_*` before any
/// OS side effect, `release_*` at terminals, latch observations) and the process runtime keeps
/// one for its **drain seam** (`pop_event` feeding `core::Input::Backend`) and latch
/// polling. It stays one logical receiver — a single consumer state, single `ShutdownComplete`
/// delivery — and in the production runtime the two handles are used only from the one core
/// thread (drain → `core.handle` → admission), so receiver method calls never race.
#[derive(Clone)]
pub struct BackendReceiver {
    shared: Arc<Shared>,
}

impl Shared {
    /// Wakes every consumer: parked drains (condvar) and the core's idle park.
    fn announce(&self) {
        self.activity.notify_all();
        self.waker.wake();
    }

    fn latch_control_overflow(&self) {
        self.control_overflow.store(true, Ordering::Release);
        self.waker.wake();
    }

    fn latch_lock_failure(&self) {
        self.lock_failure.store(true, Ordering::Release);
        self.waker.wake();
    }

    /// Fails `conn` without the state lock: recorded in the lock-free register, which the
    /// next drain turns into the link's single failure marker. A full register (impossible
    /// while live links ≤ 32 and drains keep running) is a genuine control failure.
    fn fail_link_lock_free(&self, conn: Token) {
        let mut stored = conn == 0;
        if !stored {
            for slot in &self.link_failures {
                let seen = slot.load(Ordering::Acquire);
                if seen == conn {
                    stored = true;
                    break;
                }
                if seen == 0 {
                    // Count first so a fence that reads the counter after observing the slot
                    // can never miss it.
                    self.failures_pending.fetch_add(1, Ordering::SeqCst);
                    if slot
                        .compare_exchange(0, conn, Ordering::SeqCst, Ordering::Acquire)
                        .is_ok()
                    {
                        stored = true;
                        break;
                    }
                    self.failures_pending.fetch_sub(1, Ordering::SeqCst);
                }
            }
        }
        if !stored || conn == 0 {
            self.latch_control_overflow();
        }
        self.announce();
    }

    /// Whether `conn` has a lock-free link failure registered but not yet applied: the fence
    /// that keeps every later fragment of a failed link out (no data after the gap).
    fn link_failure_pending(&self, conn: Token) -> bool {
        self.failures_pending.load(Ordering::SeqCst) != 0
            && self
                .link_failures
                .iter()
                .any(|slot| slot.load(Ordering::SeqCst) == conn)
    }

    /// Commits everything deferred past a contended lock, in emission order, through the
    /// ordinary admission code — then applies lock-free link failures. The marker is placed
    /// after the lane, but note the commit fence (a fragment whose link already has a pending
    /// registration is rejected at commit) is conservative: a lane fragment emitted *before*
    /// the failing one can be rejected too, and a control emitted between the two can then be
    /// committed behind the marker. That only ever fails safe (no data after the gap; a send
    /// that really completed may surface as errored by the disconnect), and "every earlier
    /// emission is ahead of its marker" is therefore a best-effort ordering, not a guarantee.
    fn drain(&self, state: &mut InnerState) {
        while let Ok(item) = state.lane_rx.try_recv() {
            if item.control {
                self.lane_controls.fetch_sub(1, Ordering::AcqRel);
            }
            let held = item.reserved.then(|| match &item.event {
                BackendEvent::Fragment { bytes, .. } => Reservation {
                    counter: &self.global_data,
                    bytes: bytes.len(),
                },
                _ => unreachable!("only fragments hold a data reservation"),
            });
            let _ = self.commit(state, item.event, item.generation, held);
        }
        for slot in &self.link_failures {
            let conn = slot.load(Ordering::SeqCst);
            if conn == 0 {
                continue;
            }
            // Apply first, then clear: the failure is visible in the link state before the
            // lock-free fence stops seeing it, so there is no window without a fence.
            if let Some(index) = state.conns.iter().position(|(t, _)| *t == conn) {
                self.mark_link_failed(state, index);
            }
            slot.store(0, Ordering::SeqCst);
            self.failures_pending.fetch_sub(1, Ordering::SeqCst);
        }
    }

    /// Marks the link failed with exactly one retained marker at its FIFO position
    /// (deliverable exactly now — wakes the parked drains and the core).
    fn mark_link_failed(&self, state: &mut InnerState, index: usize) {
        let slot = &mut state.conns[index].1;
        if slot.data_failed {
            return;
        }
        slot.data_failed = true;
        slot.marker_pending = true;
        slot.stream.push_back(StreamEntry::FailureMarker);
        self.announce();
    }

    /// The admission rules for one event under the state lock — shared by direct emission
    /// and the lane drain. `generation` is the release generation live at emission; `held`
    /// is the global data reservation of a deferred fragment (released on every rejecting
    /// return by drop).
    fn commit(
        &self,
        state: &mut InnerState,
        event: BackendEvent,
        generation: u64,
        held: Option<Reservation<'_>>,
    ) -> EmitStatus {
        match event {
            BackendEvent::ShutdownComplete => {
                // Generation attribution (see the module docs): the terminal is stamped
                // with the release generation live at its emission. Same-generation
                // strays collapse into the staged entry; a terminal at or below the
                // delivered generation is consumed silently (its generation can satisfy
                // nothing later — a pre-existing/previously seen terminal never confirms
                // a later release). The terminal stages even while admission is closed
                // (it is the terminal); only the cap rejects it, fail closed.
                let staged = state
                    .shutdown_queue
                    .back()
                    .is_some_and(|&last| last >= generation)
                    || state
                        .delivered_terminal
                        .is_some_and(|delivered| delivered >= generation);
                if !staged {
                    if state.shutdown_queue.len() >= SHUTDOWN_TERMINALS_MAX {
                        self.latch_control_overflow();
                        return EmitStatus::ControlOverflow;
                    }
                    state.shutdown_queue.push_back(generation);
                    self.announce();
                }
                EmitStatus::Accepted
            }
            // Closed admission (terminal progress): once a process-wide terminal has been
            // staged or delivered, no further work is admitted — scans, advertisements,
            // fragments and controls are all rejected here, so the already-retained
            // (bounded) work drains in a finite number of pops and the terminal is
            // delivered, never postponed by continuing traffic.
            _ if !state.shutdown_queue.is_empty() || state.shutdown_delivered => {
                EmitStatus::Shutdown
            }
            event @ (BackendEvent::ScanStarted { scan }
            | BackendEvent::ScanFailed { scan, .. }
            | BackendEvent::ScanEnded { scan, .. }
            | BackendEvent::ScanReleased { scan }) => {
                let Some((_, slot)) = state.scans.iter_mut().find(|(t, _)| *t == scan) else {
                    return EmitStatus::StaleToken;
                };
                if slot.ctl_events >= CONTROL_EVENTS_PER_SCAN_MAX {
                    self.latch_control_overflow();
                    return EmitStatus::ControlOverflow;
                }
                slot.ctl_events += 1;
                slot.stream.push_back(ScanEntry::Control(event));
                self.announce();
                EmitStatus::Accepted
            }
            BackendEvent::Advertisement { scan, device } => {
                let Some((_, slot)) = state.scans.iter_mut().find(|(t, _)| *t == scan) else {
                    return EmitStatus::AdvertisementDropped;
                };
                let Some(device) = bounded_discovered(device) else {
                    // The opaque routing hint is unrepresentable at the protocol ceiling:
                    // the device is rejected whole, never stage-rewritten.
                    return EmitStatus::AdvertisementDropped;
                };
                if let Some(existing) = slot.stream.iter_mut().find_map(|entry| match entry {
                    ScanEntry::Discover(discovered)
                        if discovered.platform_hint == device.platform_hint =>
                    {
                        Some(discovered)
                    }
                    _ => None,
                }) {
                    // Latest-wins coalescing in place: the staged position (and thus the
                    // per-scan emission order) is preserved, only the metadata is latest.
                    *existing = device;
                    return EmitStatus::Accepted;
                }
                if slot.discoveries >= DISCOVERY_STAGING_MAX {
                    return EmitStatus::AdvertisementDropped;
                }
                slot.discoveries += 1;
                slot.stream.push_back(ScanEntry::Discover(device));
                self.announce();
                EmitStatus::Accepted
            }
            BackendEvent::Fragment { conn, bytes } => {
                self.commit_fragment(state, conn, bytes, held)
            }
            BackendEvent::Connected {
                conn,
                capacity,
                platform_hint,
            } => {
                let Some((_, slot)) = state.conns.iter_mut().find(|(t, _)| *t == conn) else {
                    return EmitStatus::StaleToken;
                };
                if slot.ctl_events >= CONTROL_EVENTS_PER_LINK_MAX {
                    self.latch_control_overflow();
                    return EmitStatus::ControlOverflow;
                }
                slot.ctl_events += 1;
                slot.stream
                    .push_back(StreamEntry::Event(BackendEvent::Connected {
                        conn,
                        capacity,
                        platform_hint: platform_hint.and_then(bounded_connected_hint),
                    }));
                self.announce();
                EmitStatus::Accepted
            }
            event @ (BackendEvent::ConnectFailed { conn, .. }
            | BackendEvent::CapacityChanged { conn, .. }
            | BackendEvent::WriteDone { conn, .. }
            | BackendEvent::WriteFailed { conn, .. }
            | BackendEvent::LinkLost { conn }
            | BackendEvent::LinkReleased { conn }) => {
                if let BackendEvent::WriteDone { send, .. } | BackendEvent::WriteFailed { send, .. } =
                    &event
                    && !state.requests.contains(send)
                {
                    drop(event);
                    return EmitStatus::StaleToken;
                }
                // Send completions reserve the link's own guaranteed completion region;
                // ordinary controls share the 32-slot per-link control tail.
                let completion = matches!(
                    &event,
                    BackendEvent::WriteDone { .. } | BackendEvent::WriteFailed { .. }
                );
                let Some((_, slot)) = state.conns.iter_mut().find(|(t, _)| *t == conn) else {
                    drop(event);
                    return EmitStatus::StaleToken;
                };
                let full = if completion {
                    slot.completions >= COMPLETIONS_PER_LINK_MAX
                } else {
                    slot.ctl_events >= CONTROL_EVENTS_PER_LINK_MAX
                };
                if full {
                    self.latch_control_overflow();
                    drop(event);
                    return EmitStatus::ControlOverflow;
                }
                if completion {
                    slot.completions += 1;
                } else {
                    slot.ctl_events += 1;
                }
                slot.stream.push_back(StreamEntry::Event(event));
                self.announce();
                EmitStatus::Accepted
            }
        }
    }

    /// The bounded data path of one fragment. A link whose data budget overflows is failed
    /// with exactly one retained marker at its real FIFO position: already-queued fragments
    /// of the link stay queued ahead of it in emission order, and controls emitted after the
    /// failure stay behind it; every later fragment of the link is rejected until the
    /// reservation is released. A fragment above the 512-byte physical ceiling is likewise a
    /// guaranteed core-visible link failure: it is rejected before any copy (its bytes are
    /// erased) and the link is marked failed with exactly one retained marker at its FIFO
    /// position, so no later fragment is admitted to a stream the core could reassemble as
    /// intact. `held` is the global reservation a deferred fragment already owns; a direct
    /// one reserves here. Either way the reservation is released on every rejecting path.
    fn commit_fragment(
        &self,
        state: &mut InnerState,
        conn: Token,
        bytes: Payload,
        held: Option<Reservation<'_>>,
    ) -> EmitStatus {
        let Some(index) = state.conns.iter().position(|(t, _)| *t == conn) else {
            return EmitStatus::StaleToken;
        };
        let (link_events, link_bytes, link_failed) = {
            let slot = &state.conns[index].1;
            (slot.data_events, slot.data_bytes, slot.data_failed)
        };
        if link_failed {
            return EmitStatus::DataOverflow;
        }
        if self.link_failure_pending(conn) {
            // A lock-free failure of this link is registered but not applied yet: apply it at
            // this FIFO position now, so nothing of the link commits behind the gap.
            self.mark_link_failed(state, index);
            return EmitStatus::DataOverflow;
        }
        if bytes.len() > FRAGMENT_FRAME_MAX {
            // Deterministically fail the link: the core-visible marker replaces the
            // silent-gap behavior; the rejected payload erases itself here.
            self.mark_link_failed(state, index);
            return EmitStatus::FragmentOversize;
        }
        let link_fits = link_events < DATA_EVENTS_PER_LINK_MAX
            && link_bytes + bytes.len() <= DATA_BYTES_PER_LINK_MAX;
        let reservation = if link_fits {
            held.or_else(|| reserve_global(&self.global_data, bytes.len()))
        } else {
            None
        };
        let Some(reservation) = reservation else {
            // Per-link or global data budget exhausted: fail this link, retain exactly one
            // marker at its FIFO position (it is deliverable exactly now — wake the parked
            // drains), and let the rejected payload erase itself here.
            self.mark_link_failed(state, index);
            return EmitStatus::DataOverflow;
        };
        reservation.keep();
        let slot = &mut state.conns[index].1;
        slot.data_events += 1;
        slot.data_bytes += bytes.len();
        slot.stream
            .push_back(StreamEntry::Event(BackendEvent::Fragment { conn, bytes }));
        self.announce();
        EmitStatus::Accepted
    }
}

impl BackendEvents {
    /// Creates the paired producer and receiver. Exactly one receiver; producers are `Clone`
    /// (an adapter holds its clone for its OS callback path, never blocking on it).
    #[allow(clippy::new_ret_no_self)]
    pub fn new() -> (Self, BackendReceiver) {
        let (lane_tx, lane_rx) = mpsc::sync_channel(LANE_CAPACITY);
        let global_data = Arc::new(AtomicU64::new(0));
        let shared = Arc::new(Shared {
            state: Mutex::new(InnerState {
                scans: Vec::new(),
                conns: Vec::new(),
                requests: Vec::new(),
                request_high_water: None,
                global_data: Arc::clone(&global_data),
                shutdown_queue: VecDeque::new(),
                shutdown_delivered: false,
                delivered_terminal: None,
                cursor: 0,
                lane_rx,
            }),
            activity: Condvar::new(),
            lock_failure: AtomicBool::new(false),
            control_overflow: AtomicBool::new(false),
            release_arms: AtomicU64::new(0),
            global_data,
            lane_tx,
            lane_controls: AtomicUsize::new(0),
            link_failures: std::array::from_fn(|_| AtomicU64::new(0)),
            failures_pending: AtomicUsize::new(0),
            waker: Arc::new(CoreWaker::default()),
            #[cfg(test)]
            parked_drains: std::sync::atomic::AtomicUsize::new(0),
        });
        (
            Self {
                shared: Arc::clone(&shared),
            },
            BackendReceiver { shared },
        )
    }

    /// Routes one backend event: stream data to the bounded per-link/global data queues,
    /// controls to the reserved per-token slots, advertisements to the bounded per-scan
    /// staging set — all non-blocking, all bounded: the callback path never waits on the
    /// state mutex (spin-hinted `try_lock` attempts only), and allocation is only the bounded
    /// staging copies (reserved-or-rejected routing hints, sanitized labels, exact-capacity
    /// advertisement copies, at most one owned fragment payload on admission).
    ///
    /// If the lock stays busy (routine contention with the core thread or another producer)
    /// the event is accepted into the bounded in-order lane as [`EmitStatus::Deferred`] and
    /// committed by the next lock holder — see the module docs for the exact overflow policy
    /// (fragments fail only their link, controls overflow latches
    /// [`BackendReceiver::control_overflow`], advertisements are dropped). Only a poisoned
    /// lock rejects as [`EmitStatus::LockBusy`] with the sticky
    /// [`emit_lock_failure`](BackendReceiver::emit_lock_failure) signal. Once the backend has
    /// reported process shutdown, admission is closed: every non-terminal event is rejected as
    /// [`EmitStatus::Shutdown`] and nothing is staged.
    pub fn emit(&self, event: BackendEvent) -> EmitStatus {
        let shared = &self.shared;
        for _ in 0..EMIT_LOCK_ATTEMPTS {
            match shared.state.try_lock() {
                Ok(mut state) => {
                    // Everything deferred earlier commits first: per-producer order.
                    shared.drain(&mut state);
                    let generation = shared.release_arms.load(Ordering::SeqCst);
                    return shared.commit(&mut state, event, generation, None);
                }
                Err(TryLockError::Poisoned(_)) => {
                    drop(event);
                    shared.latch_lock_failure();
                    return EmitStatus::LockBusy;
                }
                Err(TryLockError::WouldBlock) => std::hint::spin_loop(),
            }
        }
        self.defer(event)
    }

    /// Fails `conn` immediately and without the state lock: the link's single failure marker is
    /// committed by the next drain and every later fragment of the link is fenced out until
    /// then. For an adapter that has already lost an in-order event (the admission gate's lane
    /// overflowed) and must not wait for its worker to notice.
    pub fn fail_link(&self, conn: Token) {
        self.shared.fail_link_lock_free(conn);
    }

    /// The contended path: hand the event to the bounded lane (never waits, never fails the
    /// process for contention alone).
    fn defer(&self, event: BackendEvent) -> EmitStatus {
        let shared = &self.shared;
        match event {
            // Lossy by contract: dropped under contention, never latched.
            BackendEvent::Advertisement { .. } => EmitStatus::AdvertisementDropped,
            BackendEvent::Fragment { conn, bytes } => {
                if shared.link_failure_pending(conn) {
                    // The link already failed lock-free: fence immediately (no post-gap data).
                    drop(bytes);
                    return EmitStatus::DataOverflow;
                }
                if bytes.len() > FRAGMENT_FRAME_MAX {
                    drop(bytes); // erased; only the link fails
                    shared.fail_link_lock_free(conn);
                    return EmitStatus::FragmentOversize;
                }
                let Some(reservation) = reserve_global(&shared.global_data, bytes.len()) else {
                    drop(bytes);
                    shared.fail_link_lock_free(conn);
                    return EmitStatus::DataOverflow;
                };
                let item = Deferred {
                    event: BackendEvent::Fragment { conn, bytes },
                    generation: 0,
                    reserved: true,
                    control: false,
                };
                if shared.lane_tx.try_send(item).is_err() {
                    // Unreachable by the capacity invariant; fail closed for the link.
                    drop(reservation);
                    shared.fail_link_lock_free(conn);
                    return EmitStatus::DataOverflow;
                }
                reservation.keep();
                shared.announce();
                EmitStatus::Deferred
            }
            control => {
                let claimed = shared
                    .lane_controls
                    .fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
                        (used < DEFERRED_CONTROLS_MAX).then_some(used + 1)
                    })
                    .is_ok();
                if !claimed {
                    drop(control);
                    shared.latch_control_overflow();
                    return EmitStatus::ControlOverflow;
                }
                let item = Deferred {
                    event: control,
                    generation: shared.release_arms.load(Ordering::SeqCst),
                    reserved: false,
                    control: true,
                };
                if shared.lane_tx.try_send(item).is_err() {
                    shared.lane_controls.fetch_sub(1, Ordering::AcqRel);
                    shared.latch_control_overflow();
                    return EmitStatus::ControlOverflow;
                }
                shared.announce();
                EmitStatus::Deferred
            }
        }
    }
}

/// The result of arming the shutdown handshake (see
/// [`BackendReceiver::arm_shutdown`]).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArmedShutdown {
    /// No process-wide terminal had been observed: the handshake is armed and
    /// a terminal reported after this point completes it.
    Armed,
    /// A process-wide terminal was already observed (staged but not yet
    /// drained, or already delivered) before this arm: the handshake can
    /// never complete and the host must fail closed. Permanent.
    UnexpectedTerminal,
}

impl BackendReceiver {
    /// Arms the deterministic core's shutdown handshake **atomically against
    /// backend emissions**: the check runs under the same ingress state lock
    /// as [`BackendEvents::emit`], so a process-wide
    /// [`BackendEvent::ShutdownComplete`] and this call are strictly ordered
    /// by the lock — which observed operation happened "first".
    ///
    /// - **`UnexpectedTerminal`**: a terminal was already observed before the
    ///   arm (staged but not yet drained, or already delivered). It predates
    ///   the handshake and can never be its completion; the handshake is
    ///   forever unexpected and the host must fail closed without a
    ///   `shutdown_complete`.
    /// - **`Armed`**: no terminal had been observed. The handshake's release
    ///   submission still linearizes its OWN generation with
    ///   [`BackendReceiver::claim_release`] (called immediately before
    ///   `Backend::shutdown`), so only a terminal emitted at the claimed
    ///   generation — including one a synchronous adapter reports from inside
    ///   the submission — completes it; anything staged earlier carries an
    ///   older generation and is rejected by attribution at delivery.
    ///
    /// The generation itself is claimed by [`BackendReceiver::claim_release`],
    /// not here: this call only gates the handshake on Task-4 semantics and
    /// the release submission is the one place a generation is minted.
    pub fn arm_shutdown(&self) -> ArmedShutdown {
        let state = self.lock();
        if state.shutdown_delivered || !state.shutdown_queue.is_empty() {
            return ArmedShutdown::UnexpectedTerminal;
        }
        ArmedShutdown::Armed
    }

    /// Claims the next release generation, immediately before submitting
    /// [`crate::backend::Backend::shutdown`] — every release submission (the
    /// explicit handshake's release and every terminal path's fire-and-forget
    /// release: EOF, rejection, exit overflow, host failure) linearizes this
    /// way, under the same state lock as backend emissions. Any terminal that
    /// was staged or delivered BEFORE this claim carries a lower generation
    /// than the returned one and can never be attributed to the submission it
    /// arms (no pre-existing terminal may confirm later cleanup); a terminal
    /// emitted after the claim — including one reported from inside the
    /// submission itself — is stamped with the returned generation and is the
    /// callback the consuming core waits for. Returns the claimed generation.
    pub fn claim_release(&self) -> u64 {
        let _state = self.lock();
        self.shared.release_arms.fetch_add(1, Ordering::SeqCst) + 1
    }

    /// The release generation of the most recently DELIVERED process-wide
    /// terminal (`None` before the first delivery). The draining consumer —
    /// the production runtime or its deterministic twin — pops the terminal
    /// and hands it to the core immediately afterwards on the same thread, so
    /// reading this upon handling a `ShutdownComplete` event attributes that
    /// event: it belongs to the claimed release exactly when the value equals
    /// the generation the core claimed for the release it waits on. Any other
    /// value identifies a stale terminal that can never confirm cleanup.
    pub fn delivered_terminal_gen(&self) -> Option<u64> {
        let state = self.lock();
        state.delivered_terminal
    }

    /// Admission: reserves the control bookkeeping of an accepted scan before any side effect.
    /// Idempotent for an already-reserved token.
    pub fn reserve_scan(&self, scan: Token) -> Result<(), ReserveError> {
        let mut state = self.lock();
        if state.scans.iter().any(|(t, _)| *t == scan) {
            return Ok(());
        }
        if state.scans.len() >= MAX_RESERVED_SCANS {
            return Err(ReserveError::Bounded);
        }
        state.scans.push((
            scan,
            ScanSlot {
                stream: VecDeque::new(),
                ctl_events: 0,
                discoveries: 0,
            },
        ));
        Ok(())
    }

    /// Admission: reserves the control bookkeeping of an accepted connection. Idempotent.
    pub fn reserve_connection(&self, conn: Token) -> Result<(), ReserveError> {
        let mut state = self.lock();
        if state.conns.iter().any(|(t, _)| *t == conn) {
            return Ok(());
        }
        if state.conns.len() >= MAX_RESERVED_CONNECTIONS {
            return Err(ReserveError::Bounded);
        }
        state.conns.push((
            conn,
            ConnSlot {
                stream: VecDeque::new(),
                data_events: 0,
                data_bytes: 0,
                ctl_events: 0,
                completions: 0,
                data_failed: false,
                marker_pending: false,
            },
        ));
        Ok(())
    }

    /// Admission: reserves the bookkeeping of an accepted send (request) token. Idempotent.
    /// Send completions route through their link's FIFO; this reservation is the bounded
    /// outstanding-completion bound of the request vocabulary (at most 64), and its size is
    /// also each link's guaranteed ([`COMPLETIONS_PER_LINK_MAX`]) completion storage — an
    /// admitted request's completion never overflows its link's capacity, however many
    /// ordinary controls that link queues.
    ///
    /// Request tokens are monotonic and never reused: the coordinator must allocate and
    /// reserve send tokens in strictly increasing order. A token that is not currently
    /// reserved is admittable only strictly above the process high-water mark; anything at
    /// or below it ([`ReserveError::Reused`]) — a released token's value, or an
    /// out-of-order lower value — is rejected after release as well, so a released token's
    /// late stale completions can never be admitted to a same-valued reused reservation.
    /// The quarantine is a single bounded mark, never a retired-set.
    pub fn reserve_request(&self, send: Token) -> Result<(), ReserveError> {
        let mut state = self.lock();
        if state.requests.contains(&send) {
            return Ok(());
        }
        if let Some(high_water) = state.request_high_water
            && send <= high_water
        {
            return Err(ReserveError::Reused);
        }
        if state.requests.len() >= MAX_RESERVED_REQUESTS {
            return Err(ReserveError::Bounded);
        }
        state.request_high_water = Some(send);
        state.requests.push(send);
        Ok(())
    }

    /// Releases an accepted scan's bookkeeping. Unconsumed queued control events and staged
    /// advertisements of the token are dropped ("late events for released tokens are
    /// ignored"). A release of an unreserved token is a no-op.
    pub fn release_scan(&self, scan: Token) {
        let mut state = self.lock();
        state.scans.retain(|(t, _)| *t != scan);
    }

    /// Releases an accepted connection's bookkeeping; unconsumed queued events of the link
    /// (including fragment payloads) drop and erase here, and the link's queued data amount
    /// is subtracted from the global data totals so no phantom usage survives the release.
    /// No-op for an unreserved token.
    pub fn release_connection(&self, conn: Token) {
        let mut state = self.lock();
        if let Some(index) = state.conns.iter().position(|(t, _)| *t == conn) {
            let (_, slot) = state.conns.remove(index);
            state
                .global_data
                .fetch_sub(pack(slot.data_events, slot.data_bytes), Ordering::AcqRel);
        }
    }

    /// Releases an accepted request's bookkeeping and cancels that token's completions
    /// already queued on any link: a cancelled `WriteDone`/`WriteFailed` is removed from the
    /// per-link stream before delivery (never deliverable to a reused token — the
    /// cancellation-safe late-event behavior) and its guaranteed completion slot is returned
    /// to the link (`COMPLETIONS_PER_LINK_MAX` accounting), immediately reusable; the link's
    /// data accounting, ordinary control slots and other events are untouched. Post-release
    /// emissions of the token are rejected as `StaleToken`, and the value is retired at the
    /// monotonic high-water mark (see `reserve_request`): it can never be re-reserved. A
    /// release of an unreserved token is a no-op beyond this purge and the value's
    /// retirement.
    pub fn release_request(&self, send: Token) {
        let mut state = self.lock();
        state.requests.retain(|t| *t != send);
        // Retire the value even if it was never reserved: a spurious release must not let a
        // later reservation rewind into a value that already existed in the coordinator.
        state.request_high_water =
            Some(state.request_high_water.map_or(send, |mark| mark.max(send)));
        for (_, slot) in state.conns.iter_mut() {
            let before = slot.stream.len();
            slot.stream.retain(
                |entry| !matches!(entry, StreamEntry::Event(event) if is_cancelled(event, send)),
            );
            // Every removed entry is that token's cancelled send completion (is_cancelled
            // matches only WriteDone/WriteFailed): the freed capacity is completion storage.
            slot.completions -= before - slot.stream.len();
        }
    }

    /// Pops the next deliverable event with deterministic bounded fairness: one strict
    /// round-robin over the combined delivery zones — each scan's FIFO (its controls and
    /// staged discoveries in exact per-scan emission order, scans in registration order)
    /// and each connection stream (per-link emission order) — every non-empty zone is
    /// served within one bounded sweep, then (only once everything else is delivered)
    /// [`crate::backend::BackendEvent::ShutdownComplete`]. Once shutdown is pending, `emit`
    /// closes admission, so the terminal cannot be postponed by new traffic. `timeout:
    /// Some(zero)` is a non-blocking drain; `None` parks until the next event.
    pub fn pop_event(&self, timeout: Option<Duration>) -> BackendPop {
        let deadline = timeout.map(|duration| Instant::now() + duration);
        let mut state = self.lock();
        #[cfg(test)]
        let mut parked = false;
        let popped = loop {
            if let Some(event) = take_event(&mut state) {
                break BackendPop::Event(event);
            }
            // Waits are sliced: a deferral that raced this check is committed within one
            // slice at the latest, whatever the interleaving.
            let slice = match deadline {
                Some(deadline) if Instant::now() >= deadline => break BackendPop::Timeout,
                Some(deadline) => (deadline - Instant::now()).min(WAIT_SLICE),
                None => WAIT_SLICE,
            };
            #[cfg(test)]
            if !parked && deadline.is_none() {
                parked = true;
                self.shared.parked_drains.fetch_add(1, Ordering::SeqCst);
            }
            state = self
                .shared
                .activity
                .wait_timeout(state, slice)
                .expect("ingress lock")
                .0;
            self.shared.drain(&mut state);
        };
        #[cfg(test)]
        if parked {
            self.shared.parked_drains.fetch_sub(1, Ordering::SeqCst);
        }
        popped
    }

    /// Global data accounting: (events, bytes) currently queued.
    pub fn data_usage(&self) -> (usize, usize) {
        unpack(self.shared.global_data.load(Ordering::Acquire))
    }

    /// Per-link data accounting: (events, bytes) currently queued for `conn`.
    pub fn link_data_usage(&self, conn: Token) -> Option<(usize, usize)> {
        let state = self.lock();
        state
            .conns
            .iter()
            .find(|(t, _)| *t == conn)
            .map(|(_, slot)| (slot.data_events, slot.data_bytes))
    }

    /// Reserved bookkeeping counts: (scans, connections, outstanding requests).
    pub fn reserved_counts(&self) -> (usize, usize, usize) {
        let state = self.lock();
        (state.scans.len(), state.conns.len(), state.requests.len())
    }

    /// Whether a control event has been dropped for lack of its bounded slot: a process-level
    /// control failure the host must fail closed on (design: "a process-level unrecoverable
    /// control failure terminates the host"). Sticky until the ingress is dropped.
    pub fn control_overflow(&self) -> bool {
        self.shared.control_overflow.load(Ordering::Acquire)
    }

    /// Whether an emission was lost to a *poisoned* ingress state lock (plain contention is
    /// never a loss: it defers into the ordered lane): a
    /// process-level failure the host must fail closed on (design: "a process-level
    /// unrecoverable control failure terminates the host"). The signal lives in a plain
    /// atomic, not the state mutex — observable even while every emitter and the receiver
    /// contend on the lock. Sticky until the ingress is dropped.
    pub fn emit_lock_failure(&self) -> bool {
        self.shared.lock_failure.load(Ordering::Acquire)
    }

    /// Takes the state lock and commits everything deferred past a contended emission first,
    /// so every receiver seam observes the exact emission order.
    fn lock(&self) -> std::sync::MutexGuard<'_, InnerState> {
        let mut state = self.shared.state.lock().expect("ingress lock");
        self.shared.drain(&mut state);
        state
    }

    /// The core-thread wake token shared with the input queue and the writer notice (see
    /// [`CoreWaker`]); every event that becomes deliverable wakes it.
    pub fn core_waker(&self) -> Arc<CoreWaker> {
        Arc::clone(&self.shared.waker)
    }
}

/// One deliverable event out of the staged state, in the documented priority. Delivery is
/// a strict round-robin over the combined delivery zones (each scan FIFO, then each
/// connection stream, in registration order; one event per zone per full sweep): every
/// non-empty zone is served within one bounded sweep, so no continuously refillable
/// class of events — advertisements included — can starve connection controls, connection
/// data, or any other zone. Per-zone FIFO order is preserved exactly (per-scan emission
/// order; per-link emission order; markers at their per-link FIFO position — after the
/// link's retained data and earlier controls, before controls emitted after the failure);
/// staged `ShutdownComplete` terminals (generation-attributed) deliver only when every
/// other zone is empty, one per staged generation.
fn take_event(state: &mut InnerState) -> Option<BackendEvent> {
    let zones = state.scans.len() + state.conns.len();
    for offset in 0..zones {
        let index = (state.cursor + offset) % zones;
        if let Some(event) = state.take_zone(index) {
            state.cursor = (index + 1) % zones;
            return Some(event);
        }
    }
    let every_other_zone_drained = state.scans.iter().all(|(_, slot)| slot.stream.is_empty())
        && state
            .conns
            .iter()
            .all(|(_, slot)| slot.stream.is_empty() && !slot.marker_pending);
    if let Some(&generation) = state.shutdown_queue.front()
        && every_other_zone_drained
    {
        state.shutdown_queue.pop_front();
        state.shutdown_delivered = true;
        state.delivered_terminal = Some(generation);
        return Some(BackendEvent::ShutdownComplete);
    }
    None
}

impl InnerState {
    /// Serves one event from delivery zone `index` (a scan FIFO below
    /// `self.scans.len()`, otherwise that connection stream), preserving the zone's own
    /// FIFO order exactly; `None` for an empty zone. Advances the served zone's counters.
    fn take_zone(&mut self, index: usize) -> Option<BackendEvent> {
        if index < self.scans.len() {
            let (token, slot) = &mut self.scans[index];
            Some(match slot.stream.pop_front()? {
                ScanEntry::Control(event) => {
                    slot.ctl_events -= 1;
                    event
                }
                ScanEntry::Discover(device) => {
                    slot.discoveries -= 1;
                    BackendEvent::Advertisement {
                        scan: *token,
                        device,
                    }
                }
            })
        } else {
            let (token, slot) = &mut self.conns[index - self.scans.len()];
            match slot.stream.pop_front()? {
                StreamEntry::Event(BackendEvent::Fragment { conn, bytes }) => {
                    let fragment_bytes = bytes.len();
                    slot.data_events -= 1;
                    slot.data_bytes -= fragment_bytes;
                    self.global_data
                        .fetch_sub(pack(1, fragment_bytes), Ordering::AcqRel);
                    Some(BackendEvent::Fragment { conn, bytes })
                }
                StreamEntry::Event(event) => {
                    if matches!(
                        &event,
                        BackendEvent::WriteDone { .. } | BackendEvent::WriteFailed { .. }
                    ) {
                        slot.completions -= 1;
                    } else {
                        slot.ctl_events -= 1;
                    }
                    Some(event)
                }
                StreamEntry::FailureMarker => {
                    slot.marker_pending = false;
                    Some(BackendEvent::LinkLost { conn: *token })
                }
            }
        }
    }
}

struct InputState {
    queue: VecDeque<Payload>,
    bytes: usize,
    /// The reader terminal: always deliverable after the queued frames drain.
    terminal: Option<InputTerminal>,
}

struct PipeShared {
    /// Wakes the core's idle park whenever a frame or the terminal becomes deliverable.
    waker: Arc<CoreWaker>,
    state: Mutex<InputState>,
    /// Wakes parked producers (capacity freed).
    space: Condvar,
    /// Wakes parked drains (frame arrived / terminal set).
    ready: Condvar,
}

/// The reader → core input frame queue (`INPUT_FRAMES_MAX` frames / `INPUT_BYTES_MAX`) with the
/// reader terminal path. One reader at a time per design; the queue is `Clone` (shared state).
#[derive(Clone)]
pub struct InputFrames {
    shared: Arc<PipeShared>,
}

impl InputFrames {
    pub fn new() -> Arc<Self> {
        Self::with_waker(Arc::new(CoreWaker::default()))
    }

    /// The runtime form: pushes and the terminal wake the core thread's idle park.
    pub fn with_waker(waker: Arc<CoreWaker>) -> Arc<Self> {
        Arc::new(Self {
            shared: Arc::new(PipeShared {
                waker,
                state: Mutex::new(InputState {
                    queue: VecDeque::new(),
                    bytes: 0,
                    terminal: None,
                }),
                space: Condvar::new(),
                ready: Condvar::new(),
            }),
        })
    }

    /// Non-blocking push used by tests and as the fast path of [`Self::push_frame_for`].
    /// Assumes the frame satisfied the reader's pre-allocation length checks (at most
    /// [`crate::limits::FRAME_MAX_BYTES`]).
    pub fn try_push_frame(&self, frame: Payload) -> Result<(), InputPushError> {
        let mut state = self.lock();
        if state.terminal.is_some() {
            return Err(InputPushError::TerminalSet);
        }
        if state.queue.len() >= INPUT_FRAMES_MAX || state.bytes + frame.len() > INPUT_BYTES_MAX {
            return Err(InputPushError::Overflow);
        }
        state.bytes += frame.len();
        state.queue.push_back(frame);
        self.shared.ready.notify_all();
        self.shared.waker.wake();
        Ok(())
    }

    /// Backpressure push: blocks (bounded by `timeout`, or indefinitely with `None`) instead of
    /// staging ahead — the reader holds at most the one frame it cannot yet enqueue. On a
    /// timeout or terminal the payload stays owned here and drops (erasing its bytes); the
    /// reader may retry from its own frame source.
    pub fn push_frame_for(
        &self,
        frame: Payload,
        timeout: Option<Duration>,
    ) -> Result<(), InputPushError> {
        let deadline = timeout.map(|duration| Instant::now() + duration);
        let mut state = self.lock();
        loop {
            if state.terminal.is_some() {
                return Err(InputPushError::TerminalSet);
            }
            if state.queue.len() < INPUT_FRAMES_MAX && state.bytes + frame.len() <= INPUT_BYTES_MAX
            {
                state.bytes += frame.len();
                state.queue.push_back(frame);
                self.shared.ready.notify_all();
                self.shared.waker.wake();
                return Ok(());
            }
            match deadline {
                Some(deadline) if Instant::now() >= deadline => {
                    drop(frame);
                    return Err(InputPushError::Overflow);
                }
                Some(deadline) => {
                    state = self
                        .shared
                        .space
                        .wait_timeout(state, deadline - Instant::now())
                        .expect("input queue lock")
                        .0;
                }
                None => {
                    state = self.shared.space.wait(state).expect("input queue lock");
                }
            }
        }
    }

    /// Backpressure push with no timeout: the stdin reader's form; the pipe reader stalls
    /// until the core drains a frame, with no read-ahead beyond its one budgeted frame.
    pub fn push_frame(&self, frame: Payload) -> Result<(), InputPushError> {
        self.push_frame_for(frame, None)
    }

    /// Reports the reader terminal. First kind wins; every subsequent call is a no-op. Wakes
    /// both parked producers and drains.
    pub fn set_terminal(&self, terminal: InputTerminal) {
        let mut state = self.lock();
        if state.terminal.is_none() {
            state.terminal = Some(terminal);
        }
        self.shared.ready.notify_all();
        self.shared.space.notify_all();
        self.shared.waker.wake();
    }

    /// Pops queued frames first; only after the queue drains does the terminal become
    /// deliverable (repeatedly, idempotently). `timeout: Some(zero)` is a non-blocking drain.
    pub fn pop_frame(&self, timeout: Option<Duration>) -> InputPop {
        let deadline = timeout.map(|duration| Instant::now() + duration);
        let mut state = self.lock();
        loop {
            if let Some(frame) = state.queue.pop_front() {
                state.bytes -= frame.len();
                self.shared.space.notify_all();
                return InputPop::Frame(frame);
            }
            if let Some(terminal) = state.terminal {
                return InputPop::Terminal(terminal);
            }
            match deadline {
                Some(deadline) if Instant::now() >= deadline => return InputPop::Timeout,
                Some(deadline) => {
                    state = self
                        .shared
                        .ready
                        .wait_timeout(state, deadline - Instant::now())
                        .expect("input queue lock")
                        .0;
                }
                None => {
                    state = self.shared.ready.wait(state).expect("input queue lock");
                }
            }
        }
    }

    /// (queued frames, queued bytes).
    pub fn frame_usage(&self) -> (usize, usize) {
        let state = self.lock();
        (state.queue.len(), state.bytes)
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, InputState> {
        self.shared.state.lock().expect("input queue lock")
    }
}

/// Whether an OS-supplied routing hint fits the protocol hint field exactly as-is: non-empty,
/// at most [`META_STRING_MAX`] bytes, no control characters. Opaque routing values are
/// preserved verbatim, or the enclosing event is rejected/degraded — never rewritten.
fn is_representable_hint(hint: &str) -> bool {
    !hint.is_empty() && hint.len() <= META_STRING_MAX && !hint.chars().any(char::is_control)
}

/// An exact-capacity copy of a bounded string, so staged metadata never reserves more than
/// the string's own bytes.
fn exact_string_copy(text: &str) -> String {
    let mut copy = String::with_capacity(text.len());
    copy.push_str(text);
    copy
}

/// An exact-capacity copy of the advertisement bytes up to [`META_ADVERTISEMENT_MAX`], so
/// staged advertisement data never reserves more than the binding ceiling.
fn bounded_advertisement(bytes: Vec<u8>) -> Vec<u8> {
    let take = bytes.len().min(META_ADVERTISEMENT_MAX);
    let mut bounded = Vec::with_capacity(take);
    bounded.extend_from_slice(&bytes[..take]);
    bounded
}

/// Safe bounded display-label construction: truncates an OS-supplied string to
/// [`META_STRING_MAX`] bytes on a UTF-8 character boundary and removes control characters (a
/// superset of the protocol hint field's no-controls constraint). Display metadata only —
/// never applied to opaque routing hints, which are preserved or rejected instead.
fn bounded_label(text: &str) -> String {
    let mut end = text.len().min(META_STRING_MAX);
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    // Control filtering only removes characters: the result never exceeds the byte cap.
    text[..end].chars().filter(|c| !c.is_control()).collect()
}

/// The optional [`BackendEvent::Connected`] platform hint bounded at admission: a
/// representable hint is preserved verbatim (exact-capacity copy); an unrepresentable one
/// degrades to `None` — bounded, explicit absence, never a rewritten partial value — because
/// the lifecycle event itself must never be dropped over metadata.
fn bounded_connected_hint(hint: String) -> Option<String> {
    if is_representable_hint(&hint) {
        Some(exact_string_copy(&hint))
    } else {
        None
    }
}

/// Bounded, preserved-or-rejected discovery construction: an unrepresentable opaque routing
/// hint rejects the device whole (`None` — never stage-rewritten); otherwise the staged
/// metadata copies are exactly bounded.
fn bounded_discovered(device: Discovered) -> Option<Discovered> {
    if !is_representable_hint(&device.platform_hint) {
        return None;
    }
    Some(Discovered {
        platform_hint: exact_string_copy(&device.platform_hint),
        label: bounded_label(&device.label),
        advertisement: device.advertisement.map(bounded_advertisement),
    })
}

/// Whether `event` is a queued completion of the cancelled `send` token (never a fragment).
fn is_cancelled(event: &BackendEvent, send: Token) -> bool {
    matches!(
        event,
        BackendEvent::WriteDone { send: queued, .. } | BackendEvent::WriteFailed { send: queued, .. }
            if *queued == send
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::thread;

    /// A held state mutex: the holder thread keeps it locked until [`HeldLock::release`]
    /// drops the release sender; the holder then unlocks and signals on `released`, so the
    /// lock being free again is a deterministic channel event, never a timing assumption.
    struct HeldLock {
        release: mpsc::Sender<()>,
        released: mpsc::Receiver<()>,
    }

    impl HeldLock {
        fn release(self) {
            drop(self.release);
            self.released
                .recv()
                .expect("holder released the state lock");
        }
    }

    /// Spawns a detached thread holding the state mutex; see [`HeldLock`].
    fn hold_state_lock(shared: &Arc<Shared>) -> HeldLock {
        let (locked_tx, locked_rx) = mpsc::channel::<()>();
        let (release_tx, release_rx) = mpsc::channel::<()>();
        let (released_tx, released_rx) = mpsc::channel::<()>();
        let shared = Arc::clone(shared);
        thread::spawn(move || {
            let guard = shared.state.lock().expect("holder locks the state");
            let _ = locked_tx.send(());
            let _ = release_rx.recv();
            drop(guard);
            let _ = released_tx.send(());
        });
        locked_rx.recv().expect("holder acquired the state lock");
        HeldLock {
            release: release_tx,
            released: released_rx,
        }
    }

    /// Emits `event` on a separate thread (never on the test thread, so a regression to a
    /// blocking emit cannot deadlock the test itself) and returns the status receiver.
    fn emit_in_thread(events: &BackendEvents, event: BackendEvent) -> mpsc::Receiver<EmitStatus> {
        let (done_tx, done_rx) = mpsc::channel();
        let emitter = events.clone();
        thread::spawn(move || {
            let _ = done_tx.send(emitter.emit(event));
        });
        done_rx
    }

    fn fragment(conn: Token, seq: u8) -> BackendEvent {
        BackendEvent::Fragment {
            conn,
            bytes: Payload::new(vec![seq; 8]),
        }
    }

    fn control(conn: Token, seq: usize) -> BackendEvent {
        BackendEvent::CapacityChanged {
            conn,
            capacity: crate::fragment::LinkCapacity::AttMtu(seq),
        }
    }

    fn popped(receiver: &BackendReceiver) -> Vec<BackendEvent> {
        let mut events = Vec::new();
        while let BackendPop::Event(event) = receiver.pop_event(Some(Duration::ZERO)) {
            events.push(event);
        }
        events
    }

    /// Review C1: routine contention is deferred in emission order — accepted, never
    /// failed — and every receiver seam commits it before observing anything.
    #[test]
    fn emit_under_lock_contention_is_deferred_in_order_and_never_fails_the_host() {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(1).expect("connection reserved");
        receiver.reserve_scan(9).expect("scan reserved");

        let held = hold_state_lock(&events.shared);

        // Every emit finishes while the lock is still held (the holder only unlocks after
        // these assertions): any wait on the state mutex would stall into the guard timeout.
        let statuses = [
            fragment(1, 0),
            fragment(1, 1),
            control(1, 185),
            fragment(1, 2),
            BackendEvent::Advertisement {
                scan: 9,
                device: Discovered {
                    platform_hint: "dev".into(),
                    label: "d".into(),
                    advertisement: None,
                },
            },
        ]
        .map(|event| {
            emit_in_thread(&events, event)
                .recv_timeout(Duration::from_secs(5))
                .expect("emit must return promptly while the state lock is held")
        });
        assert_eq!(
            statuses,
            [
                EmitStatus::Deferred,
                EmitStatus::Deferred,
                EmitStatus::Deferred,
                EmitStatus::Deferred,
                // Lossy by contract: dropped under contention, never a failure.
                EmitStatus::AdvertisementDropped,
            ]
        );
        assert!(!receiver.emit_lock_failure());
        assert!(!receiver.control_overflow());
        // In-transit fragments already count against the one global ceiling.
        assert_eq!(receiver.data_usage(), (3, 24));

        held.release();

        // Nothing lost, exact order, and the accounting is exact after the drain.
        assert_eq!(
            popped(&receiver),
            vec![
                fragment(1, 0),
                fragment(1, 1),
                control(1, 185),
                fragment(1, 2)
            ]
        );
        assert_eq!(receiver.data_usage(), (0, 0));
        assert!(!receiver.emit_lock_failure());
        assert!(!receiver.control_overflow());
    }

    /// A direct emission commits everything deferred before it (per-producer order).
    #[test]
    fn a_direct_emission_commits_earlier_deferrals_first() {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(1).expect("connection reserved");
        let held = hold_state_lock(&events.shared);
        assert_eq!(events.emit(fragment(1, 0)), EmitStatus::Deferred);
        assert_eq!(events.emit(control(1, 200)), EmitStatus::Deferred);
        held.release();
        assert_eq!(events.emit(fragment(1, 1)), EmitStatus::Accepted);
        assert_eq!(
            popped(&receiver),
            vec![fragment(1, 0), control(1, 200), fragment(1, 1)]
        );
    }

    /// Reserves links 1..=32 and exhausts the one global data budget *in the lane* while the
    /// state lock is held: links 1..=30 sit at their own 48-event cap, link 31 emits 96
    /// (per-link overflow only bites at commit), which totals exactly the 1,536-event ceiling.
    fn exhaust_global_budget_in_the_lane(events: &BackendEvents, receiver: &BackendReceiver) {
        for conn in 1..=30 {
            for seq in 0..DATA_EVENTS_PER_LINK_MAX as u8 {
                assert_eq!(events.emit(fragment(conn, seq)), EmitStatus::Deferred);
            }
        }
        for seq in 0..96u8 {
            assert_eq!(events.emit(fragment(31, seq)), EmitStatus::Deferred);
        }
        assert_eq!(receiver.data_usage().0, DATA_EVENTS_GLOBAL_MAX);
    }

    /// Overflow policy 1: fragments that cannot enter the lane fail *their link*, never the
    /// process; the global ceiling is exact across lanes and the per-link ceiling still bites at
    /// commit.
    #[test]
    fn deferred_fragment_overflow_fails_only_its_link() {
        let (events, receiver) = BackendEvents::new();
        for conn in 1..=32 {
            receiver.reserve_connection(conn).expect("reserved");
        }
        let held = hold_state_lock(&events.shared);
        exhaust_global_budget_in_the_lane(&events, &receiver);
        // The next fragment (link 32) exceeds the global ceiling: its link fails, nothing else.
        assert_eq!(events.emit(fragment(32, 0)), EmitStatus::DataOverflow);
        // A deferred oversize fragment is a deterministic link failure, too (no copy kept).
        assert_eq!(
            events.emit(BackendEvent::Fragment {
                conn: 30,
                bytes: Payload::new(vec![0; FRAGMENT_FRAME_MAX + 1]),
            }),
            EmitStatus::FragmentOversize
        );
        assert!(!receiver.emit_lock_failure());
        assert!(!receiver.control_overflow());
        held.release();

        let delivered = popped(&receiver);
        let mut lost: Vec<Token> = delivered
            .iter()
            .filter_map(|event| match event {
                BackendEvent::LinkLost { conn } => Some(*conn),
                _ => None,
            })
            .collect();
        lost.sort_unstable();
        assert_eq!(
            lost,
            vec![30, 31, 32],
            "exactly the failed links (oversize, per-link overflow at commit, global), once each"
        );
        let seqs = |conn: Token| -> Vec<u8> {
            delivered
                .iter()
                .filter_map(|event| match event {
                    BackendEvent::Fragment { conn: c, bytes } if *c == conn => {
                        Some(bytes.as_slice()[0])
                    }
                    _ => None,
                })
                .collect()
        };
        for conn in 1..=29 {
            assert_eq!(
                seqs(conn),
                (0..48).collect::<Vec<u8>>(),
                "link {conn} lost nothing"
            );
        }
        assert_eq!(
            seqs(31),
            (0..48).collect::<Vec<u8>>(),
            "link 31 keeps exactly its 48-event bound, then fails"
        );
        assert!(seqs(32).is_empty());
        assert_eq!(receiver.data_usage(), (0, 0));
        assert!(!receiver.control_overflow());
    }

    /// Overflow policy 2: controls own dedicated lane slots data can never occupy; running
    /// out of them is a genuine control failure (latched), not contention.
    #[test]
    fn deferred_controls_keep_their_own_slots_and_overflow_latches() {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(1).expect("connection reserved");
        receiver.reserve_connection(2).expect("connection reserved");
        let held = hold_state_lock(&events.shared);
        // A full data flood cannot displace a control.
        for conn in [1, 2] {
            for seq in 0..16 {
                assert_eq!(events.emit(fragment(conn, seq)), EmitStatus::Deferred);
            }
        }
        for n in 0..DEFERRED_CONTROLS_MAX {
            assert_eq!(
                events.emit(control(2, n)),
                EmitStatus::Deferred,
                "control {n}"
            );
        }
        assert!(!receiver.control_overflow());
        assert_eq!(
            events.emit(control(2, 9_999)),
            EmitStatus::ControlOverflow,
            "the dedicated control slots are exhausted"
        );
        assert!(receiver.control_overflow(), "genuine overflow latches");
        held.release();
    }

    /// A terminal keeps the release generation live at *emission*, not at the later commit.
    #[test]
    fn deferred_terminal_keeps_its_emission_generation() {
        let (events, receiver) = BackendEvents::new();
        let held = hold_state_lock(&events.shared);
        assert_eq!(
            events.emit(BackendEvent::ShutdownComplete),
            EmitStatus::Deferred
        );
        held.release();
        // The claim commits the earlier deferral first (still generation 0) and only then
        // mints generation 1: the stray terminal can never confirm the claimed release.
        assert_eq!(receiver.claim_release(), 1);
        assert_eq!(
            receiver.pop_event(Some(Duration::ZERO)),
            BackendPop::Event(BackendEvent::ShutdownComplete)
        );
        assert_eq!(receiver.delivered_terminal_gen(), Some(0));
        assert!(!receiver.emit_lock_failure());

        // Emitted after the claim, under contention: stamped with the claimed generation.
        let (events, receiver) = BackendEvents::new();
        assert_eq!(receiver.claim_release(), 1);
        let held = hold_state_lock(&events.shared);
        assert_eq!(
            events.emit(BackendEvent::ShutdownComplete),
            EmitStatus::Deferred
        );
        held.release();
        assert_eq!(
            receiver.pop_event(Some(Duration::ZERO)),
            BackendPop::Event(BackendEvent::ShutdownComplete)
        );
        assert_eq!(receiver.delivered_terminal_gen(), Some(1));
    }

    #[test]
    fn emit_on_a_poisoned_state_lock_latches_the_signal_without_panicking() {
        let (events, receiver) = BackendEvents::new();
        let poison_shared = Arc::clone(&events.shared);
        let poisoner = thread::spawn(move || {
            let _guard = poison_shared
                .state
                .lock()
                .expect("poisoner locks the state");
            panic!("poisoning the ingress state lock")
        });
        let _ = poisoner.join(); // the intentional panic is what poisons the mutex here

        // A poisoned lock must not panic the callback path either: same typed rejection,
        // same sticky signal.
        assert_eq!(
            events.emit(BackendEvent::ScanStarted { scan: 1 }),
            EmitStatus::LockBusy
        );
        assert!(receiver.emit_lock_failure());
        assert_eq!(
            events.emit(BackendEvent::ShutdownComplete),
            EmitStatus::LockBusy
        );
        assert!(receiver.emit_lock_failure());
    }

    /// Hands `receiver` to a drainer thread that forwards every pop to `done_tx` until
    /// it observes the link's `LinkLost`, then returns the receiver on `back_tx`. The
    /// drainer parks (`pop_event(None)`) whenever nothing is staged; the wake tests spin
    /// on the test-only parked census instead of sleeping.
    fn drain_until_link_lost(
        receiver: BackendReceiver,
        done_tx: mpsc::Sender<BackendPop>,
        back_tx: mpsc::Sender<BackendReceiver>,
        conn: Token,
    ) -> thread::JoinHandle<()> {
        thread::spawn(move || {
            loop {
                let popped = receiver.pop_event(None);
                let terminal = matches!(
                    &popped,
                    BackendPop::Event(BackendEvent::LinkLost { conn: seen }) if *seen == conn
                );
                let _ = done_tx.send(popped);
                if terminal {
                    let _ = back_tx.send(receiver);
                    return;
                }
            }
        })
    }

    /// Asserts (with a bounded spin on the test-only parked census, never sleeps or wall
    /// clocks) that at least one drainer is parked in `pop_event(None)`'s indefinite wait.
    fn assert_parked_drain(shared: &Shared) {
        for _ in 0..10_000_000 {
            if shared.parked_drains.load(Ordering::SeqCst) > 0 {
                return;
            }
        }
        panic!("the drain never reached its parked wait");
    }

    /// Review round 5, N1: a failure marker staged on an otherwise EMPTY link must wake a
    /// drain parked in `pop_event(None)` — an oversize fragment (rejected pre-copy, zero
    /// retained data) is a deliverable terminal exactly then, and a parked core must not
    /// sleep on it forever.
    #[test]
    fn oversize_marker_on_an_empty_link_wakes_a_parked_drain() {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(1).expect("connection reserved");

        let (done_tx, done_rx) = mpsc::channel();
        let (back_tx, back_rx) = mpsc::channel();
        drain_until_link_lost(receiver, done_tx, back_tx, 1);
        assert_parked_drain(&events.shared);

        assert_eq!(
            events.emit(BackendEvent::Fragment {
                conn: 1,
                bytes: Payload::new(vec![0x41; 513]),
            }),
            EmitStatus::FragmentOversize,
            "the oversize fragment is rejected pre-copy on the empty link"
        );

        let popped = done_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("the staged marker must wake the parked drain (N1)");
        assert_eq!(
            popped,
            BackendPop::Event(BackendEvent::LinkLost { conn: 1 })
        );
        let receiver = back_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("the drainer returns the receiver");
        assert_eq!(
            receiver.pop_event(Some(Duration::ZERO)),
            BackendPop::Timeout,
            "the oversize marker was the link's deliverable terminal"
        );
    }

    /// A deferral wakes a drain parked in `pop_event(None)` and the core's park token: a
    /// deferred failure marker is observed without any later emission.
    #[test]
    fn a_deferral_wakes_a_parked_drain_and_the_core_waker() {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(1).expect("connection reserved");
        let waker = receiver.core_waker();
        waker.bind_current();

        let (done_tx, done_rx) = mpsc::channel();
        let (back_tx, back_rx) = mpsc::channel();
        drain_until_link_lost(receiver, done_tx, back_tx, 1);
        assert_parked_drain(&events.shared);

        let held = hold_state_lock(&events.shared);
        assert_eq!(
            events.emit(BackendEvent::Fragment {
                conn: 1,
                bytes: Payload::new(vec![0; FRAGMENT_FRAME_MAX + 1]),
            }),
            EmitStatus::FragmentOversize
        );
        // The unpark token is level-triggered: this park returns at once.
        thread::park_timeout(Duration::from_secs(5));
        held.release();
        let popped = done_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("the deferred marker must reach the parked drain");
        assert_eq!(
            popped,
            BackendPop::Event(BackendEvent::LinkLost { conn: 1 })
        );
        back_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("receiver returned");
    }

    /// Review follow-up #1 (F1/F2): once a deferred fragment (F1) has failed its link
    /// lock-free, no later fragment (F2) of that link may commit before the single `LinkLost`
    /// marker, even if capacity frees before the failure is drained into the link FIFO. The race
    /// (failure registered after a drain's register scan, capacity freed by the same pop) is
    /// forced deterministically by driving the internals: the failure is registered and the
    /// capacity freed *without* any drain in between.
    #[test]
    fn a_fragment_after_a_deferred_budget_failure_never_commits_before_link_lost() {
        let (events, receiver) = BackendEvents::new();
        for conn in 1..=32 {
            receiver.reserve_connection(conn).expect("reserved");
        }
        let held = hold_state_lock(&events.shared);
        exhaust_global_budget_in_the_lane(&events, &receiver);
        // F1: the global budget is exhausted -> link 32 is failed lock-free, still undrained.
        assert_eq!(events.emit(fragment(32, 1)), EmitStatus::DataOverflow);
        // Capacity frees before any drain applied the failure.
        events
            .shared
            .global_data
            .fetch_sub(pack(1, 8), Ordering::AcqRel);
        // (a) F2 emitted now would reserve fine; the lock-free fence refuses it up front.
        assert_eq!(
            events.emit(fragment(32, 2)),
            EmitStatus::DataOverflow,
            "F2 must be fenced at emission, not deferred behind the gap"
        );
        // (b) F2 already in the lane (passed the emission check before F1 was registered):
        // the commit-time fence rejects it and fails the link at its FIFO position.
        events
            .shared
            .global_data
            .fetch_add(pack(1, 8), Ordering::AcqRel); // F2's own reservation
        events
            .shared
            .lane_tx
            .try_send(Deferred {
                event: fragment(32, 3),
                generation: 0,
                reserved: true,
                control: false,
            })
            .expect("lane has room");
        held.release();

        let delivered = popped(&receiver);
        let for_link: Vec<String> = delivered
            .iter()
            .filter_map(|event| match event {
                BackendEvent::Fragment { conn: 32, bytes } => {
                    Some(format!("frag{}", bytes.as_slice()[0]))
                }
                BackendEvent::LinkLost { conn: 32 } => Some("lost".into()),
                _ => None,
            })
            .collect();
        assert_eq!(for_link, ["lost"], "no post-gap data, exactly one marker");
        assert!(!receiver.control_overflow() && !receiver.emit_lock_failure());
        // Undo the simulated "capacity freed" (it had no real event behind it): every real
        // reservation, F2's included, must then have been released.
        events
            .shared
            .global_data
            .fetch_add(pack(1, 8), Ordering::AcqRel);
        assert_eq!(
            receiver.data_usage(),
            (0, 0),
            "every reservation was released"
        );
    }

    /// Same-link ordering at the real cap: a link deferring its full 48-fragment bound
    /// interleaved with controls keeps the exact emission order; the 49th deferred fragment
    /// fails the link at commit (marker after the retained prefix); later emissions of the
    /// failed link — direct or deferred — never queue behind the gap.
    #[test]
    fn same_link_deferred_order_is_exact_up_to_the_cap_then_fails_closed() {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(1).expect("reserved");
        receiver.reserve_connection(2).expect("reserved");
        let held = hold_state_lock(&events.shared);
        let mut expected = Vec::new();
        for seq in 0..DATA_EVENTS_PER_LINK_MAX {
            assert_eq!(events.emit(fragment(1, seq as u8)), EmitStatus::Deferred);
            expected.push(fragment(1, seq as u8));
            if seq % 7 == 3 {
                assert_eq!(events.emit(control(1, 300 + seq)), EmitStatus::Deferred);
                expected.push(control(1, 300 + seq));
            }
            // An unrelated link's traffic interleaves without disturbing link 1's order.
            assert_eq!(events.emit(fragment(2, seq as u8)), EmitStatus::Deferred);
        }
        assert_eq!(
            events.emit(fragment(1, 200)),
            EmitStatus::Deferred,
            "49th is queued"
        );
        held.release();
        let delivered = popped(&receiver);
        let link1: Vec<&BackendEvent> = delivered
            .iter()
            .filter(|event| {
                matches!(
                    event,
                    BackendEvent::Fragment { conn: 1, .. }
                        | BackendEvent::CapacityChanged { conn: 1, .. }
                        | BackendEvent::LinkLost { conn: 1 }
                )
            })
            .collect();
        expected.push(BackendEvent::LinkLost { conn: 1 });
        assert_eq!(link1.len(), expected.len());
        for (got, want) in link1.iter().zip(&expected) {
            assert_eq!(*got, want);
        }
        let link2 = delivered
            .iter()
            .filter(|event| matches!(event, BackendEvent::Fragment { conn: 2, .. }))
            .count();
        assert_eq!(
            link2, DATA_EVENTS_PER_LINK_MAX,
            "the other link lost nothing"
        );
        assert_eq!(events.emit(fragment(1, 201)), EmitStatus::DataOverflow);
        assert_eq!(receiver.data_usage(), (0, 0));
        assert!(!receiver.control_overflow() && !receiver.emit_lock_failure());
    }

    /// The independent per-link byte cap also bites at commit for deferred fragments.
    #[test]
    fn deferred_fragments_respect_the_per_link_byte_cap() {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(1).expect("reserved");
        let held = hold_state_lock(&events.shared);
        for _ in 0..DATA_BYTES_PER_LINK_MAX / FRAGMENT_FRAME_MAX {
            let status = events.emit(BackendEvent::Fragment {
                conn: 1,
                bytes: Payload::new(vec![7; FRAGMENT_FRAME_MAX]),
            });
            assert_eq!(status, EmitStatus::Deferred);
        }
        // One more 512-byte fragment is within the global budget (so it is queued) but the link's
        // 16 KiB byte cap rejects it at commit.
        assert_eq!(
            events.emit(BackendEvent::Fragment {
                conn: 1,
                bytes: Payload::new(vec![7; FRAGMENT_FRAME_MAX]),
            }),
            EmitStatus::Deferred
        );
        held.release();
        let delivered = popped(&receiver);
        let fragments = delivered
            .iter()
            .filter(|event| matches!(event, BackendEvent::Fragment { .. }))
            .count();
        assert_eq!(fragments, DATA_BYTES_PER_LINK_MAX / FRAGMENT_FRAME_MAX);
        assert_eq!(delivered.last(), Some(&BackendEvent::LinkLost { conn: 1 }));
        assert_eq!(receiver.data_usage(), (0, 0));
    }
}
