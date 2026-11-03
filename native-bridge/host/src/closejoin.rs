//! Platform-neutral release bookkeeping for link and process teardown (OTP-24 Task 6).
//!
//! Adapted from the reviewed feasibility `closejoin.rs` (OTP-23 lifecycle fix S-1: a close that
//! nobody awaits is invisible to the process exit). The feasibility version routed worker
//! `JoinHandle`s into a sink that `shutdown` joined serially under one budget, and kept an
//! environment-selected detached legacy mode. The production adaptation keeps the lesson —
//! cleanup must be *observed*, not assumed — and changes the mechanism:
//!
//! - no serial joins on the core thread: each link owns a [`ReleaseGate`] that the link's own
//!   worker completes after every OS resource of the link was closed, and the *observed*
//!   release is announced from exactly one place (the worker, or the disconnect submission when
//!   the worker had already finished), exactly once per link;
//! - no legacy/detached arm and no environment switch: there is one behavior;
//! - process-wide completion is a [`Completion`] the shutdown coordinator waits on after the
//!   per-link gates, so `ShutdownComplete` provenance is "every worker and scan starter
//!   finished", never "shutdown was requested".
//!
//! Nothing here touches an OS API, so the semantics are unit-tested on any host.

use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};

use crate::backend::Token;

#[derive(Default)]
struct CompletionState {
    done: bool,
    /// The completing worker reported that closing its OS resources failed (an `Err` or a
    /// panic of a close/unsubscribe step). The work is over, but the release is NOT observed.
    failed: bool,
}

/// A one-shot completion flag with blocking waiters and a clean/failed outcome.
#[derive(Default)]
pub struct Completion {
    state: Mutex<CompletionState>,
    changed: Condvar,
}

impl Completion {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, CompletionState> {
        // Teardown bookkeeping must still complete after a panicking worker.
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Completes cleanly: every resource of the worker was observed closed.
    pub fn complete(&self) {
        self.complete_with(true);
    }

    /// Completes and wakes every waiter. `clean == false` records a failed cleanup; a failure
    /// is sticky (a later clean completion cannot erase it). Idempotent.
    pub fn complete_with(&self, clean: bool) {
        let mut state = self.lock();
        state.done = true;
        state.failed |= !clean;
        self.changed.notify_all();
    }

    pub fn is_complete(&self) -> bool {
        self.lock().done
    }

    /// Completed with every close observed successful.
    pub fn is_clean(&self) -> bool {
        let state = self.lock();
        state.done && !state.failed
    }

    /// Blocks until completed (clean or failed). Unbounded by design: the process exit deadline
    /// is enforced by the runtime's independent watchdog, which fails closed rather than
    /// trusting this wait.
    pub fn wait(&self) {
        let mut state = self.lock();
        while !state.done {
            state = self.changed.wait(state).unwrap_or_else(|e| e.into_inner());
        }
    }
}

#[derive(Default)]
struct GateState {
    /// The core asked for the link to be released (`disconnect`/`shutdown`).
    requested: bool,
    /// The link's worker closed every OS resource of the link.
    finished: bool,
    /// `LinkReleased` was already handed out to be announced.
    announced: bool,
    /// The worker finished but closing its OS resources failed: the release is never observed.
    failed: bool,
}

/// Per-link release bookkeeping. `LinkReleased` is reported only after the link's resources
/// are closed **and** the core asked for the release, exactly once, whichever side arrives
/// last: the caller that receives `true` from [`ReleaseGate::request`] /
/// [`ReleaseGate::finish`] must emit the event.
#[derive(Default)]
pub struct ReleaseGate {
    state: Mutex<GateState>,
    finished: Completion,
}

impl ReleaseGate {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, GateState> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// The core requested the release. Returns `true` when the worker had already finished
    /// and the caller must announce the (observed) release now.
    #[must_use]
    pub fn request(&self) -> bool {
        let mut state = self.lock();
        state.requested = true;
        if state.finished && !state.failed && !state.announced {
            state.announced = true;
            return true;
        }
        false
    }

    /// The worker closed every OS resource of the link. Returns `true` when a release was
    /// already requested and the caller must announce it now.
    #[must_use]
    pub fn finish(&self) -> bool {
        self.finish_with(true)
    }

    /// The worker is done. `clean == false` means a close/unsubscribe step failed or panicked:
    /// the worker is finished (waiters wake) but the release is never observed, so nothing is
    /// ever announced for this link and the core keeps the target fenced.
    #[must_use]
    pub fn finish_with(&self, clean: bool) -> bool {
        let mut state = self.lock();
        state.finished = true;
        state.failed |= !clean;
        self.finished.complete_with(clean);
        if !state.failed && state.requested && !state.announced {
            state.announced = true;
            return true;
        }
        false
    }

    /// Cleanup of this link failed (release withheld).
    pub fn is_failed(&self) -> bool {
        self.lock().failed
    }

    pub fn is_requested(&self) -> bool {
        self.lock().requested
    }

    /// Whether the link's resources were observed closed (worker finished).
    pub fn is_finished(&self) -> bool {
        self.finished.is_complete()
    }

    /// Whether the release was announced (finished + requested). A gate that is announced can
    /// be dropped from the adapter's bookkeeping.
    pub fn is_announced(&self) -> bool {
        self.lock().announced
    }

    /// Blocks until the worker finished (used by the shutdown coordinator only).
    pub fn wait_finished(&self) {
        self.finished.wait();
    }
}

/// Ends a scan worker: records its completion (clean or failed) and — **only when every
/// cleanup step was observed successful** — announces the scan's OS release through `announce`
/// (the adapters emit [`crate::backend::BackendEvent::ScanReleased`]). Completion is recorded
/// first, so a release the core observes always finds the completion visible. A failed cleanup
/// announces nothing: the scan stays counted by the core (and fenced against the ceiling) and
/// the process-wide release is withheld, exactly like a failed link close.
pub fn complete_scan(done: &Completion, clean: bool, announce: impl FnOnce()) {
    done.complete_with(clean);
    if clean {
        announce();
    }
}

/// The adapters' scan bookkeeping (shared by WinRT and macOS): one slot per scan worker plus a
/// **sticky** record that some scan's cleanup failed.
///
/// Pruning drops completed workers so the table stays bounded, but a worker that finished
/// *unclean* (a failed `RemoveReceived`/`Stop`, a panicked close, an unobserved stop barrier)
/// must not be forgotten with it: its OS resource was never released, so a later `shutdown`
/// must still withhold the process-wide release. The failure is therefore latched here at
/// prune time and re-presented to the shutdown coordinator as an already failed completion.
pub struct ScanLedger<T> {
    slots: HashMap<Token, (T, Arc<Completion>)>,
    cleanup_failed: bool,
}

impl<T> Default for ScanLedger<T> {
    fn default() -> Self {
        Self {
            slots: HashMap::new(),
            cleanup_failed: false,
        }
    }
}

impl<T> ScanLedger<T> {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&mut self, scan: Token, slot: T, done: Arc<Completion>) {
        self.slots.insert(scan, (slot, done));
    }

    pub fn get(&self, scan: &Token) -> Option<&T> {
        self.slots.get(scan).map(|(slot, _)| slot)
    }

    /// Workers still tracked (running or finished-but-unpruned).
    pub fn len(&self) -> usize {
        self.slots.len()
    }

    pub fn is_empty(&self) -> bool {
        self.slots.is_empty()
    }

    /// Whether any scan cleanup is known to have failed (sticky across pruning).
    pub fn cleanup_failed(&self) -> bool {
        self.cleanup_failed
    }

    /// Drops completed workers; an unclean completion is latched, never forgotten.
    pub fn prune(&mut self) {
        let mut failed = false;
        self.slots.retain(|_, (_, done)| {
            if !done.is_complete() {
                return true;
            }
            failed |= !done.is_clean();
            false
        });
        self.cleanup_failed |= failed;
    }

    /// Takes every slot for the process-wide release: the slots (to cancel) and the
    /// completions the coordinator must wait on. A latched earlier failure is appended as an
    /// already failed completion, so the coordinator withholds `ShutdownComplete`.
    pub fn drain_for_shutdown(&mut self) -> (Vec<T>, Vec<Arc<Completion>>) {
        self.prune_unclean_only();
        let mut slots = Vec::new();
        let mut done = Vec::new();
        for (_, (slot, completion)) in self.slots.drain() {
            slots.push(slot);
            done.push(completion);
        }
        if self.cleanup_failed {
            let failed = Completion::new();
            failed.complete_with(false);
            done.push(failed);
        }
        (slots, done)
    }

    /// Latches failures of already completed workers without dropping them.
    fn prune_unclean_only(&mut self) {
        let failed = self
            .slots
            .values()
            .any(|(_, done)| done.is_complete() && !done.is_clean());
        self.cleanup_failed |= failed;
    }
}

/// Spawns the process-wide release coordinator: it waits for every scan worker completion and
/// every link gate to finish, then invokes `announce` exactly once. This is the only source of
/// `ShutdownComplete` provenance — "every worker finished", never "shutdown was requested". A
/// wedged worker withholds `announce` forever (the runtime's independent watchdog bounds the
/// process). Returns the spawn error without announcing anything, so a coordinator that cannot
/// start can never fabricate an observation.
pub fn spawn_release_coordinator(
    scans: Vec<Arc<Completion>>,
    links: Vec<Arc<ReleaseGate>>,
    announce: impl FnOnce() + Send + 'static,
) -> std::io::Result<()> {
    std::thread::Builder::new()
        .name("release-coordinator".into())
        .spawn(move || {
            for scan in &scans {
                scan.wait();
            }
            for link in &links {
                link.wait_finished();
            }
            // A worker that finished with a failed/panicked close did not release its
            // resources: withhold the process-wide release (the watchdog fails the exit).
            if scans.iter().all(|scan| scan.is_clean()) && links.iter().all(|g| !g.is_failed()) {
                announce();
            }
        })
        .map(|_detached| ())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;
    use std::time::Duration;

    #[test]
    fn release_waits_for_both_request_and_finish_in_either_order() {
        let gate = ReleaseGate::new();
        assert!(!gate.request(), "worker still running: nothing to announce");
        assert!(!gate.is_announced());
        assert!(gate.finish(), "last arrival announces");
        assert!(gate.is_announced());

        let gate = ReleaseGate::new();
        assert!(
            !gate.finish(),
            "no request yet: resources closed, not released"
        );
        assert!(gate.is_finished() && !gate.is_announced());
        assert!(gate.request(), "request after finish announces immediately");
    }

    #[test]
    fn release_is_announced_exactly_once() {
        let gate = ReleaseGate::new();
        assert!(!gate.request());
        assert!(gate.finish());
        assert!(!gate.request(), "a repeated request never re-announces");
        assert!(!gate.finish(), "a repeated finish never re-announces");
    }

    #[test]
    fn slow_link_does_not_block_another_gate() {
        let slow = ReleaseGate::new();
        let fast = ReleaseGate::new();
        assert!(!slow.request());
        assert!(!fast.request());
        assert!(
            fast.finish(),
            "the fast link announces without the slow one"
        );
        assert!(!slow.is_finished());
        assert!(slow.finish());
    }

    #[test]
    fn wait_finished_blocks_until_the_worker_finishes() {
        let gate = ReleaseGate::new();
        let worker = {
            let gate = Arc::clone(&gate);
            thread::spawn(move || {
                thread::sleep(Duration::from_millis(50));
                let _ = gate.finish();
            })
        };
        gate.wait_finished();
        assert!(gate.is_finished());
        worker.join().expect("worker");
    }

    #[test]
    fn completion_is_sticky_and_idempotent() {
        let completion = Completion::new();
        assert!(!completion.is_complete());
        completion.complete();
        completion.complete();
        completion.wait();
        assert!(completion.is_complete());
    }

    #[test]
    fn poisoned_gate_still_completes_teardown() {
        let gate = ReleaseGate::new();
        let poisoner = {
            let gate = Arc::clone(&gate);
            thread::spawn(move || {
                let _guard = gate.state.lock().unwrap();
                panic!("poison");
            })
        };
        assert!(poisoner.join().is_err());
        assert!(!gate.request());
        assert!(gate.finish(), "teardown completes despite a poisoned lock");
    }
    #[test]
    fn coordinator_announces_only_after_every_worker_finished() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let scan = Completion::new();
        let fast = ReleaseGate::new();
        let slow = ReleaseGate::new();
        let announced = Arc::new(AtomicUsize::new(0));
        let probe = Arc::clone(&announced);
        spawn_release_coordinator(
            vec![Arc::clone(&scan)],
            vec![Arc::clone(&fast), Arc::clone(&slow)],
            move || {
                probe.fetch_add(1, Ordering::SeqCst);
            },
        )
        .expect("spawn");
        let _ = fast.finish();
        scan.complete();
        thread::sleep(Duration::from_millis(100));
        assert_eq!(
            announced.load(Ordering::SeqCst),
            0,
            "one wedged link withholds the process-wide release"
        );
        let _ = slow.finish();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while announced.load(Ordering::SeqCst) == 0 && std::time::Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        thread::sleep(Duration::from_millis(50));
        assert_eq!(announced.load(Ordering::SeqCst), 1, "exactly once");
    }

    #[test]
    fn coordinator_with_nothing_outstanding_announces_once() {
        use std::sync::mpsc;
        let (tx, rx) = mpsc::channel();
        spawn_release_coordinator(Vec::new(), Vec::new(), move || {
            let _ = tx.send(());
        })
        .expect("spawn");
        rx.recv_timeout(Duration::from_secs(5)).expect("announced");
        assert!(rx.recv_timeout(Duration::from_millis(50)).is_err());
    }

    #[test]
    fn a_failed_cleanup_never_announces_the_release_in_either_order() {
        let gate = ReleaseGate::new();
        assert!(!gate.request());
        assert!(!gate.finish_with(false), "failed close: no LinkReleased");
        assert!(gate.is_finished() && gate.is_failed() && !gate.is_announced());
        assert!(
            !gate.request(),
            "a repeated request still announces nothing"
        );

        let gate = ReleaseGate::new();
        assert!(!gate.finish_with(false));
        assert!(
            !gate.request(),
            "request after a failed finish announces nothing"
        );
        assert!(gate.is_failed());
    }

    #[test]
    fn a_failed_worker_still_wakes_waiters_but_withholds_the_process_release() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let good = ReleaseGate::new();
        let bad = ReleaseGate::new();
        let scan_bad = Completion::new();
        let scan_good = Completion::new();
        let announced = Arc::new(AtomicUsize::new(0));
        let probe = Arc::clone(&announced);
        let (tx, rx) = std::sync::mpsc::channel();
        // The coordinator must return (not hang) once every worker finished, even failed.
        let sync = tx.clone();
        spawn_release_coordinator(
            vec![Arc::clone(&scan_good), Arc::clone(&scan_bad)],
            vec![Arc::clone(&good), Arc::clone(&bad)],
            move || {
                probe.fetch_add(1, Ordering::SeqCst);
                let _ = sync.send(());
            },
        )
        .expect("spawn");
        scan_good.complete();
        scan_bad.complete_with(false);
        let _ = good.finish();
        let _ = bad.finish_with(false);
        assert!(rx.recv_timeout(Duration::from_millis(300)).is_err());
        assert_eq!(announced.load(Ordering::SeqCst), 0);
        assert!(scan_bad.is_complete() && !scan_bad.is_clean());
        drop(tx);
    }

    #[test]
    fn a_failed_scan_completion_alone_withholds_the_process_release() {
        let scan = Completion::new();
        let (tx, rx) = std::sync::mpsc::channel();
        spawn_release_coordinator(vec![Arc::clone(&scan)], Vec::new(), move || {
            let _ = tx.send(());
        })
        .expect("spawn");
        scan.complete_with(false);
        assert!(rx.recv_timeout(Duration::from_millis(300)).is_err());
        // A later clean completion cannot erase the failure.
        scan.complete();
        assert!(!scan.is_clean());
    }

    #[test]
    fn ledger_latches_an_unclean_completion_across_pruning() {
        let mut ledger: ScanLedger<u8> = ScanLedger::new();
        let (bad, good, running) = (Completion::new(), Completion::new(), Completion::new());
        ledger.insert(1, 1, Arc::clone(&bad));
        ledger.insert(2, 2, Arc::clone(&good));
        ledger.insert(3, 3, Arc::clone(&running));
        bad.complete_with(false);
        good.complete();
        ledger.prune();
        assert_eq!(ledger.len(), 1, "only the running worker is tracked");
        assert!(ledger.cleanup_failed(), "the unclean completion is sticky");
        assert!(ledger.get(&3).is_some() && ledger.get(&1).is_none());
        ledger.prune();
        assert!(ledger.cleanup_failed(), "pruning again never clears it");
    }

    #[test]
    fn ledger_shutdown_presents_a_forgotten_failure_as_a_failed_completion() {
        let mut ledger: ScanLedger<u8> = ScanLedger::new();
        let bad = Completion::new();
        ledger.insert(1, 1, Arc::clone(&bad));
        bad.complete_with(false);
        ledger.prune();
        let (slots, done) = ledger.drain_for_shutdown();
        assert!(slots.is_empty());
        assert!(done.iter().any(|c| c.is_complete() && !c.is_clean()));
        assert!(ledger.is_empty());
    }

    #[test]
    fn ledger_shutdown_sees_an_unpruned_unclean_completion() {
        let mut ledger: ScanLedger<u8> = ScanLedger::new();
        let bad = Completion::new();
        ledger.insert(1, 1, Arc::clone(&bad));
        bad.complete_with(false);
        let (slots, done) = ledger.drain_for_shutdown();
        assert_eq!(slots, vec![1]);
        assert!(done.iter().any(|c| !c.is_clean()));
    }

    #[test]
    fn complete_scan_announces_only_a_clean_release_after_recording_it() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let announced = AtomicUsize::new(0);
        let done = Completion::new();
        complete_scan(&done, true, || {
            assert!(done.is_complete(), "completion is visible to the announcee");
            announced.fetch_add(1, Ordering::SeqCst);
        });
        assert_eq!(announced.load(Ordering::SeqCst), 1);
        let failed = Completion::new();
        complete_scan(&failed, false, || {
            announced.fetch_add(1, Ordering::SeqCst);
        });
        assert_eq!(announced.load(Ordering::SeqCst), 1, "unclean: no release");
        assert!(failed.is_complete() && !failed.is_clean());
    }
}
