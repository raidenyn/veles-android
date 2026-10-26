//! Deterministic harness: a recording fake backend, a fake clock and an inspectable output queue.
#![allow(dead_code)]

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use veles_otp23_feasibility::backend::{Backend, BackendEvent, Discovered, Failure, Target, Token};
use veles_otp23_feasibility::core::{Config, Core, Input};
use veles_otp23_feasibility::fragment::{LinkCapacity, fragment};
use veles_otp23_feasibility::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop};

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Call {
    StartScan(Token, String),
    StopScan(Token),
    Connect(Token, Target),
    Write(Token, Token, Vec<Vec<u8>>),
    Disconnect(Token),
    Shutdown,
}

#[derive(Clone, Default)]
pub struct FakeBackend {
    pub calls: Arc<Mutex<Vec<Call>>>,
}

impl Backend for FakeBackend {
    fn start_scan(&mut self, scan: Token, service_uuid: &str) {
        self.calls
            .lock()
            .unwrap()
            .push(Call::StartScan(scan, service_uuid.to_string()));
    }
    fn stop_scan(&mut self, scan: Token) {
        self.calls.lock().unwrap().push(Call::StopScan(scan));
    }
    fn connect(&mut self, conn: Token, target: &Target) {
        self.calls
            .lock()
            .unwrap()
            .push(Call::Connect(conn, target.clone()));
    }
    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Vec<u8>>) {
        self.calls
            .lock()
            .unwrap()
            .push(Call::Write(conn, send, frames));
    }
    fn disconnect(&mut self, conn: Token) {
        self.calls.lock().unwrap().push(Call::Disconnect(conn));
    }
    fn shutdown(&mut self) {
        self.calls.lock().unwrap().push(Call::Shutdown);
    }
}

pub const SCAN_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";

pub struct Rig {
    pub core: Core<FakeBackend>,
    pub out: Arc<OutQueue>,
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
        let config = Config {
            bridge_version: "otp23-test/0".into(),
            platform: "windows",
        };
        let core = Core::new(backend, out.clone(), config);
        Rig {
            core,
            out,
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

    pub fn input(&mut self, input: Input) {
        let result = self.core.handle(input, self.now);
        if self.exit.is_none() {
            self.exit = result;
        } else {
            assert_eq!(
                result, self.exit,
                "an exited core keeps reporting its exit code"
            );
        }
    }

    pub fn send(&mut self, value: Value) {
        self.input(Input::Frame(serde_json::to_vec(&value).unwrap()));
    }

    pub fn send_raw(&mut self, bytes: &[u8]) {
        self.input(Input::Frame(bytes.to_vec()));
    }

    pub fn backend(&mut self, event: BackendEvent) {
        self.input(Input::Backend(event));
    }

    pub fn advance(&mut self, by: Duration) {
        self.now += by;
        self.input(Input::Tick);
    }

    /// Drains every queued message (as a writer thread would) and parses it.
    pub fn events(&mut self) -> Vec<Value> {
        let mut out = Vec::new();
        while let Pop::Frame(bytes) = self.out.pop(Duration::ZERO) {
            out.push(serde_json::from_slice(&bytes).expect("host output is JSON"));
            self.out.finish_write();
        }
        out
    }

    pub fn calls(&self) -> Vec<Call> {
        self.calls.lock().unwrap().clone()
    }

    pub fn clear_calls(&self) {
        self.calls.lock().unwrap().clear();
    }

    pub fn hello(&mut self) {
        self.send(
            json!({"envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]}),
        );
        let events = self.events();
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["type"], "ready");
    }

    pub fn req(&mut self, kind: &str, id: &str, extra: Value) {
        let mut value = json!({"envelope_version":1,"api_version":1,"type":kind,"request_id":id});
        for (key, field) in extra.as_object().unwrap() {
            value[key] = field.clone();
        }
        self.send(value);
    }

    pub fn start_scan_token(&self) -> Token {
        self.calls()
            .iter()
            .rev()
            .find_map(|c| {
                if let Call::StartScan(t, _) = c {
                    Some(*t)
                } else {
                    None
                }
            })
            .expect("a StartScan call")
    }

    pub fn connect_token(&self) -> Token {
        self.calls()
            .iter()
            .rev()
            .find_map(|c| {
                if let Call::Connect(t, _) = c {
                    Some(*t)
                } else {
                    None
                }
            })
            .expect("a Connect call")
    }

    /// Opens a scan and returns (scan_handle, backend token).
    pub fn open_scan(&mut self, id: &str) -> (String, Token) {
        self.req("start_scan", id, json!({"service_uuid": SCAN_UUID}));
        let token = self.start_scan_token();
        self.backend(BackendEvent::ScanStarted { scan: token });
        let events = self.events();
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["type"], "scan_state");
        assert_eq!(events[0]["status"], "started");
        assert_eq!(events[0]["request_id"], id);
        (
            events[0]["scan_handle"].as_str().unwrap().to_string(),
            token,
        )
    }

    pub fn advertise(&mut self, scan: Token, hint: &str, label: &str) {
        self.backend(BackendEvent::Advertisement {
            scan,
            device: Discovered {
                platform_hint: hint.to_string(),
                label: label.to_string(),
                advertisement: None,
            },
        });
    }

    /// Connects by platform hint and completes GATT setup. Returns (connection_handle, token).
    pub fn connect_hint(&mut self, id: &str, hint: &str, mtu: usize) -> (String, Token) {
        self.req("connect", id, json!({"platform_hint": hint}));
        let token = self.connect_token();
        self.backend(BackendEvent::Connected {
            conn: token,
            capacity: LinkCapacity::AttMtu(mtu),
            platform_hint: None,
        });
        let events = self.events();
        assert_eq!(events.len(), 1, "{events:?}");
        assert_eq!(events[0]["type"], "connected");
        assert_eq!(events[0]["request_id"], id);
        (
            events[0]["connection_handle"].as_str().unwrap().to_string(),
            token,
        )
    }

    /// Starts a `send` and returns the (conn token, send token) of the Write call it produced.
    pub fn last_write(&self) -> (Token, Token, Vec<Vec<u8>>) {
        self.calls()
            .iter()
            .rev()
            .find_map(|c| {
                if let Call::Write(a, b, f) = c {
                    Some((*a, *b, f.clone()))
                } else {
                    None
                }
            })
            .expect("a Write call")
    }

    pub fn inbound(&mut self, conn: Token, record: &[u8], mtu: usize, transaction: u8) {
        for frame in fragment(record, crate::common::budget(mtu), transaction).unwrap() {
            self.backend(BackendEvent::Fragment { conn, bytes: frame });
        }
    }
}

pub fn failure(stage: &'static str, retryable: bool) -> Failure {
    Failure::new(stage, retryable)
}

pub fn is_handle(value: &Value) -> bool {
    value.as_str().is_some_and(|s| {
        s.len() == 16
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

pub fn types(events: &[Value]) -> Vec<String> {
    events
        .iter()
        .map(|e| e["type"].as_str().unwrap_or("?").to_string())
        .collect()
}
