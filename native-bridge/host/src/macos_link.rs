//! Portable link-lifecycle logic of the macOS adapter (OTP-24 Task 7 review fixes): the
//! decisions `src/macos.rs` makes around the CoreBluetooth calls, factored out so they run — and
//! are tested against the real admission gate, ingress and a fault-injecting OS model — on any
//! host. Nothing here touches an OS API; `macos.rs` supplies [`OsLink`] / [`Clock`] and the
//! notification items.
//!
//! - **Notification path**: the (patched, loss-aware) transport yields [`Notification`] items;
//!   data is admitted through the [`AdmissionGate`], while a transport error — including the
//!   explicit overflow gap — or an ended stream is a [`Notification::Lost`] that ends the link
//!   through the gate's exactly-once terminal. No later fragment is admitted after it.
//! - **Release**: [`release`] counts only an authoritative native completion — the OS
//!   acknowledging the disconnect, or a native completion already observed (`didFailToConnect`,
//!   the delegate's disconnect event). A state poll is never evidence: a pending connect whose
//!   cancellation is not acknowledged, an unavailable adapter or an exhausted budget is NOT a
//!   release (fence kept, identity registered, nothing announced).
//! - **Isolation**: [`spawn_isolated`] gives every scan and link its own thread, so a wedged
//!   synchronous native call blocks only its own worker.

use std::future::Future;
use std::task::Poll;
use std::time::Duration;

use crate::admission::{AdmissionGate, Push, STAGED_DATA_EVENTS_MAX};
use crate::backend::{BackendEvent, Failure, Token};
use crate::buffer::Payload;

/// One item of a link's notification stream.
#[derive(Debug, PartialEq, Eq)]
pub enum Notification {
    Data(Vec<u8>),
    /// The stream errored (OS error, disconnect, or the explicit overflow gap) or ended: the
    /// link can no longer be trusted to carry an intact byte stream.
    Lost,
}

/// How the link loop continues after one item.
#[derive(Debug, PartialEq, Eq)]
pub enum Flow {
    Continue,
    /// The link ended through the gate's single terminal; leave the loop.
    Ended,
}

/// The one terminal for a lost stream: a safe `LinkLost` once `Connected` was announced, a
/// retryable `ConnectFailed` before.
pub fn loss_terminal(conn: Token, announced: bool) -> Option<BackendEvent> {
    Some(if announced {
        BackendEvent::LinkLost { conn }
    } else {
        BackendEvent::ConnectFailed {
            conn,
            failure: Failure::new("subscribe", true),
        }
    })
}

/// Admits one live item. A loss, or data the gate could not admit in order, ends the link via
/// [`AdmissionGate::terminate`], which suppresses any second terminal.
pub fn on_update(gate: &AdmissionGate, conn: Token, item: Notification) -> Flow {
    match item {
        Notification::Data(bytes) => {
            let pushed = gate.push(BackendEvent::Fragment {
                conn,
                bytes: Payload::new(bytes),
            });
            if pushed == Push::Failed {
                gate.terminate(|announced| loss_terminal(conn, announced));
                return Flow::Ended;
            }
            Flow::Continue
        }
        Notification::Lost => {
            gate.terminate(|announced| loss_terminal(conn, announced));
            Flow::Ended
        }
    }
}

/// Result of draining what already arrived between the subscription and `Connected`.
#[derive(Debug, PartialEq, Eq)]
pub enum Preopen {
    /// Open the gate: staged items (if any) follow `Connected` in order.
    Open,
    /// The stream was lost before `Connected`: the gate already ended the link with its one
    /// terminal; do not open it and do not poll the stream again.
    Ended,
}

/// Drains already-ready notifications (`next` returns `Poll::Pending` when none is ready) into
/// the gate's bounded staging. A loss ends the link here, once; staging overflow stops the drain
/// and is failed closed by `AdmissionGate::open`. Upstream (bluest's independent 16-slot
/// per-peripheral buffer) can never hold more than 16 ready items without having overflowed
/// already — which is a loss — so the staging bound is not the effective limit of this drain;
/// it is harmless only because subscribe → drain takes microseconds.
pub fn drain_preopen(
    gate: &AdmissionGate,
    conn: Token,
    mut next: impl FnMut() -> Poll<Notification>,
) -> Preopen {
    for _ in 0..STAGED_DATA_EVENTS_MAX {
        match next() {
            Poll::Pending => break,
            Poll::Ready(Notification::Lost) => {
                gate.terminate(|announced| loss_terminal(conn, announced));
                return Preopen::Ended;
            }
            Poll::Ready(Notification::Data(bytes)) => {
                let pushed = gate.push(BackendEvent::Fragment {
                    conn,
                    bytes: Payload::new(bytes),
                });
                if pushed == Push::Failed {
                    break; // `open` fails the link closed on the latched overflow
                }
            }
        }
    }
    Preopen::Open
}

// ---- release ----------------------------------------------------------------------------------------

/// What the worker holds in the OS and must release.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Held {
    /// Nothing was acquired, or the OS already completed the connection attempt unsuccessfully
    /// (`didFailToConnect`), or the OS itself reported the disconnect (`didDisconnectPeripheral`
    /// event): an authoritative native completion has been observed.
    Nothing,
    /// A connect was issued but never confirmed (cancelled, timed out or interrupted
    /// mid-flight). CoreBluetooth gives no completion for a cancelled pending connect, so this is
    /// released only if the OS acknowledges a disconnect.
    PendingConnect,
    /// The OS connection was established and no native disconnect was observed yet.
    Established,
}

/// Time source of the release operation (tokio in production, paused time in tests).
pub trait Clock {
    fn timeout<T: Send, F: Future<Output = T> + Send>(
        &self,
        duration: Duration,
        future: F,
    ) -> impl Future<Output = Option<T>> + Send;
}

/// The OS calls the release needs. They may block or never answer; [`release`] bounds them
/// with the [`Clock`] — which can only preempt a call that yields (see [`spawn_isolated`]).
pub trait OsLink {
    /// Acquire whatever is needed to talk to the OS (the adapter singleton); `false` = cannot.
    fn prepare(&self) -> impl Future<Output = bool> + Send;
    /// Issues the disconnect/cancel and resolves `true` only on the OS acknowledgement (the
    /// delegate's disconnect event for this peripheral).
    fn disconnect(&self) -> impl Future<Output = bool> + Send;
}

#[derive(Clone, Copy, Debug)]
pub struct ReleaseBudgets {
    /// Bounds the entire release, adapter acquisition included.
    pub whole: Duration,
    /// Bounds the wait for the acknowledgement inside `whole`.
    pub disconnect: Duration,
}

/// Releases `held` and reports whether the release was OBSERVED. A release counts only through
/// an authoritative native completion: [`Held::Nothing`], or the OS acknowledging the
/// disconnect. A state poll is never evidence, so a pending connect whose cancellation is never
/// acknowledged, an unavailable adapter, an unanswered or failed disconnect, or an exhausted
/// budget is `false`: the caller must keep the target fenced, keep the closing identity
/// registered and announce nothing (the process-exit watchdog then fails the exit closed).
pub async fn release<O: OsLink + Sync, C: Clock + Sync>(
    os: &O,
    clock: &C,
    held: Held,
    budgets: ReleaseBudgets,
) -> bool {
    if held == Held::Nothing {
        return true;
    }
    clock
        .timeout(budgets.whole, async {
            os.prepare().await
                && clock.timeout(budgets.disconnect, os.disconnect()).await == Some(true)
        })
        .await
        .unwrap_or(false)
}

// ---- isolated workers ----------------------------------------------------------------------------------

/// Starts a worker on its OWN OS thread. Every native CoreBluetooth call a worker makes is
/// synchronous (`bluest` dispatches onto the CoreBluetooth serial queue and blocks the polling
/// thread until it returns), and an async timeout cannot preempt a poll that does not yield.
/// Giving each scan and each link a dedicated thread (with its own single-threaded runtime) means
/// a wedged call blocks only that worker: the core thread and every other worker keep running
/// and keep honoring their own deadlines. The wedged worker never finishes, so its release gate
/// is never announced and the process-wide `ShutdownComplete` is withheld — fail closed, never a
/// clean claim — until the runtime's independent exit watchdog ends the process. Returns the
/// spawn error without running `body`.
pub fn spawn_isolated(name: String, body: impl FnOnce() + Send + 'static) -> std::io::Result<()> {
    std::thread::Builder::new()
        .name(name)
        .spawn(body)
        .map(|_| ())
}
