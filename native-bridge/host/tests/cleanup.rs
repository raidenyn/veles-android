//! Partial-setup cleanup (`cleanup::SetupStack`) with error injection at every acquisition.
//!
//! `SetupStack` is the production seam `winrt.rs::set_up_link` registers every OS acquisition
//! through; `LINK_SETUP_ORDER` is the shared acquisition contract (debug-asserted by
//! `acquire`). The tests walk that same sequence, fail after each prefix, and check exactly
//! what the closers observed. The closers' WinRT calls themselves run on Windows only.

use std::sync::{Arc, Mutex};

use veles_ble_host::cleanup::{
    CleanupOutcome, DropClose, LINK_SETUP_ORDER, SCAN_TEARDOWN_ORDER, SetupStack, TeardownGuard,
    attempt, scan_teardown,
};

type Log = Arc<Mutex<Vec<&'static str>>>;

/// Acquires the first `count` steps of the documented order, then drops the stack as an error
/// return would.
fn acquire_then_fail(count: usize) -> Vec<&'static str> {
    let log: Log = Arc::default();
    {
        let mut stack = SetupStack::new(CleanupOutcome::new());
        for step in LINK_SETUP_ORDER.iter().take(count) {
            let log = Arc::clone(&log);
            let step = *step;
            stack.acquire(step, move || {
                log.lock().unwrap().push(step);
                true
            });
        }
        assert_eq!(stack.len(), count);
    }
    log.lock().unwrap().clone()
}

#[test]
fn a_failure_after_any_acquisition_closes_exactly_what_was_acquired_last_first() {
    for count in 0..=LINK_SETUP_ORDER.len() {
        let closed = acquire_then_fail(count);
        let mut expected: Vec<&str> = LINK_SETUP_ORDER[..count].to_vec();
        expected.reverse();
        assert_eq!(closed, expected, "failure after {count} acquisitions");
    }
}

#[test]
fn the_service_is_closed_on_every_error_after_it_was_acquired() {
    let service = LINK_SETUP_ORDER
        .iter()
        .position(|s| *s == "service")
        .unwrap();
    for count in 0..=LINK_SETUP_ORDER.len() {
        let closed = acquire_then_fail(count);
        assert_eq!(
            closed.contains(&"service"),
            count > service,
            "failure after {count} acquisitions: {closed:?}"
        );
    }
    // Specifically the two review findings: ValueChanged / MaxPduSizeChanged registration
    // failing means "service" was acquired and "value_handler"/"pdu_handler" were not.
    let value_failed = acquire_then_fail(service + 1);
    assert_eq!(value_failed, ["service", "session", "device"]);
    let pdu_failed = acquire_then_fail(service + 2);
    assert_eq!(
        pdu_failed,
        ["value_handler", "service", "session", "device"]
    );
}

#[test]
fn a_fully_established_link_closes_in_the_reviewed_order() {
    // Handlers unsubscribed, then service, then session (maintain off + close), then device.
    assert_eq!(
        acquire_then_fail(LINK_SETUP_ORDER.len()),
        [
            "status_handler",
            "pdu_handler",
            "value_handler",
            "service",
            "session",
            "device"
        ]
    );
}

#[test]
fn a_panicking_closer_does_not_skip_the_remaining_closers() {
    let log: Log = Arc::default();
    {
        let mut stack = SetupStack::new(CleanupOutcome::new());
        for step in LINK_SETUP_ORDER.iter().take(3) {
            let log = Arc::clone(&log);
            let step = *step;
            stack.acquire(step, move || {
                if step == "service" {
                    panic!("closer failure");
                }
                log.lock().unwrap().push(step);
                true
            });
        }
    }
    assert_eq!(*log.lock().unwrap(), ["session", "device"]);
}

#[test]
fn unwinding_runs_the_closers() {
    let log: Log = Arc::default();
    let inner = Arc::clone(&log);
    let result = std::panic::catch_unwind(move || {
        let mut stack = SetupStack::new(CleanupOutcome::new());
        let first = Arc::clone(&inner);
        stack.acquire("device", move || {
            first.lock().unwrap().push("device");
            true
        });
        panic!("worker panic after the first acquisition");
    });
    assert!(result.is_err());
    assert_eq!(*log.lock().unwrap(), ["device"]);
}

#[cfg(debug_assertions)]
#[test]
#[should_panic(expected = "out of the documented order")]
fn acquisitions_out_of_the_documented_order_are_rejected() {
    let mut stack = SetupStack::new(CleanupOutcome::new());
    stack.acquire("service", || true);
}

/// Runs the documented sequence with `fail` steps' closers failing the given way, returns the
/// closers' execution order and the shared outcome.
fn run_with_failures(
    failing: &[(
        &'static str,
        bool, /* panic instead of returning false */
    )],
) -> (Vec<&'static str>, Arc<CleanupOutcome>) {
    let log: Log = Arc::default();
    let outcome = CleanupOutcome::new();
    {
        let mut stack = SetupStack::new(Arc::clone(&outcome));
        for step in LINK_SETUP_ORDER {
            let log = Arc::clone(&log);
            let mode = failing.iter().find(|(name, _)| *name == step).map(|f| f.1);
            stack.acquire(step, move || {
                log.lock().unwrap().push(step);
                match mode {
                    Some(true) => panic!("close panic"),
                    Some(false) => false,
                    None => true,
                }
            });
        }
    }
    let order = log.lock().unwrap().clone();
    (order, outcome)
}

#[test]
fn clean_closes_leave_the_outcome_clean() {
    let (order, outcome) = run_with_failures(&[]);
    assert_eq!(order.len(), LINK_SETUP_ORDER.len());
    assert!(outcome.is_clean());
}

#[test]
fn every_close_failure_or_panic_is_recorded_and_the_rest_still_run() {
    for step in LINK_SETUP_ORDER {
        for panics in [false, true] {
            let (order, outcome) = run_with_failures(&[(step, panics)]);
            assert_eq!(
                order.len(),
                LINK_SETUP_ORDER.len(),
                "{step} (panic={panics}): all closers attempted"
            );
            assert!(
                !outcome.is_clean(),
                "{step} (panic={panics}): failure must be recorded"
            );
        }
    }
    // Several failures at once stay recorded.
    let (order, outcome) = run_with_failures(&[("service", true), ("device", false)]);
    assert_eq!(order.len(), LINK_SETUP_ORDER.len());
    assert!(!outcome.is_clean());
}

// ---- scan teardown / setup-error cleanup ------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Debug)]
enum Mode {
    Ok,
    Fail,
    Panic,
}

fn run_scan_teardown(modes: [Mode; 3]) -> (Vec<&'static str>, Arc<CleanupOutcome>) {
    let log: Log = Arc::default();
    let outcome = CleanupOutcome::new();
    let step = |index: usize| {
        let log = Arc::clone(&log);
        let name = SCAN_TEARDOWN_ORDER[index];
        let mode = modes[index];
        move || {
            log.lock().unwrap().push(name);
            match mode {
                Mode::Ok => true,
                Mode::Fail => false,
                Mode::Panic => panic!("scan close panic"),
            }
        }
    };
    scan_teardown(&outcome, step(0), step(1), step(2));
    let order = log.lock().unwrap().clone();
    (order, outcome)
}

#[test]
fn clean_scan_teardown_runs_in_order_and_stays_clean() {
    let (order, outcome) = run_scan_teardown([Mode::Ok; 3]);
    assert_eq!(order, SCAN_TEARDOWN_ORDER);
    assert!(outcome.is_clean());
}

#[test]
fn every_scan_teardown_step_failure_or_panic_is_recorded_and_all_steps_still_run() {
    for index in 0..3 {
        for mode in [Mode::Fail, Mode::Panic] {
            let mut modes = [Mode::Ok; 3];
            modes[index] = mode;
            let (order, outcome) = run_scan_teardown(modes);
            assert_eq!(
                order, SCAN_TEARDOWN_ORDER,
                "{index} {mode:?}: all attempted"
            );
            assert!(!outcome.is_clean(), "{index} {mode:?}: failure recorded");
        }
    }
}

#[test]
fn a_setup_error_unsubscribe_failure_is_recorded() {
    // `Stopped` registration failed: the only acquisition is `Received`; its removal result is
    // part of the outcome (the review's `RemoveReceived` path).
    for (result, clean) in [(true, true), (false, false)] {
        let outcome = CleanupOutcome::new();
        assert_eq!(attempt(&outcome, || result), result);
        assert_eq!(outcome.is_clean(), clean);
    }
    let outcome = CleanupOutcome::new();
    assert!(!attempt(&outcome, || panic!("remove panic")));
    assert!(!outcome.is_clean());
}

#[test]
fn a_watch_dropped_without_teardown_is_a_failed_cleanup() {
    // An early return or panic unwinding past a live watcher must not certify a release.
    let outcome = CleanupOutcome::new();
    drop(TeardownGuard::new(Arc::clone(&outcome)));
    assert!(!outcome.is_clean());

    let outcome = CleanupOutcome::new();
    let mut guard = TeardownGuard::new(Arc::clone(&outcome));
    guard.done();
    drop(guard);
    assert!(outcome.is_clean());

    let outcome = CleanupOutcome::new();
    let inner = Arc::clone(&outcome);
    let _ = std::panic::catch_unwind(move || {
        let _guard = TeardownGuard::new(inner);
        panic!("panic past a live watcher");
    });
    assert!(!outcome.is_clean());
}

#[test]
fn a_scan_completion_built_from_the_outcome_withholds_the_process_release() {
    // The scan worker completes with `complete_with(outcome.is_clean())`, exactly as below.
    use veles_ble_host::closejoin::Completion;
    for (modes, clean) in [
        ([Mode::Ok; 3], true),
        ([Mode::Ok, Mode::Fail, Mode::Ok], false),
    ] {
        let (_, outcome) = run_scan_teardown(modes);
        let done = Completion::new();
        done.complete_with(outcome.is_clean());
        assert_eq!(done.is_clean(), clean);
    }
}

// ---- partial scan acquisition (Received registered, Stopped registration fails/panics) --------

fn received_guard(outcome: &Arc<CleanupOutcome>, log: &Log, result: bool) -> DropClose {
    let log = Arc::clone(log);
    DropClose::new(Arc::clone(outcome), move || {
        log.lock().unwrap().push("remove_received");
        result
    })
}

#[test]
fn an_error_return_after_received_removes_it_and_reports_the_removal_result() {
    for (result, clean) in [(true, true), (false, false)] {
        let (outcome, log): (_, Log) = (CleanupOutcome::new(), Arc::default());
        {
            let _guard = received_guard(&outcome, &log, result);
            // `Stopped` registration returned an error: the scan setup just returns.
        }
        assert_eq!(*log.lock().unwrap(), ["remove_received"]);
        assert_eq!(outcome.is_clean(), clean);
    }
}

#[test]
fn a_panic_in_stopped_registration_unsubscribes_received_and_marks_the_scan_unclean() {
    use veles_ble_host::closejoin::{Completion, spawn_release_coordinator};
    let (outcome, log): (_, Log) = (CleanupOutcome::new(), Arc::default());
    let (inner_outcome, inner_log) = (Arc::clone(&outcome), Arc::clone(&log));
    let result = std::panic::catch_unwind(move || {
        // `Received` acquired -> guard armed -> `Stopped` registration panics.
        let _guard = received_guard(&inner_outcome, &inner_log, true);
        panic!("Stopped registration panic");
    });
    assert!(result.is_err());
    assert_eq!(
        *log.lock().unwrap(),
        ["remove_received"],
        "unsubscription attempted"
    );
    assert!(
        !outcome.is_clean(),
        "the removal succeeded but the abandoned setup is not a certified release"
    );
    // The scan worker completes from this outcome, and the coordinator withholds the release.
    let done = Completion::new();
    done.complete_with(outcome.is_clean());
    let (tx, rx) = std::sync::mpsc::channel();
    spawn_release_coordinator(vec![done], Vec::new(), move || {
        let _ = tx.send(());
    })
    .expect("spawn");
    assert!(
        rx.recv_timeout(std::time::Duration::from_millis(300))
            .is_err()
    );
}

#[test]
fn a_panic_with_a_failing_and_panicking_removal_is_still_isolated_and_recorded() {
    let (outcome, log): (_, Log) = (CleanupOutcome::new(), Arc::default());
    let (inner_outcome, inner_log) = (Arc::clone(&outcome), Arc::clone(&log));
    let result = std::panic::catch_unwind(move || {
        let log = inner_log;
        let _guard = DropClose::new(Arc::clone(&inner_outcome), move || {
            log.lock().unwrap().push("remove_received");
            panic!("removal panic during unwinding");
        });
        panic!("Stopped registration panic");
    });
    assert!(result.is_err(), "no double-panic abort");
    assert_eq!(*log.lock().unwrap(), ["remove_received"]);
    assert!(!outcome.is_clean());
}

#[test]
fn a_disarmed_guard_leaves_the_registration_to_its_owner() {
    let (outcome, log): (_, Log) = (CleanupOutcome::new(), Arc::default());
    let mut guard = received_guard(&outcome, &log, true);
    guard.disarm();
    drop(guard);
    assert!(log.lock().unwrap().is_empty());
    assert!(outcome.is_clean());
}
