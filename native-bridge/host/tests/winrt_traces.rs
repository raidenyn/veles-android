//! Adapter event traces against the real core and ingress (Task 6).
//!
//! These are FIXTURES: a deterministic model of the Windows adapter's event discipline built
//! from the REAL portable adapter parts (`winrt_map::normalize_hint`, `closejoin::ReleaseGate`),
//! driven through the real `Core` and `BackendEvents`. They prove the core/adapter contract
//! (release provenance, per-target fencing, stale-callback handling, one slow link never gating
//! another) on any host. They do NOT exercise the WinRT code in `src/winrt.rs`, which compiles
//! and runs on Windows only, and they are never physical evidence.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::backend::{Backend, BackendEvent, NormalizedTarget, Target, Token};
use veles_ble_host::buffer::Payload;
use veles_ble_host::closejoin::ReleaseGate;
use veles_ble_host::core::{Config, Core, Input};
use veles_ble_host::fragment::LinkCapacity;
use veles_ble_host::ingress::{BackendEvents, BackendPop, BackendReceiver, EmitStatus};
use veles_ble_host::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop as OutPop};
use veles_ble_host::winrt_map::normalize_hint;

type Gates = Arc<Mutex<HashMap<Token, Arc<ReleaseGate>>>>;
type Log = Arc<Mutex<Vec<String>>>;

/// The adapter's submission discipline, minus the WinRT calls: `connect` only registers the
/// link's gate, `disconnect` asks for the release and announces it iff the "worker" already
/// finished — exactly the `WinrtBackend::disconnect` / `FinishGuard` split.
struct ModelAdapter {
    events: BackendEvents,
    gates: Gates,
    log: Log,
}

impl Backend for ModelAdapter {
    fn start_scan(&mut self, scan: Token, _service_uuid: &str) {
        self.log.lock().unwrap().push(format!("scan {scan}"));
    }
    fn stop_scan(&mut self, scan: Token) {
        self.log.lock().unwrap().push(format!("stop_scan {scan}"));
        // The adapters' scan-release obligation: one `ScanReleased` per started scan (the
        // worker's last act), otherwise the core keeps the scan counted against its 2-scan cap.
        self.events.emit(BackendEvent::ScanReleased { scan });
    }
    fn connect(&mut self, conn: Token, target: &Target) {
        let Target::Hint(hint) = target;
        self.log
            .lock()
            .unwrap()
            .push(format!("connect {conn} {hint}"));
        self.gates.lock().unwrap().insert(conn, ReleaseGate::new());
    }
    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        normalize_hint(hint)
    }
    fn write_fragments(&mut self, conn: Token, send: Token, _frames: Vec<Payload>) {
        self.log
            .lock()
            .unwrap()
            .push(format!("write {conn} {send}"));
    }
    fn disconnect(&mut self, conn: Token) {
        self.log.lock().unwrap().push(format!("disconnect {conn}"));
        let gate = self.gates.lock().unwrap().get(&conn).cloned();
        if let Some(gate) = gate
            && gate.request()
        {
            self.events.emit(BackendEvent::LinkReleased { conn });
        }
    }
    fn shutdown(&mut self) {
        self.log.lock().unwrap().push("shutdown".into());
    }
}

struct Rig {
    core: Core<ModelAdapter>,
    out: Arc<OutQueue>,
    events: BackendEvents,
    receiver: BackendReceiver,
    gates: Gates,
    log: Log,
    now: Instant,
}

impl Rig {
    fn ready() -> Self {
        let gates: Gates = Arc::default();
        let log: Log = Arc::default();
        let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
        let (events, receiver) = BackendEvents::new();
        let adapter = ModelAdapter {
            events: events.clone(),
            gates: Arc::clone(&gates),
            log: Arc::clone(&log),
        };
        let core = Core::new(
            adapter,
            out.clone(),
            Config {
                bridge_version: "otp24-test/0".into(),
                platform: "windows",
                ingress: receiver.clone(),
            },
        );
        let mut rig = Self {
            core,
            out,
            events,
            receiver,
            gates,
            log,
            now: Instant::now(),
        };
        rig.frame(
            json!({"envelope_version":1,"type":"hello","request_id":"hello","api_versions":[1]}),
        );
        assert_eq!(rig.drain()[0]["type"], "ready");
        rig
    }

    fn frame(&mut self, value: Value) {
        let bytes = serde_json::to_vec(&value).unwrap();
        assert_eq!(
            self.core
                .handle(Input::Frame(Payload::new(bytes)), self.now),
            None
        );
    }

    fn req(&mut self, kind: &str, id: &str, extra: Value) {
        let mut value = json!({"envelope_version":1,"api_version":1,"type":kind,"request_id":id});
        for (key, field) in extra.as_object().unwrap() {
            value[key.clone()] = field.clone();
        }
        self.frame(value);
    }

    fn pump(&mut self) {
        while let BackendPop::Event(event) = self.receiver.pop_event(Some(Duration::ZERO)) {
            assert_eq!(self.core.handle(Input::Backend(event), self.now), None);
        }
    }

    fn inject(&mut self, event: BackendEvent) {
        assert_eq!(self.events.emit(event), EmitStatus::Accepted);
        self.pump();
    }

    fn drain(&mut self) -> Vec<Value> {
        let mut parsed = Vec::new();
        while let OutPop::Frame(payload) = self.out.pop(Duration::ZERO) {
            parsed.push(serde_json::from_slice(payload.as_slice()).unwrap());
            self.out.finish_write();
        }
        parsed
    }

    fn connect_calls(&self) -> usize {
        self.log
            .lock()
            .unwrap()
            .iter()
            .filter(|line| line.starts_with("connect "))
            .count()
    }

    fn conn_token(&self, hint: &str) -> Token {
        self.log
            .lock()
            .unwrap()
            .iter()
            .rev()
            .find_map(|line| {
                let mut parts = line.split(' ');
                (parts.next() == Some("connect") && line.ends_with(hint))
                    .then(|| parts.next().unwrap().parse().unwrap())
            })
            .expect("connect call")
    }

    /// Connects and completes setup; returns (handle, token).
    fn open(&mut self, id: &str, hint: &str) -> (String, Token) {
        self.req("connect", id, json!({"platform_hint": hint}));
        let token = self.conn_token(hint);
        self.inject(BackendEvent::Connected {
            conn: token,
            capacity: LinkCapacity::AttMtu(517),
            platform_hint: None,
        });
        let events = self.drain();
        assert_eq!(events[0]["type"], "connected", "{events:?}");
        (
            events[0]["connection_handle"].as_str().unwrap().to_string(),
            token,
        )
    }

    /// The "worker" finished closing every OS resource of the link (the `FinishGuard`).
    fn worker_finished(&mut self, conn: Token) {
        let gate = self.gates.lock().unwrap().get(&conn).cloned().unwrap();
        if gate.finish() {
            self.inject(BackendEvent::LinkReleased { conn });
        }
    }
}

#[test]
fn a_slow_link_release_never_gates_another_link_and_fences_only_its_own_target() {
    let mut rig = Rig::ready();
    let (slow_handle, slow) = rig.open("c1", "winrt:aabbccddeeff:1");
    let (fast_handle, fast) = rig.open("c2", "winrt:112233445566:0");

    rig.req(
        "disconnect",
        "d1",
        json!({"connection_handle": slow_handle}),
    );
    rig.req(
        "disconnect",
        "d2",
        json!({"connection_handle": fast_handle}),
    );
    assert_eq!(
        rig.drain().len(),
        2,
        "both disconnects answered immediately"
    );

    // The fast link's worker finished: its release is observed while the slow worker is
    // still wedged inside an OS call.
    rig.worker_finished(fast);
    rig.req(
        "connect",
        "c3",
        json!({"platform_hint": "winrt:112233445566:2"}),
    );
    assert_eq!(rig.connect_calls(), 3, "the released target reconnects");

    // The slow target stays fenced (any observation variant) until ITS release.
    rig.req(
        "connect",
        "c4",
        json!({"platform_hint": "winrt:AABBCCDDEEFF:0"}),
    );
    let events = rig.drain();
    assert_eq!(events[0]["stage"], "already_connected", "{events:?}");
    assert_eq!(
        rig.connect_calls(),
        3,
        "no submission for the fenced target"
    );

    rig.worker_finished(slow);
    rig.req(
        "connect",
        "c5",
        json!({"platform_hint": "winrt:AABBCCDDEEFF:0"}),
    );
    assert_eq!(
        rig.connect_calls(),
        4,
        "the observed release lifts the fence"
    );
}

#[test]
fn release_is_observed_only_when_the_worker_finishes_even_if_disconnect_comes_first() {
    let mut rig = Rig::ready();
    let (handle, token) = rig.open("c1", "winrt:aabbccddeeff:1");
    rig.req("disconnect", "d1", json!({"connection_handle": handle}));
    rig.drain();
    // No LinkReleased until the worker finished: the target is still fenced.
    rig.req(
        "connect",
        "c2",
        json!({"platform_hint": "winrt:aabbccddeeff:2"}),
    );
    assert_eq!(rig.drain()[0]["stage"], "already_connected");
    rig.worker_finished(token);
    rig.req(
        "connect",
        "c3",
        json!({"platform_hint": "winrt:aabbccddeeff:2"}),
    );
    assert_eq!(rig.connect_calls(), 2);
}

#[test]
fn setup_failure_closes_before_the_release_and_lifts_the_fence_only_then() {
    let mut rig = Rig::ready();
    rig.req(
        "connect",
        "c1",
        json!({"platform_hint": "winrt:aabbccddeeff:1"}),
    );
    let token = rig.conn_token("winrt:aabbccddeeff:1");
    // The worker abandons its partially opened OS objects, reports ConnectFailed and only
    // then finishes: finish before any request announces nothing.
    let gate = rig.gates.lock().unwrap().get(&token).cloned().unwrap();
    rig.inject(BackendEvent::ConnectFailed {
        conn: token,
        failure: veles_ble_host::backend::Failure::new("radio_off", true),
    });
    let events = rig.drain();
    assert_eq!(events[0]["type"], "error", "{events:?}");
    assert_eq!(events[0]["stage"], "radio_off");
    // The core's disconnect after the failure requested the release; the worker's finish
    // is the last arrival and announces it.
    assert!(gate.is_requested() && !gate.is_finished());
    rig.req(
        "connect",
        "c2",
        json!({"platform_hint": "winrt:aabbccddeeff:1"}),
    );
    assert_eq!(rig.drain()[0]["stage"], "already_connected");
    rig.worker_finished(token);
    rig.req(
        "connect",
        "c3",
        json!({"platform_hint": "winrt:aabbccddeeff:1"}),
    );
    assert_eq!(rig.connect_calls(), 2, "restored: a fresh request connects");
}

#[test]
fn stale_callbacks_of_a_released_link_are_ignored_without_output() {
    let mut rig = Rig::ready();
    let (handle, token) = rig.open("c1", "winrt:aabbccddeeff:1");
    rig.req("disconnect", "d1", json!({"connection_handle": handle}));
    rig.worker_finished(token);
    rig.drain();
    // Late OS callbacks and completions racing the release: dropped at the ingress by the
    // retired token, and the core emits nothing.
    for event in [
        BackendEvent::Fragment {
            conn: token,
            bytes: Payload::new(vec![0, 0, 0, 0, 1]),
        },
        BackendEvent::WriteDone {
            conn: token,
            send: 1,
        },
        BackendEvent::LinkLost { conn: token },
        BackendEvent::CapacityChanged {
            conn: token,
            capacity: LinkCapacity::AttMtu(185),
        },
        BackendEvent::LinkReleased { conn: token },
    ] {
        assert_eq!(rig.events.emit(event), EmitStatus::StaleToken);
    }
    rig.pump();
    assert!(rig.drain().is_empty());
}

#[test]
fn capacity_change_to_another_budget_tears_the_link_down_and_waits_for_release() {
    let mut rig = Rig::ready();
    let (_handle, token) = rig.open("c1", "winrt:aabbccddeeff:1");
    // An identical derived budget (both clamp to 512) changes nothing.
    rig.inject(BackendEvent::CapacityChanged {
        conn: token,
        capacity: LinkCapacity::AttMtu(515),
    });
    assert!(rig.drain().is_empty());
    // A different budget disconnects the link; the target stays fenced until the release.
    rig.inject(BackendEvent::CapacityChanged {
        conn: token,
        capacity: LinkCapacity::AttMtu(247),
    });
    let events = rig.drain();
    assert_eq!(events[0]["type"], "disconnected", "{events:?}");
    assert!(
        rig.log
            .lock()
            .unwrap()
            .contains(&format!("disconnect {token}"))
    );
    rig.req(
        "connect",
        "c2",
        json!({"platform_hint": "winrt:aabbccddeeff:0"}),
    );
    assert_eq!(rig.drain()[0]["stage"], "already_connected");
    rig.worker_finished(token);
    rig.req(
        "connect",
        "c3",
        json!({"platform_hint": "winrt:aabbccddeeff:0"}),
    );
    assert_eq!(rig.connect_calls(), 2);
}

#[test]
fn unresolvable_winrt_hints_are_refused_before_any_adapter_submission() {
    let mut rig = Rig::ready();
    for hint in ["macos:aabbccddeeff", "winrt:+abbccddeeff:1", "phone-1"] {
        rig.req("connect", "c", json!({"platform_hint": hint}));
        let events = rig.drain();
        assert_eq!(events[0]["type"], "error", "{hint}: {events:?}");
    }
    assert_eq!(rig.connect_calls(), 0);
}

#[test]
fn model_adapter_releases_every_stopped_scan_so_the_two_scan_cap_never_sticks() {
    // The adapters' obligation (ScanReleased once per scan after its stop) is modelled: more
    // than two start/stop cycles keep being accepted instead of answering `cap_exceeded`.
    let mut rig = Rig::ready();
    for round in 0..5 {
        rig.req(
            "start_scan",
            &format!("s{round}"),
            json!({"service_uuid": "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"}),
        );
        let token = rig
            .log
            .lock()
            .unwrap()
            .iter()
            .rev()
            .find_map(|line| {
                line.strip_prefix("scan ")
                    .map(|t| t.parse::<Token>().unwrap())
            })
            .expect("scan call");
        rig.inject(BackendEvent::ScanStarted { scan: token });
        let started = rig.drain();
        let handle = started[0]["scan_handle"]
            .as_str()
            .unwrap_or_else(|| panic!("round {round}: {started:?}"))
            .to_string();
        rig.req(
            "stop_scan",
            &format!("x{round}"),
            json!({"scan_handle": handle}),
        );
        rig.pump(); // the model's ScanReleased
        rig.drain();
    }
}
