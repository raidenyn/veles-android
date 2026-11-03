//! CoreBluetooth hint format, target identity, capacity proof, classification, release settle
//! and the adapter's event discipline against the real core and ingress (Task 7).
//!
//! These are portable FIXTURES: the pure mappings are the REAL `macos_map` functions the macOS
//! adapter (`src/macos.rs`) calls, and the traces drive the REAL `Core`, `BackendEvents`,
//! `ReleaseGate` and `fragment` budget rules through a model of the adapter's submission
//! discipline. They do NOT execute `src/macos.rs` (it compiles and runs on macOS only) and are
//! never physical evidence.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::backend::{Backend, BackendEvent, NormalizedTarget, Target, Token};
use veles_ble_host::buffer::Payload;
use veles_ble_host::closejoin::ReleaseGate;
use veles_ble_host::core::{Config, Core, Input};
use veles_ble_host::fragment::{LinkBudget, LinkCapacity};
use veles_ble_host::ingress::{BackendEvents, BackendPop, BackendReceiver, EmitStatus};
use veles_ble_host::macos_map::{
    Authorization, BatchCheck, BoundedSet, CapacityError, OsError, capacity_failure,
    check_before_batch, classify, fallback_label, format_hint, normalize_hint, parse_hint,
    proven_capacity, scan_precheck, unavailable_timeout, valid_uuid,
};
use veles_ble_host::outqueue::{MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop as OutPop};

const UUID: &str = "0a1b2c3d-4e5f-6789-abcd-ef0123456789";

// ---- hint format and identity ------------------------------------------------------------------

#[test]
fn hint_round_trips_in_canonical_lowercase() {
    let hint = format_hint(UUID).unwrap();
    assert_eq!(hint, format!("macos:{UUID}"));
    assert_eq!(parse_hint(&hint).as_deref(), Some(UUID));
    // The OS identifier text must itself be canonical lowercase.
    assert_eq!(format_hint(&UUID.to_uppercase()), None);
    assert!(valid_uuid(UUID) && !valid_uuid(&UUID.to_uppercase()));
}

#[test]
fn malformed_hints_are_refused() {
    let almost = UUID.replace('-', "");
    for hint in [
        "",
        "macos",
        "macos:",
        "macos:0a1b2c3d",
        &format!("macos:{almost}"),
        &format!("macos:{UUID}0"),
        &format!("macos:{}", &UUID[..35]),
        &format!("macos:{{{UUID}}}"),
        &format!("macos:urn:uuid:{UUID}"),
        &format!("macos: {UUID}"),
        &format!("macos:{UUID} "),
        &format!("macos:+{}", &UUID[1..]),
        &format!("macos:{}g", &UUID[..35]),
        &format!("MACOS:{UUID}"),
        &format!("winrt:{UUID}"),
        &format!("macos:{UUID}:extra"),
        "winrt:aabbccddeeff:1",
        "sim:phone-1",
        // Wrong hyphen placement with the right length.
        "macos:0a1b2c3d4-e5f-6789-abcd-ef0123456789",
        // Non-ASCII look-alike digits never match.
        "macos:０a1b2c3d-4e5f-6789-abcd-ef0123456789",
    ] {
        assert_eq!(parse_hint(hint), None, "{hint:?}");
        assert_eq!(normalize_hint(hint), None, "{hint:?}");
    }
}

#[test]
fn identity_is_the_uuid_value_not_its_text_case() {
    let canonical = NormalizedTarget::new(format!("macos:{UUID}"));
    for hint in [
        format!("macos:{UUID}"),
        format!("macos:{}", UUID.to_uppercase()),
        "macos:0A1b2C3d-4E5f-6789-aBcD-eF0123456789".to_string(),
    ] {
        assert_eq!(normalize_hint(&hint), Some(canonical.clone()), "{hint:?}");
    }
    // The routed hint keeps its own spelling; only the identity is canonical.
    assert_eq!(
        parse_hint("macos:0A1B2C3D-4E5F-6789-ABCD-EF0123456789").as_deref(),
        Some(UUID)
    );
    // Distinct identifiers never collapse, including a one-digit difference.
    assert_ne!(
        normalize_hint(&format!("macos:{UUID}")),
        normalize_hint("macos:0a1b2c3d-4e5f-6789-abcd-ef012345678a")
    );
    // The platform prefix is part of the identity: no cross-platform collision.
    assert_ne!(
        normalize_hint(&format!("macos:{UUID}")),
        Some(NormalizedTarget::new(UUID))
    );
}

#[test]
fn discovered_hints_normalize_to_their_own_identity() {
    let hint = format_hint(UUID).unwrap();
    assert_eq!(
        normalize_hint(&hint),
        Some(NormalizedTarget::new(format!("macos:{UUID}")))
    );
}

#[test]
fn fallback_label_is_short_valid_and_not_the_full_identifier() {
    let label = fallback_label(&format!("macos:{UUID}"));
    assert_eq!(label, "BLE device ..6789");
    assert!(label.len() <= 64 && !label.chars().any(char::is_control));
}

// ---- capacity proof -------------------------------------------------------------------------------

#[test]
fn write_lengths_below_512_are_refused_as_undetermined() {
    // 0 (unknown), 181/182 (the ATT MTU floor's equivalents, still not provable), 511.
    for write in [0, 1, 20, 181, 182, 183, 244, 509, 510, 511] {
        assert_eq!(
            proven_capacity(write),
            Err(CapacityError::Undetermined),
            "{write}"
        );
    }
    let failure = capacity_failure(&CapacityError::Undetermined);
    assert_eq!(
        (failure.stage, failure.retryable),
        ("write_capacity", false)
    );
}

#[test]
fn write_lengths_from_512_are_clamped_to_the_attribute_maximum() {
    for write in [512, 513, 515, 517, 1024, 4096, usize::MAX] {
        assert_eq!(
            proven_capacity(write),
            Ok(LinkCapacity::MaxWriteWithoutResponse(512)),
            "{write}"
        );
    }
    // The clamped capacity is exactly what the shared budget rule derives.
    let budget = LinkBudget::from_capacity(LinkCapacity::MaxWriteWithoutResponse(512)).unwrap();
    assert_eq!((budget.attribute(), budget.payload()), (512, 508));
}

#[test]
fn a_changed_capacity_refuses_the_batch_before_any_frame_is_written() {
    let frames = [508 + 4, 100];
    // The proven capacity still holds (also when the OS now reports more).
    assert_eq!(
        check_before_batch(512, Some(512), frames),
        BatchCheck::Proceed
    );
    assert_eq!(
        check_before_batch(512, Some(4096), frames),
        BatchCheck::Proceed
    );
    // A later batch after the OS reports a smaller write length: refused, reported unclamped
    // (the core classifies it as undetermined and drops the link).
    for write in [0, 182, 511] {
        assert_eq!(
            check_before_batch(512, Some(write), frames),
            BatchCheck::CapacityChanged(LinkCapacity::MaxWriteWithoutResponse(write)),
            "{write}"
        );
    }
    // An unreadable value is never assumed to still be fine.
    assert_eq!(
        check_before_batch(512, None, frames),
        BatchCheck::Unreadable
    );
    // A frame above the 512-byte attribute maximum is refused whatever the OS says.
    assert_eq!(
        check_before_batch(512, Some(4096), [513]),
        BatchCheck::FrameTooLarge
    );
    assert_eq!(
        check_before_batch(512, Some(512), [512]),
        BatchCheck::Proceed
    );
    // A different announced value (never produced by setup) is reported as a change.
    assert_eq!(
        check_before_batch(300, Some(512), frames),
        BatchCheck::CapacityChanged(LinkCapacity::MaxWriteWithoutResponse(512))
    );
}

// ---- classification -------------------------------------------------------------------------------

#[test]
fn authorization_and_radio_states_distinguish_denied_from_off() {
    assert_eq!(Authorization::from_raw(0), Authorization::NotDetermined);
    assert_eq!(Authorization::from_raw(1), Authorization::Restricted);
    assert_eq!(Authorization::from_raw(2), Authorization::Denied);
    assert_eq!(Authorization::from_raw(3), Authorization::AllowedAlways);
    for raw in [-1, 4, 99, isize::MAX] {
        assert_eq!(
            Authorization::from_raw(raw),
            Authorization::Unknown,
            "{raw}"
        );
    }
    // A settled refusal fails at once; an undecided or unknown authorization never does.
    for auth in [Authorization::Denied, Authorization::Restricted] {
        let failure = scan_precheck(auth).unwrap();
        assert_eq!((failure.stage, failure.retryable), ("access_denied", true));
    }
    for auth in [
        Authorization::NotDetermined,
        Authorization::AllowedAlways,
        Authorization::Unknown,
    ] {
        assert_eq!(scan_precheck(auth), None, "{auth:?}");
    }
    // After the wait budget: radio_off only when access is settled as allowed.
    let stage = |auth| unavailable_timeout(auth).stage;
    assert_eq!(stage(Authorization::AllowedAlways), "radio_off");
    assert_eq!(stage(Authorization::NotDetermined), "permission_pending");
    assert_eq!(stage(Authorization::Denied), "access_denied");
    assert_eq!(stage(Authorization::Restricted), "access_denied");
    assert_eq!(stage(Authorization::Unknown), "adapter_unavailable");
    assert!(unavailable_timeout(Authorization::AllowedAlways).retryable);
}

#[test]
fn os_errors_map_to_bounded_stage_tokens() {
    let cases = [
        (OsError::AdapterUnavailable, "radio_off", true),
        (OsError::NotAuthorized, "access_denied", true),
        (OsError::NotSupported, "le_unsupported", false),
        (OsError::AlreadyScanning, "resource_in_use", true),
        (OsError::NotFound, "gatt_profile", false),
        (OsError::NotConnected, "gatt_profile", true),
        (OsError::ConnectionFailed, "gatt_profile", true),
        (OsError::Timeout, "setup_timeout", true),
        (OsError::Other, "gatt_profile", true),
    ];
    for (error, stage, retryable) in cases {
        let failure = classify(error, "gatt_profile");
        assert_eq!(
            (failure.stage, failure.retryable),
            (stage, retryable),
            "{error:?}"
        );
        assert!(
            (1..=32).contains(&failure.stage.len())
                && failure
                    .stage
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
            "{stage}"
        );
    }
}

#[test]
fn bounded_set_dedupes_and_caps() {
    let mut set = BoundedSet::new(2);
    assert!(set.is_empty());
    assert!(set.insert("a"));
    assert!(!set.insert("a"));
    assert!(set.insert("b"));
    assert!(!set.insert("c"), "beyond the cap");
    assert_eq!(set.len(), 2);
}

// ---- adapter event discipline against the real core ---------------------------------------------------

type Gates = Arc<Mutex<HashMap<Token, Arc<ReleaseGate>>>>;
type Log = Arc<Mutex<Vec<String>>>;

/// The macOS adapter's submission discipline, minus CoreBluetooth: `normalize_target` is the real
/// mapping, `connect` only registers the link's gate, `disconnect` asks for the release and
/// announces it iff the "worker" already observed it — the `MacosBackend::disconnect` /
/// `FinishGuard` split.
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
                platform: "macos",
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

    /// Connects and completes setup with the capacity the adapter's own proof yields for
    /// `write` (the real `proven_capacity`); returns (handle, token).
    fn open(&mut self, id: &str, hint: &str, write: usize) -> (String, Token) {
        self.req("connect", id, json!({"platform_hint": hint}));
        let token = self.conn_token(hint);
        let capacity = proven_capacity(write).expect("proven capacity");
        self.inject(BackendEvent::Connected {
            conn: token,
            capacity,
            platform_hint: None,
        });
        let events = self.drain();
        assert_eq!(events[0]["type"], "connected", "{events:?}");
        (
            events[0]["connection_handle"].as_str().unwrap().to_string(),
            token,
        )
    }

    /// The worker *observed* the OS release of the link (`FinishGuard`, clean).
    fn worker_finished(&mut self, conn: Token) {
        let gate = self.gates.lock().unwrap().get(&conn).cloned().unwrap();
        if gate.finish() {
            self.inject(BackendEvent::LinkReleased { conn });
        }
    }

    /// The worker finished but could NOT observe the release (settle budget exhausted, close
    /// failed, panic): never announced.
    fn worker_finished_unobserved(&mut self, conn: Token) {
        let gate = self.gates.lock().unwrap().get(&conn).cloned().unwrap();
        assert!(!gate.finish_with(false));
    }
}

fn hint() -> String {
    format!("macos:{UUID}")
}

#[test]
fn a_clamped_capacity_connects_and_an_identical_clamp_changes_nothing() {
    let mut rig = Rig::ready();
    let (_handle, token) = rig.open("c1", &hint(), 515);
    // The OS now reports a larger write length: same clamped budget, nothing cancelled.
    let BatchCheck::Proceed = check_before_batch(512, Some(4096), [512]) else {
        panic!("identical clamp must proceed");
    };
    rig.inject(BackendEvent::CapacityChanged {
        conn: token,
        capacity: proven_capacity(4096).unwrap(),
    });
    assert!(rig.drain().is_empty());
}

#[test]
fn capacity_change_before_a_later_batch_tears_the_link_down_and_waits_for_release() {
    let mut rig = Rig::ready();
    let (_handle, token) = rig.open("c1", &hint(), 512);
    // The adapter's pre-batch check found W == 511: it reports exactly what the check returned.
    let BatchCheck::CapacityChanged(capacity) = check_before_batch(512, Some(511), [100]) else {
        panic!("a smaller write length must be a capacity change");
    };
    rig.inject(BackendEvent::CapacityChanged {
        conn: token,
        capacity,
    });
    let events = rig.drain();
    assert_eq!(events[0]["type"], "disconnected", "{events:?}");
    assert!(
        rig.log
            .lock()
            .unwrap()
            .contains(&format!("disconnect {token}"))
    );
    // The target stays fenced until the observed release, whatever its UUID spelling.
    rig.req(
        "connect",
        "c2",
        json!({"platform_hint": format!("macos:{}", UUID.to_uppercase())}),
    );
    assert_eq!(rig.drain()[0]["stage"], "already_connected");
    rig.worker_finished(token);
    rig.req("connect", "c3", json!({"platform_hint": hint()}));
    assert_eq!(rig.connect_calls(), 2);
}

#[test]
fn an_unproven_capacity_that_reaches_the_core_is_still_refused() {
    // Defense in depth: even if an adapter handed an undetermined write length to the core, the
    // shared budget rule refuses the connection before any handle is disclosed.
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": hint()}));
    let token = rig.conn_token(&hint());
    rig.inject(BackendEvent::Connected {
        conn: token,
        capacity: LinkCapacity::MaxWriteWithoutResponse(511),
        platform_hint: None,
    });
    let events = rig.drain();
    assert_eq!(events[0]["type"], "error", "{events:?}");
    assert_eq!(events[0]["stage"], "write_capacity");
}

#[test]
fn cancellation_with_os_work_in_flight_fences_the_target_until_the_release_is_observed() {
    let mut rig = Rig::ready();
    rig.req("connect", "c1", json!({"platform_hint": hint()}));
    let token = rig.conn_token(&hint());
    // The pending connect fails (the worker's OS connect/discovery is still being torn down):
    // the core drops the connection and asks the adapter to release it.
    rig.inject(BackendEvent::ConnectFailed {
        conn: token,
        failure: veles_ble_host::backend::Failure::new("gatt_connect", true),
    });
    let _ = rig.drain();
    assert!(
        rig.log
            .lock()
            .unwrap()
            .contains(&format!("disconnect {token}")),
        "the failed setup reaches the adapter as a disconnect"
    );
    // The release was requested but the worker has not observed it: still fenced.
    rig.req("connect", "c2", json!({"platform_hint": hint()}));
    assert_eq!(rig.connect_calls(), 1);
    // A worker that could not observe the release never lifts the fence ...
    rig.worker_finished_unobserved(token);
    rig.req("connect", "c3", json!({"platform_hint": hint()}));
    assert_eq!(
        rig.connect_calls(),
        1,
        "an unobserved release lifts nothing"
    );
}

#[test]
fn stale_events_of_a_released_link_are_ignored_without_output() {
    let mut rig = Rig::ready();
    let (_handle, token) = rig.open("c1", &hint(), 512);
    rig.inject(BackendEvent::LinkLost { conn: token });
    let _ = rig.drain();
    rig.worker_finished(token);
    let _ = rig.drain();
    for event in [
        BackendEvent::Fragment {
            conn: token,
            bytes: Payload::new(vec![0; 8]),
        },
        BackendEvent::CapacityChanged {
            conn: token,
            capacity: LinkCapacity::MaxWriteWithoutResponse(100),
        },
        BackendEvent::LinkLost { conn: token },
    ] {
        // Late callbacks are consumed or rejected by the ingress bookkeeping; none may reach
        // the output.
        let _ = rig.events.emit(event);
        rig.pump();
    }
    assert!(rig.drain().is_empty());
}

#[test]
fn unresolvable_macos_hints_are_refused_before_any_adapter_submission() {
    let mut rig = Rig::ready();
    for hint in [
        "winrt:aabbccddeeff:1",
        "macos:aabbccddeeff",
        "macos:+a1b2c3d-4e5f-6789-abcd-ef0123456789",
        "phone-1",
    ] {
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
