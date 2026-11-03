//! Ordered callback admission (`admission::AdmissionGate`) against the REAL ingress.
//!
//! `AdmissionGate` is the production seam `winrt.rs` routes every scan and link callback
//! through (advertisement / early `Stopped` behind `ScanStarted`; notification / loss / capacity
//! behind `Connected`). These tests force the callback-before-open races deterministically and
//! observe what the core would receive. They do not exercise WinRT itself (Windows only).

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::AtomicBool;
use std::time::Duration;

use veles_ble_host::admission::{
    AdmissionGate, EventSink, Push, STAGED_DATA_BYTES_MAX, STAGED_DATA_EVENTS_MAX,
    link_contention_terminal, link_panic_terminal, scan_panic_terminal,
};
use veles_ble_host::backend::{BackendEvent, Discovered, Failure};
use veles_ble_host::buffer::Payload;
use veles_ble_host::cleanup::CleanupOutcome;
use veles_ble_host::closejoin::{Completion, spawn_release_coordinator};
use veles_ble_host::fragment::LinkCapacity;
use veles_ble_host::ingress::{BackendEvents, BackendPop, BackendReceiver, EmitStatus};

fn drain(receiver: &BackendReceiver) -> Vec<BackendEvent> {
    let mut events = Vec::new();
    while let BackendPop::Event(event) = receiver.pop_event(Some(Duration::ZERO)) {
        events.push(event);
    }
    events
}

fn fragment(conn: u64, tag: u8, len: usize) -> BackendEvent {
    BackendEvent::Fragment {
        conn,
        bytes: Payload::new(vec![tag; len]),
    }
}

fn connected(conn: u64) -> BackendEvent {
    BackendEvent::Connected {
        conn,
        capacity: LinkCapacity::AttMtu(517),
        platform_hint: None,
    }
}

fn names(events: &[BackendEvent]) -> Vec<String> {
    events
        .iter()
        .map(|event| match event {
            BackendEvent::ScanStarted { .. } => "scan_started".into(),
            BackendEvent::ScanEnded { .. } => "scan_ended".into(),
            BackendEvent::Advertisement { .. } => "advert".into(),
            BackendEvent::Connected { .. } => "connected".into(),
            BackendEvent::Fragment { bytes, .. } => format!("frag{}", bytes.as_slice()[0]),
            BackendEvent::LinkLost { .. } => "lost".into(),
            BackendEvent::ConnectFailed { .. } => "connect_failed".into(),
            BackendEvent::ScanFailed { .. } => "scan_failed".into(),
            BackendEvent::CapacityChanged { .. } => "capacity".into(),
            other => format!("{other:?}"),
        })
        .collect()
}

fn advert(scan: u64, hint: &str) -> BackendEvent {
    BackendEvent::Advertisement {
        scan,
        device: Discovered {
            platform_hint: hint.into(),
            label: "x".into(),
            advertisement: None,
        },
    }
}

#[test]
fn early_advertisement_and_early_stopped_follow_scan_started() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    // Both callbacks fire before the worker returns from startup.
    gate.push(advert(1, "winrt:aabbccddeeff:1"));
    gate.push(BackendEvent::ScanEnded {
        scan: 1,
        failure: Some(Failure::new("radio_off", true)),
    });
    assert!(drain(&receiver).is_empty(), "nothing precedes ScanStarted");
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    assert_eq!(
        names(&drain(&receiver)),
        ["scan_started", "advert", "scan_ended"]
    );
    // After opening, callbacks emit directly and in order.
    gate.push(advert(1, "winrt:112233445566:0"));
    assert_eq!(names(&drain(&receiver)), ["advert"]);
}

#[test]
fn failed_scan_start_discards_staged_callbacks() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    gate.push(advert(1, "winrt:aabbccddeeff:1"));
    gate.push(BackendEvent::ScanEnded {
        scan: 1,
        failure: None,
    });
    gate.abort();
    assert!(!gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    gate.push(advert(1, "winrt:aabbccddeeff:2"));
    assert!(drain(&receiver).is_empty(), "an aborted gate emits nothing");
}

#[test]
fn fragments_and_loss_before_connected_are_ordered_behind_it_without_loss() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    gate.push(fragment(7, 1, 100));
    gate.push(fragment(7, 2, 100));
    gate.push(BackendEvent::CapacityChanged {
        conn: 7,
        capacity: LinkCapacity::AttMtu(517),
    });
    gate.push(BackendEvent::LinkLost { conn: 7 });
    assert!(drain(&receiver).is_empty());
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    assert_eq!(
        names(&drain(&receiver)),
        ["connected", "frag1", "frag2", "capacity", "lost"]
    );
}

#[test]
fn staging_overflow_fails_the_link_closed_instead_of_dropping_stream_bytes() {
    // Too many fragments.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    for tag in 0..=STAGED_DATA_EVENTS_MAX as u8 {
        gate.push(fragment(7, tag, 1));
    }
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    assert_eq!(names(&drain(&receiver)), ["connected", "lost"]);
    gate.push(fragment(7, 9, 1));
    assert!(
        drain(&receiver).is_empty(),
        "a failed link admits nothing more"
    );

    // Too many bytes.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(8).unwrap();
    let gate = AdmissionGate::new(events);
    let chunk = 512;
    for tag in 0..=(STAGED_DATA_BYTES_MAX / chunk) as u8 {
        gate.push(fragment(8, tag, chunk));
    }
    assert!(gate.open(connected(8), Some(BackendEvent::LinkLost { conn: 8 })));
    assert_eq!(names(&drain(&receiver)), ["connected", "lost"]);
}

#[test]
fn cancelled_setup_emits_nothing_and_erases_staged_payloads() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    gate.push(fragment(7, 1, 64));
    gate.abort();
    assert!(!gate.open(connected(7), None));
    assert!(drain(&receiver).is_empty());
    assert!(!gate.is_open());
}

#[test]
fn concurrent_callbacks_racing_open_are_ordered_and_either_complete_or_fail_closed() {
    for round in 0..50 {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(7).unwrap();
        let gate = AdmissionGate::new(events);
        let producer = {
            let gate = gate.clone();
            std::thread::spawn(move || {
                for tag in 0..10u8 {
                    gate.push(fragment(7, tag, 8));
                    if tag % 3 == 0 {
                        std::thread::yield_now();
                    }
                }
            })
        };
        if round % 2 == 0 {
            std::thread::yield_now();
        }
        assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
        producer.join().unwrap();
        let mut seen = names(&drain(&receiver));
        assert_eq!(seen.remove(0), "connected", "round {round}");
        if gate.contention_failed() {
            // A callback lost the race for the gate: it failed closed (latched) rather than
            // waiting. Whatever was admitted is still strictly ordered, and the link ends in
            // exactly one loss (from `open` when the latch preceded it, else the worker's
            // terminate).
            let lost_by_open = seen.last().is_some_and(|name| name == "lost");
            if !lost_by_open {
                assert!(gate.terminate(|ann| link_contention_terminal(7, ann)));
                seen.extend(names(&drain(&receiver)));
            }
            assert_eq!(
                seen.last().map(String::as_str),
                Some("lost"),
                "round {round}"
            );
            seen.pop();
            let tags: Vec<u32> = seen
                .iter()
                .map(|name| name.trim_start_matches("frag").parse().unwrap())
                .collect();
            assert!(
                tags.windows(2).all(|w| w[0] < w[1]),
                "round {round}: ordered {tags:?}"
            );
        } else {
            let expected: Vec<String> = (0..10).map(|tag| format!("frag{tag}")).collect();
            assert_eq!(seen, expected, "round {round}: ordered, none lost");
        }
    }
}

// ---- panic before / during open: exactly one terminal ---------------------------------------

/// The worker's panic bookkeeping, exactly as `winrt.rs::link_main` runs it.
fn link_terminate_after_panic(gate: &AdmissionGate, conn: u64, _lost: &AtomicBool) -> bool {
    gate.terminate(|announced| link_panic_terminal(conn, announced))
}

#[test]
fn panic_before_open_emits_exactly_one_connect_failed_and_no_staged_data() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    let lost = AtomicBool::new(false);
    // Callbacks staged, then the setup panics before `open` ran.
    assert_eq!(gate.push(fragment(7, 1, 10)), Push::Accepted);
    assert_eq!(
        gate.push(BackendEvent::LinkLost { conn: 7 }),
        Push::Accepted
    );
    assert!(link_terminate_after_panic(&gate, 7, &lost));
    assert_eq!(names(&drain(&receiver)), ["connect_failed"]);
    // Neither a second terminal nor a late open or callback can add anything.
    assert!(!link_terminate_after_panic(&gate, 7, &lost));
    assert!(!gate.open(connected(7), None));
    gate.push(fragment(7, 2, 10));
    assert!(drain(&receiver).is_empty());
}

#[test]
fn panic_during_open_after_connected_emits_exactly_one_safe_loss() {
    for panic_at in [0usize, 1, 2] {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(7).unwrap();
        let gate = AdmissionGate::new(events);
        let lost = AtomicBool::new(false);
        gate.push(fragment(7, 1, 10));
        gate.push(fragment(7, 2, 10));
        let result = catch_unwind(AssertUnwindSafe(|| {
            gate.open_with_hook(
                connected(7),
                Some(BackendEvent::LinkLost { conn: 7 }),
                |index| {
                    if index == panic_at {
                        panic!("panic inside open");
                    }
                },
            )
        }));
        assert!(result.is_err());
        assert!(link_terminate_after_panic(&gate, 7, &lost));
        let seen = names(&drain(&receiver));
        // `Connected` was already announced, so the single terminal is a loss, never a
        // ConnectFailed; staged events flushed before the panic stay in order.
        let flushed: Vec<String> = ["connected", "frag1", "frag2"]
            .iter()
            .take(panic_at + 1)
            .map(|name| name.to_string())
            .collect();
        let mut expected = flushed;
        expected.push("lost".into());
        assert_eq!(seen, expected, "panic at {panic_at}");
        assert!(!link_terminate_after_panic(&gate, 7, &lost));
        assert!(drain(&receiver).is_empty());
    }
}

#[test]
fn panic_after_a_delivered_loss_adds_no_duplicate_loss() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    let lost = AtomicBool::new(false);
    assert!(gate.open(connected(7), None));
    // A loss was already reported (the `lost` flag is set by the callback that pushed it).
    lost.store(true, std::sync::atomic::Ordering::SeqCst);
    gate.push(BackendEvent::LinkLost { conn: 7 });
    assert!(link_terminate_after_panic(&gate, 7, &lost));
    assert_eq!(names(&drain(&receiver)), ["connected", "lost"]);
}

#[test]
fn scan_panic_terminal_is_failed_before_start_and_ended_after_it() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let pending = AdmissionGate::new(events.clone());
    assert!(pending.terminate(|announced| Some(scan_panic_terminal(1, announced))));
    assert_eq!(names(&drain(&receiver)), ["scan_failed"]);

    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(2).unwrap();
    let started = AdmissionGate::new(events);
    assert!(started.open(BackendEvent::ScanStarted { scan: 2 }, None));
    assert!(started.terminate(|announced| Some(scan_panic_terminal(2, announced))));
    assert_eq!(names(&drain(&receiver)), ["scan_started", "scan_ended"]);
}

// ---- nonblocking callbacks: a held gate defers (bounded, ordered); only a full lane fails ------

#[test]
fn a_callback_never_waits_on_a_held_gate_and_its_stream_stays_intact() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    assert_eq!(gate.push(fragment(7, 1, 10)), Push::Accepted);

    let started = std::time::Instant::now();
    let (advert_result, fragment_result) = gate.with_gate_held(|| {
        // Another thread's callbacks while a slow holder owns the gate.
        let callbacks = {
            let gate = gate.clone();
            std::thread::spawn(move || {
                let advert = gate.push(advert(1, "winrt:aabbccddeeff:1"));
                let frag = gate.push(fragment(7, 2, 10));
                (advert, frag)
            })
        };
        callbacks
            .join()
            .expect("callback thread returned without waiting")
    });
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "callbacks returned while the gate was held: {:?}",
        started.elapsed()
    );
    assert_eq!(advert_result, Push::Dropped, "advertisements are lossy");
    assert_eq!(
        fragment_result,
        Push::Accepted,
        "stream data is deferred in order"
    );
    assert!(!gate.contention_failed(), "contention is not a failure");
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    assert_eq!(names(&drain(&receiver)), ["connected", "frag1", "frag2"]);
}

/// Only a genuinely full lane (the holder is stuck while the radio keeps delivering) fails the
/// stream closed — bounded backpressure; nothing of the link is emitted behind the gap.
#[test]
fn a_full_gate_lane_fails_the_stream_closed_and_never_gaps_it() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    let results = gate.with_gate_held(|| {
        let gate = gate.clone();
        std::thread::spawn(move || {
            (0..=STAGED_DATA_EVENTS_MAX as u8)
                .map(|tag| gate.push(fragment(7, tag, 10)))
                .collect::<Vec<_>>()
        })
        .join()
        .unwrap()
    });
    assert!(
        results[..STAGED_DATA_EVENTS_MAX]
            .iter()
            .all(|result| *result == Push::Accepted)
    );
    assert_eq!(results[STAGED_DATA_EVENTS_MAX], Push::Failed);
    assert!(gate.contention_failed());
    // `open` fails the link closed (Connected then LinkLost), without a gapped stream.
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    assert_eq!(names(&drain(&receiver)), ["connected", "lost"]);
}

#[test]
fn lane_overflow_after_open_is_terminated_by_the_worker_with_a_single_loss() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    // A fragment above the lane's byte bound cannot be queued while the gate is held.
    let result = gate.with_gate_held(|| {
        let gate = gate.clone();
        std::thread::spawn(move || gate.push(fragment(7, 5, STAGED_DATA_BYTES_MAX + 1)))
            .join()
            .unwrap()
    });
    assert_eq!(result, Push::Failed);
    assert!(gate.contention_failed());
    // The worker (woken by `Command::Fail`) terminates the link once.
    assert!(gate.terminate(|announced| link_contention_terminal(7, announced)));
    assert!(!gate.terminate(|announced| link_contention_terminal(7, announced)));
    assert_eq!(names(&drain(&receiver)), ["connected", "lost"]);
}

/// Opens link 7, emits one good fragment (tag 1) on the direct path, then makes the lane
/// genuinely overflow (a fragment above the lane byte bound while the gate is held) so the gate
/// returns `Push::Failed` for a dropped fragment (tag 5).
fn open_link_with_a_gap() -> (std::sync::Arc<AdmissionGate>, BackendReceiver) {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    assert_eq!(gate.push(fragment(7, 1, 10)), Push::Accepted);
    let result = gate.with_gate_held(|| {
        let gate = gate.clone();
        std::thread::spawn(move || gate.push(fragment(7, 5, STAGED_DATA_BYTES_MAX + 1)))
            .join()
            .unwrap()
    });
    assert_eq!(result, Push::Failed);
    (gate, receiver)
}

#[test]
fn no_fragment_or_record_passes_after_a_lane_overflow_gap_before_termination() {
    // I-A: the worker (blocked in a write, say) has not terminated the link yet. Every later
    // same-link fragment and control must be fenced, never emitted behind the missing bytes.
    let (gate, receiver) = open_link_with_a_gap();
    assert_eq!(gate.push(fragment(7, 6, 10)), Push::Accepted); // post-gap: erased
    assert_eq!(
        gate.push(BackendEvent::WriteDone { conn: 7, send: 3 }),
        Push::Accepted
    ); // post-gap control: erased
    assert_eq!(gate.push(fragment(7, 7, 10)), Push::Accepted);
    let mut seen = names(&drain(&receiver));
    assert!(
        !seen.iter().any(|name| name == "frag6" || name == "frag7"),
        "post-gap fragment emitted: {seen:?}"
    );
    assert!(
        !seen.iter().any(|name| name.contains("WriteDone")),
        "post-gap control emitted: {seen:?}"
    );
    // The loss is delivered exactly once in total: the immediate marker is the loss, so the
    // worker's later terminate adds none.
    gate.terminate(|announced| link_contention_terminal(7, announced));
    seen.extend(names(&drain(&receiver)));
    assert_eq!(
        seen.iter().filter(|name| *name == "lost").count(),
        1,
        "{seen:?}"
    );
    assert!(
        !seen.iter().any(|name| name == "frag6" || name == "frag7"),
        "{seen:?}"
    );
}

#[test]
fn a_lane_overflow_fails_the_link_in_the_ingress_without_waiting_for_the_worker() {
    // The gap is reported to the ingress at the moment of overflow (a WinRT worker blocked in
    // a write would otherwise leave the link looking healthy for a whole write budget).
    let (_gate, receiver) = open_link_with_a_gap();
    let seen = names(&drain(&receiver));
    assert!(
        seen.iter().any(|name| name == "lost"),
        "no immediate link failure marker: {seen:?}"
    );
}

/// Runs `push` on another thread while the calling thread holds the gate (inside an
/// `abort`/`terminate` hook): the callback cannot take the gate, so its event is deferred into
/// the gate lane and acknowledged `Accepted`.
fn deferred_from_a_callback(gate: &std::sync::Arc<AdmissionGate>, event: BackendEvent) {
    let gate = gate.clone();
    let pushed = std::thread::spawn(move || gate.push(event)).join().unwrap();
    assert_eq!(pushed, Push::Accepted, "deferred, not dropped or failed");
}

fn scan_ended_radio_off() -> BackendEvent {
    BackendEvent::ScanEnded {
        scan: 1,
        failure: Some(Failure::new("radio_off", true)),
    }
}

fn scan_ended_failures(events: &[BackendEvent]) -> Vec<Option<String>> {
    events
        .iter()
        .filter_map(|event| match event {
            BackendEvent::ScanEnded { failure, .. } => {
                Some(failure.as_ref().map(|failure| format!("{failure:?}")))
            }
            _ => None,
        })
        .collect()
}

#[test]
fn a_scan_ended_deferred_during_abort_is_delivered_not_discarded() {
    // M1: the OS `Stopped` handler could not take the gate (the worker's `abort` holds it), so
    // its `ScanEnded{radio_off}` sits in the lane acknowledged `Accepted`. Closing must not
    // silently drop an accepted terminal: it is committed, once, with its classification.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    gate.abort_with_hook(|| deferred_from_a_callback(&gate, scan_ended_radio_off()));
    let seen = drain(&receiver);
    assert_eq!(names(&seen), ["scan_started", "scan_ended"], "{seen:?}");
    assert_eq!(scan_ended_failures(&seen).len(), 1);
    assert!(
        scan_ended_failures(&seen)[0]
            .as_deref()
            .is_some_and(|text| text.contains("radio_off")),
        "failure classification preserved: {seen:?}"
    );
    // A later duplicate callback after the close is still dropped (exactly once).
    assert_eq!(gate.push(scan_ended_radio_off()), Push::Accepted);
    assert!(drain(&receiver).is_empty());
}

#[test]
fn a_scan_ended_deferred_during_terminate_is_delivered_once_in_order() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    // The worker's own terminal and a deferred OS terminal: exactly one ScanEnded, the
    // worker's (it was emitted first); the deferred duplicate must not end the scan twice.
    gate.terminate_with_hook(
        |_| Some(scan_ended_radio_off()),
        || deferred_from_a_callback(&gate, scan_ended_radio_off()),
    );
    assert_eq!(names(&drain(&receiver)), ["scan_started", "scan_ended"]);
    // With no worker terminal (`None`), the deferred OS terminal is the scan's end.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    gate.terminate_with_hook(
        |_| None,
        || deferred_from_a_callback(&gate, scan_ended_radio_off()),
    );
    let seen = drain(&receiver);
    assert_eq!(names(&seen), ["scan_started", "scan_ended"], "{seen:?}");
}

#[test]
fn deferred_data_and_unannounced_terminals_are_still_discarded_on_close() {
    // Only an accepted *terminal of an announced operation* survives the close; stream bytes
    // and anything for a never-announced scan stay erased.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    gate.abort_with_hook(|| deferred_from_a_callback(&gate, fragment(7, 9, 10)));
    assert_eq!(names(&drain(&receiver)), ["connected"]);

    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    gate.abort_with_hook(|| deferred_from_a_callback(&gate, scan_ended_radio_off()));
    assert!(
        drain(&receiver).is_empty(),
        "never announced: nothing emitted"
    );
}

#[test]
fn callbacks_do_not_serialize_through_a_blocking_lock_when_free() {
    // Uncontended callbacks from many threads all succeed (bounded retries), preserving the
    // per-producer order; contention is the exception that fails closed, not the norm.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    let mut accepted = 0;
    for _ in 0..8 {
        if gate.push(advert(1, "winrt:aabbccddeeff:1")) == Push::Accepted {
            accepted += 1;
        }
    }
    assert_eq!(accepted, 8);
    assert!(!gate.contention_failed());
    drain(&receiver);
}

// ---- scan: panic with a staged early Stopped must not end the scan twice ---------------------

fn scan_ended(scan: u64) -> BackendEvent {
    BackendEvent::ScanEnded {
        scan,
        failure: Some(Failure::new("radio_off", true)),
    }
}

fn count(names: &[String], wanted: &str) -> usize {
    names.iter().filter(|name| *name == wanted).count()
}

#[test]
fn panic_during_or_after_flushing_a_staged_stopped_never_ends_the_scan_twice() {
    // Staged: one advertisement, then the early OS `Stopped` (ScanEnded). Hook indices:
    // 0 after ScanStarted, 1 after the advertisement, 2 after the staged ScanEnded.
    for panic_at in [0usize, 1, 2] {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_scan(1).unwrap();
        let gate = AdmissionGate::new(events);
        gate.push(advert(1, "winrt:aabbccddeeff:1"));
        gate.push(scan_ended(1));
        let result = catch_unwind(AssertUnwindSafe(|| {
            gate.open_with_hook(BackendEvent::ScanStarted { scan: 1 }, None, |index| {
                if index == panic_at {
                    panic!("panic during flush");
                }
            })
        }));
        assert!(result.is_err());
        // The worker's panic bookkeeping, as in `scan_main`.
        assert!(gate.terminate(|announced| Some(scan_panic_terminal(1, announced))));
        let seen = names(&drain(&receiver));
        assert_eq!(
            count(&seen, "scan_ended"),
            1,
            "panic at {panic_at}: exactly one scan terminal, got {seen:?}"
        );
        assert_eq!(seen[0], "scan_started");
        // The terminal delivered by the staged Stopped (index 2) is the real radio_off end,
        // not a second synthetic panic end.
        if panic_at == 2 {
            assert_eq!(seen, ["scan_started", "advert", "scan_ended"]);
        }
        assert!(!gate.terminate(|announced| Some(scan_panic_terminal(1, announced))));
        assert!(drain(&receiver).is_empty());
    }
}

#[test]
fn panic_after_a_fully_opened_gate_that_delivered_the_end_adds_nothing() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    gate.push(scan_ended(1));
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    assert_eq!(names(&drain(&receiver)), ["scan_started", "scan_ended"]);
    assert!(gate.terminate(|announced| Some(scan_panic_terminal(1, announced))));
    assert!(drain(&receiver).is_empty(), "the end was already delivered");
}

#[test]
fn an_end_delivered_directly_after_open_also_suppresses_the_panic_terminal() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    gate.push(scan_ended(1));
    assert!(gate.terminate(|announced| Some(scan_panic_terminal(1, announced))));
    assert_eq!(names(&drain(&receiver)), ["scan_started", "scan_ended"]);
}

#[test]
fn a_staged_but_unflushed_stopped_still_gets_exactly_one_terminal() {
    // Panic before the flush reached the staged end: the staged event is discarded and the
    // single terminal is the synthetic one.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_scan(1).unwrap();
    let gate = AdmissionGate::new(events);
    gate.push(scan_ended(1));
    let _ = catch_unwind(AssertUnwindSafe(|| {
        gate.open_with_hook(BackendEvent::ScanStarted { scan: 1 }, None, |_| {
            panic!("panic right after ScanStarted")
        })
    }));
    assert!(gate.terminate(|announced| Some(scan_panic_terminal(1, announced))));
    assert_eq!(names(&drain(&receiver)), ["scan_started", "scan_ended"]);
}

#[test]
fn link_loss_staged_then_flushed_is_not_duplicated_by_the_panic_terminal() {
    for panic_at in [0usize, 1] {
        let (events, receiver) = BackendEvents::new();
        receiver.reserve_connection(7).unwrap();
        let gate = AdmissionGate::new(events);
        gate.push(BackendEvent::LinkLost { conn: 7 });
        let _ = catch_unwind(AssertUnwindSafe(|| {
            gate.open_with_hook(connected(7), None, |index| {
                if index == panic_at {
                    panic!("panic during flush");
                }
            })
        }));
        assert!(gate.terminate(|announced| link_panic_terminal(7, announced)));
        let seen = names(&drain(&receiver));
        assert_eq!(count(&seen, "lost"), 1, "panic at {panic_at}: {seen:?}");
    }
}

// ---- rejected terminals: only EmitStatus::Accepted is delivery --------------------------------

/// Scripted ingress: returns the next scripted status for each emission (default `Accepted`)
/// and logs every attempted event by name, so retries and false deliveries are observable.
struct ScriptedSink {
    statuses: std::sync::Mutex<std::collections::VecDeque<EmitStatus>>,
    log: std::sync::Mutex<Vec<String>>,
}

impl ScriptedSink {
    fn new(statuses: &[EmitStatus]) -> std::sync::Arc<Self> {
        std::sync::Arc::new(Self {
            statuses: std::sync::Mutex::new(statuses.iter().copied().collect()),
            log: std::sync::Mutex::new(Vec::new()),
        })
    }

    fn attempts(&self) -> Vec<String> {
        self.log.lock().unwrap().clone()
    }
}

impl EventSink for ScriptedSink {
    fn emit(&self, event: BackendEvent) -> EmitStatus {
        self.log.lock().unwrap().push(names(&[event])[0].clone());
        self.statuses
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or(EmitStatus::Accepted)
    }
}

fn gate_with(
    statuses: &[EmitStatus],
) -> (
    std::sync::Arc<AdmissionGate>,
    std::sync::Arc<ScriptedSink>,
    std::sync::Arc<CleanupOutcome>,
) {
    let sink = ScriptedSink::new(statuses);
    let outcome = CleanupOutcome::new();
    let gate = AdmissionGate::with_sink(sink.clone(), Some(outcome.clone()));
    (gate, sink, outcome)
}

const PROCESS_REJECTIONS: [EmitStatus; 2] = [EmitStatus::LockBusy, EmitStatus::ControlOverflow];

#[test]
fn a_rejected_direct_terminal_is_not_delivery_and_fails_the_process_closed() {
    for rejection in PROCESS_REJECTIONS {
        // Accepted opening event, then the rejected direct terminal.
        let (gate, sink, outcome) = gate_with(&[EmitStatus::Accepted, rejection]);
        assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
        gate.push(scan_ended(1));
        assert!(!gate.terminal_delivered(), "{rejection:?} is not delivery");
        assert_eq!(gate.terminal_rejection(), Some(rejection));
        assert!(gate.process_failed());
        assert!(!outcome.is_clean(), "the worker's release is withheld");
        // No retry, no replacement: the panic terminal is suppressed, one attempt only.
        assert!(gate.terminate(|ann| Some(scan_panic_terminal(1, ann))));
        assert_eq!(sink.attempts(), ["scan_started", "scan_ended"]);
    }
}

#[test]
fn a_rejected_flushed_terminal_fails_closed_and_a_later_panic_adds_no_retry() {
    for rejection in PROCESS_REJECTIONS {
        // ScanStarted ok, staged advert ok, staged Stopped (ScanEnded) rejected.
        let (gate, sink, outcome) =
            gate_with(&[EmitStatus::Accepted, EmitStatus::Accepted, rejection]);
        gate.push(advert(1, "winrt:aabbccddeeff:1"));
        gate.push(scan_ended(1));
        let _ = catch_unwind(AssertUnwindSafe(|| {
            gate.open_with_hook(BackendEvent::ScanStarted { scan: 1 }, None, |index| {
                if index == 2 {
                    panic!("panic right after the rejected flush");
                }
            })
        }));
        assert!(gate.terminate(|ann| Some(scan_panic_terminal(1, ann))));
        assert!(!gate.terminal_delivered());
        assert!(gate.process_failed() && !outcome.is_clean());
        assert_eq!(
            sink.attempts(),
            ["scan_started", "advert", "scan_ended"],
            "{rejection:?}: the rejected terminal is attempted once, never retried"
        );
    }
}

#[test]
fn a_rejected_overflow_failure_and_a_rejected_terminate_are_process_failures() {
    // Overflow: Connected accepted, the fail-closed LinkLost rejected.
    let (gate, sink, outcome) = gate_with(&[EmitStatus::Accepted, EmitStatus::LockBusy]);
    for tag in 0..=STAGED_DATA_EVENTS_MAX as u8 {
        gate.push(fragment(7, tag, 1));
    }
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    assert_eq!(sink.attempts(), ["connected", "lost"]);
    assert!(!gate.terminal_delivered() && gate.process_failed() && !outcome.is_clean());

    // terminate: the synthetic terminal itself is rejected.
    let (gate, sink, outcome) = gate_with(&[EmitStatus::ControlOverflow]);
    assert!(gate.terminate(|ann| link_panic_terminal(7, ann)));
    assert_eq!(sink.attempts(), ["connect_failed"]);
    assert!(!gate.terminal_delivered());
    assert_eq!(gate.terminal_rejection(), Some(EmitStatus::ControlOverflow));
    assert!(gate.process_failed() && !outcome.is_clean());
    assert!(
        !gate.terminate(|ann| link_panic_terminal(7, ann)),
        "closed, no retry"
    );
    assert_eq!(sink.attempts().len(), 1);
}

#[test]
fn stale_or_shutdown_rejections_are_moot_not_delivered_and_not_process_failures() {
    for rejection in [EmitStatus::StaleToken, EmitStatus::Shutdown] {
        let (gate, sink, outcome) = gate_with(&[EmitStatus::Accepted, rejection]);
        assert!(gate.open(connected(7), None));
        gate.push(BackendEvent::LinkLost { conn: 7 });
        assert!(!gate.terminal_delivered(), "{rejection:?} is not delivery");
        assert_eq!(gate.terminal_rejection(), Some(rejection));
        assert!(!gate.process_failed() && outcome.is_clean());
        assert!(gate.terminate(|ann| link_panic_terminal(7, ann)));
        assert_eq!(
            sink.attempts(),
            ["connected", "lost"],
            "no retry for {rejection:?}"
        );
    }
}

#[test]
fn an_accepted_terminal_is_delivery_and_never_a_process_failure() {
    let (gate, sink, outcome) = gate_with(&[]);
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    gate.push(scan_ended(1));
    assert!(gate.terminal_delivered() && gate.terminal_rejection().is_none());
    assert!(!gate.process_failed() && outcome.is_clean());
    assert!(gate.terminate(|ann| Some(scan_panic_terminal(1, ann))));
    assert_eq!(sink.attempts(), ["scan_started", "scan_ended"]);
}

#[test]
fn real_ingress_rejections_of_terminals_are_classified_the_same_way() {
    // StaleToken: the connection was never reserved (or already retired).
    let (events, receiver) = BackendEvents::new();
    let outcome = CleanupOutcome::new();
    let gate = AdmissionGate::with_outcome(events, outcome.clone());
    assert!(gate.open(connected(9), None));
    gate.push(BackendEvent::LinkLost { conn: 9 });
    assert_eq!(gate.terminal_rejection(), Some(EmitStatus::StaleToken));
    assert!(!gate.terminal_delivered() && !gate.process_failed() && outcome.is_clean());
    drop(receiver);

    // Shutdown: admission closed by the process-wide terminal.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    let outcome = CleanupOutcome::new();
    let gate = AdmissionGate::with_outcome(events, outcome.clone());
    assert!(gate.open(connected(7), None));
    gate.push(BackendEvent::LinkLost { conn: 7 });
    assert_eq!(gate.terminal_rejection(), Some(EmitStatus::Shutdown));
    assert!(!gate.terminal_delivered() && !gate.process_failed());

    // ControlOverflow: the link's control slots are full when the terminal arrives; the
    // ingress itself latches its process-level signal as well.
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let outcome = CleanupOutcome::new();
    let gate = AdmissionGate::with_outcome(events, outcome.clone());
    assert!(gate.open(connected(7), None));
    for _ in 0..40 {
        gate.push(BackendEvent::CapacityChanged {
            conn: 7,
            capacity: LinkCapacity::AttMtu(517),
        });
    }
    gate.push(BackendEvent::LinkLost { conn: 7 });
    assert_eq!(gate.terminal_rejection(), Some(EmitStatus::ControlOverflow));
    assert!(!gate.terminal_delivered());
    assert!(gate.process_failed() && !outcome.is_clean());
    assert!(
        receiver.control_overflow(),
        "the ingress signal is latched too"
    );
}

#[test]
fn a_process_failed_terminal_withholds_the_process_wide_release() {
    let (gate, _sink, outcome) = gate_with(&[EmitStatus::Accepted, EmitStatus::LockBusy]);
    assert!(gate.open(BackendEvent::ScanStarted { scan: 1 }, None));
    gate.push(scan_ended(1));
    // The scan worker completes from the outcome, as `scan_main` does.
    let done = Completion::new();
    done.complete_with(outcome.is_clean());
    let (tx, rx) = std::sync::mpsc::channel();
    spawn_release_coordinator(vec![done], Vec::new(), move || {
        let _ = tx.send(());
    })
    .expect("spawn");
    assert!(rx.recv_timeout(Duration::from_millis(300)).is_err());
}

/// Review C1: a terminal the ingress deferred past a contended lock (accepted into its ordered
/// lane) is delivery — not a process failure, and never retried.
#[test]
fn a_deferred_terminal_counts_as_delivered() {
    let (gate, sink, outcome) = gate_with(&[EmitStatus::Accepted, EmitStatus::Deferred]);
    assert!(gate.open(connected(7), None));
    gate.push(BackendEvent::LinkLost { conn: 7 });
    assert!(gate.terminal_delivered());
    assert_eq!(gate.terminal_rejection(), None);
    assert!(!gate.process_failed() && outcome.is_clean());
    assert_eq!(sink.attempts(), ["connected", "lost"]);
}

// ---- review follow-up #2: contention must never fail a link ------------------------------------

/// A slow gate holder (e.g. `open` flushing into the ingress) must not turn concurrent callbacks
/// into a link failure: they are deferred in order and committed when the gate frees.
#[test]
fn callbacks_while_the_gate_is_held_are_deferred_in_order_not_failed() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    let results = gate.with_gate_held(|| {
        let gate = gate.clone();
        std::thread::spawn(move || {
            [
                gate.push(fragment(7, 1, 10)),
                gate.push(fragment(7, 2, 10)),
                gate.push(BackendEvent::CapacityChanged {
                    conn: 7,
                    capacity: LinkCapacity::AttMtu(247),
                }),
                gate.push(fragment(7, 3, 10)),
            ]
        })
        .join()
        .unwrap()
    });
    assert_eq!(results, [Push::Accepted; 4], "deferred = accepted");
    assert!(!gate.contention_failed());
    assert_eq!(
        names(&drain(&receiver)),
        ["connected", "frag1", "frag2", "capacity", "frag3"]
    );
}

#[test]
fn callbacks_held_before_open_stage_in_order_and_open_is_not_failed() {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    let result = gate.with_gate_held(|| {
        let gate = gate.clone();
        std::thread::spawn(move || [gate.push(fragment(7, 1, 10)), gate.push(fragment(7, 2, 10))])
            .join()
            .unwrap()
    });
    assert_eq!(result, [Push::Accepted; 2]);
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    assert!(!gate.contention_failed());
    assert_eq!(names(&drain(&receiver)), ["connected", "frag1", "frag2"]);
}

/// Real concurrent callbacks (WinRT may deliver GATT notifications on several threadpool
/// threads) hammering one open gate while the consumer also drains: nothing is lost, each
/// callback thread's own events stay in order, and no contention failure is ever latched.
#[test]
fn concurrent_callbacks_on_one_gate_lose_nothing_and_never_fail_the_link() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};
    const THREADS: usize = 4;
    const PER_THREAD: u32 = 3_000;
    const WINDOW: usize = 3; // per thread: 4 x 3 = 12 < the 16-event per-link ingress bound

    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(7).unwrap();
    let gate = AdmissionGate::new(events);
    assert!(gate.open(connected(7), Some(BackendEvent::LinkLost { conn: 7 })));
    let credits: Arc<Vec<AtomicUsize>> =
        Arc::new((0..THREADS).map(|_| AtomicUsize::new(0)).collect());
    let producers: Vec<_> = (0..THREADS)
        .map(|t| {
            let gate = gate.clone();
            let credits = Arc::clone(&credits);
            std::thread::spawn(move || {
                for seq in 0..PER_THREAD {
                    while credits[t].load(Ordering::Acquire) >= WINDOW {
                        std::thread::yield_now();
                    }
                    credits[t].fetch_add(1, Ordering::AcqRel);
                    let mut bytes = vec![t as u8];
                    bytes.extend_from_slice(&seq.to_be_bytes());
                    let pushed = gate.push(BackendEvent::Fragment {
                        conn: 7,
                        bytes: Payload::new(bytes),
                    });
                    assert_eq!(pushed, Push::Accepted, "thread {t} seq {seq}");
                }
            })
        })
        .collect();
    // A worker-side holder churning the gate (open/terminate-style critical sections) forces
    // the callbacks onto the deferred lane and exercises the settle-after-release step.
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let churn = {
        let gate = gate.clone();
        let stop = Arc::clone(&stop);
        std::thread::spawn(move || {
            while !stop.load(Ordering::Acquire) {
                gate.with_gate_held(std::thread::yield_now);
            }
        })
    };
    let mut next = [0u32; THREADS];
    let started = std::time::Instant::now();
    while next.iter().any(|&n| n < PER_THREAD) {
        assert!(
            started.elapsed() < Duration::from_secs(60),
            "stalled {next:?}"
        );
        assert!(
            !gate.contention_failed(),
            "contention latched a link failure"
        );
        if let BackendPop::Event(event) = receiver.pop_event(Some(Duration::from_millis(1))) {
            match event {
                BackendEvent::Fragment { bytes, .. } => {
                    let b = bytes.as_slice();
                    let t = b[0] as usize;
                    let seq = u32::from_be_bytes(b[1..5].try_into().unwrap());
                    assert_eq!(seq, next[t], "thread {t}: lost or reordered");
                    next[t] += 1;
                    credits[t].fetch_sub(1, Ordering::AcqRel);
                }
                BackendEvent::Connected { .. } => {}
                other => panic!("unexpected {other:?}"),
            }
        }
    }
    for p in producers {
        p.join().unwrap();
    }
    stop.store(true, Ordering::Release);
    churn.join().unwrap();
    assert!(!gate.contention_failed());
    assert!(!receiver.emit_lock_failure() && !receiver.control_overflow());
}

/// Two links' gates, both open, each receiving its full approved per-link bound (48 fragments)
/// zero-gap from its own thread while nothing drains the ingress: exact content and order per
/// link, nothing crossed over; the 49th unconsumed fragment fails only that link.
#[test]
fn two_open_gates_hold_a_full_per_link_bound_each_without_cross_over_then_fail_closed() {
    use veles_ble_host::admission::STAGED_DATA_EVENTS_MAX;
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(1).unwrap();
    receiver.reserve_connection(2).unwrap();
    let gates = [
        AdmissionGate::new(events.clone()),
        AdmissionGate::new(events.clone()),
    ];
    assert!(gates[0].open(connected(1), Some(BackendEvent::LinkLost { conn: 1 })));
    assert!(gates[1].open(connected(2), Some(BackendEvent::LinkLost { conn: 2 })));
    let threads: Vec<_> = (0..2)
        .map(|link| {
            let gate = gates[link].clone();
            std::thread::spawn(move || {
                for tag in 0..STAGED_DATA_EVENTS_MAX {
                    let bytes = vec![link as u8 + 1, tag as u8];
                    let pushed = gate.push(BackendEvent::Fragment {
                        conn: link as u64 + 1,
                        bytes: Payload::new(bytes),
                    });
                    assert_eq!(pushed, Push::Accepted, "link {link} fragment {tag}");
                }
            })
        })
        .collect();
    for thread in threads {
        thread.join().unwrap();
    }
    // Link 1 only: the 49th unconsumed fragment.
    assert_eq!(gates[0].push(fragment(1, 99, 2)), Push::Accepted);
    let drained = drain(&receiver);
    for link in 0..2usize {
        let tags: Vec<u8> = drained
            .iter()
            .filter_map(|event| match event {
                BackendEvent::Fragment { conn, bytes } if *conn == link as u64 + 1 => {
                    assert_eq!(bytes.as_slice()[0], link as u8 + 1, "cross-over");
                    Some(bytes.as_slice()[1])
                }
                _ => None,
            })
            .collect();
        assert_eq!(
            tags,
            (0..STAGED_DATA_EVENTS_MAX as u8).collect::<Vec<_>>(),
            "link {link}: exact order, exact content"
        );
    }
    assert!(
        drained.contains(&BackendEvent::LinkLost { conn: 1 }),
        "link 1 exceeded its bound and failed closed"
    );
    assert!(!drained.contains(&BackendEvent::LinkLost { conn: 2 }));
}
