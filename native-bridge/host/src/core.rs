//! Deterministic request, connection and cancellation core (OTP-24 Task 4).
//!
//! The coordinator is synchronous: it consumes [`Input`]s with the explicit clock of
//! [`Core::handle`], owns protocol phase, the request table, handles, the discovery cache,
//! connection state, send storage accounting, monotonic deadlines and stdout event ordering,
//! and never waits for OS BLE work. Every OS operation is a nonblocking
//! [`crate::backend::Backend`] submission; backend callbacks arrive as [`Input::Backend`]
//! events the runtime drained from the bounded ingress (`crate::ingress::BackendReceiver`) —
//! the same receiver the core reserves on. A backend release never blocks the core: teardown
//! submissions (`disconnect`/`shutdown`) are fire-and-forget, and the observed
//! [`crate::backend::BackendEvent::LinkReleased`] /
//! [`crate::backend::BackendEvent::ShutdownComplete`] drive the bookkeeping release, so a
//! stalled adapter can never stall the core.
//!
//! Production adaptations over the reviewed feasibility core (design disposition table: "Separate
//! protocol/lifecycle concerns where useful; audit all bounds and races"):
//!
//! - **Ingress admission**: reservations (`ingress::BackendReceiver::reserve_*`) happen before
//!   any OS side effect, and each terminal event releases the matching reservation. Send tokens
//!   are allocated from one strictly monotonic lifetime counter together with request
//!   sequencing — reserved before `write_fragments`, retired on release. Allocation refuses to
//!   wrap (fail closed instead of reuse), as does the random 16-hex handle generator.
//! - **Closing links** stay owned between teardown and the observed OS release
//!   ([`Conn::closing`]): they are capacity-accounted against the 32-connection bound, fence
//!   reconnects to the same normalized target, and their connector handle is de-registered
//!   immediately, until [`crate::backend::BackendEvent::LinkReleased`].
//! - **Normalized-target fence**: pending/open/closing connections are keyed by the backend's
//!   OS-normalized target identity ([`Backend::normalize_target`]), never by the opaque routing
//!   hint text — distinct platform aliases of one device fence as one target, and a hint the
//!   backend cannot resolve is refused before any backend side effect.
//! - **Frozen fragment budget**: the budget derived from the first verified GATT capacity is
//!   frozen for the connection; fragments are only ever generated for a verified capacity. A
//!   [`crate::backend::BackendEvent::CapacityChanged`] with an identical derived budget is a
//!   no-op; any other change (or an unrepresentable capacity) cancels the affected work and
//!   closes the connection — the in-flight record is never re-fragmented.
//! - **The 4,096/5,120 split**: a `send` decoding into the 4,097..=5,120-byte band is answered
//!   with the correlated fatal `oversize_record` before any fragment is created or submitted
//!   (GATT profile ceiling), while the envelope itself still admits those decoded sizes.
//! - **Shutdown**: every other outstanding request is errored, the remaining unsolicited
//!   lifecycle events are emitted, and the handshake is armed atomically under the ingress
//!   state lock before the release is submitted — `shutdown_complete` follows only the
//!   backend's own [`crate::backend::BackendEvent::ShutdownComplete`], observed after the
//!   arm; a terminal staged before the arm never completes the handshake (host fail-closed
//!   instead). EOF/silent fail-closed paths release resources without any shutdown
//!   acknowledgement. Cleanup confirmation is generation-attributed: the release generation
//!   is claimed ([`crate::ingress::BackendReceiver::claim_release`]) immediately before each
//!   (once-per-process) `Backend::shutdown` submission, and a delivered terminal confirms
//!   that release only when its delivery generation equals the claimed one — a pre-existing
//!   stale terminal can neither complete the handshake nor confirm post-exit cleanup.
//! - Records and fragments are owned erased [`Payload`] buffers end to end; nothing logs,
//!   `Debug`-prints or traces record content.

use std::collections::{HashMap, HashSet, VecDeque};
use std::hash::{BuildHasher, Hasher, RandomState};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::backend::{
    Backend, BackendEvent, Discovered, Failure, NormalizedTarget, Target as BackendTarget, Token,
};
use crate::buffer::Payload;
use crate::fragment::{
    BudgetError, GlobalBudget, LinkBudget, LinkCapacity, MAX_RECORD, PushOutcome, Reassembler,
    fragment,
};
use crate::ingress::{ArmedShutdown, BackendReceiver, META_ADVERTISEMENT_MAX, META_STRING_MAX};
use crate::outqueue::{OutQueue, PushError};
use crate::protocol::{
    CAPABILITY_RECORD_V1, ErrorCode, Event, Offer, ParseFailure, Phase, Recoverability, Rejection,
    Request, RequestBody, SUPPORTED_API_VERSIONS, Target, parse_request,
};

/// Exit code 0: clean shutdown handshake or clean stdout EOF.
pub const EXIT_OK: i32 = 0;
/// Exit code 1: host failure — stdin I/O failure, or the process-level ingress
/// control/lock loss the backend seam latched (never a wire-level error path).
pub const EXIT_HOST_FAILURE: i32 = 1;
/// Exit code 2: rejected input failed closed — uncorrelatable input silently,
/// pre-ready rejections after flushing their correlated error, and duplicate
/// live request IDs.
pub const EXIT_REJECTED: i32 = 2;
/// Exit code 3: `incompatible_api` after flushing the correlated error.
pub const EXIT_INCOMPATIBLE: i32 = 3;
/// Exit code 4: bounded output queue overflow — fail closed, nothing further.
pub const EXIT_OUTPUT_OVERFLOW: i32 = 4;

pub const MAX_OUTSTANDING_REQUESTS: usize = 64;
pub const MAX_SCANS: usize = 2;
pub const MAX_CONNECTIONS: usize = 32;
/// Named ceilings mirrored by `protocol/v1/limits.json` (builder entries) and
/// enforced by constant-conformance checks: scan lifetime, request timeout,
/// discovery rate/per-scan caps, discovery cache and send storage.
pub const SCAN_CAP: Duration = Duration::from_secs(300);
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
pub const DEVICE_FOUND_PER_SECOND: usize = 64;
pub const DEVICE_FOUND_PER_SCAN: usize = 1024;
pub const DEVICE_CACHE: usize = 1024;
pub const SEND_RECORDS_PER_LINK: usize = 16;
pub const SEND_BYTES_PER_LINK: usize = 16 * 1024;
pub const SEND_RECORDS_GLOBAL: usize = 128;
pub const SEND_BYTES_GLOBAL: usize = 128 * 1024;
/// The envelope `label` bound (1..64 bytes; sanitized below).
pub const LABEL_MAX_BYTES: usize = 64;
/// The envelope `label` field is non-empty; a device name that sanitizes to
/// nothing still gets a valid display label.
pub const FALLBACK_LABEL: &str = "BLE device";

/// One runtime input. Parsed requests arrive as whole frames; bounded backend
/// callbacks arrive one event at a time, exactly as the runtime drained them
/// from the ingress; the process-level failure input is fed when the runtime
/// observed the ingress' sticky latches.
#[derive(Debug)]
pub enum Input {
    /// One complete Native Messaging JSON frame (owned; erased on release).
    Frame(Payload),
    /// stdin EOF / Chrome port closure.
    StdinEof,
    /// Strict framing violation on stdin (zero/oversize/truncated frame).
    StdinFraming,
    /// stdin OS read failure.
    StdinIoError,
    /// The single writer could not deliver a frame (pipe closed).
    StdoutClosed,
    /// One bounded backend callback drained from the ingress.
    Backend(BackendEvent),
    /// A process-level ingress control/lock failure the runtime observed.
    /// The core additionally observes the sticky latches itself at every input.
    BackendOverflow,
    /// Clock housekeeping (deadlines, inactivity, scan caps).
    Tick,
}

pub struct Config {
    pub bridge_version: String,
    pub platform: &'static str,
    /// The core side of the one shared ingress receiver: admission
    /// reservations happen here before any OS side effect, and terminal events
    /// release the bookkeeping. The runtime keeps the drain/poll handle of the
    /// same receiver (see `ingress::BackendReceiver`'s docs).
    pub ingress: BackendReceiver,
}

/// How a link ends. Single teardown path ([`Core::drop_conn`]): affected
/// request errors first, then exactly one `disconnected`.
enum Teardown {
    /// Requested `disconnect`: one correlated `disconnected`.
    Requested(String),
    /// Link loss (real or the ingress' synthesized per-link failure marker).
    Lost,
    /// Request timeout: the timed-out request reports `timeout` first.
    Timeout(String),
    /// Failed write: the failing send reports it verbatim.
    WriteFailed(String, Failure),
    /// Setup/capacity refusal during (or instead of) GATT setup: the connect
    /// request reports it.
    SetupFailed(Failure),
    /// The frozen fragment budget was invalidated: affected sends report it
    /// (with the mapped capacity stage).
    CapacityChanged(&'static str),
}

enum ReqKind {
    StartScan,
    Connect(Token),
    Send(Token),
}

struct Req {
    seq: u64,
    kind: ReqKind,
    deadline: Option<Instant>,
}

struct Scan {
    handle: String,
    request_id: Option<String>,
    started: bool,
    /// The scan ended for the protocol (stop submitted, 300 s cap, OS-ended or failed start)
    /// but its OS release was not observed yet: it stays in the table — counted against
    /// [`MAX_SCANS`] and keeping its ingress reservation — until
    /// [`BackendEvent::ScanReleased`]. A closing scan is never disclosed again.
    closing: bool,
    started_at: Instant,
    emitted: usize,
    window: VecDeque<Instant>,
    seen: HashSet<String>,
}

struct PendingSend {
    request_id: String,
    record: Payload,
}

struct InFlight {
    request_id: String,
    send: Token,
    len: usize,
}

struct Conn {
    handle: String,
    /// The fence key: the backend's OS-normalized identity of the target
    /// (never the opaque routing hint, which is only passed through to the
    /// adapter at submit time and disclosed from discovery).
    normalized: NormalizedTarget,
    open: bool,
    closing: bool,
    connect_request: Option<String>,
    link: Option<LinkBudget>,
    reassembler: Option<Reassembler>,
    next_transaction: u8,
    pending: VecDeque<PendingSend>,
    in_flight: Option<InFlight>,
    queued_bytes: usize,
}

impl Conn {
    /// Send-storage records of this link, including the in-flight record.
    fn queued_records(&self) -> usize {
        self.pending.len() + usize::from(self.in_flight.is_some())
    }
}

/// Guaranteed-unique opaque handles: a keyed bijection over a counter, never
/// reused, refusing to wrap (None on exhaustion).
struct HandleGen {
    key: u64,
    counter: u64,
}

impl HandleGen {
    fn new() -> Self {
        Self {
            key: RandomState::new().build_hasher().finish(),
            counter: 0,
        }
    }

    fn try_next(&mut self) -> Option<String> {
        if self.counter == u64::MAX {
            return None;
        }
        self.counter += 1;
        let mut value = self.counter ^ self.key;
        value = value.wrapping_mul(0x9E37_79B9_7F4A_7C15);
        value ^= value >> 32;
        value = value.wrapping_mul(0xD6E8_FEB8_6659_FD93);
        value ^= value >> 32;
        Some(format!("{value:016x}"))
    }
}

/// Bounded display metadata sanitize: control characters removed, byte length
/// capped at UTF-8 character boundaries. Display only — never applied to the
/// opaque routing hints, which are preserved or rejected by the parsers.
fn sanitize(text: &str, max_bytes: usize) -> String {
    let mut out = String::new();
    for char in text
        .chars()
        .filter(|char| !(*char <= '\u{1f}' || *char == '\u{7f}'))
    {
        if out.len() + char.len_utf8() > max_bytes {
            break;
        }
        out.push(char);
    }
    out
}

pub struct Core<B: Backend> {
    backend: B,
    out: Arc<OutQueue>,
    config: Config,
    now: Instant,
    exit: Option<i32>,
    phase: Phase,
    handles: HandleGen,
    /// One strictly monotonic allocator for scan/connection/send tokens and
    /// request sequencing: every value is used exactly once over the process
    /// lifetime and allocation refuses to wrap.
    monotonic: u64,
    budget: Arc<GlobalBudget>,
    requests: HashMap<String, Req>,
    scans: HashMap<Token, Scan>,
    conns: HashMap<Token, Conn>,
    conn_by_handle: HashMap<String, Token>,
    devices: VecDeque<(String, String)>,
    /// The `shutdown` request id while its handshake waits for the backend's
    /// `ShutdownComplete` (a live reservation of the id).
    shutdown: Option<String>,
    /// Whether the backend's process-wide [`crate::backend::BackendEvent::ShutdownComplete`]
    /// has been observed — also after a terminal exit. Bookkeeping only: it
    /// never emits anything and never changes the exit code.
    shutdown_complete_seen: bool,
    /// The generation claimed for the (at most one) genuine release submission
    /// ([`crate::ingress::BackendReceiver::claim_release`], immediately before
    /// `Backend::shutdown`). `None` until the first release was submitted. A
    /// delivered terminal confirms this release — and is only ever accepted as
    /// cleanup evidence — when its delivery generation equals this value; any
    /// other (pre-claim stale) terminal confirms nothing.
    release_gen: Option<u64>,
}

impl<B: Backend> Core<B> {
    pub fn new(backend: B, out: Arc<OutQueue>, config: Config) -> Self {
        Self {
            backend,
            out,
            config,
            now: Instant::now(),
            exit: None,
            phase: Phase::PreReady,
            handles: HandleGen::new(),
            monotonic: 0,
            budget: GlobalBudget::new(),
            requests: HashMap::new(),
            scans: HashMap::new(),
            conns: HashMap::new(),
            conn_by_handle: HashMap::new(),
            devices: VecDeque::new(),
            shutdown: None,
            shutdown_complete_seen: false,
            release_gen: None,
        }
    }

    pub fn backend(&self) -> &B {
        &self.backend
    }

    /// Whether the backend's own [`crate::backend::BackendEvent::ShutdownComplete`]
    /// has actually been observed — `false` while its release is still
    /// pending, including after the core's terminal exit (EOF releases
    /// resources fire-and-forget without waiting). The observation survives
    /// the exit, and the terminal is accepted after the exit as pure
    /// bookkeeping: nothing new is emitted and the exit code stays exactly as
    /// finished. Only a delivered terminal whose generation equals the
    /// claimed release generation counts — a pre-existing stale terminal
    /// delivered to the exited core can never fabricate this observation.
    pub fn shutdown_complete_received(&self) -> bool {
        self.shutdown_complete_seen
    }

    /// Whether an accepted explicit `shutdown` handshake is armed and still waiting for the
    /// backend's attributed release. The runtime bounds that wait (five seconds from the
    /// accepted request) because nothing else would: stdin may stay open forever.
    pub fn shutdown_pending(&self) -> bool {
        self.exit.is_none() && self.shutdown.is_some()
    }

    /// Fails a pending shutdown handshake closed: the release was never confirmed in time, so
    /// the host exits with [`EXIT_HOST_FAILURE`] and never emits `shutdown_complete`. Releases
    /// everything (fire-and-forget) exactly like every other failure exit. No-op unless a
    /// handshake is pending.
    pub fn abort_pending_shutdown(&mut self) -> Option<i32> {
        if !self.shutdown_pending() {
            return self.exit;
        }
        self.shutdown = None;
        self.finish(EXIT_HOST_FAILURE);
        self.exit
    }

    /// Processes one input. Returns the process exit code once the core has
    /// fully released every scan, link and handle and accepts no further input
    /// (an exited core keeps reporting its code).
    pub fn handle(&mut self, input: Input, now: Instant) -> Option<i32> {
        if self.exit.is_some() {
            // The only input still processed after the exit is the backend's
            // process-wide terminal: pure bookkeeping for the runtime's
            // release observation, without emitting anything and without
            // touching the finished exit code. Attribution is generation-
            // equality with the claimed release: a stale terminal delivered
            // here predates the (at most one, once-per-process) release
            // submission and confirms nothing — the runtime keeps waiting
            // for the attributed one and fails closed at its deadline
            // otherwise. (Deadlines for a stalled release belong to the
            // runtime, not here.)
            if let Input::Backend(BackendEvent::ShutdownComplete) = input
                && self.config.ingress.delivered_terminal_gen() == self.release_gen
            {
                self.shutdown_complete_seen = true;
            }
            return self.exit;
        }
        self.now = now;
        // The runtime polls the ingress' process-level latches at bounded
        // intervals and feeds them here as `BackendOverflow`; the core also
        // observes the sticky latches itself on every input, so a lost control
        // or emission is never absorbed silently (host fail-closed either way).
        if matches!(input, Input::BackendOverflow)
            || self.config.ingress.emit_lock_failure()
            || self.config.ingress.control_overflow()
        {
            self.finish(EXIT_HOST_FAILURE);
            return self.exit;
        }
        match input {
            Input::Frame(frame) => self.on_frame(frame.as_slice()),
            Input::StdinEof | Input::StdoutClosed => self.finish(EXIT_OK),
            Input::StdinFraming => self.finish(EXIT_REJECTED),
            Input::StdinIoError | Input::BackendOverflow => self.finish(EXIT_HOST_FAILURE),
            Input::Backend(event) => self.on_backend(event),
            Input::Tick => self.on_tick(),
        }
        self.exit
    }

    /// Earliest instant at which `Input::Tick` must be delivered.
    pub fn next_deadline(&self) -> Option<Instant> {
        if self.exit.is_some() {
            return None;
        }
        let requests = self
            .requests
            .values()
            .filter_map(|request| request.deadline);
        let scans = self
            .scans
            .values()
            .filter(|scan| scan.started && !scan.closing)
            .map(|scan| scan.started_at + SCAN_CAP);
        let reassembly = self
            .conns
            .values()
            .filter_map(|conn| conn.reassembler.as_ref().and_then(Reassembler::next_expiry));
        requests.chain(scans).chain(reassembly).min()
    }

    // ---- allocation and emission ----------------------------------------------------------------

    /// The strictly monotonic allocator; `None` means the lifetime counter is
    /// exhausted and the host fails closed before any value could be reused.
    fn alloc(&mut self) -> Option<u64> {
        let next = self.monotonic.checked_add(1)?;
        self.monotonic = next;
        Some(next)
    }

    fn api_version(&self) -> Option<u64> {
        match self.phase {
            Phase::PreReady => None,
            Phase::Ready { api_version } => Some(api_version),
        }
    }

    /// Queues one event. A full queue fails the whole process closed instead
    /// of dropping or reordering (exit 4).
    fn emit(&mut self, event: Event) -> bool {
        if self.exit.is_some() {
            return false;
        }
        let payload = event.to_json(self.api_version());
        let request_id = event.request_id().map(str::to_string);
        match self.out.push(Payload::new(payload), request_id) {
            Ok(()) => true,
            Err(PushError::Overflow) => {
                self.finish(EXIT_OUTPUT_OVERFLOW);
                false
            }
            // The writer closed the queue after a failed delivery (closed Chrome pipe): the
            // same clean stdout-closed termination as `Input::StdoutClosed`, not an overflow.
            Err(PushError::Closed) => {
                self.finish(EXIT_OK);
                false
            }
        }
    }

    fn error(
        &mut self,
        request_id: Option<&str>,
        code: ErrorCode,
        retryable: bool,
        stage: &str,
        scan_handle: Option<&str>,
        connection_handle: Option<&str>,
    ) -> bool {
        self.emit(Event::Error {
            request_id: request_id.map(str::to_string),
            code,
            recoverability: if retryable {
                Recoverability::Retryable
            } else {
                Recoverability::Fatal
            },
            stage: stage.to_string(),
            scan_handle: scan_handle.map(str::to_string),
            connection_handle: connection_handle.map(str::to_string),
        })
    }

    // ---- release and exit ------------------------------------------------------------------------

    /// Stops every scan, releases every link (erasing retained records and
    /// reassembly state), releases the ingress reservations of every still
    /// reserved token and shuts the backend down. Used by every exit path and
    /// by the `shutdown` handshake — submissions are fire-and-forget, never
    /// blocking on the OS release.
    fn release_all(&mut self) {
        let scans: Vec<Token> = self.scans.keys().copied().collect();
        for scan in scans {
            self.backend.stop_scan(scan);
            self.config.ingress.release_scan(scan);
        }
        let conns: Vec<Token> = self.conns.keys().copied().collect();
        for conn in conns {
            if let Some(conn) = self.conns.get_mut(&conn) {
                conn.pending.clear();
                conn.queued_bytes = 0;
                conn.reassembler.take();
                if let Some(in_flight) = conn.in_flight.take() {
                    self.config.ingress.release_request(in_flight.send);
                }
            }
            self.backend.disconnect(conn);
            self.config.ingress.release_connection(conn);
        }
        // The release generation is claimed atomically under the ingress
        // state lock, immediately before the submission: every terminal
        // emitted from this point (including one a synchronous adapter
        // reports from inside the submission) is stamped with this value, so
        // only terminals emitted after this claim can confirm the release's
        // cleanup. Claimed at most once per process: a later release_all call
        // (a terminal superseding an armed handshake's pending submission —
        // e.g. stdin EOF while the release is still in flight) submits again
        // without a second generation, so every post-claim terminal stamps
        // the already-claimed generation and a pre-existing terminal can
        // never retro-attribute to a claim minted after its emission.
        //
        // The submission itself happens once per process too: a terminal superseding an armed
        // handshake (EOF, deadline abort) must not call into a possibly blocking adapter a
        // second time — its release is already in flight and every post-claim terminal still
        // stamps the claimed generation.
        if self.release_gen.is_none() {
            self.release_gen = Some(self.config.ingress.claim_release());
            self.backend.shutdown();
        }
        self.scans.clear();
        self.conns.clear();
        self.conn_by_handle.clear();
        self.requests.clear();
        self.devices.clear();
    }

    /// Orderly exit: release everything, emit nothing further.
    fn finish(&mut self, code: i32) {
        if self.exit.is_some() {
            return;
        }
        self.exit = Some(code);
        self.release_all();
    }

    /// A lifetime counter ran out: fail closed instead of reusing a value.
    fn fail_exhausted(&mut self) {
        self.finish(EXIT_HOST_FAILURE);
    }

    // ---- inbound frames ------------------------------------------------------------------------------

    fn on_frame(&mut self, bytes: &[u8]) {
        let request = match parse_request(bytes, self.phase) {
            Ok(request) => request,
            Err(ParseFailure::Uncorrelatable) => return self.finish(EXIT_REJECTED),
            Err(ParseFailure::Rejected(rejection)) => return self.on_rejection(rejection),
        };
        if matches!(self.phase, Phase::Ready { .. }) {
            // A second live use of a request id is ambiguous (a second
            // correlated response could not be told apart from a duplicate
            // terminal): nothing is emitted, the retained state is released
            // and the fail-closed exit follows (OTP2-BRD-003/OTP2-BRD-011).
            let reused = self.requests.contains_key(&request.request_id)
                || self.out.holds_request_id(&request.request_id)
                || self
                    .shutdown
                    .as_deref()
                    .is_some_and(|held| held == request.request_id);
            if reused {
                return self.finish(EXIT_REJECTED);
            }
        }
        if matches!(request.body, RequestBody::Hello(_)) {
            if let RequestBody::Hello(offer) = request.body {
                let Request {
                    request_id,
                    body: _,
                } = request;
                self.on_hello(request_id, offer);
            }
            return;
        }
        if self.shutdown.is_some() {
            // Shutdown stopped admission; parseable frames get no responses
            // (no event may follow the handshake's lifecycle events).
            return;
        }
        // No exception for `shutdown` (OTP2-BRD-030): with the table full,
        // every further request — shutdown included — is refused with the
        // correlated retryable `cap_exceeded` and nothing is torn down.
        if self.requests.len() >= MAX_OUTSTANDING_REQUESTS {
            self.error(
                Some(&request.request_id),
                ErrorCode::CapExceeded,
                true,
                "requests",
                None,
                None,
            );
            return;
        }
        self.dispatch(request);
    }

    fn on_rejection(&mut self, rejection: Rejection) {
        let Rejection {
            request_id,
            code,
            recoverability,
            stage,
        } = rejection;
        let pre_ready = matches!(self.phase, Phase::PreReady);
        if !pre_ready
            && (self.requests.contains_key(&request_id)
                || self.out.holds_request_id(&request_id)
                || self
                    .shutdown
                    .as_deref()
                    .is_some_and(|held| held == request_id.as_str()))
        {
            return self.finish(EXIT_REJECTED);
        }
        if self.shutdown.is_some() {
            // Admission is stopped (see `on_frame`): no responses emit.
            return;
        }
        if !self.error(
            Some(&request_id),
            code,
            recoverability == Recoverability::Retryable,
            stage,
            None,
            None,
        ) {
            return;
        }
        if pre_ready {
            self.finish(EXIT_REJECTED);
        }
    }

    fn on_hello(&mut self, request_id: String, offer: Offer) {
        match offer.select(SUPPORTED_API_VERSIONS) {
            Some(api_version) => {
                self.phase = Phase::Ready { api_version };
                self.emit(Event::Ready {
                    request_id,
                    bridge_version: self.config.bridge_version.clone(),
                    platform: self.config.platform,
                    capabilities: vec![CAPABILITY_RECORD_V1.to_string()],
                });
            }
            None => {
                self.error(
                    Some(&request_id),
                    ErrorCode::IncompatibleApi,
                    false,
                    "negotiation",
                    None,
                    None,
                );
                self.finish(EXIT_INCOMPATIBLE);
            }
        }
    }

    fn dispatch(&mut self, request: Request) {
        let Request {
            request_id: id,
            body,
        } = request;
        match body {
            RequestBody::Hello(_) => {}
            RequestBody::StartScan { service_uuid } => self.start_scan(id, &service_uuid),
            RequestBody::StopScan { scan_handle } => self.stop_scan(id, &scan_handle),
            RequestBody::Connect(target) => self.connect(id, target),
            RequestBody::Disconnect { connection_handle } => {
                self.disconnect(id, &connection_handle)
            }
            RequestBody::Send {
                connection_handle,
                record,
            } => self.send(id, &connection_handle, record),
            RequestBody::Shutdown => self.shutdown(id),
        }
    }

    fn track(&mut self, id: &str, kind: ReqKind, deadline: Option<Instant>) -> bool {
        let Some(seq) = self.alloc() else {
            self.fail_exhausted();
            return false;
        };
        self.requests.insert(
            id.to_string(),
            Req {
                seq,
                kind,
                deadline,
            },
        );
        true
    }

    // ---- scans ------------------------------------------------------------------------------------------

    fn start_scan(&mut self, id: String, uuid: &str) {
        if self.scans.len() >= MAX_SCANS {
            self.error(Some(&id), ErrorCode::CapExceeded, true, "scans", None, None);
            return;
        }
        let Some(token) = self.alloc() else {
            return self.fail_exhausted();
        };
        let Some(handle) = self.handles.try_next() else {
            return self.fail_exhausted();
        };
        // Reservation before any side effect; a full reservation set is the
        // same bounded ceiling the core table enforces above.
        if self.config.ingress.reserve_scan(token).is_err() {
            self.error(Some(&id), ErrorCode::CapExceeded, true, "scans", None, None);
            return;
        }
        self.scans.insert(
            token,
            Scan {
                handle,
                request_id: Some(id.clone()),
                started: false,
                closing: false,
                started_at: self.now,
                emitted: 0,
                window: VecDeque::new(),
                seen: HashSet::new(),
            },
        );
        if !self.track(&id, ReqKind::StartScan, None) {
            return;
        }
        self.backend.start_scan(token, uuid);
    }

    fn scan_by_handle(&self, handle: &str) -> Option<Token> {
        self.scans
            .iter()
            .find(|(_, scan)| scan.started && !scan.closing && scan.handle == handle)
            .map(|(token, _)| *token)
    }

    fn stop_scan(&mut self, id: String, handle: &str) {
        let Some(token) = self.scan_by_handle(handle) else {
            self.error(
                Some(&id),
                ErrorCode::UnknownHandle,
                false,
                "scan_handle",
                None,
                None,
            );
            return;
        };
        // The scan is only *closing* now: it stays counted (and its ingress reservation
        // stays, so the release event can be delivered) until the backend observed the OS
        // release. The protocol answer does not wait for it.
        if let Some(scan) = self.scans.get_mut(&token) {
            scan.closing = true;
        }
        self.backend.stop_scan(token);
        self.emit(Event::ScanState {
            request_id: Some(id),
            scan_handle: handle.to_string(),
            started: false,
        });
    }

    fn on_scan_started(&mut self, token: Token) {
        let now = self.now;
        let Some(scan) = self
            .scans
            .get_mut(&token)
            .filter(|scan| !scan.started && !scan.closing)
        else {
            return;
        };
        scan.started = true;
        scan.started_at = now;
        let (handle, request) = (scan.handle.clone(), scan.request_id.take());
        if let Some(request_id) = request {
            self.requests.remove(&request_id);
            self.emit(Event::ScanState {
                request_id: Some(request_id),
                scan_handle: handle,
                started: true,
            });
        }
    }

    fn on_scan_failed(&mut self, token: Token, failure: Failure) {
        let Some(scan) = self
            .scans
            .get_mut(&token)
            .filter(|scan| !scan.started && !scan.closing)
        else {
            return;
        };
        scan.closing = true;
        let request = scan.request_id.take();
        if let Some(request_id) = request {
            self.requests.remove(&request_id);
            self.error(
                Some(&request_id),
                ErrorCode::ScanFailed,
                failure.retryable,
                failure.stage,
                None,
                None,
            );
        }
    }

    fn on_scan_ended(&mut self, token: Token, failure: Option<Failure>) {
        let Some(scan) = self
            .scans
            .get_mut(&token)
            .filter(|scan| scan.started && !scan.closing)
        else {
            return;
        };
        scan.closing = true;
        let handle = scan.handle.clone();
        if let Some(failure) = failure
            && !self.error(
                None,
                ErrorCode::ScanFailed,
                failure.retryable,
                failure.stage,
                Some(&handle),
                None,
            )
        {
            return;
        }
        self.emit(Event::ScanState {
            request_id: None,
            scan_handle: handle,
            started: false,
        });
    }

    /// The OS release of a scan was observed: the only point where a scan stops counting.
    /// A release for a scan the protocol still considers live (the OS let go of it without
    /// a preceding terminal) first ends it for the protocol exactly like the terminal would
    /// have, then frees the slot and the ingress reservation.
    fn on_scan_released(&mut self, token: Token) {
        let Some(scan) = self.scans.get(&token) else {
            return;
        };
        if !scan.closing {
            if scan.started {
                self.on_scan_ended(token, None);
            } else {
                self.on_scan_failed(token, Failure::new("scan_start", true));
            }
            if self.exit.is_some() {
                return;
            }
        }
        self.scans.remove(&token);
        self.config.ingress.release_scan(token);
    }

    fn on_advertisement(&mut self, token: Token, device: Discovered) {
        let now = self.now;
        // Immutable admission filters first: an unstarted scan, an
        // unrepresentable hint, per-scan dedupe/caps, and the discovery-cache
        // bound (a self-wide check — the mutable bookkeeping below follows
        // only when every filter passed and the device will be emitted).
        let Some(scan) = self.scans.get(&token) else {
            return;
        };
        if !scan.started || scan.closing {
            return;
        }
        let hint = device.platform_hint;
        if hint.is_empty() || hint.len() > META_STRING_MAX || hint.chars().any(char::is_control) {
            return;
        }
        if scan.seen.contains(&hint) || scan.emitted >= DEVICE_FOUND_PER_SCAN {
            return;
        }
        // The discovery cache bound comes before any rate bookkeeping: a
        // full cache with no evictable (non-connected) entry admits nothing —
        // the discovery is dropped whole so no device handle is ever emitted
        // that a `connect` cannot resolve, and no active entry is evicted.
        if !self.cache_room() {
            return;
        }
        let Some(scan) = self.scans.get_mut(&token) else {
            return;
        };
        while scan
            .window
            .front()
            .is_some_and(|at| now.saturating_duration_since(*at) >= Duration::from_secs(1))
        {
            scan.window.pop_front();
        }
        if scan.window.len() >= DEVICE_FOUND_PER_SECOND {
            return;
        }
        scan.window.push_back(now);
        scan.emitted += 1;
        scan.seen.insert(hint.clone());
        let scan_handle = scan.handle.clone();
        let mut label = sanitize(&device.label, LABEL_MAX_BYTES);
        if label.is_empty() {
            label = FALLBACK_LABEL.to_string();
        }
        let advertisement = device
            .advertisement
            .filter(|bytes| !bytes.is_empty())
            .map(|bytes| {
                let take = bytes.len().min(META_ADVERTISEMENT_MAX);
                bytes[..take].to_vec()
            });
        let Some(device_handle) = self.handles.try_next() else {
            return self.fail_exhausted();
        };
        // The disclosed handle is cached before the event leaves the core, so
        // a `connect` naming it can be resolved; the bounded cache evicts the
        // oldest non-connected entry (see `cache_device`).
        self.cache_device(device_handle.clone(), hint.clone());
        self.emit(Event::DeviceFound {
            scan_handle,
            device_handle,
            label,
            platform_hint: Some(hint),
            advertisement,
        });
    }

    /// The design's normalized-target fence: a pending/open/closing
    /// connection owns its target identity until the OS release is observed.
    /// The key is the backend's OS-normalized identity, never the opaque
    /// routing hint text.
    fn target_in_use(&self, identity: &NormalizedTarget) -> bool {
        self.conns.values().any(|conn| &conn.normalized == identity)
    }

    /// Whether the target behind a cached (raw) hint is currently owned:
    /// the cached hint is normalized through the same backend seam, and a
    /// hint it cannot resolve is never pinned (such a target can never
    /// connect).
    fn cached_target_in_use(&self, hint: &str) -> bool {
        self.backend
            .normalize_target(hint)
            .is_some_and(|identity| self.target_in_use(&identity))
    }

    /// Whether a new cache entry fits the 1,024-entry process bound: under
    /// the cap, or with at least one entry that is not currently connected
    /// (FIFO eviction candidate). Entries of connected targets are pinned.
    fn cache_room(&self) -> bool {
        self.devices.len() < DEVICE_CACHE
            || self
                .devices
                .iter()
                .any(|(_, cached)| !self.cached_target_in_use(cached))
    }

    fn cache_device(&mut self, handle: String, hint: String) {
        if self.devices.len() >= DEVICE_CACHE {
            // FIFO eviction of the oldest entry that is not currently
            // connected (connected targets stay pinned). A fully pinned
            // cache never enlarges: admissions are gated by [`Self::cache_room`],
            // so the device was already dropped on that path and this branch
            // is the defensive tail.
            let Some(position) = self
                .devices
                .iter()
                .position(|(_, cached)| !self.cached_target_in_use(cached))
            else {
                return;
            };
            self.devices.remove(position);
        }
        self.devices.push_back((handle, hint));
    }

    // ---- connections -----------------------------------------------------------------------------------------

    fn connect(&mut self, id: String, target: Target) {
        let hint = match target {
            Target::Hint(hint) => hint,
            Target::Device(handle) => {
                match self
                    .devices
                    .iter()
                    .find(|(cached, _)| *cached == handle)
                    .map(|(_, hint)| hint.clone())
                {
                    Some(hint) => hint,
                    None => {
                        self.error(
                            Some(&id),
                            ErrorCode::UnknownHandle,
                            false,
                            "device_handle",
                            None,
                            None,
                        );
                        return;
                    }
                }
            }
        };
        // The fence key is the backend's OS-normalized identity of the
        // resolved target, never the opaque hint text: distinct platform
        // aliases of one device fence as one target, and a hint the backend
        // cannot resolve is refused here, before any side effect (no token,
        // no handle, no ingress reservation, no submission, no fence slot).
        let Some(normalized) = self.backend.normalize_target(&hint) else {
            self.error(
                Some(&id),
                ErrorCode::InvalidRequest,
                false,
                "unsupported_target",
                None,
                None,
            );
            return;
        };
        // Concurrent/overlapping connects to the same normalized OS target
        // are refused until the link's release is observed (closing links
        // fence).
        if self.target_in_use(&normalized) {
            self.error(
                Some(&id),
                ErrorCode::Busy,
                true,
                "already_connected",
                None,
                None,
            );
            return;
        }
        if self.conns.len() >= MAX_CONNECTIONS {
            self.error(
                Some(&id),
                ErrorCode::CapExceeded,
                true,
                "connections",
                None,
                None,
            );
            return;
        }
        let Some(token) = self.alloc() else {
            return self.fail_exhausted();
        };
        let Some(handle) = self.handles.try_next() else {
            return self.fail_exhausted();
        };
        if self.config.ingress.reserve_connection(token).is_err() {
            self.error(
                Some(&id),
                ErrorCode::CapExceeded,
                true,
                "connections",
                None,
                None,
            );
            return;
        }
        self.conns.insert(
            token,
            Conn {
                handle,
                normalized,
                open: false,
                closing: false,
                connect_request: Some(id.clone()),
                link: None,
                reassembler: None,
                next_transaction: 1,
                pending: VecDeque::new(),
                in_flight: None,
                queued_bytes: 0,
            },
        );
        if !self.track(
            &id,
            ReqKind::Connect(token),
            Some(self.now + REQUEST_TIMEOUT),
        ) {
            return;
        }
        self.backend.connect(token, &BackendTarget::Hint(hint));
    }

    /// GATT discovery/link capacity/CCCD setup completed; the one frozen link
    /// budget is derived here (once, never re-derived except by closing the
    /// connection), and the connection handle is disclosed.
    fn on_connected(
        &mut self,
        token: Token,
        capacity: LinkCapacity,
        refreshed_hint: Option<String>,
    ) {
        if !self
            .conns
            .get(&token)
            .is_some_and(|conn| !conn.open && !conn.closing)
        {
            return;
        }
        let link = match LinkBudget::from_capacity(capacity) {
            Ok(link) => link,
            Err(BudgetError::Undetermined) => {
                return self.drop_conn(
                    token,
                    Teardown::SetupFailed(Failure::new("write_capacity", false)),
                );
            }
            Err(_) => {
                return self.drop_conn(
                    token,
                    Teardown::SetupFailed(Failure::new("mtu_range", false)),
                );
            }
        };
        let Some(conn) = self.conns.get_mut(&token) else {
            return;
        };
        conn.open = true;
        conn.link = Some(link);
        conn.reassembler = Some(Reassembler::new(link, self.budget.clone()));
        let handle = conn.handle.clone();
        let connect_request = conn.connect_request.take();
        self.conn_by_handle.insert(handle.clone(), token);
        let platform_hint = refreshed_hint
            .map(|hint| sanitize(&hint, META_STRING_MAX))
            .filter(|hint| !hint.is_empty());
        if let Some(request_id) = connect_request {
            self.requests.remove(&request_id);
            self.emit(Event::Connected {
                request_id,
                connection_handle: handle,
                platform_hint,
            });
        }
    }

    fn disconnect(&mut self, id: String, handle: &str) {
        match self.conn_by_handle.get(handle).copied() {
            Some(token) => self.drop_conn(token, Teardown::Requested(id)),
            None => {
                self.error(
                    Some(&id),
                    ErrorCode::UnknownHandle,
                    false,
                    "connection_handle",
                    None,
                    None,
                );
            }
        }
    }

    /// Single exit path for every way a link can end. Errors for affected
    /// requests come first, then exactly one `disconnected` (correlated for a
    /// requested disconnect, otherwise unsolicited and only if the connector
    /// ever observed the handle). A cancelled send's completion is purged from
    /// the ingress (its token is released), so no late `sent` can follow. The
    /// link keeps its resource ownership as a closing entry — capacity
    /// accounted, reconnect fenced — until the backend's (`never blocking`)
    /// release is observed.
    fn drop_conn(&mut self, token: Token, why: Teardown) {
        let Some(conn) = self.conns.get_mut(&token) else {
            return;
        };
        if conn.closing {
            return; // teardown already ran; late events change nothing
        }
        let disclosed = conn.open;
        let handle = conn.handle.clone();
        conn.reassembler.take();
        let mut affected: Vec<(String, bool)> = Vec::new(); // (request id, is_connect)
        if let Some(request_id) = conn.connect_request.take() {
            affected.push((request_id, true));
        }
        if let Some(in_flight) = conn.in_flight.take() {
            self.config.ingress.release_request(in_flight.send);
            affected.push((in_flight.request_id, false));
        }
        for pending in conn.pending.drain(..) {
            let PendingSend { record, request_id } = pending;
            drop(record); // erases
            affected.push((request_id, false));
        }
        conn.queued_bytes = 0;
        self.conn_by_handle.remove(&handle);
        let conn_handle = disclosed.then_some(handle.clone());

        for (request_id, is_connect) in affected {
            self.requests.remove(&request_id);
            let default_code = if is_connect {
                ErrorCode::ConnectFailed
            } else {
                ErrorCode::SendFailed
            };
            let (code, retryable, stage) = match &why {
                Teardown::Timeout(timed_out) if *timed_out == request_id => {
                    (ErrorCode::Timeout, true, "timeout")
                }
                Teardown::WriteFailed(failed, failure) if *failed == request_id => {
                    (default_code, failure.retryable, failure.stage)
                }
                Teardown::SetupFailed(failure) if is_connect => {
                    (default_code, failure.retryable, failure.stage)
                }
                Teardown::CapacityChanged(stage) => (default_code, false, *stage),
                Teardown::Requested(_) => (default_code, false, "disconnected"),
                _ => (default_code, false, "link_lost"),
            };
            if !self.error(
                Some(&request_id),
                code,
                retryable,
                stage,
                None,
                conn_handle.as_deref(),
            ) {
                return;
            }
        }
        if let Some(conn) = self.conns.get_mut(&token) {
            conn.closing = true;
        }
        match why {
            Teardown::Requested(id) => {
                self.emit(Event::Disconnected {
                    request_id: Some(id),
                    connection_handle: handle,
                });
            }
            _ if disclosed => {
                self.emit(Event::Disconnected {
                    request_id: None,
                    connection_handle: handle,
                });
            }
            _ => {}
        }
        self.backend.disconnect(token);
    }

    // ---- send -----------------------------------------------------------------------------------------

    fn send(&mut self, id: String, handle: &str, record: Payload) {
        let Some(token) = self.conn_by_handle.get(handle).copied() else {
            self.error(
                Some(&id),
                ErrorCode::UnknownHandle,
                false,
                "connection_handle",
                None,
                None,
            );
            return;
        };
        // The envelope still admits up to its 5,120-byte decode ceiling; the
        // GATT profile rejects the band above 4,096 before anything is created
        // or written, and the live handle stays usable.
        if record.len() > MAX_RECORD {
            self.error(
                Some(&id),
                ErrorCode::OversizeRecord,
                false,
                "record",
                None,
                Some(handle),
            );
            return;
        }
        let (global_records, global_bytes) = self
            .conns
            .values()
            .fold((0usize, 0usize), |(records, bytes), conn| {
                (records + conn.queued_records(), bytes + conn.queued_bytes)
            });
        let conn = self
            .conns
            .get_mut(&token)
            .expect("handle map points at a live connection");
        // Full send storage rejects the new request with a retryable
        // `cap_exceeded` before storing/submitting its record; earlier work is
        // preserved (no unaccounted waiting queue).
        if conn.queued_records() >= SEND_RECORDS_PER_LINK
            || conn.queued_bytes + record.len() > SEND_BYTES_PER_LINK
            || global_records >= SEND_RECORDS_GLOBAL
            || global_bytes + record.len() > SEND_BYTES_GLOBAL
        {
            self.error(
                Some(&id),
                ErrorCode::CapExceeded,
                true,
                "send_queue",
                None,
                Some(handle),
            );
            return;
        }
        conn.queued_bytes += record.len();
        conn.pending.push_back(PendingSend {
            request_id: id.clone(),
            record,
        });
        if !self.track(&id, ReqKind::Send(token), Some(self.now + REQUEST_TIMEOUT)) {
            return;
        }
        self.pump_sends(token);
    }

    /// Starts the next queued record when the link has no batch in flight.
    /// The send token is allocated (strictly monotonic; failure before wrap)
    /// and reserved in the ingress BEFORE the write submission.
    fn pump_sends(&mut self, token: Token) {
        if self.exit.is_some() {
            return;
        }
        match self.step_send(token) {
            PumpStep::Idle => {}
            PumpStep::Prepared {
                request_id,
                record_len,
                frames,
            } => {
                let Some(send) = self.alloc() else {
                    return self.fail_exhausted();
                };
                if self.config.ingress.reserve_request(send).is_err() {
                    // Unreachable with strictly increasing fresh tokens and
                    // the admission-side request cap; still, the request gets
                    // exactly one terminal instead of an unresolvable one.
                    if let Some(conn) = self.conns.get_mut(&token) {
                        conn.queued_bytes = conn.queued_bytes.saturating_sub(record_len);
                    }
                    self.requests.remove(&request_id);
                    let handle = self
                        .conns
                        .get(&token)
                        .map(|conn| conn.handle.clone())
                        .unwrap_or_default();
                    self.error(
                        Some(&request_id),
                        ErrorCode::Internal,
                        false,
                        "send_queue",
                        None,
                        Some(&handle),
                    );
                    return;
                }
                self.backend.write_fragments(token, send, frames);
                if let Some(conn) = self.conns.get_mut(&token) {
                    conn.in_flight = Some(InFlight {
                        request_id,
                        send,
                        len: record_len,
                    });
                }
            }
            PumpStep::FragmentError {
                request_id,
                record_len,
            } => {
                if let Some(conn) = self.conns.get_mut(&token) {
                    conn.queued_bytes = conn.queued_bytes.saturating_sub(record_len);
                }
                self.requests.remove(&request_id);
                let handle = self
                    .conns
                    .get(&token)
                    .map(|conn| conn.handle.clone())
                    .unwrap_or_default();
                self.error(
                    Some(&request_id),
                    ErrorCode::Internal,
                    false,
                    "fragment",
                    None,
                    Some(&handle),
                );
            }
        }
    }

    /// Splits the next pending record: `Ok(Idle)` when nothing is due, the
    /// owned fragment batch on success (the record bytes are consumed into the
    /// frames and erased from storage), or a correlated fragment failure.
    fn step_send(&mut self, token: Token) -> PumpStep {
        let Some(conn) = self.conns.get_mut(&token) else {
            return PumpStep::Idle;
        };
        if conn.closing || conn.in_flight.is_some() {
            return PumpStep::Idle;
        }
        let Some(link) = conn.link else {
            return PumpStep::Idle; // fragments only after verified GATT capacity
        };
        let Some(pending) = conn.pending.pop_front() else {
            return PumpStep::Idle;
        };
        let record_len = pending.record.len();
        let transaction = conn.next_transaction;
        conn.next_transaction = (conn.next_transaction % 255) + 1;
        // Destructure the pending entry so every release path erases its
        // record bytes (on Ok they live in the frames now; on Err they erase).
        let PendingSend { record, request_id } = pending;
        match fragment(record.as_slice(), link, transaction) {
            Ok(frames) => {
                drop(record);
                PumpStep::Prepared {
                    request_id,
                    record_len,
                    frames,
                }
            }
            // Unreachable for admitted records (they passed the size checks at
            // admission); the request still gets its correlated terminal.
            Err(_) => {
                drop(record);
                PumpStep::FragmentError {
                    request_id,
                    record_len,
                }
            }
        }
    }

    fn on_write_done(&mut self, token: Token, send: Token) {
        let Some(conn) = self.conns.get_mut(&token) else {
            return;
        };
        let Some(in_flight) = conn.in_flight.take_if(|in_flight| in_flight.send == send) else {
            return; // stale/late completion of a cancelled or unknown batch
        };
        conn.queued_bytes = conn.queued_bytes.saturating_sub(in_flight.len);
        self.config.ingress.release_request(send);
        self.requests.remove(&in_flight.request_id);
        let handle = conn.handle.clone();
        self.emit(Event::Sent {
            request_id: in_flight.request_id,
            connection_handle: handle,
        });
        self.pump_sends(token);
    }

    fn on_write_failed(&mut self, token: Token, send: Token, failure: Failure) {
        let Some(conn) = self.conns.get(&token) else {
            return;
        };
        let Some(in_flight) = conn
            .in_flight
            .as_ref()
            .filter(|in_flight| in_flight.send == send)
        else {
            return;
        };
        let request_id = in_flight.request_id.clone();
        self.drop_conn(token, Teardown::WriteFailed(request_id, failure));
    }

    // ---- inbound GATT records ---------------------------------------------------------------------------

    fn on_fragment(&mut self, token: Token, bytes: Payload) {
        let now = self.now;
        let Some(conn) = self.conns.get_mut(&token).filter(|conn| conn.open) else {
            return;
        };
        let outcome = match conn.reassembler.as_mut() {
            Some(reassembler) => reassembler.push(bytes.as_slice(), now),
            None => return,
        };
        let handle = conn.handle.clone();
        match outcome {
            PushOutcome::Complete(record) => {
                self.emit(Event::Message {
                    connection_handle: handle,
                    record,
                });
            }
            PushOutcome::Disconnect => self.drop_conn(token, Teardown::Lost),
            PushOutcome::Incomplete | PushOutcome::Ignored | PushOutcome::Discarded(_) => {}
        }
    }

    // ---- backend events -----------------------------------------------------------------------------------------

    fn on_backend(&mut self, event: BackendEvent) {
        match event {
            BackendEvent::ScanStarted { scan } => self.on_scan_started(scan),
            BackendEvent::ScanFailed { scan, failure } => self.on_scan_failed(scan, failure),
            BackendEvent::ScanEnded { scan, failure } => self.on_scan_ended(scan, failure),
            BackendEvent::ScanReleased { scan } => self.on_scan_released(scan),
            BackendEvent::Advertisement { scan, device } => self.on_advertisement(scan, device),
            BackendEvent::Connected {
                conn,
                capacity,
                platform_hint,
            } => self.on_connected(conn, capacity, platform_hint),
            BackendEvent::ConnectFailed { conn, failure } => {
                if self
                    .conns
                    .get(&conn)
                    .is_some_and(|conn| !conn.open && !conn.closing)
                {
                    self.drop_conn(conn, Teardown::SetupFailed(failure));
                }
            }
            BackendEvent::CapacityChanged { conn, capacity } => {
                self.on_capacity_changed(conn, capacity)
            }
            BackendEvent::Fragment { conn, bytes } => self.on_fragment(conn, bytes),
            BackendEvent::WriteDone { conn, send } => self.on_write_done(conn, send),
            BackendEvent::WriteFailed {
                conn,
                send,
                failure,
            } => self.on_write_failed(conn, send, failure),
            BackendEvent::LinkLost { conn } => self.drop_conn(conn, Teardown::Lost),
            BackendEvent::LinkReleased { conn } => self.on_link_released(conn),
            BackendEvent::ShutdownComplete => self.on_shutdown_complete(),
        }
    }

    /// The frozen budget is only ever replaced by a connection teardown: an
    /// identical derived budget (e.g. one within the 512-byte clamp) is a
    /// no-op; anything else invalidates it, cancels the affected work and
    /// closes the link — never re-fragmenting an in-flight record.
    fn on_capacity_changed(&mut self, token: Token, capacity: LinkCapacity) {
        match LinkBudget::from_capacity(capacity) {
            // An identical derived budget (e.g. one within the 512-byte
            // clamp): no wire change, nothing cancelled.
            Ok(fresh) if self.link_budget(token) == Some(fresh) => {}
            Ok(_) => self.drop_conn(token, Teardown::CapacityChanged("capacity_changed")),
            Err(BudgetError::Undetermined) => {
                self.drop_conn(token, Teardown::CapacityChanged("write_capacity"))
            }
            Err(_) => self.drop_conn(token, Teardown::CapacityChanged("mtu_range")),
        }
    }

    fn link_budget(&self, token: Token) -> Option<LinkBudget> {
        self.conns.get(&token).and_then(|conn| conn.link)
    }

    fn on_link_released(&mut self, token: Token) {
        if self.conns.get(&token).is_some_and(|conn| !conn.closing) {
            // Defensive: a release observed for a still-open link is a loss
            // (the OS has let go of the link either way).
            self.drop_conn(token, Teardown::Lost);
        }
        self.conns.remove(&token);
        self.config.ingress.release_connection(token);
    }

    /// A process-wide release terminal was delivered. Attribution first: the
    /// terminal confirms cleanup only when its delivery generation equals the
    /// generation claimed immediately before the (one) genuine release
    /// submission — a stale terminal that pre-dates the claim can neither
    /// complete the handshake nor fabricate the observation.
    ///
    /// With the handshake armed and the terminal stale, the handshake is NOT
    /// torn down: it keeps waiting for its attributed terminal (which the
    /// ingress may still have queued behind the stale one), so a stale event
    /// can never bypass — or preempt — the real release's confirmation; the
    /// Task-5 runtime bounds this wait and fails closed at its deadline.
    /// Without an armed handshake an unexpected (never attributed) terminal
    /// is a bogus backend callback whose delivery closed the ingress' own
    /// admission: the host fails closed for a full cleanup — no accepted
    /// request is stranded behind the closed ingress, and no event follows.
    fn on_shutdown_complete(&mut self) {
        if self.config.ingress.delivered_terminal_gen() != self.release_gen {
            if self.shutdown.is_some() {
                // Armed handshake: the attributed terminal may still be
                // queued behind this stale one. Keep waiting (no exit set,
                // nothing emitted).
                return;
            }
            self.finish(EXIT_HOST_FAILURE);
            return;
        }
        self.shutdown_complete_seen = true;
        let Some(request_id) = self.shutdown.take() else {
            // Unreachable in production: every pre-exit attribution has the
            // armed handshake's id (exit paths pass through the post-exit
            // bookkeeping above). Fail closed for an attributed-but-unarmed
            // topology instead of emitting anything.
            self.finish(EXIT_HOST_FAILURE);
            return;
        };
        if self.emit(Event::ShutdownComplete { request_id }) {
            self.exit = Some(EXIT_OK);
        }
    }

    // ---- time -------------------------------------------------------------------------------------------

    fn on_tick(&mut self) {
        let now = self.now;
        let mut expired: Vec<(u64, String, Token)> = self
            .requests
            .iter()
            .filter(|(_, request)| request.deadline.is_some_and(|deadline| now >= deadline))
            .filter_map(|(id, request)| match request.kind {
                ReqKind::Connect(token) | ReqKind::Send(token) => {
                    Some((request.seq, id.clone(), token))
                }
                ReqKind::StartScan => None,
            })
            .collect();
        expired.sort();
        for (_, request_id, token) in expired {
            if self.requests.contains_key(&request_id) {
                self.drop_conn(token, Teardown::Timeout(request_id));
            }
            if self.exit.is_some() {
                return;
            }
        }
        for conn in self.conns.values_mut() {
            if let Some(reassembler) = conn.reassembler.as_mut() {
                reassembler.expire(now);
            }
        }
        let capped: Vec<(Token, String)> = self
            .scans
            .iter()
            .filter(|(_, scan)| {
                scan.started
                    && !scan.closing
                    && now.saturating_duration_since(scan.started_at) >= SCAN_CAP
            })
            .map(|(token, scan)| (*token, scan.handle.clone()))
            .collect();
        for (token, handle) in capped {
            if let Some(scan) = self.scans.get_mut(&token) {
                scan.closing = true;
            }
            self.backend.stop_scan(token);
            if !self.emit(Event::ScanState {
                request_id: None,
                scan_handle: handle,
                started: false,
            }) {
                return;
            }
        }
    }

    // ---- shutdown ---------------------------------------------------------------------------------------

    /// 1. Every other outstanding request is errored (admission order, each
    ///    once). 2. The unsolicited lifecycle events follow: scan stops, then
    ///    disconnects of open links. 3. The handshake is armed atomically
    ///    under the ingress state lock (see
    ///    `ingress::BackendReceiver::arm_shutdown`), and only then is every
    ///    OS resource released (fire-and-forget) and the state dropped (the
    ///    release generation is claimed immediately before the submission);
    ///    the final completion is emitted only once the backend's
    ///    generation-attributed `ShutdownComplete` — the terminal whose
    ///    delivery generation equals the claimed one — is observed after the
    ///    arm; a pre-arm terminal fails the host closed instead, and a stale
    ///    post-arm terminal is skipped by attribution (never a completion).
    fn shutdown(&mut self, id: String) {
        let mut pending: Vec<(u64, String, Option<String>)> = self
            .requests
            .iter()
            .map(|(request_id, request)| {
                (
                    request.seq,
                    request_id.clone(),
                    match request.kind {
                        ReqKind::Send(token) | ReqKind::Connect(token) => self
                            .conns
                            .get(&token)
                            .filter(|conn| conn.open)
                            .map(|conn| conn.handle.clone()),
                        ReqKind::StartScan => None,
                    },
                )
            })
            .collect();
        pending.sort();
        for (_, request_id, handle) in pending {
            if !self.error(
                Some(&request_id),
                ErrorCode::Internal,
                false,
                "shutdown",
                None,
                handle.as_deref(),
            ) {
                return;
            }
        }
        let mut scans: Vec<(Token, String)> = self
            .scans
            .iter()
            .filter(|(_, scan)| scan.started && !scan.closing)
            .map(|(token, scan)| (*token, scan.handle.clone()))
            .collect();
        scans.sort_by_key(|(token, _)| *token);
        for (_, handle) in scans {
            if !self.emit(Event::ScanState {
                request_id: None,
                scan_handle: handle,
                started: false,
            }) {
                return;
            }
        }
        let mut open: Vec<(Token, String)> = self
            .conns
            .iter()
            .filter(|(_, conn)| conn.open && !conn.closing)
            .map(|(token, conn)| (*token, conn.handle.clone()))
            .collect();
        open.sort_by_key(|(token, _)| *token);
        for (_, handle) in open {
            if !self.emit(Event::Disconnected {
                request_id: None,
                connection_handle: handle,
            }) {
                return;
            }
        }
        // The handshake is ARMED under the ingress state lock BEFORE the
        // backend's release submission, so the arm is atomically ordered
        // against every backend callback: a process-wide terminal observed
        // before this instant (staged but undrained, or delivered) predates
        // the handshake and permanently makes it unexpected (the host fails
        // closed when the terminal drains — never a completion), while a
        // terminal reported after the arm completes it — including one a
        // synchronous adapter reports from inside the `shutdown()` submission
        // this release now performs. There is no check-then-arm gap and no
        // success without an observed real release.
        match self.config.ingress.arm_shutdown() {
            ArmedShutdown::Armed => {
                self.shutdown = Some(id);
                self.release_all();
            }
            ArmedShutdown::UnexpectedTerminal => {
                // The already-staged terminal drains to the unarmed core and
                // fails it closed with no `shutdown_complete` (see
                // `on_shutdown_complete`).
                self.finish(EXIT_HOST_FAILURE);
            }
        }
    }
}

enum PumpStep {
    Idle,
    Prepared {
        request_id: String,
        record_len: usize,
        frames: Vec<Payload>,
    },
    FragmentError {
        request_id: String,
        record_len: usize,
    },
}

/// The in-module lifetime-counter exhaustion tests (private state access; the
/// integration tests cover everything reachable through the public seams).
#[cfg(test)]
mod tests {
    use super::*;
    use crate::ingress::BackendEvents;
    use crate::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES};

    #[derive(Default)]
    struct Probe {
        commands: std::sync::Arc<std::sync::Mutex<Vec<&'static str>>>,
    }

    impl Backend for Probe {
        fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
            (!hint.is_empty()).then(|| NormalizedTarget::new(hint.to_string()))
        }
        fn start_scan(&mut self, _scan: Token, _service_uuid: &str) {
            self.commands.lock().unwrap().push("start_scan");
        }
        fn stop_scan(&mut self, _scan: Token) {
            self.commands.lock().unwrap().push("stop_scan");
        }
        fn connect(&mut self, _conn: Token, _target: &BackendTarget) {
            self.commands.lock().unwrap().push("connect");
        }
        fn write_fragments(&mut self, _conn: Token, _send: Token, _frames: Vec<Payload>) {
            self.commands.lock().unwrap().push("write");
        }
        fn disconnect(&mut self, _conn: Token) {
            self.commands.lock().unwrap().push("disconnect");
        }
        fn shutdown(&mut self) {
            self.commands.lock().unwrap().push("shutdown");
        }
    }

    fn frame_json(value: &serde_json::Value) -> Input {
        Input::Frame(Payload::new(serde_json::to_vec(value).unwrap()))
    }

    fn make(monotonic: u64, counter: u64) -> (Core<Probe>, Arc<OutQueue>) {
        let (events, receiver) = BackendEvents::new();
        let _ = events;
        let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
        let config = Config {
            bridge_version: "test/0".into(),
            platform: "windows",
            ingress: receiver,
        };
        let mut core = Core::new(Probe::default(), out.clone(), config);
        core.monotonic = monotonic;
        core.handles.counter = counter;
        (core, out)
    }

    #[test]
    fn emit_on_a_writer_closed_queue_exits_clean_not_overflow() {
        // Deterministic regression for the broken-stdout race: the writer closed the queue after
        // a failed delivery before the runtime consumed its notice, then the next input emits.
        let (mut core, out) = make(0, 0);
        out.close();
        let exit = core.handle(
            frame_json(&serde_json::json!({
                "envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]
            })),
            Instant::now(),
        );
        assert_eq!(exit, Some(EXIT_OK));
    }

    #[test]
    fn emit_on_a_full_queue_still_exits_overflow() {
        let (events, receiver) = BackendEvents::new();
        let _ = events;
        let out = OutQueue::new(0, MAX_QUEUED_BYTES);
        let config = Config {
            bridge_version: "test/0".into(),
            platform: "windows",
            ingress: receiver,
        };
        let mut core = Core::new(Probe::default(), out, config);
        let exit = core.handle(
            frame_json(&serde_json::json!({
                "envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]
            })),
            Instant::now(),
        );
        assert_eq!(exit, Some(EXIT_OUTPUT_OVERFLOW));
    }

    #[test]
    fn token_counter_exhaustion_fails_before_wrap_and_reuse() {
        let (mut core, out) = make(u64::MAX, 0);
        let _ = out.pop(Duration::ZERO); // ready a pre-emptive empty drain: queue is empty
        core.handle(
            frame_json(&serde_json::json!({
                "envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]
            })),
            Instant::now(),
        );
        // The next allocation would wrap: admission fails closed instead.
        core.handle(
            frame_json(&serde_json::json!({
                "envelope_version":1,"api_version":1,"type":"start_scan",
                "request_id":"s1","service_uuid":"8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"
            })),
            Instant::now(),
        );
        assert_eq!(core.exit, Some(EXIT_HOST_FAILURE));
        assert!(
            core.backend.commands.lock().unwrap().last() == Some(&"shutdown"),
            "the exhausted allocation never reached the backend: {:?}",
            core.backend.commands.lock().unwrap()
        );
        // The exit is stable; the same counter never wraps into reuse.
        assert_eq!(
            core.handle(Input::Tick, Instant::now()),
            Some(EXIT_HOST_FAILURE)
        );
    }

    #[test]
    fn handle_generator_exhaustion_fails_closed_before_reuse() {
        let (mut core, out) = make(0, u64::MAX);
        let _ = out.pop(Duration::ZERO);
        core.handle(
            frame_json(&serde_json::json!({
                "envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]
            })),
            Instant::now(),
        );
        core.handle(
            frame_json(&serde_json::json!({
                "envelope_version":1,"api_version":1,"type":"start_scan",
                "request_id":"s1","service_uuid":"8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"
            })),
            Instant::now(),
        );
        assert_eq!(core.exit, Some(EXIT_HOST_FAILURE));
        assert!(
            !core
                .backend
                .commands
                .lock()
                .unwrap()
                .contains(&"start_scan"),
            "no submission for an impossible handle"
        );
        // The emitted handles stay unique format-wise in ordinary operation.
        let (mut core, _) = make(0, 0);
        let mut seen = std::collections::HashSet::new();
        for _ in 0..64 {
            let handle = core.handles.try_next().expect("fresh handle");
            assert!(
                handle.len() == 16
                    && handle
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
                "{handle}"
            );
            seen.insert(handle);
        }
        assert_eq!(seen.len(), 64, "handles are never reused");
    }
}
