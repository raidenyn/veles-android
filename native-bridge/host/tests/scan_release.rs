//! I3 / I4: a scan stays counted (pending / active / closing) until the backend OBSERVED its
//! OS release (`BackendEvent::ScanReleased`), and a scan cleanup that failed is never forgotten
//! by a later shutdown.
//!
//! Two layers, both on the real core and the real ingress, no wall-clock scheduling except
//! bounded waits for the shutdown coordinator thread:
//!
//! - the recording [`support::Rig`] (pure core/ingress semantics, explicit event injection);
//! - a [`ScriptedBackend`] built on the same shared adapter seams the WinRT and macOS
//!   backends use (`closejoin::{ScanLedger, complete_scan, spawn_release_coordinator}`) whose
//!   scan "workers" are finished by the test, cleanly, uncleanly, or never.

#![allow(unused_imports)]

mod support;

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::backend::{
    Backend, BackendEvent, Discovered, Failure, NormalizedTarget, Target, Token,
};
use veles_ble_host::buffer::Payload;
use veles_ble_host::closejoin::{Completion, ScanLedger, complete_scan, spawn_release_coordinator};
use veles_ble_host::core::{Config, Core, EXIT_OK, Input};
use veles_ble_host::ingress::{BackendEvents, BackendPop, BackendReceiver, EmitStatus};
use veles_ble_host::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop as OutPop};

use support::{Call, Rig, SCAN_UUID, ZERO, failure, types};

fn start(rig: &mut Rig, id: &str) -> Vec<Value> {
    rig.req("start_scan", id, json!({"service_uuid": SCAN_UUID}));
    rig.events()
}

fn starts(rig: &Rig) -> usize {
    rig.calls()
        .iter()
        .filter(|call| matches!(call, Call::StartScan(..)))
        .count()
}

// ---- core + ingress semantics (recording rig) -------------------------------------------------

#[test]
fn repeated_start_stop_cannot_grow_scans_past_the_cap_without_a_release() {
    let mut rig = Rig::ready();
    let (first, _t1) = rig.open_scan("s1");
    let (second, _t2) = rig.open_scan("s2");
    rig.req("stop_scan", "x1", json!({"scan_handle": first}));
    rig.req("stop_scan", "x2", json!({"scan_handle": second}));
    rig.events();
    for round in 0..10 {
        let events = start(&mut rig, &format!("again{round}"));
        assert_eq!(events.len(), 1, "round {round}: {events:?}");
        assert_eq!(events[0]["code"], "cap_exceeded", "round {round}");
        assert_eq!(events[0]["recoverability"], "retryable");
    }
    assert_eq!(starts(&rig), 2, "no worker beyond the 2-scan ceiling");
    assert_eq!(rig.exit, None);
}

#[test]
fn live_workers_never_exceed_two_over_many_start_stop_release_rounds() {
    let mut rig = Rig::ready();
    let mut unreleased: Vec<Token> = Vec::new();
    for round in 0..60usize {
        let before = starts(&rig);
        rig.req(
            "start_scan",
            &format!("s{round}"),
            json!({"service_uuid": SCAN_UUID}),
        );
        let accepted = starts(&rig) > before;
        if accepted {
            let token = rig.start_scan_token();
            rig.inject(BackendEvent::ScanStarted { scan: token });
            let events = rig.events();
            let handle = events[0]["scan_handle"].as_str().unwrap().to_string();
            rig.req(
                "stop_scan",
                &format!("x{round}"),
                json!({"scan_handle": handle}),
            );
            rig.events();
            unreleased.push(token);
        } else {
            let events = rig.events();
            assert_eq!(events[0]["code"], "cap_exceeded", "{events:?}");
        }
        assert!(unreleased.len() <= 2, "round {round}: {unreleased:?}");
        // The OS releases every third round, oldest first.
        if round % 3 == 2 && !unreleased.is_empty() {
            let token = unreleased.remove(0);
            rig.inject(BackendEvent::ScanReleased { scan: token });
            assert!(rig.events().is_empty(), "a release emits nothing");
        }
    }
    assert_eq!(rig.exit, None);
}

#[test]
fn a_release_frees_exactly_one_slot_and_its_ingress_reservation() {
    let mut rig = Rig::ready();
    let (first, t1) = rig.open_scan("s1");
    let (_second, _t2) = rig.open_scan("s2");
    rig.req("stop_scan", "x1", json!({"scan_handle": first}));
    rig.events();
    assert_eq!(start(&mut rig, "blocked")[0]["code"], "cap_exceeded");

    rig.inject(BackendEvent::ScanReleased { scan: t1 });
    assert!(rig.events().is_empty());
    rig.req("start_scan", "ok", json!({"service_uuid": SCAN_UUID}));
    assert!(rig.events().is_empty(), "accepted: pending until started");
    assert_eq!(starts(&rig), 3);
    assert_eq!(start(&mut rig, "blocked2")[0]["code"], "cap_exceeded");

    // The released token is retired: its reservation is gone at the ingress.
    assert_eq!(
        rig.inject_status(BackendEvent::ScanEnded {
            scan: t1,
            failure: None
        }),
        EmitStatus::StaleToken
    );
}

#[test]
fn pending_failed_ended_and_capped_scans_all_stay_counted_until_released() {
    let mut rig = Rig::ready();
    // (1) a pending (unstarted) scan counts.
    rig.req("start_scan", "p1", json!({"service_uuid": SCAN_UUID}));
    let pending = rig.start_scan_token();
    rig.req("start_scan", "p2", json!({"service_uuid": SCAN_UUID}));
    let pending2 = rig.start_scan_token();
    assert_eq!(start(&mut rig, "p3")[0]["code"], "cap_exceeded");

    // (2) a failed start: the request is answered, the slot stays until the release.
    rig.inject(BackendEvent::ScanFailed {
        scan: pending,
        failure: failure("radio_off", true),
    });
    let events = rig.events();
    assert_eq!(events[0]["code"], "scan_failed");
    assert_eq!(events[0]["request_id"], "p1");
    assert_eq!(start(&mut rig, "p4")[0]["code"], "cap_exceeded");
    rig.inject(BackendEvent::ScanReleased { scan: pending });
    assert!(rig.events().is_empty());

    // (3) an OS-ended scan.
    rig.inject(BackendEvent::ScanStarted { scan: pending2 });
    rig.events();
    rig.inject(BackendEvent::ScanEnded {
        scan: pending2,
        failure: Some(failure("internal_ble", false)),
    });
    assert_eq!(types(&rig.events()), vec!["error", "scan_state"]);
    rig.req("start_scan", "p5", json!({"service_uuid": SCAN_UUID}));
    let third = rig.start_scan_token();
    assert!(rig.events().is_empty(), "one slot (the failed one) freed");
    assert_eq!(start(&mut rig, "p6")[0]["code"], "cap_exceeded");
    rig.inject(BackendEvent::ScanReleased { scan: pending2 });

    // (4) the 300 s cap: the protocol answer is immediate, the slot is held.
    rig.inject(BackendEvent::ScanStarted { scan: third });
    rig.events();
    rig.advance(Duration::from_secs(300));
    assert_eq!(types(&rig.events()), vec!["scan_state"]);
    assert!(rig.calls().contains(&Call::StopScan(third)));
    rig.req("start_scan", "p7", json!({"service_uuid": SCAN_UUID}));
    let fourth = rig.start_scan_token();
    assert!(rig.events().is_empty());
    assert_eq!(start(&mut rig, "p8")[0]["code"], "cap_exceeded");
    rig.advance(Duration::from_secs(1));
    assert!(
        rig.events().is_empty(),
        "a closing scan is never capped twice"
    );
    assert_eq!(
        rig.calls()
            .iter()
            .filter(|c| **c == Call::StopScan(third))
            .count(),
        1
    );
    let _ = fourth;
    assert_eq!(rig.exit, None);
}

#[test]
fn stale_callbacks_of_a_closing_scan_change_nothing() {
    let mut rig = Rig::ready();
    let (handle, token) = rig.open_scan("s1");
    rig.req("stop_scan", "stop", json!({"scan_handle": handle}));
    assert_eq!(types(&rig.events()), vec!["scan_state"]);

    // A second stop of the closing handle is an unknown handle.
    rig.req("stop_scan", "stop2", json!({"scan_handle": handle}));
    let events = rig.events();
    assert_eq!(events[0]["code"], "unknown_handle");

    // Late OS callbacks of the closing scan: staged by the ingress (reservation kept so the
    // release can be delivered), never disclosed by the core.
    rig.inject(BackendEvent::Advertisement {
        scan: token,
        device: Discovered {
            platform_hint: "late".into(),
            label: "Late".into(),
            advertisement: None,
        },
    });
    rig.inject(BackendEvent::ScanStarted { scan: token });
    rig.inject(BackendEvent::ScanEnded {
        scan: token,
        failure: Some(failure("internal_ble", false)),
    });
    rig.inject(BackendEvent::ScanFailed {
        scan: token,
        failure: failure("scan_start", true),
    });
    assert!(rig.events().is_empty(), "no event for a closing scan");

    // The release frees it; any later callback or a duplicate release is stale and harmless.
    rig.inject(BackendEvent::ScanReleased { scan: token });
    assert_eq!(
        rig.inject_status(BackendEvent::ScanReleased { scan: token }),
        EmitStatus::StaleToken
    );
    assert_eq!(
        rig.inject_status(BackendEvent::ScanReleased { scan: 9_999 }),
        EmitStatus::StaleToken
    );
    assert!(matches!(
        rig.receiver.pop_event(Some(ZERO)),
        BackendPop::Timeout
    ));
    assert!(rig.events().is_empty());
    assert!(!rig.receiver.control_overflow() && !rig.receiver.emit_lock_failure());
    assert_eq!(rig.exit, None);
}

#[test]
fn a_release_without_a_terminal_ends_the_scan_for_the_protocol_first() {
    let mut rig = Rig::ready();
    let (handle, token) = rig.open_scan("s1");
    rig.inject(BackendEvent::ScanReleased { scan: token });
    let events = rig.events();
    assert_eq!(types(&events), vec!["scan_state"], "{events:?}");
    assert_eq!(events[0]["status"], "stopped");
    assert_eq!(events[0]["scan_handle"], handle);

    rig.req("start_scan", "p1", json!({"service_uuid": SCAN_UUID}));
    let pending = rig.start_scan_token();
    rig.inject(BackendEvent::ScanReleased { scan: pending });
    let events = rig.events();
    assert_eq!(events[0]["code"], "scan_failed");
    assert_eq!(events[0]["request_id"], "p1");
    assert_eq!(rig.exit, None);
}

#[test]
fn closing_scans_never_delay_survivor_links() {
    let mut rig = Rig::ready();
    let (a, _ta) = rig.open_scan("s1");
    let (b, _tb) = rig.open_scan("s2");
    rig.req("stop_scan", "x1", json!({"scan_handle": a}));
    rig.req("stop_scan", "x2", json!({"scan_handle": b}));
    rig.events();

    // Both scans are stuck closing; links still connect, receive and send promptly.
    let (handle, conn) = rig.connect_hint("c1", "phone-1", 517);
    rig.inbound(conn, b"hello-record", 517, 1);
    let events = rig.events();
    assert_eq!(types(&events), vec!["message"], "{events:?}");
    rig.req(
        "send",
        "send1",
        json!({"connection_handle": handle, "record_base64": "AAEC"}),
    );
    assert!(
        !rig.write_calls().is_empty(),
        "the write was submitted immediately"
    );
    assert_eq!(rig.exit, None);
}

#[test]
fn shutdown_with_an_unreleased_scan_still_waits_for_the_backend_terminal() {
    let mut rig = Rig::ready();
    let (handle, token) = rig.open_scan("s1");
    rig.req("stop_scan", "x", json!({"scan_handle": handle}));
    rig.events();
    rig.req("shutdown", "bye", json!({}));
    let events = rig.events();
    assert!(
        events.is_empty(),
        "the stop was already announced: {events:?}"
    );
    assert!(rig.calls().contains(&Call::Shutdown));
    assert_eq!(rig.exit, None, "no clean exit without the observed release");
    // The release of the stuck scan after the terminal-arming is a harmless stale emission.
    let status = rig.inject_status(BackendEvent::ScanReleased { scan: token });
    assert!(
        matches!(
            status,
            EmitStatus::Accepted | EmitStatus::StaleToken | EmitStatus::Shutdown
        ),
        "{status:?}"
    );
    rig.inject(BackendEvent::ShutdownComplete);
    assert_eq!(types(&rig.events()), vec!["shutdown_complete"]);
    assert_eq!(rig.exit, Some(EXIT_OK));
}

// ---- scripted backend on the shared adapter seams ---------------------------------------------

type Workers = Arc<Mutex<HashMap<Token, Arc<Completion>>>>;

/// A backend whose scan workers the test finishes by hand. Everything between the core and the
/// ingress is the adapters' shared code: the [`ScanLedger`], [`complete_scan`] and the release
/// coordinator.
struct ScriptedBackend {
    events: BackendEvents,
    scans: ScanLedger<()>,
    workers: Workers,
    stops: Arc<Mutex<Vec<Token>>>,
    shutting_down: bool,
}

impl Backend for ScriptedBackend {
    fn start_scan(&mut self, scan: Token, _service_uuid: &str) {
        if self.shutting_down {
            return;
        }
        self.scans.prune();
        let done = Completion::new();
        self.workers.lock().unwrap().insert(scan, Arc::clone(&done));
        self.scans.insert(scan, (), done);
    }

    fn stop_scan(&mut self, scan: Token) {
        self.stops.lock().unwrap().push(scan);
    }

    fn connect(&mut self, _conn: Token, _target: &Target) {}

    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        Some(NormalizedTarget::new(hint.to_string()))
    }

    fn write_fragments(&mut self, _conn: Token, _send: Token, _frames: Vec<Payload>) {}

    fn disconnect(&mut self, _conn: Token) {}

    fn shutdown(&mut self) {
        if self.shutting_down {
            return;
        }
        self.shutting_down = true;
        let (_slots, done) = self.scans.drain_for_shutdown();
        let events = self.events.clone();
        spawn_release_coordinator(done, Vec::new(), move || {
            let _ = events.emit(BackendEvent::ShutdownComplete);
        })
        .expect("coordinator");
    }
}

struct Harness {
    core: Core<ScriptedBackend>,
    out: Arc<OutQueue>,
    events: BackendEvents,
    receiver: BackendReceiver,
    workers: Workers,
    stops: Arc<Mutex<Vec<Token>>>,
    starts: usize,
    exit: Option<i32>,
}

impl Harness {
    fn new() -> Self {
        let out = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
        let (events, receiver) = BackendEvents::new();
        let workers: Workers = Arc::default();
        let stops: Arc<Mutex<Vec<Token>>> = Arc::default();
        let backend = ScriptedBackend {
            events: events.clone(),
            scans: ScanLedger::new(),
            workers: Arc::clone(&workers),
            stops: Arc::clone(&stops),
            shutting_down: false,
        };
        let core = Core::new(
            backend,
            out.clone(),
            Config {
                bridge_version: "otp24-test/0".into(),
                platform: "windows",
                ingress: receiver.clone(),
            },
        );
        let mut harness = Self {
            core,
            out,
            events,
            receiver,
            workers,
            stops,
            starts: 0,
            exit: None,
        };
        harness.req("hello", "hello", json!({}));
        assert_eq!(types(&harness.drain()), vec!["ready"]);
        harness
    }

    fn input(&mut self, input: Input) {
        let result = self.core.handle(input, Instant::now());
        if self.exit.is_none() {
            self.exit = result;
        }
    }

    fn req(&mut self, kind: &str, id: &str, extra: Value) {
        let mut value = if kind == "hello" {
            json!({"envelope_version":1,"type":"hello","request_id":id,"api_versions":[1]})
        } else {
            json!({"envelope_version":1,"api_version":1,"type":kind,"request_id":id})
        };
        for (key, field) in extra.as_object().unwrap() {
            value[key.clone()] = field.clone();
        }
        self.input(Input::Frame(Payload::new(
            serde_json::to_vec(&value).unwrap(),
        )));
        self.pump();
    }

    /// Drains the ingress the way the runtime does, waiting up to `wait` for each event.
    fn pump_for(&mut self, wait: Duration) {
        while let BackendPop::Event(event) = self.receiver.pop_event(Some(wait)) {
            self.input(Input::Backend(event));
        }
    }

    fn pump(&mut self) {
        self.pump_for(ZERO);
    }

    fn drain(&mut self) -> Vec<Value> {
        let mut parsed = Vec::new();
        while let OutPop::Frame(payload) = self.out.pop(ZERO) {
            parsed.push(serde_json::from_slice(payload.as_slice()).unwrap());
            self.out.finish_write();
        }
        parsed
    }

    fn token_of(&self, nth: usize) -> Token {
        let mut tokens: Vec<Token> = self.workers.lock().unwrap().keys().copied().collect();
        tokens.sort_unstable();
        tokens[nth]
    }

    fn open_scan(&mut self, id: &str) -> (String, Token) {
        self.req("start_scan", id, json!({"service_uuid": SCAN_UUID}));
        self.starts += 1;
        let token = self.token_of(self.starts - 1);
        let _ = self.events.emit(BackendEvent::ScanStarted { scan: token });
        self.pump();
        let events = self.drain();
        assert_eq!(events[0]["status"], "started", "{events:?}");
        (events[0]["scan_handle"].as_str().unwrap().into(), token)
    }

    fn stop(&mut self, id: &str, handle: &str) {
        self.req("stop_scan", id, json!({"scan_handle": handle}));
        self.drain();
    }

    /// The scan worker finishes: the shared adapter code records the completion and announces
    /// the release only for a clean teardown.
    fn finish_worker(&self, token: Token, clean: bool) {
        let done = self.workers.lock().unwrap()[&token].clone();
        complete_scan(&done, clean, || {
            let _ = self.events.emit(BackendEvent::ScanReleased { scan: token });
        });
    }
}

#[test]
fn a_slow_or_stuck_scan_stop_keeps_the_worker_count_at_two_until_it_finishes() {
    let mut h = Harness::new();
    let (a, ta) = h.open_scan("s1");
    let (b, _tb) = h.open_scan("s2");
    h.stop("x1", &a);
    h.stop("x2", &b);

    // Both workers are stuck inside their stop: spamming start/stop creates none.
    for round in 0..25 {
        h.req(
            "start_scan",
            &format!("spam{round}"),
            json!({"service_uuid": SCAN_UUID}),
        );
        let events = h.drain();
        assert_eq!(events[0]["code"], "cap_exceeded", "round {round}");
        assert!(h.workers.lock().unwrap().len() <= 2, "round {round}");
    }
    assert_eq!(h.core.backend().scans.len(), 2);

    // One worker finally finishes cleanly: exactly one slot reopens.
    h.finish_worker(ta, true);
    h.pump_for(Duration::from_millis(200));
    assert!(h.drain().is_empty());
    h.req("start_scan", "after", json!({"service_uuid": SCAN_UUID}));
    assert!(h.drain().is_empty());
    assert_eq!(h.workers.lock().unwrap().len(), 3);
    h.req("start_scan", "blocked", json!({"service_uuid": SCAN_UUID}));
    assert_eq!(h.drain()[0]["code"], "cap_exceeded");
    assert!(
        h.core.backend().scans.len() <= 2,
        "the finished worker was pruned by the next start"
    );
    assert_eq!(h.exit, None);
}

#[test]
fn late_worker_callbacks_after_a_stop_are_resource_safe() {
    let mut h = Harness::new();
    let (a, ta) = h.open_scan("s1");
    h.stop("x1", &a);
    // The stuck worker keeps delivering OS callbacks while its stop is slow.
    for n in 0..200 {
        let _ = h.events.emit(BackendEvent::Advertisement {
            scan: ta,
            device: Discovered {
                platform_hint: format!("late-{n}"),
                label: "Late".into(),
                advertisement: None,
            },
        });
    }
    h.pump();
    assert!(
        h.drain().is_empty(),
        "nothing is disclosed for a closing scan"
    );
    h.finish_worker(ta, true);
    h.pump();
    assert_eq!(
        h.events.emit(BackendEvent::Advertisement {
            scan: ta,
            device: Discovered {
                platform_hint: "after-release".into(),
                label: "x".into(),
                advertisement: None,
            },
        }),
        EmitStatus::AdvertisementDropped
    );
    assert!(!h.receiver.control_overflow() && !h.receiver.emit_lock_failure());
    assert!(matches!(
        h.receiver.pop_event(Some(ZERO)),
        BackendPop::Timeout
    ));
}

fn shutdown_outcome(h: &mut Harness) -> (Vec<Value>, Option<i32>) {
    h.req("shutdown", "bye", json!({}));
    h.pump_for(Duration::from_millis(400));
    (h.drain(), h.exit)
}

#[test]
fn all_clean_scans_then_shutdown_completes_cleanly() {
    let mut h = Harness::new();
    let (a, ta) = h.open_scan("s1");
    h.stop("x1", &a);
    h.finish_worker(ta, true);
    h.pump();
    let (events, exit) = shutdown_outcome(&mut h);
    assert_eq!(types(&events), vec!["shutdown_complete"], "{events:?}");
    assert_eq!(exit, Some(EXIT_OK));
}

#[test]
fn an_unclean_scan_completion_then_a_later_shutdown_never_claims_a_clean_release() {
    let mut h = Harness::new();
    let (a, ta) = h.open_scan("s1");
    h.stop("x1", &a);
    // The scan's cleanup failed: no release announcement, the slot stays counted.
    h.finish_worker(ta, false);
    h.pump_for(Duration::from_millis(100));
    assert!(h.drain().is_empty());

    // A later scan start prunes the (completed) worker; the failure must survive the prune.
    let (b, tb) = h.open_scan("s2");
    assert!(
        h.core.backend().scans.cleanup_failed(),
        "the failed cleanup is latched at prune time"
    );
    h.stop("x2", &b);
    h.finish_worker(tb, true);
    h.pump();

    let (events, exit) = shutdown_outcome(&mut h);
    assert!(
        !types(&events).contains(&"shutdown_complete".to_string()),
        "no clean release may be claimed: {events:?}"
    );
    assert_eq!(
        exit, None,
        "the core keeps waiting; the runtime watchdog fails closed"
    );
}

#[test]
fn an_unclean_completion_pruned_between_requests_is_still_counted_by_the_core() {
    let mut h = Harness::new();
    let (a, ta) = h.open_scan("s1");
    let (_b, _tb) = h.open_scan("s2");
    h.stop("x1", &a);
    h.finish_worker(ta, false);
    // The unreleased scan keeps its core slot forever (fail closed), whatever the adapter's
    // pruning did with its worker bookkeeping.
    h.req("start_scan", "s3", json!({"service_uuid": SCAN_UUID}));
    assert_eq!(h.drain()[0]["code"], "cap_exceeded");
}

#[test]
fn a_scan_that_finishes_after_the_shutdown_request_gates_the_terminal() {
    let mut h = Harness::new();
    let (_a, ta) = h.open_scan("s1");
    h.req("shutdown", "bye", json!({}));
    h.pump_for(Duration::from_millis(200));
    let early = h.drain();
    assert!(
        !types(&early).contains(&"shutdown_complete".to_string()),
        "the worker is still closing: {early:?}"
    );
    assert!(h.stops.lock().unwrap().contains(&ta));
    // The worker finishes (clean): the coordinator announces, the core completes the handshake,
    // and the worker's own late ScanReleased is a harmless rejected emission.
    h.finish_worker(ta, true);
    h.pump_for(Duration::from_secs(5));
    let events = h.drain();
    assert_eq!(types(&events), vec!["shutdown_complete"], "{events:?}");
    assert_eq!(h.exit, Some(EXIT_OK));
    assert!(!h.receiver.control_overflow() && !h.receiver.emit_lock_failure());
}

#[test]
fn a_wedged_scan_at_shutdown_withholds_the_terminal_without_failing_other_state() {
    let mut h = Harness::new();
    let (_a, _ta) = h.open_scan("s1");
    let (events, exit) = shutdown_outcome(&mut h);
    assert!(
        !types(&events).contains(&"shutdown_complete".to_string()),
        "{events:?}"
    );
    assert_eq!(exit, None);
    let _: Failure = failure("x", false);
}
