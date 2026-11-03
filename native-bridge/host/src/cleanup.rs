//! Partial-acquisition cleanup for adapter setup (OTP-24 Task 6 review fix).
//!
//! A link's setup acquires OS resources one at a time (device, session, GATT service, then
//! three callback subscriptions). Every error path — including a `?` between two acquisitions —
//! must close exactly what was acquired so far, in reverse order, before the worker may announce
//! `LinkReleased`. Hand-written per-path `abandon` calls missed the service close on two paths;
//! [`SetupStack`] makes that structural: each acquisition registers its own closer
//! *immediately*, and dropping the stack (error return, cancel, panic unwind, or the
//! established link's own drop) runs every closer once, last-acquired first. A closer reports
//! success as a `bool`; a failure or panic is recorded in the shared [`CleanupOutcome`] so the
//! worker's finish bookkeeping withholds `LinkReleased` / `ShutdownComplete` (an attempted close
//! is not an observed release) while the remaining closers still run.
//!
//! The acquisition order is the shared contract [`LINK_SETUP_ORDER`]; [`SetupStack::acquire`]
//! asserts it, so the real adapter and the error-injection tests walk the same sequence. The
//! reverse of it is the reviewed close order (handlers, service, maintain-off + session,
//! device). Pure Rust: tested on any host; the closers themselves are the adapter's WinRT calls.

use std::panic::{self, AssertUnwindSafe};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

/// The OS acquisitions of one link's setup, in order. The `session` closer also clears
/// `MaintainConnection`; the three handler steps are subscriptions whose closers unsubscribe.
pub const LINK_SETUP_ORDER: [&str; 6] = [
    "device",
    "session",
    "service",
    "value_handler",
    "pdu_handler",
    "status_handler",
];

type Closer = (&'static str, Box<dyn FnOnce() -> bool>);

/// Sticky record of whether any close/unsubscribe step failed (returned `false` or panicked).
/// Shared between the [`SetupStack`] that runs the closers and the worker's finish bookkeeping,
/// which must not certify a release that was only *attempted*.
#[derive(Default)]
pub struct CleanupOutcome {
    failed: AtomicBool,
}

impl CleanupOutcome {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn mark_failed(&self) {
        self.failed.store(true, Ordering::SeqCst);
    }

    pub fn is_clean(&self) -> bool {
        !self.failed.load(Ordering::SeqCst)
    }
}

pub struct SetupStack {
    closers: Vec<Closer>,
    outcome: Arc<CleanupOutcome>,
}

impl SetupStack {
    pub fn new(outcome: Arc<CleanupOutcome>) -> Self {
        Self {
            closers: Vec::new(),
            outcome,
        }
    }

    /// Registers the closer of a resource acquired *just now*. Panics in debug builds if the
    /// step is not the next one of [`LINK_SETUP_ORDER`].
    /// A closer returns `true` only if every native close/unsubscribe it performed succeeded.
    pub fn acquire(&mut self, step: &'static str, closer: impl FnOnce() -> bool + 'static) {
        debug_assert_eq!(
            LINK_SETUP_ORDER.get(self.closers.len()).copied(),
            Some(step),
            "setup acquisition out of the documented order"
        );
        self.closers.push((step, Box::new(closer)));
    }

    /// Acquired steps so far, in acquisition order.
    pub fn steps(&self) -> Vec<&'static str> {
        self.closers.iter().map(|(step, _)| *step).collect()
    }

    pub fn len(&self) -> usize {
        self.closers.len()
    }

    pub fn is_empty(&self) -> bool {
        self.closers.is_empty()
    }
}

impl Drop for SetupStack {
    fn drop(&mut self) {
        while let Some((_, closer)) = self.closers.pop() {
            // Every closer is attempted, whatever an earlier one did; a `false` result or a
            // panic is recorded so the release is not certified.
            match panic::catch_unwind(AssertUnwindSafe(closer)) {
                Ok(true) => {}
                Ok(false) | Err(_) => self.outcome.mark_failed(),
            }
        }
    }
}

/// Runs one cleanup step with panic isolation, recording `false`/panic as a failed cleanup.
/// Returns whether the step succeeded. Every caller attempts its remaining steps regardless.
pub fn attempt(outcome: &CleanupOutcome, step: impl FnOnce() -> bool) -> bool {
    match panic::catch_unwind(AssertUnwindSafe(step)) {
        Ok(true) => true,
        Ok(false) | Err(_) => {
            outcome.mark_failed();
            false
        }
    }
}

/// The scan watcher's teardown steps in their fixed order: unsubscribe the advertisement
/// callback, request the stop and observe a terminal watcher state, unsubscribe `Stopped`.
pub const SCAN_TEARDOWN_ORDER: [&str; 3] = ["remove_received", "stop_observed", "remove_stopped"];

/// The scan teardown sequence shared by `winrt.rs` (`ScanWatch::stop`) and the failure-injection
/// tests: all three steps are always attempted, in [`SCAN_TEARDOWN_ORDER`], each isolated from
/// the others' failure or panic; any failure is recorded in `outcome`.
pub fn scan_teardown(
    outcome: &CleanupOutcome,
    remove_received: impl FnOnce() -> bool,
    stop_observed: impl FnOnce() -> bool,
    remove_stopped: impl FnOnce() -> bool,
) {
    attempt(outcome, remove_received);
    attempt(outcome, stop_observed);
    attempt(outcome, remove_stopped);
}

/// Marks a started OS resource whose teardown has not run: dropping the guard without
/// [`TeardownGuard::done`] (an early return or a panic unwinding past a live watcher) records a
/// failed cleanup, so a resource that was never released is never certified as released.
pub struct TeardownGuard {
    outcome: Arc<CleanupOutcome>,
    done: bool,
}

impl TeardownGuard {
    pub fn new(outcome: Arc<CleanupOutcome>) -> Self {
        Self {
            outcome,
            done: false,
        }
    }

    /// The teardown sequence ran (its own failures are recorded separately).
    pub fn done(&mut self) {
        self.done = true;
    }
}

impl Drop for TeardownGuard {
    fn drop(&mut self) {
        if !self.done {
            self.outcome.mark_failed();
        }
    }
}

/// RAII close of ONE already-acquired OS registration whose owner may not complete (a
/// partial acquisition): dropped armed — an early return or a panic unwinding past it — it
/// attempts the close ([`attempt`], panic-isolated) and, if it was dropped while panicking,
/// additionally records a failed cleanup (an abandoned setup is not a certified release).
/// [`DropClose::disarm`] hands the registration over to its full owner once that owns it.
pub struct DropClose {
    outcome: Arc<CleanupOutcome>,
    closer: Option<Box<dyn FnOnce() -> bool>>,
}

impl DropClose {
    pub fn new(outcome: Arc<CleanupOutcome>, closer: impl FnOnce() -> bool + 'static) -> Self {
        Self {
            outcome,
            closer: Some(Box::new(closer)),
        }
    }

    pub fn disarm(&mut self) {
        self.closer = None;
    }
}

impl Drop for DropClose {
    fn drop(&mut self) {
        if let Some(closer) = self.closer.take() {
            attempt(&self.outcome, closer);
            if std::thread::panicking() {
                self.outcome.mark_failed();
            }
        }
    }
}
