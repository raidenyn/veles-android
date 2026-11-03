//! The macOS adapter's portable link-lifecycle logic (`macos_link`) against the REAL admission
//! gate and ingress, plus the whole-bounded release against a fault-injecting OS model.
//!
//! `macos.rs` (macOS only) feeds these same functions with CoreBluetooth items and calls; the
//! tests drive them with scripted items and calls. They are portable FIXTURES: they do not run
//! CoreBluetooth and are never physical evidence.

use std::collections::VecDeque;
use std::future::{Future, pending};
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};
use std::task::Poll;
use std::time::{Duration, Instant};

use veles_ble_host::admission::{AdmissionGate, STAGED_DATA_EVENTS_MAX};
use veles_ble_host::backend::BackendEvent;
use veles_ble_host::buffer::Payload;
use veles_ble_host::closejoin::{ReleaseGate, spawn_release_coordinator};
use veles_ble_host::fragment::LinkCapacity;
use veles_ble_host::ingress::{BackendEvents, BackendPop, BackendReceiver};
use veles_ble_host::macos_link::{
    Clock, Flow, Held, Notification, OsLink, Preopen, ReleaseBudgets, drain_preopen, on_update,
    release, spawn_isolated,
};

const CONN: u64 = 5;

fn rig() -> (
    BackendEvents,
    BackendReceiver,
    std::sync::Arc<AdmissionGate>,
) {
    let (events, receiver) = BackendEvents::new();
    receiver.reserve_connection(CONN).unwrap();
    let gate = AdmissionGate::new(events.clone());
    (events, receiver, gate)
}

fn drain(receiver: &BackendReceiver) -> Vec<BackendEvent> {
    let mut out = Vec::new();
    while let BackendPop::Event(event) = receiver.pop_event(Some(Duration::ZERO)) {
        out.push(event);
    }
    out
}

fn names(events: &[BackendEvent]) -> Vec<String> {
    events
        .iter()
        .map(|event| match event {
            BackendEvent::Connected { .. } => "connected".into(),
            BackendEvent::Fragment { bytes, .. } => format!("frag{}", bytes.as_slice()[0]),
            BackendEvent::LinkLost { .. } => "lost".into(),
            BackendEvent::ConnectFailed { failure, .. } => {
                format!("connect_failed:{}", failure.stage)
            }
            other => format!("{other:?}"),
        })
        .collect()
}

fn open(gate: &AdmissionGate) -> bool {
    gate.open(
        BackendEvent::Connected {
            conn: CONN,
            capacity: LinkCapacity::MaxWriteWithoutResponse(512),
            platform_hint: None,
        },
        Some(BackendEvent::LinkLost { conn: CONN }),
    )
}

fn scripted(items: Vec<Notification>) -> impl FnMut() -> Poll<Notification> {
    let mut queue: VecDeque<Notification> = items.into();
    move || queue.pop_front().map_or(Poll::Pending, Poll::Ready)
}

// ---- notification path -------------------------------------------------------------------------------

#[test]
fn preopen_data_follows_connected_in_arrival_order() {
    let (_events, receiver, gate) = rig();
    let outcome = drain_preopen(
        &gate,
        CONN,
        scripted(vec![
            Notification::Data(vec![1]),
            Notification::Data(vec![2]),
        ]),
    );
    assert_eq!(outcome, Preopen::Open);
    assert!(drain(&receiver).is_empty(), "nothing before Connected");
    assert!(open(&gate));
    assert_eq!(names(&drain(&receiver)), ["connected", "frag1", "frag2"]);
    assert_eq!(
        on_update(&gate, CONN, Notification::Data(vec![3])),
        Flow::Continue
    );
    assert_eq!(names(&drain(&receiver)), ["frag3"]);
}

#[test]
fn a_stream_lost_before_open_ends_the_link_exactly_once() {
    let (_events, receiver, gate) = rig();
    // Data, then the stream ends/errs/overflows before `Connected`.
    let outcome = drain_preopen(
        &gate,
        CONN,
        scripted(vec![
            Notification::Data(vec![1]),
            Notification::Lost,
            Notification::Data(vec![9]),
        ]),
    );
    assert_eq!(outcome, Preopen::Ended);
    // The adapter then returns without opening: exactly one terminal, no Connected, no data.
    assert_eq!(names(&drain(&receiver)), ["connect_failed:subscribe"]);
    // Polling the ended stream again (a late loss, a repeated drain, the live path) adds nothing.
    assert_eq!(on_update(&gate, CONN, Notification::Lost), Flow::Ended);
    assert_eq!(
        on_update(&gate, CONN, Notification::Data(vec![2])),
        Flow::Continue
    );
    assert!(!open(&gate), "a terminated gate never opens");
    assert!(drain(&receiver).is_empty());
}

#[test]
fn a_loss_after_open_emits_one_terminal_and_no_later_fragment() {
    let (_events, receiver, gate) = rig();
    assert!(open(&gate));
    assert_eq!(
        on_update(&gate, CONN, Notification::Data(vec![1])),
        Flow::Continue
    );
    // The loss-aware transport reports an overflow gap as a loss: the link ends before any
    // later fragment is admitted, and a second loss never adds a second terminal.
    assert_eq!(on_update(&gate, CONN, Notification::Lost), Flow::Ended);
    assert_eq!(
        on_update(&gate, CONN, Notification::Data(vec![2])),
        Flow::Continue
    );
    assert_eq!(on_update(&gate, CONN, Notification::Lost), Flow::Ended);
    assert_eq!(names(&drain(&receiver)), ["connected", "frag1", "lost"]);
}

#[test]
fn a_loss_staged_by_the_preopen_gate_is_not_followed_by_a_second_one() {
    // The loss arrives through the gate callback path (not the drain) before open: `open`
    // flushes it once and the live loop's later loss is suppressed.
    let (_events, receiver, gate) = rig();
    gate.push(BackendEvent::LinkLost { conn: CONN });
    assert!(open(&gate));
    assert_eq!(on_update(&gate, CONN, Notification::Lost), Flow::Ended);
    let events = names(&drain(&receiver));
    assert_eq!(
        events.iter().filter(|e| *e == "lost").count(),
        1,
        "{events:?}"
    );
}

#[test]
fn preopen_burst_up_to_the_ingress_bound_is_admitted_whole_and_the_rest_stays_upstream() {
    let (_events, receiver, gate) = rig();
    let items = (0..STAGED_DATA_EVENTS_MAX as u8 + 4)
        .map(|n| Notification::Data(vec![n]))
        .collect();
    // The drain reads at most the staging size (48 = the per-link ingress event bound); what it
    // did not read stays queued upstream (macOS: in bluest's own bounded event channel).
    assert_eq!(drain_preopen(&gate, CONN, scripted(items)), Preopen::Open);
    assert!(open(&gate));
    let events = names(&drain(&receiver));
    // `Connected`, then the whole bounded prefix of the burst in order — no gap, no loss.
    assert_eq!(events[0], "connected");
    let frags: Vec<usize> = events[1..]
        .iter()
        .map(|e| e.strip_prefix("frag").unwrap().parse().unwrap())
        .collect();
    assert_eq!(
        frags,
        (0..STAGED_DATA_EVENTS_MAX).collect::<Vec<_>>(),
        "{events:?}"
    );
    assert!(!events.iter().any(|e| e == "lost"), "{events:?}");
}

/// Staging beyond the bound still fails closed (never a gap followed by later bytes): the gate
/// is pushed past its own bound directly, as a callback burst racing the open would.
#[test]
fn preopen_staging_beyond_the_bound_fails_the_link_closed_never_gapped() {
    let (_events, receiver, gate) = rig();
    for n in 0..STAGED_DATA_EVENTS_MAX as u8 + 4 {
        gate.push(BackendEvent::Fragment {
            conn: CONN,
            bytes: Payload::new(vec![n]),
        });
    }
    assert!(open(&gate));
    let events = names(&drain(&receiver));
    assert_eq!(events, ["connected", "lost"], "{events:?}");
    assert_eq!(
        on_update(&gate, CONN, Notification::Data(vec![1])),
        Flow::Continue
    );
    assert!(drain(&receiver).is_empty());
}

// ---- release ----------------------------------------------------------------------------------------------

/// Scripted OS: `ack` is what `disconnect` resolves to (`None` = the OS never answers, like a
/// cancelled pending connect that produces no native completion).
struct Os {
    prepare: Option<bool>, // None = never answers
    ack: Option<bool>,
    disconnects: AtomicU32,
}

impl Os {
    fn new(ack: Option<bool>) -> Self {
        Self {
            prepare: Some(true),
            ack,
            disconnects: AtomicU32::new(0),
        }
    }
}

impl OsLink for Os {
    fn prepare(&self) -> impl Future<Output = bool> + Send {
        let prepare = self.prepare;
        async move {
            match prepare {
                Some(ok) => ok,
                None => pending().await,
            }
        }
    }
    fn disconnect(&self) -> impl Future<Output = bool> + Send {
        self.disconnects.fetch_add(1, Ordering::SeqCst);
        let ack = self.ack;
        async move {
            match ack {
                Some(ok) => ok,
                None => pending().await,
            }
        }
    }
}

struct Tokio;

impl Clock for Tokio {
    async fn timeout<T: Send, F: Future<Output = T> + Send>(
        &self,
        duration: Duration,
        future: F,
    ) -> Option<T> {
        tokio::time::timeout(duration, future).await.ok()
    }
}

fn budgets() -> ReleaseBudgets {
    ReleaseBudgets {
        whole: Duration::from_secs(4),
        disconnect: Duration::from_secs(3),
    }
}

#[tokio::test(start_paused = true)]
async fn an_authoritative_native_completion_needs_no_further_release() {
    // `Held::Nothing` = nothing acquired, `didFailToConnect` or the delegate's disconnect event.
    let os = Os::new(None);
    assert!(release(&os, &Tokio, Held::Nothing, budgets()).await);
    assert_eq!(os.disconnects.load(Ordering::SeqCst), 0);
}

#[tokio::test(start_paused = true)]
async fn a_pending_connect_without_a_cancellation_acknowledgement_is_never_released() {
    // The cancelled pending connect produces no native completion: no ack, however long we
    // wait and whatever any state poll might say (there is no poll to consult any more).
    let os = Os::new(None);
    let started = tokio::time::Instant::now();
    assert!(!release(&os, &Tokio, Held::PendingConnect, budgets()).await);
    assert_eq!(os.disconnects.load(Ordering::SeqCst), 1);
    assert_eq!(
        started.elapsed(),
        budgets().disconnect,
        "bounded by the ack budget"
    );
}

#[tokio::test(start_paused = true)]
async fn an_acknowledged_cancellation_or_disconnect_releases() {
    for held in [Held::PendingConnect, Held::Established] {
        let os = Os::new(Some(true));
        assert!(release(&os, &Tokio, held, budgets()).await, "{held:?}");
        assert_eq!(os.disconnects.load(Ordering::SeqCst), 1);
    }
}

#[tokio::test(start_paused = true)]
async fn a_failed_disconnect_is_not_a_release() {
    for held in [Held::PendingConnect, Held::Established] {
        assert!(
            !release(&Os::new(Some(false)), &Tokio, held, budgets()).await,
            "{held:?}"
        );
    }
}

#[tokio::test(start_paused = true)]
async fn an_unavailable_adapter_withholds_the_release_without_disconnecting() {
    let mut os = Os::new(Some(true));
    os.prepare = Some(false);
    assert!(!release(&os, &Tokio, Held::Established, budgets()).await);
    assert_eq!(os.disconnects.load(Ordering::SeqCst), 0);
}

#[tokio::test(start_paused = true)]
async fn a_yielding_wedge_cannot_outlive_the_whole_release_budget() {
    let mut os = Os::new(Some(true));
    os.prepare = None;
    let started = tokio::time::Instant::now();
    assert!(!release(&os, &Tokio, Held::PendingConnect, budgets()).await);
    assert_eq!(started.elapsed(), budgets().whole);
}

// ---- wedged synchronous native call --------------------------------------------------------------------------

/// One isolated worker: a thread that runs `release` on its own single-threaded runtime and then
/// finishes its gate exactly like `FinishGuard` (clean only on an observed release).
fn spawn_release_worker(gate: Arc<ReleaseGate>, os: Os, held: Held) {
    spawn_isolated("test-link".into(), move || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .unwrap();
        let released = runtime.block_on(release(&os, &Tokio, held, budgets()));
        let _ = gate.finish_with(released);
    })
    .unwrap();
}

fn wait_until(mut condition: impl FnMut() -> bool) -> bool {
    let end = Instant::now() + Duration::from_secs(5);
    while Instant::now() < end {
        if condition() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    false
}

#[test]
fn a_wedged_synchronous_native_call_blocks_only_its_own_worker() {
    // Phone A's native call blocks its thread synchronously (a `dispatch_sync` that never
    // returns): no async timeout can preempt it.
    let (wedge_tx, wedge_rx) = std::sync::mpsc::channel::<()>();
    let gate_a = ReleaseGate::new();
    {
        let gate = Arc::clone(&gate_a);
        spawn_isolated("wedged-link".into(), move || {
            let _ = wedge_rx.recv(); // wedged inside a synchronous native call
            let _ = gate.finish_with(true);
        })
        .unwrap();
    }
    // Phone B releases normally on its own thread, concurrently.
    let gate_b = ReleaseGate::new();
    spawn_release_worker(Arc::clone(&gate_b), Os::new(Some(true)), Held::Established);
    assert!(wait_until(|| gate_b.is_finished()), "B must not wait for A");
    assert!(!gate_b.is_failed());
    assert!(gate_b.request(), "B's observed release is announced");
    // A is still wedged: nothing about it is finished, requested-release is withheld, and the
    // process-wide release is not announced (fail closed, no clean-release claim).
    assert!(!gate_a.is_finished());
    assert!(!gate_a.request());
    let announced = Arc::new(AtomicU32::new(0));
    {
        let announced = Arc::clone(&announced);
        spawn_release_coordinator(
            vec![],
            vec![Arc::clone(&gate_a), Arc::clone(&gate_b)],
            move || {
                announced.fetch_add(1, Ordering::SeqCst);
            },
        )
        .unwrap();
    }
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(announced.load(Ordering::SeqCst), 0);
    // Un-wedging A lets the coordinator observe both gates; only then is the release announced.
    wedge_tx.send(()).unwrap();
    assert!(wait_until(|| announced.load(Ordering::SeqCst) == 1));
}

#[test]
fn an_unacknowledged_pending_connect_never_announces_even_after_the_core_requests_it() {
    let gate = ReleaseGate::new();
    spawn_release_worker(Arc::clone(&gate), Os::new(None), Held::PendingConnect);
    // The worker finishes (bounded) but UNCLEAN: the core's request announces nothing.
    assert!(wait_until(|| gate.is_finished()));
    assert!(gate.is_failed());
    assert!(!gate.request());
    assert!(!gate.is_announced());
    let announced = Arc::new(AtomicU32::new(0));
    {
        let announced = Arc::clone(&announced);
        spawn_release_coordinator(vec![], vec![Arc::clone(&gate)], move || {
            announced.fetch_add(1, Ordering::SeqCst);
        })
        .unwrap();
    }
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(
        announced.load(Ordering::SeqCst),
        0,
        "no ShutdownComplete either"
    );
}
