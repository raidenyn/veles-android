//! Platform-neutral host state machine: negotiation, request correlation, process-local handles,
//! bounded scan/connection state, fragmentation and ordered stdout events (OTP2-BRD-010..032).
//!
//! The core is synchronous and deterministic: it consumes [`Input`]s with an explicit clock and
//! talks to the OS only through [`Backend`]. Everything it writes goes through the bounded
//! [`OutQueue`]; an output overflow fails closed (exit 4) without dropping or reordering.

use std::collections::{HashMap, HashSet, VecDeque};
use std::hash::{BuildHasher, Hasher, RandomState};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::backend::{Backend, BackendEvent, Discovered, Failure, Target as BackendTarget, Token};
use crate::fragment::{
    GlobalBudget, LinkBudget, LinkCapacity, MAX_RECORD, PushOutcome, Reassembler, fragment, wipe,
};
use crate::outqueue::OutQueue;
use crate::protocol::{
    CAPABILITY_RECORD_V1, ErrorCode, Event, Offer, ParseFailure, Phase, Recoverability, Rejection,
    Request, RequestBody, SUPPORTED_API_VERSIONS, Target, parse_request,
};

pub const EXIT_OK: i32 = 0;
pub const EXIT_IO: i32 = 1;
pub const EXIT_REJECTED: i32 = 2;
pub const EXIT_INCOMPATIBLE: i32 = 3;
pub const EXIT_OUTPUT_OVERFLOW: i32 = 4;
/// Feasibility-specific: backend callback channel overflowed (fail closed).
pub const EXIT_BACKEND_OVERFLOW: i32 = 5;

pub const MAX_OUTSTANDING_REQUESTS: usize = 64;
pub const MAX_SCANS: usize = 2;
pub const MAX_CONNECTIONS: usize = 32;
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
pub const SCAN_CAP: Duration = Duration::from_secs(300);
pub const DEVICE_FOUND_PER_SECOND: usize = 64;
pub const DEVICE_FOUND_PER_SCAN: usize = 1024;
pub const DEVICE_CACHE: usize = 1024;
pub const SEND_QUEUE_RECORDS: usize = 16;
pub const SEND_QUEUE_BYTES: usize = 16 * 1024;
pub const GLOBAL_SEND_QUEUE_RECORDS: usize = 128;
pub const GLOBAL_SEND_QUEUE_BYTES: usize = 128 * 1024;

const MAX_LABEL: usize = 64;
const MAX_HINT: usize = 256;
const MAX_ADVERTISEMENT: usize = 256;

#[derive(Debug)]
pub enum Input {
    Frame(Vec<u8>),
    /// stdin EOF == Chrome port closure.
    StdinEof,
    /// Framing violation on stdin (zero/oversize/truncated).
    StdinFraming,
    StdinIoError,
    StdoutClosed,
    BackendOverflow,
    Backend(BackendEvent),
    Tick,
}

pub struct Config {
    pub bridge_version: String,
    pub platform: &'static str,
}

enum Teardown {
    /// Requested `disconnect`: one correlated `disconnected`, no separate lifecycle event.
    Requested(String),
    /// Link loss, failed write or hostile peer: unsolicited `disconnected` for an open link.
    Lost,
    /// Request timeout: the timed-out request gets `timeout`; the link is dropped.
    Timeout(String),
    /// Failed write: the failing send gets this error.
    WriteFailed(String, Failure),
    /// Setup failure: the connect request gets this error.
    SetupFailed(Failure),
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
    started_at: Instant,
    emitted: usize,
    window: VecDeque<Instant>,
    seen: HashSet<String>,
}

struct PendingSend {
    request_id: String,
    record: Vec<u8>,
}

struct InFlight {
    request_id: String,
    send: Token,
    len: usize,
}

struct Conn {
    handle: String,
    hint: String,
    open: bool,
    connect_request: Option<String>,
    link: Option<LinkBudget>,
    reassembler: Option<Reassembler>,
    next_transaction: u8,
    pending: VecDeque<PendingSend>,
    in_flight: Option<InFlight>,
    queued_bytes: usize,
}

impl Conn {
    fn queued_records(&self) -> usize {
        self.pending.len() + usize::from(self.in_flight.is_some())
    }
}

/// Guaranteed-unique opaque handles: a keyed bijection over a counter (never reused).
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

    fn next(&mut self) -> String {
        self.counter += 1;
        let mut x = self.counter ^ self.key;
        x = x.wrapping_mul(0x9E37_79B9_7F4A_7C15);
        x ^= x >> 32;
        x = x.wrapping_mul(0xD6E8_FEB8_6659_FD93);
        x ^= x >> 32;
        format!("{x:016x}")
    }
}

fn sanitize(text: &str, max_bytes: usize) -> String {
    let mut out = String::new();
    for c in text.chars().filter(|c| !(*c <= '\u{1f}' || *c == '\u{7f}')) {
        if out.len() + c.len_utf8() > max_bytes {
            break;
        }
        out.push(c);
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
    next_token: Token,
    next_seq: u64,
    budget: Arc<GlobalBudget>,
    requests: HashMap<String, Req>,
    scans: HashMap<Token, Scan>,
    conns: HashMap<Token, Conn>,
    conn_by_handle: HashMap<String, Token>,
    devices: VecDeque<(String, String)>,
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
            next_token: 1,
            next_seq: 0,
            budget: GlobalBudget::new(),
            requests: HashMap::new(),
            scans: HashMap::new(),
            conns: HashMap::new(),
            conn_by_handle: HashMap::new(),
            devices: VecDeque::new(),
        }
    }

    pub fn backend(&self) -> &B {
        &self.backend
    }

    /// Processes one input. Returns the process exit code once the core has fully released
    /// every scan, link and handle and will accept no further input.
    pub fn handle(&mut self, input: Input, now: Instant) -> Option<i32> {
        if self.exit.is_some() {
            return self.exit;
        }
        self.now = now;
        match input {
            Input::Frame(bytes) => self.on_frame(&bytes),
            Input::StdinEof | Input::StdoutClosed => self.finish(EXIT_OK),
            Input::StdinFraming => self.finish(EXIT_REJECTED),
            Input::StdinIoError => self.finish(EXIT_IO),
            Input::BackendOverflow => self.finish(EXIT_BACKEND_OVERFLOW),
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
        let requests = self.requests.values().filter_map(|r| r.deadline);
        let scans = self
            .scans
            .values()
            .filter(|s| s.started)
            .map(|s| s.started_at + SCAN_CAP);
        let reassembly = self
            .conns
            .values()
            .filter_map(|c| c.reassembler.as_ref()?.next_expiry());
        requests.chain(scans).chain(reassembly).min()
    }

    // ---- emission ------------------------------------------------------------------------------

    fn api_version(&self) -> Option<u64> {
        match self.phase {
            Phase::PreReady => None,
            Phase::Ready { api_version } => Some(api_version),
        }
    }

    /// Queues one event. A full queue fails the whole process closed instead of dropping.
    fn emit(&mut self, event: Event) -> bool {
        if self.exit.is_some() {
            return false;
        }
        let payload = event.to_json(self.api_version());
        let request_id = event.request_id().map(str::to_string);
        if self.out.push(payload, request_id).is_err() {
            self.finish(EXIT_OUTPUT_OVERFLOW);
            return false;
        }
        true
    }

    #[allow(clippy::too_many_arguments)]
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

    // ---- release / exit --------------------------------------------------------------------------

    /// Stops every scan, releases every link, shuts the backend down and drops all state.
    fn release_all(&mut self) {
        let scans: Vec<Token> = self.scans.keys().copied().collect();
        for scan in scans {
            self.backend.stop_scan(scan);
        }
        let conns: Vec<Token> = self.conns.keys().copied().collect();
        for conn in conns {
            self.backend.disconnect(conn);
        }
        self.backend.shutdown();
        for conn in self.conns.values_mut() {
            for pending in &mut conn.pending {
                wipe(&mut pending.record);
            }
            if let Some(reassembler) = conn.reassembler.as_mut() {
                reassembler.clear();
            }
        }
        self.scans.clear();
        self.conns.clear();
        self.conn_by_handle.clear();
        self.requests.clear();
        self.devices.clear();
    }

    /// Orderly silent exit: release everything, emit nothing further.
    fn finish(&mut self, code: i32) {
        if self.exit.is_some() {
            return;
        }
        self.exit = Some(code);
        self.release_all();
    }

    // ---- inbound frames ------------------------------------------------------------------------------

    fn on_frame(&mut self, bytes: &[u8]) {
        let request = match parse_request(bytes, self.phase) {
            Ok(request) => request,
            Err(ParseFailure::Uncorrelatable) => return self.finish(EXIT_REJECTED),
            Err(ParseFailure::Rejected(rejection)) => return self.on_rejection(rejection),
        };
        if matches!(self.phase, Phase::Ready { .. }) {
            let reused = self.requests.contains_key(&request.request_id)
                || self.out.holds_request_id(&request.request_id);
            if reused {
                return self.finish(EXIT_REJECTED);
            }
        }
        if matches!(request.body, RequestBody::Hello(_)) {
            if let RequestBody::Hello(offer) = request.body {
                self.on_hello(request.request_id, offer);
            }
            return;
        }
        if self.requests.len() >= MAX_OUTSTANDING_REQUESTS
            && !matches!(request.body, RequestBody::Shutdown)
        {
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
            && (self.requests.contains_key(&request_id) || self.out.holds_request_id(&request_id))
        {
            return self.finish(EXIT_REJECTED);
        }
        self.error(
            Some(&request_id),
            code,
            recoverability == Recoverability::Retryable,
            stage,
            None,
            None,
        );
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

    fn token(&mut self) -> Token {
        let token = self.next_token;
        self.next_token += 1;
        token
    }

    fn track(&mut self, id: &str, kind: ReqKind, deadline: Option<Instant>) {
        self.next_seq += 1;
        self.requests.insert(
            id.to_string(),
            Req {
                seq: self.next_seq,
                kind,
                deadline,
            },
        );
    }

    // ---- scans ---------------------------------------------------------------------------------------------

    fn start_scan(&mut self, id: String, uuid: &str) {
        if self.scans.len() >= MAX_SCANS {
            self.error(Some(&id), ErrorCode::CapExceeded, true, "scans", None, None);
            return;
        }
        let token = self.token();
        let handle = self.handles.next();
        self.scans.insert(
            token,
            Scan {
                handle,
                request_id: Some(id.clone()),
                started: false,
                started_at: self.now,
                emitted: 0,
                window: VecDeque::new(),
                seen: HashSet::new(),
            },
        );
        self.track(&id, ReqKind::StartScan, None);
        self.backend.start_scan(token, uuid);
    }

    fn scan_by_handle(&self, handle: &str) -> Option<Token> {
        self.scans
            .iter()
            .find(|(_, s)| s.started && s.handle == handle)
            .map(|(t, _)| *t)
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
        self.backend.stop_scan(token);
        self.scans.remove(&token);
        self.emit(Event::ScanState {
            request_id: Some(id),
            scan_handle: handle.to_string(),
            started: false,
        });
    }

    fn on_advertisement(&mut self, token: Token, device: Discovered) {
        let now = self.now;
        let Some(scan) = self.scans.get_mut(&token).filter(|s| s.started) else {
            return;
        };
        let hint = device.platform_hint;
        if hint.is_empty() || hint.len() > MAX_HINT || hint.chars().any(char::is_control) {
            return;
        }
        if scan.seen.contains(&hint) || scan.emitted >= DEVICE_FOUND_PER_SCAN {
            return;
        }
        while scan
            .window
            .front()
            .is_some_and(|t| now.saturating_duration_since(*t) >= Duration::from_secs(1))
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
        let mut label = sanitize(&device.label, MAX_LABEL);
        if label.is_empty() {
            label = "BLE device".to_string();
        }
        let advertisement = device
            .advertisement
            .filter(|bytes| !bytes.is_empty())
            .map(|bytes| bytes[..bytes.len().min(MAX_ADVERTISEMENT)].to_vec());
        let device_handle = self.handles.next();
        self.cache_device(device_handle.clone(), hint.clone());
        self.emit(Event::DeviceFound {
            scan_handle,
            device_handle,
            label,
            platform_hint: Some(hint),
            advertisement,
        });
    }

    fn hint_in_use(&self, hint: &str) -> bool {
        self.conns.values().any(|c| c.hint == hint)
    }

    fn cache_device(&mut self, handle: String, hint: String) {
        if self.devices.len() >= DEVICE_CACHE {
            let evict = self.devices.iter().position(|(_, h)| !self.hint_in_use(h));
            if let Some(position) = evict {
                self.devices.remove(position);
            }
        }
        self.devices.push_back((handle, hint));
    }

    fn on_scan_started(&mut self, token: Token) {
        let now = self.now;
        let Some(scan) = self.scans.get_mut(&token).filter(|s| !s.started) else {
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
        let Some(scan) = self.scans.get(&token).filter(|s| !s.started) else {
            return;
        };
        let request = scan.request_id.clone();
        self.scans.remove(&token);
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
        let Some(scan) = self.scans.get(&token).filter(|s| s.started) else {
            return;
        };
        let handle = scan.handle.clone();
        self.scans.remove(&token);
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

    // ---- connections ------------------------------------------------------------------------------------------

    fn connect(&mut self, id: String, target: Target) {
        let hint = match target {
            Target::Hint(hint) => hint,
            Target::Device(handle) => {
                match self
                    .devices
                    .iter()
                    .find(|(h, _)| *h == handle)
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
        if self.hint_in_use(&hint) {
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
        let token = self.token();
        let handle = self.handles.next();
        self.conns.insert(
            token,
            Conn {
                handle,
                hint: hint.clone(),
                open: false,
                connect_request: Some(id.clone()),
                link: None,
                reassembler: None,
                next_transaction: 1,
                pending: VecDeque::new(),
                in_flight: None,
                queued_bytes: 0,
            },
        );
        self.track(
            &id,
            ReqKind::Connect(token),
            Some(self.now + REQUEST_TIMEOUT),
        );
        self.backend.connect(token, &BackendTarget::Hint(hint));
    }

    fn on_connected(
        &mut self,
        token: Token,
        capacity: LinkCapacity,
        refreshed_hint: Option<String>,
    ) {
        let Some(conn) = self.conns.get_mut(&token).filter(|c| !c.open) else {
            return;
        };
        let Ok(link) = LinkBudget::from_capacity(capacity) else {
            return self.drop_conn(
                token,
                Teardown::SetupFailed(Failure::new("mtu_range", false)),
            );
        };
        conn.open = true;
        conn.link = Some(link);
        conn.reassembler = Some(Reassembler::new(link, self.budget.clone()));
        let handle = conn.handle.clone();
        let request = conn.connect_request.take();
        self.conn_by_handle.insert(handle.clone(), token);
        if let Some(request_id) = request {
            self.requests.remove(&request_id);
            let platform_hint = refreshed_hint
                .map(|hint| sanitize(&hint, MAX_HINT))
                .filter(|hint| !hint.is_empty());
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

    /// Single exit path for every way a link can end. Errors for affected requests come first,
    /// then exactly one `disconnected` (correlated for a requested disconnect, otherwise
    /// unsolicited and only if the connection handle was ever disclosed).
    fn drop_conn(&mut self, token: Token, why: Teardown) {
        let Some(mut conn) = self.conns.remove(&token) else {
            return;
        };
        self.conn_by_handle.remove(&conn.handle);
        let disclosed = conn.open;
        let handle = conn.handle.clone();
        let handle_ref = disclosed.then_some(handle.as_str());

        let mut affected: Vec<(String, bool)> = Vec::new(); // (request id, is_connect)
        if let Some(request_id) = conn.connect_request.take() {
            affected.push((request_id, true));
        }
        if let Some(in_flight) = conn.in_flight.take() {
            affected.push((in_flight.request_id, false));
        }
        for mut pending in conn.pending.drain(..) {
            wipe(&mut pending.record);
            affected.push((pending.request_id, false));
        }
        self.backend.disconnect(token);
        drop(conn);

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
                Teardown::Requested(_) => (default_code, false, "disconnected"),
                _ => (default_code, false, "link_lost"),
            };
            if !self.error(Some(&request_id), code, retryable, stage, None, handle_ref) {
                return;
            }
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
    }

    // ---- send ---------------------------------------------------------------------------------------------------

    fn send(&mut self, id: String, handle: &str, record: Vec<u8>) {
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
        let (global_records, global_bytes) = self.conns.values().fold((0, 0), |(r, b), c| {
            (r + c.queued_records(), b + c.queued_bytes)
        });
        let conn = self
            .conns
            .get_mut(&token)
            .expect("handle map points at a live connection");
        if conn.queued_records() >= SEND_QUEUE_RECORDS
            || conn.queued_bytes + record.len() > SEND_QUEUE_BYTES
            || global_records >= GLOBAL_SEND_QUEUE_RECORDS
            || global_bytes + record.len() > GLOBAL_SEND_QUEUE_BYTES
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
        self.track(&id, ReqKind::Send(token), Some(self.now + REQUEST_TIMEOUT));
        self.pump_sends(token);
    }

    /// Starts the next queued record when the link has no transaction in flight.
    fn pump_sends(&mut self, token: Token) {
        let send = self.next_token;
        let Some(conn) = self.conns.get_mut(&token) else {
            return;
        };
        if conn.in_flight.is_some() {
            return;
        }
        let Some(mut pending) = conn.pending.pop_front() else {
            return;
        };
        let transaction = conn.next_transaction;
        conn.next_transaction = conn.next_transaction % 255 + 1;
        let frames = match conn.link {
            Some(link) => fragment(&pending.record, link, transaction),
            None => Err(crate::fragment::FragmentError::OversizeRecord),
        };
        wipe(&mut pending.record);
        match frames {
            Ok(frames) => {
                self.next_token += 1;
                conn.in_flight = Some(InFlight {
                    request_id: pending.request_id,
                    send,
                    len: pending.record.len(),
                });
                self.backend.write_fragments(token, send, frames);
            }
            Err(_) => {
                // Unreachable for validated records; fail the request rather than the process.
                conn.queued_bytes = conn.queued_bytes.saturating_sub(pending.record.len());
                self.requests.remove(&pending.request_id);
                let handle = conn.handle.clone();
                self.error(
                    Some(&pending.request_id),
                    ErrorCode::Internal,
                    false,
                    "fragment",
                    None,
                    Some(&handle),
                );
            }
        }
    }

    fn on_write_done(&mut self, token: Token, send: Token) {
        let Some(conn) = self.conns.get_mut(&token) else {
            return;
        };
        if conn.in_flight.as_ref().map(|f| f.send) != Some(send) {
            return;
        }
        let done = conn.in_flight.take().expect("checked above");
        // Queue accounting counts in-flight bytes; release the finished record's share.
        conn.queued_bytes = conn.queued_bytes.saturating_sub(done.len);
        let handle = conn.handle.clone();
        self.requests.remove(&done.request_id);
        self.emit(Event::Sent {
            request_id: done.request_id,
            connection_handle: handle,
        });
        self.pump_sends(token);
    }

    fn on_write_failed(&mut self, token: Token, send: Token, failure: Failure) {
        let Some(conn) = self.conns.get(&token) else {
            return;
        };
        let Some(in_flight) = conn.in_flight.as_ref().filter(|f| f.send == send) else {
            return;
        };
        let request_id = in_flight.request_id.clone();
        self.drop_conn(token, Teardown::WriteFailed(request_id, failure));
    }

    // ---- inbound GATT notifications -------------------------------------------------------------------------------------

    fn on_fragment(&mut self, token: Token, bytes: &[u8]) {
        let now = self.now;
        let Some(conn) = self.conns.get_mut(&token).filter(|c| c.open) else {
            return;
        };
        let outcome = match conn.reassembler.as_mut() {
            Some(reassembler) => reassembler.push(bytes, now),
            None => return,
        };
        let handle = conn.handle.clone();
        match outcome {
            PushOutcome::Complete(mut record) => {
                self.emit(Event::Message {
                    connection_handle: handle,
                    record: record.clone(),
                });
                wipe(&mut record);
            }
            PushOutcome::Disconnect => self.drop_conn(token, Teardown::Lost),
            PushOutcome::Incomplete | PushOutcome::Ignored | PushOutcome::Discarded(_) => {}
        }
    }

    // ---- backend events ---------------------------------------------------------------------------------------------------------

    fn on_backend(&mut self, event: BackendEvent) {
        match event {
            BackendEvent::ScanStarted { scan } => self.on_scan_started(scan),
            BackendEvent::ScanFailed { scan, failure } => self.on_scan_failed(scan, failure),
            BackendEvent::ScanEnded { scan, failure } => self.on_scan_ended(scan, failure),
            BackendEvent::Advertisement { scan, device } => self.on_advertisement(scan, device),
            BackendEvent::Connected {
                conn,
                capacity,
                platform_hint,
            } => self.on_connected(conn, capacity, platform_hint),
            BackendEvent::ConnectFailed { conn, failure } => {
                if self.conns.get(&conn).is_some_and(|c| !c.open) {
                    self.drop_conn(conn, Teardown::SetupFailed(failure));
                }
            }
            BackendEvent::Fragment { conn, bytes } => self.on_fragment(conn, &bytes),
            BackendEvent::WriteDone { conn, send } => self.on_write_done(conn, send),
            BackendEvent::WriteFailed {
                conn,
                send,
                failure,
            } => self.on_write_failed(conn, send, failure),
            BackendEvent::LinkLost { conn } => self.drop_conn(conn, Teardown::Lost),
        }
    }

    // ---- time -----------------------------------------------------------------------------------------------------------------------

    fn on_tick(&mut self) {
        let now = self.now;
        let mut expired: Vec<(u64, String, Token)> = self
            .requests
            .iter()
            .filter(|(_, r)| r.deadline.is_some_and(|d| now >= d))
            .filter_map(|(id, r)| match r.kind {
                ReqKind::Connect(token) | ReqKind::Send(token) => Some((r.seq, id.clone(), token)),
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
        let capped: Vec<Token> = self
            .scans
            .iter()
            .filter(|(_, s)| s.started && now.saturating_duration_since(s.started_at) >= SCAN_CAP)
            .map(|(t, _)| *t)
            .collect();
        for token in capped {
            if let Some(scan) = self.scans.remove(&token) {
                self.backend.stop_scan(token);
                if !self.emit(Event::ScanState {
                    request_id: None,
                    scan_handle: scan.handle,
                    started: false,
                }) {
                    return;
                }
            }
        }
    }

    // ---- shutdown ----------------------------------------------------------------------------------------------------------------------

    fn shutdown(&mut self, id: String) {
        // 1. Every other outstanding request ends with a correlated internal/fatal error.
        let mut pending: Vec<(u64, String, Option<String>)> = self
            .requests
            .iter()
            .map(|(rid, r)| {
                let handle = match r.kind {
                    ReqKind::Send(token) | ReqKind::Connect(token) => self
                        .conns
                        .get(&token)
                        .filter(|c| c.open)
                        .map(|c| c.handle.clone()),
                    ReqKind::StartScan => None,
                };
                (r.seq, rid.clone(), handle)
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
        // 2. Remaining unsolicited lifecycle events: scan stops, then disconnects of open links.
        let mut scans: Vec<(Token, String)> = self
            .scans
            .iter()
            .filter(|(_, s)| s.started)
            .map(|(t, s)| (*t, s.handle.clone()))
            .collect();
        scans.sort();
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
            .filter(|(_, c)| c.open)
            .map(|(t, c)| (*t, c.handle.clone()))
            .collect();
        open.sort();
        for (_, handle) in open {
            if !self.emit(Event::Disconnected {
                request_id: None,
                connection_handle: handle,
            }) {
                return;
            }
        }
        // 3. Release every OS resource, then the final correlated completion.
        self.release_all();
        if self.emit(Event::ShutdownComplete { request_id: id }) {
            self.exit = Some(EXIT_OK);
        }
    }
}
