//! Deterministic harness of the core tests: a recording fake backend, a
//! controlled clock, the real ingress pair (producer staged from outside, the
//! runtime seam draining it), and an inspectable output queue.
//!
//! Backend completions are injected through the real [`BackendEvents`]
//! producer and delivered to the core exactly the way the Task 5 runtime will
//! drain them (one `pop_event` per `Input::Backend`), so per-link FIFO order
//! and exactly-once processing are exercised through the actual seams.

#![allow(dead_code)]

pub mod adversarial;
pub mod fake_backend;

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::backend::{BackendEvent, Discovered, Failure};
use veles_ble_host::buffer::Payload;
use veles_ble_host::core::{Config, Core, Input};
use veles_ble_host::fragment::LinkBudget;
use veles_ble_host::ingress::{BackendEvents, BackendPop, BackendReceiver, EmitStatus};
use veles_ble_host::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop as OutPop};

pub use fake_backend::{Call, FakeBackend, alias};

pub const ZERO: Duration = Duration::from_secs(0);
pub const SCAN_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";

pub fn failure(stage: &'static str, retryable: bool) -> Failure {
    Failure::new(stage, retryable)
}

pub fn budget(mtu: usize) -> LinkBudget {
    LinkBudget::from_att_mtu(mtu).expect("valid ATT MTU")
}

/// A rig with a custom message-bound output queue (byte bound at the
/// reviewed 256 KiB default).
pub fn rig_with_queue(messages: usize) -> Rig {
    Rig::with_queue(messages, 256 * 1024)
}

pub fn is_handle(value: &Value) -> bool {
    value.as_str().is_some_and(|s| {
        s.len() == 16
            && s.bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

pub fn types(events: &[Value]) -> Vec<String> {
    events
        .iter()
        .map(|event| event["type"].as_str().unwrap_or("?").to_string())
        .collect()
}

pub struct Rig {
    pub core: Core<FakeBackend>,
    pub out: Arc<OutQueue>,
    pub events: BackendEvents,
    /// The runtime-side handle of the same ingress receiver (used to drain it
    /// and to assert on its observed process-level latches).
    pub receiver: BackendReceiver,
    pub calls: Arc<Mutex<Vec<Call>>>,
    pub now: Instant,
    pub exit: Option<i32>,
}

impl Rig {
    pub fn new() -> Self {
        Self::with_queue(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES)
    }

    pub fn with_queue(messages: usize, bytes: usize) -> Self {
        let backend = FakeBackend::default();
        let calls = backend.calls.clone();
        let out = OutQueue::new(messages, bytes);
        let (events, receiver) = BackendEvents::new();
        let config = Config {
            bridge_version: "otp24-test/0".into(),
            platform: "windows",
            ingress: receiver.clone(),
        };
        let core = Core::new(backend, out.clone(), config);
        Self {
            core,
            out,
            events,
            receiver,
            calls,
            now: Instant::now(),
            exit: None,
        }
    }

    /// A rig whose fake backend reports the process-wide release
    /// synchronously inside its `shutdown()` submission (see
    /// [`FakeBackend::arm_sync_release`]).
    pub fn with_synchronous_release() -> Self {
        let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
        let (events, receiver) = BackendEvents::new();
        let mut backend = FakeBackend::default();
        backend.arm_sync_release(events.clone());
        let calls = backend.calls.clone();
        let config = Config {
            bridge_version: "otp24-test/0".into(),
            platform: "windows",
            ingress: receiver.clone(),
        };
        let core = Core::new(backend, out.clone(), config);
        Self {
            core,
            out,
            events,
            receiver,
            calls,
            now: Instant::now(),
            exit: None,
        }
    }

    pub fn ready() -> Self {
        let mut rig = Self::new();
        rig.hello();
        rig
    }

    /// Processes one input at the rig clock. The first returned exit code is
    /// remembered; an exited core must keep reporting it on later inputs.
    pub fn input(&mut self, input: Input) {
        let result = self.core.handle(input, self.now);
        if self.exit.is_none() {
            self.exit = result;
        } else {
            assert_eq!(
                self.exit, result,
                "an exited core keeps reporting its exit code"
            );
        }
    }

    pub fn frame(&mut self, value: Value) {
        self.input(Input::Frame(Payload::new(
            serde_json::to_vec(&value).unwrap(),
        )));
    }

    pub fn frame_raw(&mut self, bytes: &[u8]) {
        self.input(Input::Frame(Payload::new(bytes.to_vec())));
    }

    /// Drains the ingress exactly the way the runtime does (one event per
    /// `Input::Backend`) so every queued event is processed exactly once.
    pub fn pump_ingress(&mut self) {
        loop {
            match self.receiver.pop_event(Some(ZERO)) {
                BackendPop::Event(event) => self.input(Input::Backend(event)),
                BackendPop::Timeout => return,
            }
        }
    }

    /// Emits one backend event through the real producer (default: accepted)
    /// and immediately drains it into the core.
    pub fn inject(&mut self, event: BackendEvent) {
        let status = self.inject_status(event);
        assert_eq!(status, EmitStatus::Accepted, "staged event was expected");
        self.pump_ingress();
    }

    /// Emits without draining; returns the producer's bounded status.
    pub fn inject_status(&mut self, event: BackendEvent) -> EmitStatus {
        self.events.emit(event)
    }

    pub fn tick(&mut self) {
        self.input(Input::Tick);
    }

    pub fn advance(&mut self, by: Duration) {
        self.now += by;
        self.tick();
    }

    /// Drains every queued message (an ordinary writer) and parses the JSON.
    pub fn events(&mut self) -> Vec<Value> {
        let mut parsed = Vec::new();
        loop {
            let popped = self.out.pop(ZERO);
            match popped {
                OutPop::Frame(payload) => {
                    parsed.push(serde_json::from_slice(payload.as_slice()).unwrap());
                    self.out.finish_write();
                }
                OutPop::Timeout => return parsed,
                OutPop::Closed => panic!("queue closed with frames pending"),
            }
        }
    }

    /// Pops one frame the way a held writer does — without `finish_write`, so
    /// the frame's request-ID reservation stays with the writer.
    pub fn pop_held(&mut self) -> Vec<u8> {
        match self.out.pop(ZERO) {
            OutPop::Frame(payload) => payload.as_slice().to_vec(),
            _ => panic!("a frame was expected to be held"),
        }
    }

    pub fn calls(&self) -> Vec<Call> {
        self.calls.lock().expect("fake backend log").clone()
    }

    pub fn clear_calls(&mut self) {
        self.calls.lock().expect("fake backend log").clear();
    }

    pub fn write_calls(&self) -> Vec<(u64, u64, Vec<Payload>)> {
        self.calls
            .lock()
            .expect("fake backend log")
            .iter()
            .filter_map(|call| match call {
                Call::WriteFragments(conn, send, frames) => Some((*conn, *send, frames.clone())),
                _ => None,
            })
            .collect()
    }

    pub fn hello(&mut self) {
        self.frame(
            json!({"envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]}),
        );
        let events = self.events();
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["type"], "ready");
    }

    pub fn req(&mut self, kind: &str, id: &str, extra: Value) {
        let mut value = json!({"envelope_version":1,"api_version":1,"type":kind,"request_id":id});
        for (key, field) in extra.as_object().unwrap() {
            value[key.clone()] = field.clone();
        }
        self.frame(value);
    }

    pub fn start_scan_token(&self) -> u64 {
        self.calls()
            .iter()
            .rev()
            .find_map(|call| match call {
                Call::StartScan(token, _) => Some(*token),
                _ => None,
            })
            .expect("a StartScan call")
    }

    pub fn connect_token(&self) -> u64 {
        self.calls()
            .iter()
            .rev()
            .find_map(|call| match call {
                Call::Connect(token, _) => Some(*token),
                _ => None,
            })
            .expect("a Connect call")
    }

    /// Starts a scan and completes it. Returns (scan_handle, backend token).
    pub fn open_scan(&mut self, id: &str) -> (String, u64) {
        self.req("start_scan", id, json!({"service_uuid": SCAN_UUID}));
        let token = self.start_scan_token();
        self.inject(BackendEvent::ScanStarted { scan: token });
        let events = self.events();
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["type"], "scan_state");
        assert_eq!(events[0]["status"], "started");
        assert_eq!(events[0]["request_id"], id);
        assert!(is_handle(&events[0]["scan_handle"]), "{events:?}");
        (
            events[0]["scan_handle"].as_str().unwrap().to_string(),
            token,
        )
    }

    /// Emits one advertisement; returns the delivered `device_found` event
    /// (draining the queue into the caller's hands).
    pub fn advertise(&mut self, scan: u64, hint: &str, label: &str) -> Value {
        self.inject(BackendEvent::Advertisement {
            scan,
            device: Discovered {
                platform_hint: hint.to_string(),
                label: label.to_string(),
                advertisement: None,
            },
        });
        let events = self.events();
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["type"], "device_found");
        events.into_iter().next().unwrap()
    }

    /// Connects by platform hint at the given MTU and completes setup.
    /// Returns (connection_handle, backend token).
    pub fn connect_hint(&mut self, id: &str, hint: &str, mtu: usize) -> (String, u64) {
        self.req("connect", id, json!({"platform_hint": hint}));
        let token = self.connect_token();
        let connected = self.connected(token, mtu);
        assert_eq!(connected.len(), 1, "{connected:?}");
        assert_eq!(connected[0]["type"], "connected");
        assert_eq!(connected[0]["request_id"], id);
        (
            connected[0]["connection_handle"]
                .as_str()
                .unwrap()
                .to_string(),
            token,
        )
    }

    /// The `Connected` callback of a pending setup; returns the parsed
    /// lifecycle events (draining the queue into the caller's hands).
    pub fn connected(&mut self, token: u64, mtu: usize) -> Vec<Value> {
        self.inject(BackendEvent::Connected {
            conn: token,
            capacity: veles_ble_host::fragment::LinkCapacity::AttMtu(mtu),
            platform_hint: None,
        });
        self.events()
    }

    /// Asserts the fake backend recorded only the release submission (the
    /// pre-negotiation and fail-closed paths do exactly this).
    pub fn only_release_submissions(&self) -> bool {
        self.calls()
            .iter()
            .all(|call| matches!(call, Call::Shutdown))
    }

    /// Starts a `send` and returns the (conn token, send token, frames) of the
    /// `WriteFragments` call it produced.
    pub fn last_write(&self) -> (u64, u64, Vec<Payload>) {
        self.write_calls().pop().expect("a WriteFragments call")
    }

    /// Feeds a complete inbound record as real fragments on `conn` (each
    /// pumped into the core immediately); the resulting events (if any) stay
    /// queued in the output queue for the test to drain and assert on.
    pub fn inbound(&mut self, conn: u64, record: &[u8], mtu: usize, transaction: u8) {
        let frames = veles_ble_host::fragment::fragment(record, budget(mtu), transaction)
            .expect("test record fragments");
        for frame in frames {
            self.inject(BackendEvent::Fragment { conn, bytes: frame });
        }
    }

    pub fn scan_started(&mut self, token: u64, handle: &str) {
        self.inject(BackendEvent::ScanStarted { scan: token });
        let events = self.events();
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["type"], "scan_state");
        assert_eq!(events[0]["scan_handle"], handle);
        assert_eq!(events[0]["status"], "started");
    }
}
