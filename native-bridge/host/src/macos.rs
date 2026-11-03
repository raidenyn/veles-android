//! Production CoreBluetooth BLE central (OTP-24 Task 7): one worker task per scan and per
//! link on its own thread driving the async `bluest` transport. Speaks raw GATT only —
//! the core owns request correlation, handles, fragmentation and reassembly. Compiled on macOS
//! only.
//!
//! # Source mapping
//!
//! Selectively adapted from the reviewed feasibility `native-bridge/feasibility/src/macos.rs`
//! (no dependency on that crate) and structured like the production WinRT adapter
//! (`winrt.rs`). Production changes against the reference:
//!
//! - **Seam**: implements the shared [`Backend`] (owned erased [`Payload`] fragments and
//!   batches, typed [`NormalizedTarget`] identity from the peripheral UUID value, observed
//!   [`BackendEvent::LinkReleased`] / [`BackendEvent::ShutdownComplete`]).
//! - **Capacity proof**: CoreBluetooth never exposes the ATT MTU, only the maximum
//!   write-without-response length `W`. `W < 512` is refused at setup
//!   (`ConnectFailed{write_capacity}`); `W >= 512` is reported clamped to 512
//!   ([`crate::macos_map::proven_capacity`]). There is no native capacity-change callback, so
//!   `W` is re-read before every write batch ([`crate::macos_map::check_before_batch`]): a
//!   changed value reports [`BackendEvent::CapacityChanged`] and refuses the batch.
//! - **Submission isolation**: every `Backend` method does bounded in-memory work (flag
//!   stores, bounded channel sends, thread spawns). Each scan and each link runs on its OWN
//!   thread with its own single-threaded runtime ([`crate::macos_link::spawn_isolated`]) and its
//!   own cancellation (`Cancel`) and [`ReleaseGate`]. Every `bluest` call is a synchronous
//!   dispatch onto the CoreBluetooth serial queue that an async timeout cannot preempt; the
//!   thread-per-worker split means such a call that never returns wedges only its own worker —
//!   the core thread and the other links keep running and keep their deadlines. The wedged
//!   worker never finishes, so its `LinkReleased` and the process-wide `ShutdownComplete` are
//!   never announced (fail closed, no clean-release claim) until the runtime's independent exit
//!   watchdog ends the process. The CoreBluetooth queue itself is shared by all peripherals; if
//!   the OS wedges it, native progress for every phone stops — that cannot be isolated away.
//! - **Observed release**: a link's `LinkReleased` is announced only after an authoritative
//!   native completion ([`crate::macos_link::release`]): the OS acknowledging the disconnect, the
//!   delegate's own disconnect event, or `didFailToConnect` — never a state poll — **and** the
//!   core asked for the release. A connect cancelled while pending produces no native
//!   completion, so unless the OS acknowledges a disconnect the release is NOT observed: the
//!   worker finishes unclean, the closing identity stays registered, nothing is announced, the
//!   core keeps the target fenced and the process watchdog fails the exit. `ShutdownComplete`
//!   comes from the shared release coordinator, only after every scan and link worker finished
//!   cleanly.
//! - **Closing identity**: a device whose link worker has not released yet stays registered
//!   (`Active`); a new link to the same peripheral UUID is refused until the old worker's
//!   observed release (defense in depth below the core's own target fence).
//! - **Cancellation with OS work in flight**: connect, discovery, subscription and a write are
//!   `select!`ed against the link's cancel token; the dropped future cancels the pending OS
//!   operation (`bluest`'s connect guard issues `cancelPeripheralConnection`) and the worker
//!   then releases whatever the OS had already established before the release is announced.
//! - **Ordered admission** ([`AdmissionGate`]): scan and link callbacks go through the shared
//!   gate: `ScanStarted` / `Connected` first, then callbacks in arrival order; fragments that
//!   arrived between the subscription and `Connected` are drained into the gate's bounded
//!   staging before it opens; overflow fails the link closed instead of gapping the stream.
//! - **Terminal discipline**: panics are caught per worker and yield exactly one terminal
//!   through the gate; failures are bounded `&'static str` stages ([`crate::macos_map::classify`]).
//! - **Writes**: `bluest` waits for `canSendWriteWithoutResponse` / the ready callback before
//!   each fragment; the batch reports `WriteDone` only after every fragment was submitted
//!   successfully. A failed, timed-out or cancelled batch poisons the link (a batch may have
//!   reached the OS partially; the core drops the handle and nothing is replayed).
//! - **No trace sinks**: the reviewed adapter's `trace!` lines are not carried over. The
//!   adapter writes nothing anywhere.
//!
//! # Pinned `bluest` patch
//!
//! The crate is the vendored, hash-pinned `bluest 0.6.9` of `vendor/bluest` (`VELES-PATCH.md`):
//! upstream's notification stream skipped `async_broadcast` overflow gaps silently; the patch
//! yields the gap as an `Err` item, which the link loop treats as a lost stream
//! ([`crate::macos_link::Notification::Lost`]) and ends the link before any later fragment is
//! admitted. The patch also marks the delegate's `didDisconnectPeripheral` event
//! (`bluest::DID_DISCONNECT_PERIPHERAL`), the one authoritative disconnect signal. OS-side copies of fragment bytes (`NSData`) are outside
//! Rust erasure; no secure erasure of those is claimed.
//!
//! Nothing in this file has been executed on a Mac by this change (see the Task 7 report).

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::panic::AssertUnwindSafe;
use std::pin::Pin;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use bluest::error::ErrorKind;
use bluest::{Adapter, Characteristic, Device, DeviceId, Service, Uuid};
use futures_lite::{FutureExt as _, StreamExt as _};
use objc2_core_bluetooth::CBManager;
use tokio::runtime::Builder;
use tokio::sync::{OnceCell, mpsc, watch};
use tokio::time::Instant as TokioInstant;

use crate::admission::{AdmissionGate, link_panic_terminal, scan_panic_terminal};
use crate::backend::{Backend, BackendEvent, Discovered, Failure, NormalizedTarget, Target, Token};
use crate::buffer::Payload;
use crate::cleanup::CleanupOutcome;
use crate::closejoin::{
    Completion, ReleaseGate, ScanLedger, complete_scan, spawn_release_coordinator,
};
use crate::fragment::LinkCapacity;
use crate::ingress::BackendEvents;
use crate::macos_link::{
    Clock, Flow, Held as LinkHeld, Notification, OsLink, Preopen, ReleaseBudgets, drain_preopen,
    loss_terminal, on_update, release, spawn_isolated,
};
use crate::macos_map::{
    Authorization, BatchCheck, BoundedSet, OsError, capacity_failure, check_before_batch, classify,
    fallback_label, format_hint, normalize_hint, parse_hint, proven_capacity, scan_precheck,
    unavailable_timeout,
};

const SERVICE_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";
const WRITE_UUID: &str = "8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51";
const NOTIFY_UUID: &str = "8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51";
/// Budget for the central to become available (powered on, authorized).
const ADAPTER_WAIT: Duration = Duration::from_secs(10);
/// Total budget for one link's connect + discovery + capacity proof + subscription.
const SETUP_BUDGET: Duration = Duration::from_secs(20);
const WRITE_BUDGET: Duration = Duration::from_secs(10);
/// Bound on the disconnect request itself (the settle observation below is bounded separately).
const DISCONNECT_BUDGET: Duration = Duration::from_secs(3);
/// Bound on observing the release; running out is "not observed", never "assumed".
const RELEASE_OBSERVE_BUDGET: Duration = Duration::from_secs(4);
/// Bound on the scan-stop serial-queue barrier.
const SCAN_STOP_BARRIER: Duration = Duration::from_secs(2);
const LINK_LOSS_POLL: Duration = Duration::from_secs(1);
const MAX_SEEN_PER_SCAN: usize = 2048;
/// Per-link bound on queued write batches. The core submits at most one batch per link at a
/// time; this only guards an unexpected caller.
const COMMAND_QUEUE: usize = 4;
/// Local names are copied at most this long (the core sanitizes the label itself).
const LABEL_COPY_MAX: usize = 256;

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

// ---- async runtime, adapter and cancellation ---------------------------------------------------

/// Runs `make()`'s future on a dedicated thread with its own single-threaded runtime (timers
/// only, no I/O driver). The runtime is built here, on the submitting thread (bounded work);
/// any error means nothing was started and `make` is dropped.
fn spawn_worker<F: Future<Output = ()>>(
    name: String,
    make: impl FnOnce() -> F + Send + 'static,
) -> std::io::Result<()> {
    let runtime = Builder::new_current_thread().enable_time().build()?;
    spawn_isolated(name, move || runtime.block_on(make()))
}

/// Current process-wide Bluetooth authorization (class property; does not create a manager and
/// never prompts).
fn authorization() -> Authorization {
    // SAFETY: `+[CBManager authorization]` is a thread-safe class getter with no arguments.
    Authorization::from_raw(unsafe { CBManager::authorization_class() }.0)
}

/// One CoreBluetooth central manager for the whole process.
async fn shared_adapter() -> Result<Adapter, Failure> {
    static ADAPTER: OnceCell<Option<Adapter>> = OnceCell::const_new();
    ADAPTER
        .get_or_init(Adapter::default)
        .await
        .clone()
        .ok_or(Failure::new("no_adapter", false))
}

fn os_error(error: &bluest::Error) -> OsError {
    match error.kind() {
        ErrorKind::AdapterUnavailable => OsError::AdapterUnavailable,
        ErrorKind::NotAuthorized => OsError::NotAuthorized,
        ErrorKind::NotSupported => OsError::NotSupported,
        ErrorKind::AlreadyScanning => OsError::AlreadyScanning,
        ErrorKind::NotFound => OsError::NotFound,
        ErrorKind::NotConnected => OsError::NotConnected,
        ErrorKind::ConnectionFailed => OsError::ConnectionFailed,
        ErrorKind::Timeout => OsError::Timeout,
        _ => OsError::Other,
    }
}

/// Rebuilds the opaque `DeviceId` from a canonical UUID. `DeviceId` has no public constructor on
/// macOS; its serde form is the inner uuid string (lowercase, hyphenated).
fn device_id(uuid: &str) -> Result<DeviceId, Failure> {
    serde_json::from_str::<DeviceId>(&format!("\"{uuid}\""))
        .map_err(|_| Failure::new("device_lookup", false))
}

/// Per-operation cancellation: sticky, observable synchronously and awaitable; independent for
/// every scan and link (cancelling one never touches another).
#[derive(Clone)]
struct Cancel {
    tx: Arc<watch::Sender<bool>>,
    rx: watch::Receiver<bool>,
}

impl Cancel {
    fn new() -> Self {
        let (tx, rx) = watch::channel(false);
        Self {
            tx: Arc::new(tx),
            rx,
        }
    }

    fn fire(&self) {
        self.tx.send_replace(true);
    }

    fn is_set(&self) -> bool {
        *self.rx.borrow()
    }

    async fn cancelled(&self) {
        let mut rx = self.rx.clone();
        let _ = rx.wait_for(|set| *set).await;
    }
}

/// Why a cancel-aware, bounded OS step did not produce a value.
enum Stop {
    Cancelled,
    Failed(Failure),
}

/// One bounded, cancel-aware OS call against a shared deadline. Dropping the future on cancel
/// or timeout cancels the pending OS operation.
async fn bounded<T>(
    deadline: TokioInstant,
    cancel: &Cancel,
    future: impl Future<Output = Result<T, bluest::Error>>,
    stage: &'static str,
) -> Result<T, Stop> {
    tokio::select! {
        biased;
        _ = cancel.cancelled() => Err(Stop::Cancelled),
        result = tokio::time::timeout_at(deadline, future) => match result {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(error)) => Err(Stop::Failed(classify(os_error(&error), stage))),
            Err(_) => Err(Stop::Failed(classify(OsError::Timeout, stage))),
        },
    }
}

// ---- scans ----------------------------------------------------------------------------------------

struct ScanSlot {
    cancel: Cancel,
}

/// Completes the scan on every exit path (return, panic, dropped task); a worker that left
/// without certifying its cleanup finishes unclean, so no `ShutdownComplete` follows from it.
struct ScanFinish {
    scan: Token,
    events: BackendEvents,
    done: Arc<Completion>,
    cleanup: Arc<CleanupOutcome>,
    certified: bool,
}

impl Drop for ScanFinish {
    fn drop(&mut self) {
        if !self.certified {
            self.cleanup.mark_failed();
        }
        // Completion first, then (clean teardown only) the observed release of the scan.
        let (scan, events) = (self.scan, &self.events);
        complete_scan(&self.done, self.cleanup.is_clean(), || {
            let _ = events.emit(BackendEvent::ScanReleased { scan });
        });
    }
}

/// Serial-queue barrier after a scan stream drop: every `bluest` CoreBluetooth call runs on one
/// serial dispatch queue, so a lookup that completes has been ordered after the `stopScan` the
/// dropped stream issued. CoreBluetooth has no stop acknowledgement; this is an ordering
/// observation, not an OS confirmation.
async fn scan_stop_barrier(adapter: &Adapter) -> bool {
    let Ok(id) = device_id("00000000-0000-0000-0000-000000000000") else {
        return false;
    };
    tokio::time::timeout(SCAN_STOP_BARRIER, adapter.open_device(&id))
        .await
        .is_ok()
}

async fn scan_main(
    scan: Token,
    service_uuid: String,
    events: BackendEvents,
    cancel: Cancel,
    done: Arc<Completion>,
) {
    let cleanup = CleanupOutcome::new();
    let mut finish = ScanFinish {
        scan,
        events: events.clone(),
        done,
        cleanup: Arc::clone(&cleanup),
        certified: false,
    };
    let gate = AdmissionGate::with_outcome(events, Arc::clone(&cleanup));
    let mut stopped_stream = false;
    let body = AssertUnwindSafe(scan_body(
        scan,
        &service_uuid,
        &gate,
        &cancel,
        &mut stopped_stream,
    ))
    .catch_unwind()
    .await;
    match body {
        Ok(result) => {
            if let Err(failure) = result {
                // A failure of the pending start, or a safe end of a started scan.
                gate.terminate(|announced| {
                    (!cancel.is_set()).then(|| {
                        if announced {
                            BackendEvent::ScanEnded {
                                scan,
                                failure: Some(failure),
                            }
                        } else {
                            BackendEvent::ScanFailed { scan, failure }
                        }
                    })
                });
            }
        }
        Err(_) => {
            gate.terminate(|announced| {
                (!cancel.is_set()).then(|| scan_panic_terminal(scan, announced))
            });
        }
    }
    gate.abort();
    if stopped_stream {
        // The stream (and so the OS scan) was dropped inside `scan_body`; order after it.
        match shared_adapter().await {
            Ok(adapter) => {
                if !scan_stop_barrier(&adapter).await {
                    cleanup.mark_failed();
                }
            }
            Err(_) => cleanup.mark_failed(),
        }
    }
    finish.certified = true;
}

/// Runs the scan to its end. `Ok(())` = ended by cancellation (nothing to report: the core
/// confirmed the stop itself); `Err` = the failure the gate reports as the one terminal.
async fn scan_body(
    scan: Token,
    service_uuid: &str,
    gate: &Arc<AdmissionGate>,
    cancel: &Cancel,
    stopped_stream: &mut bool,
) -> Result<(), Failure> {
    let uuid = Uuid::parse_str(service_uuid).map_err(|_| Failure::new("scan_start", false))?;
    if let Some(failure) = scan_precheck(authorization()) {
        return Err(failure);
    }
    let adapter = shared_adapter().await?;
    // CoreBluetooth cannot do anything before the central is powered on; one that stays
    // off/unavailable for the whole budget fails closed, classified by the observed
    // authorization.
    let deadline = TokioInstant::from_std(Instant::now() + ADAPTER_WAIT);
    tokio::select! {
        biased;
        _ = cancel.cancelled() => return Ok(()),
        powered = tokio::time::timeout_at(deadline, adapter.wait_available()) => match powered {
            Ok(Ok(())) => {}
            Ok(Err(error)) => return Err(classify(os_error(&error), "scan_start")),
            Err(_) => return Err(unavailable_timeout(authorization())),
        },
    }
    let services = [uuid];
    let mut stream = tokio::select! {
        biased;
        _ = cancel.cancelled() => return Ok(()),
        stream = adapter.scan(&services) => match stream {
            Ok(stream) => stream,
            Err(error) => return Err(classify(os_error(&error), "scan_start")),
        },
    };
    *stopped_stream = true;
    // `ScanStarted` first; the stream is only polled after this, so no advertisement can be
    // emitted ahead of it.
    if cancel.is_set() || !gate.open(BackendEvent::ScanStarted { scan }, None) {
        return Ok(());
    }
    let mut seen = BoundedSet::new(MAX_SEEN_PER_SCAN);
    loop {
        let device = tokio::select! {
            biased;
            _ = cancel.cancelled() => return Ok(()), // dropping the stream stops the OS scan
            next = stream.next() => match next {
                Some(device) => device,
                // The central powered off or reset mid-scan: the scan ended on its own.
                None => return Err(Failure::new("radio_off", true)),
            },
        };
        let Some(hint) = format_hint(&device.device.id().to_string()) else {
            continue; // an unusable identifier is never forwarded
        };
        if !seen.insert(&hint) {
            continue; // duplicate or beyond the scan-side cap
        }
        let name = device
            .adv_data
            .local_name
            .filter(|name| !name.is_empty())
            .map(|name| truncate_to_boundary(name, LABEL_COPY_MAX));
        let label = name.unwrap_or_else(|| fallback_label(&hint));
        gate.push(BackendEvent::Advertisement {
            scan,
            device: Discovered {
                platform_hint: hint,
                label,
                advertisement: None,
            },
        });
    }
}

fn truncate_to_boundary(mut text: String, max: usize) -> String {
    if text.len() > max {
        let mut end = max;
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        text.truncate(end);
    }
    text
}

// ---- links ----------------------------------------------------------------------------------------

enum Command {
    Write { send: Token, frames: Vec<Payload> },
}

struct LinkSlot {
    commands: Option<mpsc::Sender<Command>>,
    cancel: Cancel,
    gate: Arc<ReleaseGate>,
}

/// Peripheral UUIDs whose link worker has not released yet. A key is removed only after the
/// worker's observed, clean release.
type Active = Arc<Mutex<HashSet<String>>>;

/// The OS resources of the link's connection that the worker must release.
#[derive(Default)]
struct Held {
    device: Option<Device>,
    /// `connect_device` succeeded: the OS connection is established and must be disconnected.
    connected: bool,
    /// The OS itself reported the end: `didFailToConnect` for the connect, or the peripheral
    /// delegate's `didDisconnectPeripheral` for the link. An authoritative native completion —
    /// nothing is left to release. Never set from a state poll or from our own cancel.
    os_ended: bool,
}

impl Held {
    fn kind(&self) -> LinkHeld {
        match (&self.device, self.connected, self.os_ended) {
            (None, _, _) | (_, _, true) => LinkHeld::Nothing,
            (Some(_), false, false) => LinkHeld::PendingConnect,
            (Some(_), true, false) => LinkHeld::Established,
        }
    }
}

/// The GATT objects of an established link; dropping them is local (no OS wait).
struct Link {
    /// Held so the GATT service object outlives the characteristics derived from it.
    _service: Service,
    write: Characteristic,
    notify: Characteristic,
    /// The clamped capacity proven at setup and last reported to the core.
    capacity: usize,
}

/// Leaves the worker on every path — return, panic or a dropped task — and announces the
/// observed release exactly once. Certifies only a release that was actually observed.
struct FinishGuard {
    conn: Token,
    events: BackendEvents,
    gate: Arc<ReleaseGate>,
    cleanup: Arc<CleanupOutcome>,
    active: Active,
    key: String,
    released: bool,
}

impl Drop for FinishGuard {
    fn drop(&mut self) {
        if !self.released {
            self.cleanup.mark_failed();
        }
        let clean = self.cleanup.is_clean();
        if clean {
            // Lifts the closing-identity registration BEFORE the release is announced, so the
            // core's reconnect after `LinkReleased` finds the device free.
            lock(&self.active).remove(&self.key);
        }
        if self.gate.finish_with(clean) {
            self.events
                .emit(BackendEvent::LinkReleased { conn: self.conn });
        }
    }
}

/// Connects, discovers the Veles service/write/notify characteristics and proves the link
/// capacity. Subscription is deliberately NOT part of this step: the notify stream borrows its
/// characteristic and must live in the link worker.
async fn set_up_link(
    uuid: &str,
    deadline: TokioInstant,
    cancel: &Cancel,
    held: &mut Held,
) -> Result<Link, Stop> {
    let id = device_id(uuid).map_err(Stop::Failed)?;
    let adapter = shared_adapter().await.map_err(Stop::Failed)?;
    if cancel.is_set() {
        return Err(Stop::Cancelled);
    }
    let device = bounded(deadline, cancel, adapter.open_device(&id), "device_lookup").await?;
    // From the first `connect_device` poll a pending connect may exist; it is released by the
    // worker whatever happens next (the dropped future also cancels it).
    held.device = Some(device.clone());
    // Cancelled or timed out mid-flight the connect stays `PendingConnect` (no native
    // completion exists for it). An error that is neither our timeout nor a powered-off
    // adapter can only come from the delegate's `didFailToConnect` event: the OS completed the
    // attempt, so nothing is pending.
    tokio::select! {
        biased;
        _ = cancel.cancelled() => return Err(Stop::Cancelled),
        result = tokio::time::timeout_at(deadline, adapter.connect_device(&device)) => match result {
            Ok(Ok(())) => held.connected = true,
            Ok(Err(error)) => {
                if os_error(&error) != OsError::AdapterUnavailable {
                    held.os_ended = true;
                }
                return Err(Stop::Failed(classify(os_error(&error), "gatt_connect")));
            }
            Err(_) => return Err(Stop::Failed(classify(OsError::Timeout, "gatt_connect"))),
        },
    }

    let expected = Uuid::parse_str(SERVICE_UUID).expect("constant UUID");
    let services = bounded(
        deadline,
        cancel,
        device.discover_services_with_uuid(expected),
        "gatt_profile",
    )
    .await?;
    let service = services
        .into_iter()
        .find(|service| service.uuid() == expected)
        .ok_or(Stop::Failed(Failure::new("gatt_profile", false)))?;
    let characteristics = bounded(
        deadline,
        cancel,
        service.discover_characteristics(),
        "gatt_profile",
    )
    .await?;
    let wanted = |text: &str| {
        let uuid = Uuid::parse_str(text).expect("constant UUID");
        characteristics
            .iter()
            .find(|characteristic| characteristic.uuid() == uuid)
            .cloned()
    };
    let (Some(write), Some(notify)) = (wanted(WRITE_UUID), wanted(NOTIFY_UUID)) else {
        return Err(Stop::Failed(Failure::new("gatt_profile", false)));
    };
    let write_properties = bounded(deadline, cancel, write.properties(), "gatt_profile").await?;
    let notify_properties = bounded(deadline, cancel, notify.properties(), "gatt_profile").await?;
    if !write_properties.write_without_response || !notify_properties.notify {
        return Err(Stop::Failed(Failure::new("gatt_profile", false)));
    }

    // CoreBluetooth hides the negotiated ATT MTU and reports only the maximum
    // write-without-response length. Below 512 it may be a platform cap, so the link is
    // refused; at or above it the clamped value is the proven capacity (the OTP-06 contract:
    // capacity known before the subscription).
    let max_write = write
        .max_write_len()
        .map_err(|_| Stop::Failed(Failure::new("mtu_unknown", false)))?;
    let capacity = match proven_capacity(max_write) {
        Ok(LinkCapacity::MaxWriteWithoutResponse(value)) => value,
        Ok(LinkCapacity::AttMtu(_)) | Err(_) => {
            return Err(Stop::Failed(capacity_failure(
                &crate::macos_map::CapacityError::Undetermined,
            )));
        }
    };
    Ok(Link {
        _service: service,
        write,
        notify,
        capacity,
    })
}

/// Tokio time source for the portable release logic.
struct TokioClock;

impl Clock for TokioClock {
    async fn timeout<T: Send, F: Future<Output = T> + Send>(
        &self,
        duration: Duration,
        future: F,
    ) -> Option<T> {
        tokio::time::timeout(duration, future).await.ok()
    }
}

/// The CoreBluetooth calls of one peripheral's release.
struct NativeLink<'a>(&'a Device);

impl OsLink for NativeLink<'_> {
    async fn prepare(&self) -> bool {
        shared_adapter().await.is_ok()
    }

    /// `disconnect_device` resolves `Ok` only on the delegate's disconnect event for this
    /// peripheral with no error.
    async fn disconnect(&self) -> bool {
        match shared_adapter().await {
            Ok(adapter) => adapter.disconnect_device(self.0).await.is_ok(),
            Err(_) => false,
        }
    }
}

/// Releases whatever the worker holds and **observes** the release; see
/// [`crate::macos_link::release`] for the rule. `false` = not observed (ambiguity).
async fn release_device(held: &mut Held) -> bool {
    let kind = held.kind();
    let Some(device) = held.device.take() else {
        return true; // nothing was ever acquired
    };
    release(
        &NativeLink(&device),
        &TokioClock,
        kind,
        ReleaseBudgets {
            whole: RELEASE_OBSERVE_BUDGET,
            disconnect: DISCONNECT_BUDGET,
        },
    )
    .await
}

/// The transport's notification item as the portable link logic sees it, plus whether it was
/// the OS's own `didDisconnectPeripheral` event (marked by the patched stream). An error — an
/// overflow gap included — or the end of the stream is a loss.
fn notification(item: Option<Result<Vec<u8>, bluest::Error>>) -> (Notification, bool) {
    match item {
        Some(Ok(bytes)) => (Notification::Data(bytes), false),
        Some(Err(error)) => {
            let os_disconnect = error.kind() == ErrorKind::NotConnected
                && error.message() == bluest::DID_DISCONNECT_PERIPHERAL;
            (Notification::Lost, os_disconnect)
        }
        None => (Notification::Lost, false),
    }
}

type WriteFuture<'a> = Pin<Box<dyn Future<Output = Result<(), Failure>> + Send + 'a>>;

/// Submits every fragment in order without response; the batch completes (`Ok`) only if every
/// submission succeeded. `bluest` waits for the native write-ready signal before each fragment.
async fn write_batch(write: &Characteristic, frames: &[Payload]) -> Result<(), Failure> {
    let deadline = TokioInstant::from_std(Instant::now() + WRITE_BUDGET);
    for frame in frames {
        match tokio::time::timeout_at(deadline, write.write_without_response(frame.as_slice()))
            .await
        {
            Ok(Ok(())) => {}
            Ok(Err(error)) => return Err(classify(os_error(&error), "write")),
            Err(_) => return Err(Failure::new("write_timeout", true)),
        }
    }
    Ok(())
}

/// Waits for the in-flight write batch if there is one; pending forever otherwise (disabled in
/// the `select!` by its precondition).
async fn in_flight_write(
    pending: &mut Option<(Token, WriteFuture<'_>)>,
) -> (Token, Result<(), Failure>) {
    match pending.as_mut() {
        Some((send, future)) => (*send, future.await),
        None => std::future::pending().await,
    }
}

#[allow(clippy::too_many_arguments)]
async fn link_main(
    uuid: String,
    hint: String,
    events: BackendEvents,
    gate: Arc<AdmissionGate>,
    cleanup: Arc<CleanupOutcome>,
    mut commands: mpsc::Receiver<Command>,
    cancel: Cancel,
    mut finish: FinishGuard,
    conn: Token,
) {
    let mut held = Held::default();
    let body = AssertUnwindSafe(run_link(
        &uuid,
        &hint,
        conn,
        &events,
        &gate,
        &mut commands,
        &cancel,
        &mut held,
    ))
    .catch_unwind()
    .await;
    if body.is_err() {
        // Exactly one terminal for a worker that died: a connect failure when the link was
        // never announced, a safe loss afterwards.
        if cancel.is_set() {
            gate.abort();
        } else {
            gate.terminate(|announced| link_panic_terminal(conn, announced));
        }
    }
    // Release whatever the OS had established — also after a panic or a cancel — and certify
    // only an observed release.
    let released = AssertUnwindSafe(release_device(&mut held))
        .catch_unwind()
        .await
        .unwrap_or(false);
    if !released {
        cleanup.mark_failed();
    }
    finish.released = released;
    // `finish` drops here: it announces the release iff observed and requested.
}

#[allow(clippy::too_many_arguments)]
async fn run_link(
    uuid: &str,
    hint: &str,
    conn: Token,
    events: &BackendEvents,
    gate: &Arc<AdmissionGate>,
    commands: &mut mpsc::Receiver<Command>,
    cancel: &Cancel,
    held: &mut Held,
) {
    let deadline = TokioInstant::from_std(Instant::now() + SETUP_BUDGET);
    let link = match set_up_link(uuid, deadline, cancel, held).await {
        Ok(link) => link,
        Err(stop) => {
            let failure = match stop {
                Stop::Cancelled => None,
                Stop::Failed(failure) => Some(failure),
            };
            gate.terminate(|_| {
                failure
                    .filter(|_| !cancel.is_set())
                    .map(|failure| BackendEvent::ConnectFailed { conn, failure })
            });
            return;
        }
    };
    // Subscribe (CCCD-equivalent) before `Connected`, bounded by the same total setup budget.
    let mut updates = match bounded(deadline, cancel, link.notify.notify(), "subscribe").await {
        Ok(updates) => updates,
        Err(stop) => {
            let failure = match stop {
                Stop::Cancelled => None,
                Stop::Failed(failure) => Some(failure),
            };
            gate.terminate(|_| {
                failure
                    .filter(|_| !cancel.is_set())
                    .map(|failure| BackendEvent::ConnectFailed { conn, failure })
            });
            return;
        }
    };
    if cancel.is_set() {
        gate.abort();
        return;
    }
    // Notifications that arrived between the subscription and `Connected` are drained into the
    // gate's bounded staging now, then emitted strictly behind `Connected`. A loss ends the
    // link here with its one terminal; staging overflow fails the link closed in `open`.
    let preopen = drain_preopen(gate, conn, || {
        match futures_lite::future::block_on(futures_lite::future::poll_once(updates.next())) {
            Some(item) => {
                let (item, os_disconnect) = notification(item);
                held.os_ended |= os_disconnect;
                std::task::Poll::Ready(item)
            }
            None => std::task::Poll::Pending,
        }
    });
    if preopen == Preopen::Ended {
        return;
    }
    let opened = gate.open(
        BackendEvent::Connected {
            conn,
            capacity: LinkCapacity::MaxWriteWithoutResponse(link.capacity),
            platform_hint: Some(hint.to_string()),
        },
        Some(BackendEvent::LinkLost { conn }),
    );
    if !opened || cancel.is_set() {
        gate.abort();
        return;
    }

    let device = held.device.clone().expect("device held after setup");
    let mut poll = tokio::time::interval(LINK_LOSS_POLL);
    poll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    // A failed, timed-out or cancelled batch may have reached the OS partially: the link is
    // poisoned and refuses later batches (the core drops the handle; nothing is replayed).
    let mut poisoned = false;
    let mut pending: Option<(Token, WriteFuture<'_>)> = None;
    let mut announced = link.capacity;
    if !device.is_connected().await {
        gate.terminate(|announced| loss_terminal(conn, announced));
        return;
    }
    loop {
        tokio::select! {
            biased;
            _ = cancel.cancelled() => break,
            // Notifications are polled in the same select as the write batch, so a long batch
            // never starves the stream.
            update = updates.next() => {
                let (item, os_disconnect) = notification(update);
                held.os_ended |= os_disconnect;
                if on_update(gate, conn, item) == Flow::Ended {
                    break;
                }
            },
            (send, result) = in_flight_write(&mut pending), if pending.is_some() => {
                pending = None;
                match result {
                    Ok(()) => { events.emit(BackendEvent::WriteDone { conn, send }); }
                    Err(failure) => {
                        poisoned = true;
                        events.emit(BackendEvent::WriteFailed { conn, send, failure });
                    }
                }
            },
            command = commands.recv(), if pending.is_none() => {
                let Some(Command::Write { send, frames }) = command else {
                    break; // the command sender is gone: the slot was dropped
                };
                if poisoned {
                    events.emit(BackendEvent::WriteFailed { conn, send, failure: Failure::new("write", false) });
                    continue;
                }
                // Re-read the capacity before any frame is submitted: there is no native
                // change callback, and a stale proof must never be written under.
                let observed = link.write.max_write_len().ok();
                match check_before_batch(announced, observed, frames.iter().map(Payload::len)) {
                    BatchCheck::Proceed => {
                        let write = &link.write;
                        pending = Some((send, Box::pin(async move { write_batch(write, &frames).await })));
                    }
                    BatchCheck::CapacityChanged(capacity) => {
                        poisoned = true;
                        if let LinkCapacity::MaxWriteWithoutResponse(value) = capacity {
                            announced = value;
                        }
                        gate.push(BackendEvent::CapacityChanged { conn, capacity });
                        events.emit(BackendEvent::WriteFailed { conn, send, failure: Failure::new("write_capacity", false) });
                    }
                    BatchCheck::Unreadable => {
                        poisoned = true;
                        events.emit(BackendEvent::WriteFailed { conn, send, failure: Failure::new("mtu_unknown", false) });
                    }
                    BatchCheck::FrameTooLarge => {
                        poisoned = true;
                        events.emit(BackendEvent::WriteFailed { conn, send, failure: Failure::new("write", false) });
                    }
                }
            },
            _ = poll.tick() => {
                if !device.is_connected().await {
                    gate.terminate(|announced| loss_terminal(conn, announced));
                    break;
                }
            },
        }
    }
    // Late callbacks are dropped from here on. A pending write future is dropped first
    // (cancelling its OS wait), then the notification stream (unsubscribing), then the worker
    // releases the connection.
    gate.abort();
    drop(pending);
    drop(updates);
}

// ---- backend --------------------------------------------------------------------------------------

pub struct MacosBackend {
    events: BackendEvents,
    scans: ScanLedger<ScanSlot>,
    links: HashMap<Token, LinkSlot>,
    /// Gates of disconnected links whose workers may still be releasing OS resources; the
    /// shutdown coordinator waits on them. Pruned once announced.
    retiring: Vec<Arc<ReleaseGate>>,
    active: Active,
    shutting_down: bool,
}

impl MacosBackend {
    pub fn new(events: BackendEvents) -> Self {
        Self {
            events,
            scans: ScanLedger::new(),
            links: HashMap::new(),
            retiring: Vec::new(),
            active: Arc::new(Mutex::new(HashSet::new())),
            shutting_down: false,
        }
    }

    fn prune(&mut self) {
        self.scans.prune();
        self.retiring.retain(|gate| !gate.is_announced());
    }

    /// Registers a link that never got a worker: its gate is born finished so the core's
    /// disconnect after the failure is released immediately and the target fence lifts.
    fn refuse(&mut self, conn: Token, failure: Failure) {
        let gate = ReleaseGate::new();
        let _ = gate.finish();
        self.links.insert(
            conn,
            LinkSlot {
                commands: None,
                cancel: Cancel::new(),
                gate,
            },
        );
        self.events
            .emit(BackendEvent::ConnectFailed { conn, failure });
    }
}

impl Backend for MacosBackend {
    fn start_scan(&mut self, scan: Token, service_uuid: &str) {
        if self.shutting_down {
            return;
        }
        self.prune();
        // CoreBluetooth's central scans serially: a second concurrent scan fails with
        // `resource_in_use` (the core caps concurrent scans at 2, the UI uses one).
        let cancel = Cancel::new();
        let done = Completion::new();
        let spawned = spawn_worker(format!("macos-scan-{scan}"), {
            let (uuid, events, cancel, done) = (
                service_uuid.to_string(),
                self.events.clone(),
                cancel.clone(),
                Arc::clone(&done),
            );
            move || scan_main(scan, uuid, events, cancel, done)
        });
        if spawned.is_err() {
            // No worker, nothing acquired: the failed start is also its (trivially clean)
            // release.
            self.events.emit(BackendEvent::ScanFailed {
                scan,
                failure: Failure::new("worker_spawn", true),
            });
            self.events.emit(BackendEvent::ScanReleased { scan });
            return;
        }
        self.scans.insert(scan, ScanSlot { cancel }, done);
    }

    fn stop_scan(&mut self, scan: Token) {
        if let Some(slot) = self.scans.get(&scan) {
            slot.cancel.fire();
        }
    }

    fn connect(&mut self, conn: Token, target: &Target) {
        if self.shutting_down {
            return;
        }
        self.prune();
        let Target::Hint(hint) = target;
        let Some(uuid) = parse_hint(hint) else {
            return self.refuse(conn, Failure::new("device_lookup", false));
        };
        if !lock(&self.active).insert(uuid.clone()) {
            // The previous worker for this peripheral has not observed its release yet.
            return self.refuse(conn, Failure::new("resource_in_use", true));
        }
        let (commands, receiver) = mpsc::channel(COMMAND_QUEUE);
        let cancel = Cancel::new();
        let release = ReleaseGate::new();
        let cleanup = CleanupOutcome::new();
        let finish = FinishGuard {
            conn,
            events: self.events.clone(),
            gate: Arc::clone(&release),
            cleanup: Arc::clone(&cleanup),
            active: Arc::clone(&self.active),
            key: uuid.clone(),
            released: false,
        };
        let gate = AdmissionGate::with_outcome(self.events.clone(), Arc::clone(&cleanup));
        let spawned = spawn_worker(format!("macos-link-{conn}"), {
            let (uuid, hint, events, cancel) = (
                uuid.clone(),
                hint.clone(),
                self.events.clone(),
                cancel.clone(),
            );
            move || {
                link_main(
                    uuid, hint, events, gate, cleanup, receiver, cancel, finish, conn,
                )
            }
        });
        if spawned.is_err() {
            // No worker started (the dropped guard finished its gate unclean): lift the closing
            // registration this call created and refuse; no OS resource was ever touched.
            lock(&self.active).remove(&uuid);
            return self.refuse(conn, Failure::new("worker_spawn", true));
        }
        self.links.insert(
            conn,
            LinkSlot {
                commands: Some(commands),
                cancel,
                gate: release,
            },
        );
    }

    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        normalize_hint(hint)
    }

    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Payload>) {
        let accepted = self.links.get(&conn).is_some_and(|slot| {
            !slot.cancel.is_set()
                && slot.commands.as_ref().is_some_and(|commands| {
                    commands.try_send(Command::Write { send, frames }).is_ok()
                })
        });
        if !accepted {
            self.events.emit(BackendEvent::WriteFailed {
                conn,
                send,
                failure: Failure::new("write", false),
            });
        }
    }

    fn disconnect(&mut self, conn: Token) {
        let Some(slot) = self.links.remove(&conn) else {
            return;
        };
        // Cancel first: the worker abandons any pending OS operation, suppresses callbacks and
        // releases. The release is announced by whoever arrives last — never before the worker
        // observed the OS release.
        slot.cancel.fire();
        if slot.gate.request() {
            self.events.emit(BackendEvent::LinkReleased { conn });
        } else {
            self.retiring.push(slot.gate);
        }
        self.prune();
    }

    fn shutdown(&mut self) {
        if self.shutting_down {
            return;
        }
        self.shutting_down = true;
        // Includes a failed completion for any earlier scan whose cleanup failed and was
        // pruned: that OS resource was never released, so no clean release may be claimed.
        let (scan_slots, scan_done) = self.scans.drain_for_shutdown();
        for slot in scan_slots {
            slot.cancel.fire();
        }
        let mut gates = std::mem::take(&mut self.retiring);
        for (_, slot) in self.links.drain() {
            slot.cancel.fire();
            gates.push(slot.gate);
        }
        // The submission returns now. The process-wide release is announced only after the
        // coordinator observed every scan worker and every link worker finish cleanly; a
        // wedged OS call or an unobserved release withholds it and the runtime's watchdog
        // fails the exit closed. If the coordinator cannot even be spawned, nothing is
        // announced for the same reason.
        let events = self.events.clone();
        let _ = spawn_release_coordinator(scan_done, gates, move || {
            events.emit(BackendEvent::ShutdownComplete);
        });
    }
}
