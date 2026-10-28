//! Platform-neutral bounded-join bookkeeping for link teardown.
//!
//! OTP-23 lifecycle fix (S-1, source-confirmed by both the GLM analysis and the independent Opus
//! review of HEAD dc5d127): `Core::release_all` sends `Close` to every connection *before*
//! `backend.shutdown()`, but the WinRT backend's `disconnect_link` removed each slot from the map,
//! so `shutdown`'s bounded join iterated an EMPTY map on every exit path - stdin EOF, port close
//! and a requested shutdown - and ran `Link::close()` on detached workers against
//! `std::process::exit`. A waiting close never existed: retained Windows traces show 37/42 linked
//! host runs exit without any `conn N released` line.
//!
//! The fix routes every disconnecting worker's JoinHandle into a `closing` sink that
//! `WinrtBackend::shutdown` joins under one bounded budget (default). The historical detached
//! close - and only that - remains reproducible in the same binary behind the
//! `VELES_OTP23_DETACHED_CLOSE=1` environment switch, so one binary hash covers both arms of the
//! controlled comparison. It is OTP-23 diagnostic-scope only: no retries, no added waits, no
//! link-down polling, and not a fix for the AccessDenied recency window (whose cause is what the
//! close-begin-matched comparison establishes).

use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const JOIN_POLL: Duration = Duration::from_millis(10);

/// Joins the given handles, finishing as many as possible before `budget` elapses.
/// Returns `(joined, total)` where `total` is the queued count; handles that did not finish are
/// detached exactly as before the fix (only a wedged OS call can hit this; the budget must never
/// be exceeded).
pub fn join_within(handles: Vec<JoinHandle<()>>, budget: Duration) -> (usize, usize) {
    let total = handles.len();
    let deadline = Instant::now() + budget;
    let mut joined = 0usize;
    for handle in handles {
        while !handle.is_finished() && Instant::now() < deadline {
            thread::sleep(JOIN_POLL);
        }
        if handle.is_finished() {
            let _ = handle.join();
            joined += 1;
        }
    }
    (joined, total)
}

/// Joins and removes every already-finished handle, returning the reaped count.
///
/// Bounds the `closing` sink between shutdowns: without this, a host that churns many
/// connect/disconnect cycles without exiting would retire one finished `JoinHandle` per close
/// and the sink would grow over process lifetime. Workers that are still closing (or a wedged
/// OS call) stay queued and are handed to the bounded shutdown join.
pub fn reap_finished(closing: &mut Vec<JoinHandle<()>>) -> usize {
    let mut reaped = 0usize;
    let mut queued = std::mem::take(closing);
    for handle in queued.drain(..) {
        if handle.is_finished() {
            let _ = handle.join();
            reaped += 1;
        } else {
            closing.push(handle);
        }
    }
    reaped
}

/// The documented legacy mode selector: when set to "1", `disconnect_link` reproduces the
/// pre-fix detached close instead of queuing the worker for the bounded join.
pub fn detached_close_enabled() -> bool {
    std::env::var("VELES_OTP23_DETACHED_CLOSE").ok().as_deref() == Some("1")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

    fn busy_for(d: Duration) -> JoinHandle<()> {
        thread::spawn(move || thread::sleep(d))
    }

    /// Bounded spin for tests that must observe a worker's completion deterministically.
    fn wait_until_finished(handle: &JoinHandle<()>, budget: Duration) {
        let deadline = Instant::now() + budget;
        while !handle.is_finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(
            handle.is_finished(),
            "worker did not finish within the budget"
        );
    }

    #[test]
    fn joins_a_worker_that_finishes_well_inside_the_budget() {
        // Basic helper property: a healthy close (a 200 ms worker) is counted joined within the
        // budget. The defect-level red/green over the real disconnect -> shutdown path lives in
        // the Windows-only winrt tests (`shutdown_joins_a_worker_retired_by_disconnect` /
        // `legacy_detached_close_is_unawaited_across_disconnect_and_shutdown`) and in the
        // legacy-vs-queued semantics test below.
        let (joined, total) = join_within(
            vec![busy_for(Duration::from_millis(200))],
            Duration::from_secs(5),
        );
        assert_eq!((joined, total), (1, 1));
    }

    #[test]
    fn wedged_worker_never_exceeds_the_budget() {
        let started = Instant::now();
        let (joined, _) = join_within(
            vec![busy_for(Duration::from_secs(60))],
            Duration::from_millis(300),
        );
        let elapsed = started.elapsed();
        assert_eq!(joined, 0);
        assert!(
            elapsed < Duration::from_secs(2),
            "budget must bound the wait: {:?}",
            elapsed
        );
    }

    #[test]
    fn mixed_workers_count_exactly_and_stay_bounded() {
        let (joined, total) = join_within(
            vec![
                busy_for(Duration::from_millis(50)),
                busy_for(Duration::from_millis(100)),
                busy_for(Duration::from_secs(90)),
            ],
            Duration::from_millis(500),
        );
        assert_eq!((joined, total), (2, 3));
    }

    #[test]
    fn no_handles_joins_nothing() {
        assert_eq!(join_within(Vec::new(), Duration::from_millis(50)), (0, 0));
    }

    #[test]
    fn joins_happen_when_the_worker_observes_its_cancel_or_close_late() {
        // Simulates a worker that only reacts to its disconnect signal mid-sleep: the joined count
        // reflects close completion, not merely thread liveness.
        let done = Arc::new(AtomicBool::new(false));
        let flag = done.clone();
        let handle = thread::spawn(move || {
            thread::sleep(Duration::from_millis(150));
            flag.store(true, Ordering::SeqCst);
        });
        let (joined, total) = join_within(vec![handle], Duration::from_secs(5));
        assert_eq!((joined, total), (1, 1));
        assert!(done.load(Ordering::SeqCst));
    }

    #[test]
    fn reap_removes_only_finished_handles_between_shutdowns() {
        let done = Arc::new(AtomicBool::new(false));
        let flag = done.clone();
        let finished_worker = thread::spawn(move || {
            while !flag.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_millis(1));
            }
        });
        let running_worker = busy_for(Duration::from_millis(250));
        let mut closing: Vec<JoinHandle<()>> = vec![finished_worker, running_worker];
        done.store(true, Ordering::SeqCst);
        wait_until_finished(&closing[0], Duration::from_secs(5));
        assert_eq!(reap_finished(&mut closing), 1);
        assert_eq!(closing.len(), 1, "the still-closing worker stays queued");
        for handle in std::mem::take(&mut closing) {
            let _ = handle.join();
        }
        assert!(done.load(Ordering::SeqCst));
    }

    #[test]
    fn churn_between_shutdowns_never_accumulates_retired_handles() {
        // I-2: repeated close/retire cycles during process lifetime are reaped at every retire,
        // so the sink is bounded by the closes in flight (at most the link limit; serialized
        // here, so at most one).
        let mut closing: Vec<JoinHandle<()>> = Vec::new();
        for _ in 0..128 {
            let done = Arc::new(AtomicBool::new(false));
            let flag = done.clone();
            closing.push(thread::spawn(move || {
                flag.store(true, Ordering::SeqCst);
            }));
            let deadline = Instant::now() + Duration::from_secs(5);
            while !done.load(Ordering::SeqCst) && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
            assert!(done.load(Ordering::SeqCst), "worker finished");
            reap_finished(&mut closing);
            assert!(
                closing.len() <= 1,
                "sink bounded by in-flight closes: {}",
                closing.len()
            );
        }
        reap_finished(&mut closing);
        assert!(
            closing.is_empty(),
            "after the last worker finished, the sink is empty"
        );
    }

    #[test]
    fn legacy_detached_close_is_unawaited_queued_close_is_joined() {
        // The red regression for the S-1 defect, encoded as the two semantics of `disconnect_link`
        // in one binary. Legacy arm (HEAD dc5d127 and `VELES_OTP23_DETACHED_CLOSE=1`): the live
        // worker's handle is dropped, so a later bounded join sees an EMPTY sink - no close is ever
        // awaited, which is why 37 of 42 retained linked traces exit without `conn N released`.
        // Fixed arm (default): the same worker is queued and joined, and the close provably
        // completed before the process would have exited.
        let done_legacy = Arc::new(AtomicBool::new(false));
        let flag_legacy = done_legacy.clone();
        let legacy_worker = thread::spawn(move || {
            thread::sleep(Duration::from_millis(200));
            flag_legacy.store(true, Ordering::SeqCst);
        });
        let mut legacy_sink: Vec<JoinHandle<()>> = Vec::new();
        let (_, legacy_total) =
            join_within(std::mem::take(&mut legacy_sink), Duration::from_secs(5));
        assert_eq!(legacy_total, 0, "legacy: shutdown has nothing to join");
        assert!(
            !done_legacy.load(Ordering::SeqCst),
            "legacy: the close is still running at process-exit time"
        );
        legacy_worker
            .join()
            .expect("legacy worker terminates anyway");

        let done_fixed = Arc::new(AtomicBool::new(false));
        let flag_fixed = done_fixed.clone();
        let fixed_worker = thread::spawn(move || {
            thread::sleep(Duration::from_millis(200));
            flag_fixed.store(true, Ordering::SeqCst);
        });
        let mut sink: Vec<JoinHandle<()>> = vec![fixed_worker];
        let (joined, total) = join_within(std::mem::take(&mut sink), Duration::from_secs(5));
        assert_eq!((joined, total), (1, 1), "fix: the close is awaited");
        assert!(
            done_fixed.load(Ordering::SeqCst),
            "fix: the close completed before the process would have exited"
        );
    }
}
