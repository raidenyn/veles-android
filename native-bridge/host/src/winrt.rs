//! Production WinRT BLE central (OTP-24 Task 6): advertisement watcher, per-link GATT worker
//! threads and cancel-aware waits. Speaks raw GATT only — the core owns request correlation,
//! handles, fragmentation and reassembly. Compiled on Windows only.
//!
//! # Source mapping
//!
//! Selectively adapted from the reviewed feasibility `native-bridge/feasibility/src/winrt.rs`
//! (no dependency on that crate): the cancel-aware `wait` poller, the scan precheck and watcher
//! setup, the connect order (device lookup → access → maintained session → uncached service
//! discovery → ATT MTU ≥ 185 **before** the CCCD write → characteristics → handler-then-CCCD),
//! the per-link worker and the six-step OS close. Production changes against that reference:
//!
//! - **Seam**: implements the shared [`Backend`] (owned erased [`Payload`] fragments and
//!   batches, typed [`NormalizedTarget`] identity, observed [`BackendEvent::LinkReleased`] and
//!   [`BackendEvent::ShutdownComplete`]).
//! - **Submission isolation**: every `Backend` method only does bounded in-memory work (flag
//!   stores, channel sends, thread spawns). Each scan and each link owns its own worker thread;
//!   every WinRT call, wait, write and close runs there. A slow or wedged link can never block
//!   the core thread or another link, and no per-link close is joined on the core thread.
//! - **Observed release**: a link's `LinkReleased` is announced only after its worker closed
//!   every OS resource **and** the core asked for the release (see
//!   [`crate::closejoin::ReleaseGate`]); `ShutdownComplete` is announced by a dedicated
//!   coordinator thread only after every scan worker and every link worker finished. Neither is
//!   inferred from a request. A wedged OS call therefore withholds the event and the runtime's
//!   independent watchdog fails the exit closed.
//! - **Pending/closing fence**: a disconnect during setup cancels the pending OS operation
//!   (`IAsyncOperation::Cancel`) and the worker closes whatever it had opened before the release
//!   is announced; the core keeps the normalized target fenced until that `LinkReleased`.
//! - **Stale callbacks**: every OS callback checks the link's cancel flag first and unsubscribes
//!   (`RemoveValueChanged`, `RemoveConnectionStatusChanged`, `RemoveMaxPduSizeChanged`) before
//!   the OS objects are closed; late events are additionally dropped by the ingress token
//!   bookkeeping.
//! - **Ordered admission** ([`crate::admission::AdmissionGate`]): every scan callback
//!   (advertisement, early `Stopped`) is staged behind `ScanStarted` and every link callback
//!   (notification, loss, capacity) behind `Connected`, with a bounded buffer that fails the
//!   link closed on overflow instead of dropping stream bytes.
//! - **Partial-setup cleanup** ([`crate::cleanup::SetupStack`]): each OS acquisition registers
//!   its closer immediately; any error return closes exactly what was acquired, last first,
//!   including the GATT service.
//! - **Close failures are not releases**: every closer reports success; a failed or panicked
//!   close/unsubscribe (link or scan) is recorded, the remaining closers still run, and the
//!   worker finishes WITHOUT certifying the release — no `LinkReleased`, no `ShutdownComplete`
//!   (the core keeps the target fenced; the process watchdog bounds the exit).
//! - **Nonblocking callbacks**: callbacks take the admission gate with bounded `try_lock`
//!   attempts only; plain contention defers into the gate's bounded ordered lane (nothing is
//!   lost). Only a genuinely *full* lane drops a non-droppable event: that fails the link in
//!   the ingress immediately (not when the worker, possibly blocked in a write, next wakes), fences
//!   every later fragment/control of the link at the gate, and `Command::Fail` makes the
//!   worker end the link — never a wait, never data behind a gap. Advertisements stay lossy.
//! - **One terminal on panic**: a worker panic before or during `open` yields exactly one
//!   `ConnectFailed` (or `LinkLost`/`ScanEnded` once announced) via `AdmissionGate::terminate`.
//! - **Observed watcher stop**: a scan's completion waits until the watcher status is
//!   terminal (`Stopped`/`Aborted`/never started), not merely until `Stop()` was requested.
//! - **Bounded ingress**: callbacks only copy at most one bounded fragment and `emit` it through
//!   the non-blocking [`BackendEvents`] producer; they never wait.
//! - **Capacity**: the ATT MTU (`GattSession::MaxPduSize`) is reported once in `Connected`; later
//!   `MaxPduSizeChanged` observations are reported as [`BackendEvent::CapacityChanged`] (the core
//!   freezes the budget and tears the link down on a different derived budget).
//! - **Writes**: every native `WriteValueWithResultAndOption` result is checked before the whole
//!   batch reports `WriteDone`; a failed or cancelled write poisons the link for further batches.
//! - **No trace sinks**: the reviewed adapter's `trace!` lines (hint text, timings, HRESULTs, env
//!   switches such as scan-any/detached-close) are not carried over. The adapter writes nothing
//!   anywhere; failures are bounded `&'static str` stages.
//!
//! OS/WinRT-side copies of fragment bytes (the `IBuffer`s) are outside Rust erasure; no secure
//! erasure of those is claimed.

use std::collections::{HashMap, HashSet};
use std::panic::{self, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use windows::Devices::Bluetooth::Advertisement::{
    BluetoothLEAdvertisementReceivedEventArgs, BluetoothLEAdvertisementWatcher,
    BluetoothLEAdvertisementWatcherStatus, BluetoothLEAdvertisementWatcherStoppedEventArgs,
    BluetoothLEScanningMode,
};
use windows::Devices::Bluetooth::GenericAttributeProfile::{
    GattCharacteristic, GattCharacteristicProperties,
    GattClientCharacteristicConfigurationDescriptorValue, GattCommunicationStatus, GattSession,
    GattValueChangedEventArgs, GattWriteOption,
};
use windows::Devices::Bluetooth::{
    BluetoothAdapter, BluetoothAddressType, BluetoothCacheMode, BluetoothConnectionStatus,
    BluetoothLEDevice,
};
use windows::Foundation::TypedEventHandler;
use windows::Storage::Streams::{DataReader, DataWriter, IBuffer};
use windows::Win32::System::WinRT::{RO_INIT_MULTITHREADED, RoInitialize};
use windows::core::{GUID, IInspectable, RuntimeType};
use windows_future::{AsyncStatus, IAsyncOperation};

use crate::admission::{
    AdmissionGate, Push, link_contention_terminal, link_panic_terminal, scan_panic_terminal,
};
use crate::backend::{Backend, BackendEvent, Discovered, Failure, NormalizedTarget, Target, Token};
use crate::buffer::Payload;
use crate::cleanup::{CleanupOutcome, DropClose, SetupStack, TeardownGuard, scan_teardown};
use crate::closejoin::{
    Completion, ReleaseGate, ScanLedger, complete_scan, spawn_release_coordinator,
};
use crate::fragment::{LinkCapacity, MAX_MTU, MIN_MTU};
use crate::ingress::{BackendEvents, FRAGMENT_FRAME_MAX};
use crate::winrt_map::{
    AdapterProbe, access_status, bluetooth_error, fallback_label, format_hint, gatt_status,
    normalize_hint, parse_hint, scan_precheck, watcher_status_is_terminal,
};

const SERVICE_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";
const WRITE_UUID: &str = "8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51";
const NOTIFY_UUID: &str = "8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51";
const SETUP_BUDGET: Duration = Duration::from_secs(20);
const PRECHECK_BUDGET: Duration = Duration::from_secs(10);
const MTU_WAIT: Duration = Duration::from_secs(3);
const WRITE_BUDGET: Duration = Duration::from_secs(10);
const SCAN_START_WAIT: Duration = Duration::from_millis(1500);
const MAX_SEEN_PER_SCAN: usize = 2048;
/// Defensive per-link bound on queued write batches. The core submits at most one batch per
/// link at a time (single in-flight send); this only guards an unexpected caller.
const MAX_QUEUED_BATCHES: usize = 16;

fn init_apartment() {
    // MTA; the call is harmless if the thread is already initialised.
    let _ = unsafe { RoInitialize(RO_INIT_MULTITHREADED) };
}

fn guid(text: &str) -> GUID {
    GUID::try_from(text).expect("constant GUID")
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    // Callbacks and teardown must never panic across the OS boundary because of a poisoned lock.
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

#[derive(Debug)]
enum WaitError {
    Cancelled,
    TimedOut,
    Os,
}

/// Polls an async operation so a stuck OS call can always be abandoned on cancel or deadline;
/// the OS operation itself is cancelled on abandonment.
fn wait<T: RuntimeType + 'static>(
    op: IAsyncOperation<T>,
    cancel: &AtomicBool,
    deadline: Instant,
) -> Result<T, WaitError> {
    loop {
        match op.Status().map_err(|_| WaitError::Os)? {
            AsyncStatus::Completed => return op.GetResults().map_err(|_| WaitError::Os),
            AsyncStatus::Started => {
                if cancel.load(Ordering::SeqCst) {
                    let _ = op.Cancel();
                    return Err(WaitError::Cancelled);
                }
                if Instant::now() >= deadline {
                    let _ = op.Cancel();
                    return Err(WaitError::TimedOut);
                }
                thread::sleep(Duration::from_millis(10));
            }
            _ => return op.GetResults().map_err(|_| WaitError::Os),
        }
    }
}

/// Copies at most `max + 1` bytes of the buffer: a value above `max` is returned one byte
/// over so the ingress rejects it as oversize (failing the link) without this callback ever
/// copying an unbounded peer-controlled length.
fn bounded_buffer_bytes(buffer: &IBuffer, max: usize) -> windows::core::Result<Vec<u8>> {
    let reader = DataReader::FromBuffer(buffer)?;
    let available = reader.UnconsumedBufferLength()? as usize;
    let mut bytes = vec![0u8; available.min(max + 1)];
    reader.ReadBytes(&mut bytes)?;
    Ok(bytes)
}

fn make_buffer(bytes: &[u8]) -> windows::core::Result<IBuffer> {
    let writer = DataWriter::new()?;
    writer.WriteBytes(bytes)?;
    writer.DetachBuffer()
}

fn wait_failure(error: WaitError, stage: &'static str) -> Failure {
    match error {
        WaitError::Cancelled => Failure::new("cancelled", true),
        WaitError::TimedOut => Failure::new("setup_timeout", true),
        WaitError::Os => Failure::new(stage, true),
    }
}

/// The ATT MTU the core receives: the session's `MaxPduSize` clamped to the profile ceiling
/// (a value below the floor is passed through so the core refuses it).
fn session_capacity(session: &GattSession) -> LinkCapacity {
    LinkCapacity::AttMtu(usize::from(session.MaxPduSize().unwrap_or(0)).min(MAX_MTU))
}

// ---- scanning -----------------------------------------------------------------------------------

struct ScanWatch {
    watcher: BluetoothLEAdvertisementWatcher,
    received: i64,
    stopped: i64,
    outcome: Arc<CleanupOutcome>,
    /// Records a failed cleanup if the watch is dropped without `stop` having run (an early
    /// return or a panic unwinding past a live watcher).
    guard: TeardownGuard,
}

impl ScanWatch {
    /// Unsubscribes the advertisement callback, requests the stop and then **observes** the
    /// watcher reach a terminal state (`Stopped`/`Aborted`/never started) before the `Stopped`
    /// subscription is released. Runs on the scan's own worker; the wait is unbounded by
    /// design — a watcher that never reaches a terminal state withholds the scan's completion
    /// (and so `ShutdownComplete`), and the runtime's process watchdog fails the exit closed.
    /// A status read that fails is not an observation and keeps waiting.
    ///
    /// All three steps are always attempted ([`scan_teardown`]); any failed or panicked step is
    /// recorded in the scan's sticky [`CleanupOutcome`], so the completion is not clean.
    fn stop(mut self) {
        let watcher = self.watcher.clone();
        let (received, stopped) = (self.received, self.stopped);
        scan_teardown(
            &self.outcome,
            || watcher.RemoveReceived(received).is_ok(),
            || {
                let requested = watcher.Stop().is_ok();
                loop {
                    if let Ok(status) = watcher.Status()
                        && watcher_status_is_terminal(status.0)
                    {
                        break;
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                requested
            },
            || watcher.RemoveStopped(stopped).is_ok(),
        );
        self.guard.done();
    }
}

struct ScanSlot {
    cancelled: Arc<AtomicBool>,
    stop: Sender<()>,
}

fn prepare_scan(
    scan: Token,
    service_uuid: &str,
    gate: &Arc<AdmissionGate>,
    cancelled: &Arc<AtomicBool>,
    stop: &Sender<()>,
    outcome: &Arc<CleanupOutcome>,
) -> Result<ScanWatch, Failure> {
    let deadline = Instant::now() + PRECHECK_BUDGET;
    let adapter = BluetoothAdapter::GetDefaultAsync()
        .ok()
        .and_then(|op| wait(op, cancelled, deadline).ok());
    let probe = match &adapter {
        None => AdapterProbe::Missing,
        Some(adapter) => AdapterProbe::Present {
            le_supported: adapter.IsLowEnergySupported().unwrap_or(false),
            radio: adapter
                .GetRadioAsync()
                .ok()
                .and_then(|op| wait(op, cancelled, deadline).ok())
                .map(|radio| radio.State().map_or(0, |state| state.0)),
        },
    };
    if let Some(failure) = scan_precheck(probe) {
        return Err(failure);
    }

    let scan_failure = || Failure::new("scan_start", false);
    let watcher = BluetoothLEAdvertisementWatcher::new().map_err(|_| scan_failure())?;
    watcher
        .SetScanningMode(BluetoothLEScanningMode::Active)
        .map_err(|_| scan_failure())?;
    let uuid = GUID::try_from(service_uuid).map_err(|_| scan_failure())?;
    watcher
        .AdvertisementFilter()
        .and_then(|filter| filter.Advertisement())
        .and_then(|advertisement| advertisement.ServiceUuids())
        .and_then(|uuids| uuids.Append(uuid))
        .map_err(|_| scan_failure())?;

    let seen: Arc<Mutex<HashSet<u64>>> = Arc::new(Mutex::new(HashSet::new()));
    let received_gate = Arc::clone(gate);
    let received_cancel = Arc::clone(cancelled);
    let received = TypedEventHandler::<
        BluetoothLEAdvertisementWatcher,
        BluetoothLEAdvertisementReceivedEventArgs,
    >::new(move |_, args| {
        if received_cancel.load(Ordering::SeqCst) {
            return Ok(());
        }
        let Some(args) = args.as_ref() else {
            return Ok(());
        };
        let address = args.BluetoothAddress()?;
        {
            let mut seen = lock(&seen);
            if seen.len() >= MAX_SEEN_PER_SCAN || !seen.insert(address) {
                return Ok(());
            }
        }
        let kind = args.BluetoothAddressType().map_or(2, |k| k.0 as u8);
        let name = args
            .Advertisement()
            .and_then(|advertisement| advertisement.LocalName())
            .map(|name| name.to_string())
            .unwrap_or_default();
        // Staged until `ScanStarted` was emitted (ordered admission), then emitted in order.
        received_gate.push(BackendEvent::Advertisement {
            scan,
            device: Discovered {
                platform_hint: format_hint(address, kind),
                label: if name.is_empty() {
                    fallback_label(address)
                } else {
                    name
                },
                advertisement: None,
            },
        });
        Ok(())
    });
    let received_token = watcher.Received(&received).map_err(|_| scan_failure())?;
    // The `Received` subscription is acquired: from here until `ScanWatch` owns it, any error
    // return or panic (also inside `Stopped` registration) removes it, and a panic marks the
    // scan's cleanup failed.
    let mut received_guard = DropClose::new(Arc::clone(outcome), {
        let watcher = watcher.clone();
        move || watcher.RemoveReceived(received_token).is_ok()
    });

    let stop_error = Arc::new(AtomicI32::new(i32::MIN));
    let stopped_gate = Arc::clone(gate);
    let stopped_cancel = Arc::clone(cancelled);
    let stopped_error = Arc::clone(&stop_error);
    let stopped_signal = stop.clone();
    let stopped = TypedEventHandler::<
        BluetoothLEAdvertisementWatcher,
        BluetoothLEAdvertisementWatcherStoppedEventArgs,
    >::new(move |_, args| {
        let code = args
            .as_ref()
            .and_then(|a| a.Error().ok())
            .map_or(4, |e| e.0);
        stopped_error.store(code, Ordering::SeqCst);
        if !stopped_cancel.load(Ordering::SeqCst) {
            stopped_gate.push(BackendEvent::ScanEnded {
                scan,
                failure: bluetooth_error(code),
            });
        }
        // Wakes the scan's worker so it releases the watcher (the core does not call
        // `stop_scan` for a scan the OS ended itself).
        let _ = stopped_signal.send(());
        Ok(())
    });
    let stopped_token = watcher.Stopped(&stopped).map_err(|_| scan_failure())?;
    let watch = ScanWatch {
        watcher,
        received: received_token,
        stopped: stopped_token,
        outcome: Arc::clone(outcome),
        guard: TeardownGuard::new(Arc::clone(outcome)),
    };
    received_guard.disarm(); // `watch` owns both subscriptions (and its guard) now

    if watch.watcher.Start().is_err() {
        watch.stop();
        return Err(scan_failure());
    }
    let started_at = Instant::now();
    loop {
        match watch.watcher.Status() {
            Ok(BluetoothLEAdvertisementWatcherStatus::Started) => return Ok(watch),
            Ok(BluetoothLEAdvertisementWatcherStatus::Aborted) => {
                let code = stop_error.load(Ordering::SeqCst);
                let failure = if code == i32::MIN {
                    None
                } else {
                    bluetooth_error(code)
                };
                watch.stop();
                return Err(failure.unwrap_or_else(scan_failure));
            }
            _ => {}
        }
        if cancelled.load(Ordering::SeqCst) {
            watch.stop();
            return Err(Failure::new("cancelled", true));
        }
        if started_at.elapsed() > SCAN_START_WAIT {
            watch.stop();
            return Err(Failure::new("scan_start", true));
        }
        thread::sleep(Duration::from_millis(20));
    }
}

fn scan_main(
    scan: Token,
    service_uuid: String,
    events: BackendEvents,
    cancelled: Arc<AtomicBool>,
    stop: Sender<()>,
    stop_requests: Receiver<()>,
    done: Arc<Completion>,
) {
    init_apartment();
    // Sticky record of every scan cleanup step (setup-error paths included) and of any
    // terminal the ingress rejected for a process-level reason.
    let cleanup = CleanupOutcome::new();
    let gate = AdmissionGate::with_outcome(events.clone(), Arc::clone(&cleanup));
    let outcome = panic::catch_unwind(AssertUnwindSafe(|| {
        match prepare_scan(scan, &service_uuid, &gate, &cancelled, &stop, &cleanup) {
            Ok(watch) => {
                // `ScanStarted` first, then every callback staged while the watcher started
                // (advertisements, an early `Stopped`) in arrival order.
                if !cancelled.load(Ordering::SeqCst)
                    && gate.open(BackendEvent::ScanStarted { scan }, None)
                {
                    // Parked until the core stops the scan or the OS ends it.
                    let _ = stop_requests.recv();
                }
                let by_core = cancelled.swap(true, Ordering::SeqCst);
                // A non-droppable callback (`Stopped`) lost to gate contention: the scan ends
                // with a safe failure instead of continuing blind.
                if !by_core && gate.contention_failed() {
                    gate.terminate(|_| {
                        Some(BackendEvent::ScanEnded {
                            scan,
                            failure: Some(Failure::new("callback_busy", true)),
                        })
                    });
                }
                gate.abort();
                watch.stop();
            }
            Err(failure) => {
                gate.terminate(|_| {
                    (!cancelled.load(Ordering::SeqCst))
                        .then_some(BackendEvent::ScanFailed { scan, failure })
                });
            }
        }
    }));
    if outcome.is_err() {
        // Exactly one terminal: a failure of the pending start, or a safe end of a started scan.
        gate.terminate(|announced| {
            (!cancelled.load(Ordering::SeqCst)).then(|| scan_panic_terminal(scan, announced))
        });
    }
    // Clean only if every cleanup step succeeded; a watch dropped without its teardown (panic)
    // was recorded failed by its guard.
    // Recorded first, then — only for a fully observed clean teardown — the scan's release
    // is announced; the core keeps the scan counted until exactly this event.
    complete_scan(&done, cleanup.is_clean(), || {
        let _ = events.emit(BackendEvent::ScanReleased { scan });
    });
}

// ---- connections --------------------------------------------------------------------------------

enum Command {
    Write {
        send: Token,
        frames: Vec<Payload>,
    },
    Close,
    /// A callback lost a non-droppable event to gate contention: fail the link closed.
    Fail,
}

struct LinkSlot {
    commands: Sender<Command>,
    cancel: Arc<AtomicBool>,
    gate: Arc<ReleaseGate>,
    queued: Arc<AtomicUsize>,
}

/// The OS objects of one live link. The `cleanup` stack owns the close of every acquired
/// resource (callbacks unsubscribed, service/session/device closed, in the reviewed order);
/// dropping the `Link` — normal teardown, cancel, or a worker panic — runs it. The same stack
/// closes a *partially* set up link on every error path of [`set_up_link`].
struct Link {
    device: BluetoothLEDevice,
    session: GattSession,
    write: GattCharacteristic,
    _cleanup: SetupStack,
}

struct LinkShared {
    conn: Token,
    events: BackendEvents,
    /// Orders `Connected` before every fragment / loss / capacity callback of this link.
    gate: Arc<AdmissionGate>,
    /// Wakes the worker from a callback (`Command::Fail`) without blocking.
    wake: Sender<Command>,
    /// Sticky record of failed/panicked close steps; read by the finish bookkeeping.
    cleanup: Arc<CleanupOutcome>,
    cancel: Arc<AtomicBool>,
    lost: AtomicBool,
    /// The capacity last handed to the core (`Connected` or `CapacityChanged`).
    announced_mtu: AtomicUsize,
}

impl LinkShared {
    fn report_lost(&self) {
        if !self.cancel.load(Ordering::SeqCst) && !self.lost.swap(true, Ordering::SeqCst) {
            let pushed = self.gate.push(BackendEvent::LinkLost { conn: self.conn });
            self.note(pushed);
            if pushed == Push::Failed {
                // The loss itself was dropped: the worker's `Fail` handling re-emits it.
                self.lost.store(false, Ordering::SeqCst);
            }
        }
    }

    /// Wakes the worker (never blocks) when a callback could not be admitted in order.
    fn note(&self, pushed: Push) {
        if pushed == Push::Failed {
            let _ = self.wake.send(Command::Fail);
        }
    }

    /// Reports a capacity observation that differs from the last reported one (staged behind
    /// `Connected` if the link is not announced yet).
    fn report_capacity(&self, session: &GattSession) {
        if self.cancel.load(Ordering::SeqCst) {
            return;
        }
        let LinkCapacity::AttMtu(mtu) = session_capacity(session) else {
            return;
        };
        if self.announced_mtu.swap(mtu, Ordering::SeqCst) != mtu {
            let pushed = self.gate.push(BackendEvent::CapacityChanged {
                conn: self.conn,
                capacity: LinkCapacity::AttMtu(mtu),
            });
            self.note(pushed);
        }
    }
}

fn set_up_link(hint: &str, shared: &Arc<LinkShared>) -> Result<(Link, usize), Failure> {
    let cancel: &AtomicBool = &shared.cancel;
    let deadline = Instant::now() + SETUP_BUDGET;
    let (address, kind) = parse_hint(hint).ok_or(Failure::new("device_lookup", false))?;
    // Every acquisition registers its closer immediately; any `?`/return below drops the stack
    // and closes exactly what was acquired so far, last-acquired first.
    let mut stack = SetupStack::new(Arc::clone(&shared.cleanup));

    let device = wait(
        BluetoothLEDevice::FromBluetoothAddressWithBluetoothAddressTypeAsync(
            address,
            BluetoothAddressType(i32::from(kind)),
        )
        .map_err(|_| Failure::new("device_lookup", false))?,
        cancel,
        deadline,
    )
    .map_err(|e| wait_failure(e, "device_lookup"))?;
    stack.acquire("device", {
        let device = device.clone();
        move || device.Close().is_ok()
    });
    if let Ok(op) = device.RequestAccessAsync()
        && let Ok(status) = wait(op, cancel, deadline)
        && let Some(failure) = access_status(status.0)
    {
        return Err(failure);
    }

    // A maintained GATT session is what keeps the LE link up and lets Windows exchange the MTU.
    let session = BluetoothLEDevice::BluetoothDeviceId(&device)
        .and_then(|id| GattSession::FromDeviceIdAsync(&id))
        .map_err(|_| Failure::new("gatt_connect", false))
        .and_then(|op| wait(op, cancel, deadline).map_err(|e| wait_failure(e, "gatt_connect")))?;
    stack.acquire("session", {
        let session = session.clone();
        move || {
            // Both steps are always attempted.
            let maintain_off = session.SetMaintainConnection(false).is_ok();
            let closed = session.Close().is_ok();
            maintain_off && closed
        }
    });
    session
        .SetMaintainConnection(true)
        .map_err(|_| Failure::new("gatt_connect", false))?;

    // Uncached discovery: a stale cache must not hide a phone whose service was just enabled.
    let services = device
        .GetGattServicesForUuidWithCacheModeAsync(guid(SERVICE_UUID), BluetoothCacheMode::Uncached)
        .map_err(|_| Failure::new("gatt_connect", false))
        .and_then(|op| wait(op, cancel, deadline).map_err(|e| wait_failure(e, "gatt_connect")))?;
    if let Some(failure) = gatt_status(services.Status().map_or(2, |s| s.0), "gatt_connect") {
        return Err(failure);
    }
    let service = services
        .Services()
        .ok()
        .and_then(|list| list.GetAt(0).ok())
        .ok_or(Failure::new("gatt_profile", false))?;
    stack.acquire("service", {
        let service = service.clone();
        move || service.Close().is_ok()
    });

    // MTU first (OTP-06: the phone refuses CCCD before its MTU callback). Windows normally
    // exchanges on connect; wait for a usable value, never subscribe below the floor.
    let mtu_deadline = Instant::now() + MTU_WAIT;
    let mtu = loop {
        let mtu = usize::from(session.MaxPduSize().unwrap_or(0));
        if mtu >= MIN_MTU {
            break mtu.min(MAX_MTU);
        }
        if cancel.load(Ordering::SeqCst) || Instant::now() >= mtu_deadline {
            return Err(Failure::new("mtu_too_small", false));
        }
        thread::sleep(Duration::from_millis(25));
    };
    shared.announced_mtu.store(mtu, Ordering::SeqCst);

    let characteristic = |uuid: &str,
                          needed: GattCharacteristicProperties|
     -> Result<GattCharacteristic, Failure> {
        let op = service
            .GetCharacteristicsForUuidWithCacheModeAsync(guid(uuid), BluetoothCacheMode::Uncached)
            .map_err(|_| Failure::new("gatt_profile", false))?;
        let result = wait(op, cancel, deadline).map_err(|e| wait_failure(e, "gatt_profile"))?;
        if let Some(failure) = gatt_status(result.Status().map_or(2, |s| s.0), "gatt_profile") {
            return Err(failure);
        }
        let found = result
            .Characteristics()
            .ok()
            .and_then(|list| list.GetAt(0).ok())
            .ok_or(Failure::new("gatt_profile", false))?;
        let properties = found
            .CharacteristicProperties()
            .map_err(|_| Failure::new("gatt_profile", false))?;
        if !properties.contains(needed) {
            return Err(Failure::new("gatt_profile", false));
        }
        Ok(found)
    };
    let write = characteristic(
        WRITE_UUID,
        GattCharacteristicProperties::WriteWithoutResponse,
    )?;
    let notify = characteristic(NOTIFY_UUID, GattCharacteristicProperties::Notify)?;

    // Handler first, then CCCD, so the first notification cannot be missed. The callback only
    // copies one bounded fragment and hands it to the link's admission gate: staged behind
    // `Connected` until the link is announced, emitted in order afterwards. A copy failure
    // fails the link (a silently dropped fragment would corrupt the stream).
    let value_shared = Arc::clone(shared);
    let value_handler =
        TypedEventHandler::<GattCharacteristic, GattValueChangedEventArgs>::new(move |_, args| {
            if value_shared.cancel.load(Ordering::SeqCst) {
                return Ok(());
            }
            let copied = args.as_ref().and_then(|args| {
                args.CharacteristicValue()
                    .and_then(|buffer| bounded_buffer_bytes(&buffer, FRAGMENT_FRAME_MAX))
                    .ok()
            });
            match copied {
                Some(bytes) => {
                    let pushed = value_shared.gate.push(BackendEvent::Fragment {
                        conn: value_shared.conn,
                        bytes: Payload::new(bytes),
                    });
                    value_shared.note(pushed);
                }
                None => value_shared.report_lost(),
            }
            Ok(())
        });
    let value_changed = notify
        .ValueChanged(&value_handler)
        .map_err(|_| Failure::new("subscribe", false))?;
    stack.acquire("value_handler", {
        let notify = notify.clone();
        move || notify.RemoveValueChanged(value_changed).is_ok()
    });

    let pdu_shared = Arc::clone(shared);
    let pdu_handler = TypedEventHandler::<GattSession, IInspectable>::new(move |sender, _| {
        if let Some(session) = sender.as_ref() {
            pdu_shared.report_capacity(session);
        }
        Ok(())
    });
    let pdu_changed = session
        .MaxPduSizeChanged(&pdu_handler)
        .map_err(|_| Failure::new("subscribe", false))?;
    stack.acquire("pdu_handler", {
        let session = session.clone();
        move || session.RemoveMaxPduSizeChanged(pdu_changed).is_ok()
    });

    let cccd = notify
        .WriteClientCharacteristicConfigurationDescriptorWithResultAsync(
            GattClientCharacteristicConfigurationDescriptorValue::Notify,
        )
        .map_err(|_| Failure::new("subscribe", false))
        .and_then(|op| wait(op, cancel, deadline).map_err(|e| wait_failure(e, "subscribe")))?;
    let cccd_status = cccd.Status().map_or(2, |s| s.0);
    if cccd_status != GattCommunicationStatus::Success.0 {
        let failure =
            gatt_status(cccd_status, "subscribe").unwrap_or(Failure::new("subscribe", false));
        return Err(Failure::new("subscribe", failure.retryable));
    }

    let status_shared = Arc::clone(shared);
    let status_handler =
        TypedEventHandler::<BluetoothLEDevice, IInspectable>::new(move |sender, _| {
            if let Some(device) = sender.as_ref()
                && device
                    .ConnectionStatus()
                    .unwrap_or(BluetoothConnectionStatus::Disconnected)
                    == BluetoothConnectionStatus::Disconnected
            {
                status_shared.report_lost();
            }
            Ok(())
        });
    let status_changed = device
        .ConnectionStatusChanged(&status_handler)
        .map_err(|_| Failure::new("gatt_connect", false))?;
    stack.acquire("status_handler", {
        let device = device.clone();
        move || device.RemoveConnectionStatusChanged(status_changed).is_ok()
    });
    Ok((
        Link {
            device,
            session,
            write,
            _cleanup: stack,
        },
        mtu,
    ))
}

/// Announces the observed release exactly once when the worker leaves — including by panic —
/// after every OS resource it held was dropped (the guard is declared before the `Link`, so it
/// drops last).
struct FinishGuard {
    conn: Token,
    events: BackendEvents,
    gate: Arc<ReleaseGate>,
    cleanup: Arc<CleanupOutcome>,
}

impl Drop for FinishGuard {
    fn drop(&mut self) {
        // Dropped after the link's `SetupStack` ran every closer (declared first in
        // `link_main`): a failed or panicked close finishes the worker WITHOUT certifying the
        // release, so no `LinkReleased` is ever announced for it.
        if self.gate.finish_with(self.cleanup.is_clean()) {
            self.events
                .emit(BackendEvent::LinkReleased { conn: self.conn });
        }
    }
}

fn link_main(
    hint: String,
    shared: Arc<LinkShared>,
    commands: Receiver<Command>,
    gate: Arc<ReleaseGate>,
    queued: Arc<AtomicUsize>,
) {
    let _finish = FinishGuard {
        conn: shared.conn,
        events: shared.events.clone(),
        gate,
        cleanup: Arc::clone(&shared.cleanup),
    };
    init_apartment();
    let body = panic::catch_unwind(AssertUnwindSafe(|| {
        run_link(&hint, &shared, &commands, &queued)
    }));
    if body.is_err() {
        // Exactly one terminal for a worker that died: a connect failure when the link was
        // never announced (also when the panic hit inside `open`, before or after `Connected`
        // was emitted, `announced` tells which), a safe loss afterwards.
        let conn = shared.conn;
        if shared.cancel.load(Ordering::SeqCst) {
            shared.gate.abort();
        } else {
            shared
                .gate
                .terminate(|announced| link_panic_terminal(conn, announced));
        }
    }
}

fn run_link(
    hint: &str,
    shared: &Arc<LinkShared>,
    commands: &Receiver<Command>,
    queued: &AtomicUsize,
) {
    let events = &shared.events;
    let conn = shared.conn;
    let (link, mtu) = match set_up_link(hint, shared) {
        Ok(ok) => ok,
        Err(failure) => {
            // The partially acquired resources were already closed by the setup stack; staged
            // callbacks of a link that never became live are discarded.
            shared.gate.terminate(|_| {
                (!shared.cancel.load(Ordering::SeqCst))
                    .then_some(BackendEvent::ConnectFailed { conn, failure })
            });
            return;
        }
    };
    if shared.cancel.load(Ordering::SeqCst) {
        shared.gate.abort();
        return; // `link` drops here: callbacks unsubscribed, resources closed.
    }
    // `Connected` first, then every notification / loss / capacity callback staged since the
    // handlers were registered, in arrival order. If more fragments arrived than can be staged,
    // the link fails closed (`LinkLost` right after `Connected`) instead of losing stream bytes.
    shared.gate.open(
        BackendEvent::Connected {
            conn,
            capacity: LinkCapacity::AttMtu(mtu),
            platform_hint: Some(hint.to_string()),
        },
        Some(BackendEvent::LinkLost { conn }),
    );
    // Observations made between measurement and `Connected` are not lost.
    shared.report_capacity(&link.session);
    if link
        .device
        .ConnectionStatus()
        .unwrap_or(BluetoothConnectionStatus::Disconnected)
        == BluetoothConnectionStatus::Disconnected
    {
        shared.report_lost();
    }

    // A failed or cancelled batch may have reached the OS partially: the link is poisoned and
    // refuses later batches (the core tears it down; no write is ever replayed).
    let mut poisoned = false;
    while let Ok(command) = commands.recv() {
        match command {
            Command::Close => break,
            Command::Fail => {
                // A callback lost an event to gate contention: terminate the link closed (a
                // loss event unless one was already delivered); the core then disconnects.
                shared
                    .gate
                    .terminate(|announced| link_contention_terminal(conn, announced));
                break;
            }
            Command::Write { send, frames } => {
                queued.fetch_sub(1, Ordering::SeqCst);
                if shared.cancel.load(Ordering::SeqCst) {
                    break;
                }
                let outcome = if poisoned {
                    Err(Failure::new("write", false))
                } else {
                    write_all(&link, &frames, &shared.cancel)
                };
                match outcome {
                    Ok(()) => {
                        events.emit(BackendEvent::WriteDone { conn, send });
                    }
                    Err(failure) => {
                        poisoned = true;
                        if !shared.cancel.load(Ordering::SeqCst) {
                            events.emit(BackendEvent::WriteFailed {
                                conn,
                                send,
                                failure,
                            });
                        }
                    }
                }
            }
        }
        if shared.cancel.load(Ordering::SeqCst) {
            break;
        }
    }
    // Late callbacks are dropped from here on; `link` then unsubscribes and closes.
    shared.gate.abort();
}

/// Submits every frame in order without response and checks each native result; the batch
/// completes (`Ok`) only if every write returned success. Cancellation is checked before each
/// frame and while a write is outstanding.
fn write_all(link: &Link, frames: &[Payload], cancel: &AtomicBool) -> Result<(), Failure> {
    let deadline = Instant::now() + WRITE_BUDGET;
    for frame in frames {
        if cancel.load(Ordering::SeqCst) {
            return Err(Failure::new("cancelled", true));
        }
        let buffer = make_buffer(frame.as_slice()).map_err(|_| Failure::new("write", false))?;
        let op = link
            .write
            .WriteValueWithResultAndOptionAsync(&buffer, GattWriteOption::WriteWithoutResponse)
            .map_err(|_| Failure::new("write", false))?;
        let result = wait(op, cancel, deadline).map_err(|e| wait_failure(e, "write"))?;
        if let Some(failure) = gatt_status(result.Status().map_or(2, |s| s.0), "write") {
            return Err(failure);
        }
    }
    Ok(())
}

// ---- backend ------------------------------------------------------------------------------------

pub struct WinrtBackend {
    events: BackendEvents,
    scans: ScanLedger<ScanSlot>,
    links: HashMap<Token, LinkSlot>,
    /// Gates of disconnected links whose workers may still be closing OS resources; the
    /// shutdown coordinator waits on them. Pruned once announced, so bounded by the core's
    /// 32-connection bound (closing links count against it until released).
    retiring: Vec<Arc<ReleaseGate>>,
    shutting_down: bool,
}

impl WinrtBackend {
    pub fn new(events: BackendEvents) -> Self {
        init_apartment();
        Self {
            events,
            scans: ScanLedger::new(),
            links: HashMap::new(),
            retiring: Vec::new(),
            shutting_down: false,
        }
    }

    fn prune(&mut self) {
        self.scans.prune();
        self.retiring.retain(|gate| !gate.is_announced());
    }
}

impl Backend for WinrtBackend {
    fn start_scan(&mut self, scan: Token, service_uuid: &str) {
        if self.shutting_down {
            return;
        }
        self.prune();
        let cancelled = Arc::new(AtomicBool::new(false));
        let done = Completion::new();
        let (stop, stop_requests) = mpsc::channel();
        let spawned = thread::Builder::new().name("winrt-scan".into()).spawn({
            let (events, cancelled, done, stop) = (
                self.events.clone(),
                Arc::clone(&cancelled),
                Arc::clone(&done),
                stop.clone(),
            );
            let uuid = service_uuid.to_string();
            move || scan_main(scan, uuid, events, cancelled, stop, stop_requests, done)
        });
        match spawned {
            Ok(_detached) => {
                self.scans.insert(scan, ScanSlot { cancelled, stop }, done);
            }
            Err(_) => {
                // No worker, nothing acquired: the failed start is also its (trivially
                // clean) release.
                self.events.emit(BackendEvent::ScanFailed {
                    scan,
                    failure: Failure::new("worker_spawn", true),
                });
                self.events.emit(BackendEvent::ScanReleased { scan });
            }
        }
    }

    fn stop_scan(&mut self, scan: Token) {
        if let Some(slot) = self.scans.get(&scan) {
            slot.cancelled.store(true, Ordering::SeqCst);
            let _ = slot.stop.send(());
        }
    }

    fn connect(&mut self, conn: Token, target: &Target) {
        if self.shutting_down {
            return;
        }
        self.prune();
        let Target::Hint(hint) = target;
        let (commands, receiver) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(false));
        let gate = ReleaseGate::new();
        let queued = Arc::new(AtomicUsize::new(0));
        let cleanup = CleanupOutcome::new();
        let shared = Arc::new(LinkShared {
            conn,
            events: self.events.clone(),
            gate: AdmissionGate::with_outcome(self.events.clone(), Arc::clone(&cleanup)),
            wake: commands.clone(),
            cleanup,
            cancel: Arc::clone(&cancel),
            lost: AtomicBool::new(false),
            announced_mtu: AtomicUsize::new(0),
        });
        let spawned = thread::Builder::new()
            .name(format!("winrt-link-{conn}"))
            .spawn({
                let (hint, gate, queued) = (hint.clone(), Arc::clone(&gate), Arc::clone(&queued));
                move || link_main(hint, shared, receiver, gate, queued)
            });
        if spawned.is_err() {
            // No worker, no OS resources: the gate is born finished so the core's disconnect
            // after this failure is released immediately and the target fence lifts.
            let _ = gate.finish();
            self.events.emit(BackendEvent::ConnectFailed {
                conn,
                failure: Failure::new("worker_spawn", true),
            });
        }
        self.links.insert(
            conn,
            LinkSlot {
                commands,
                cancel,
                gate,
                queued,
            },
        );
    }

    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        normalize_hint(hint)
    }

    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Payload>) {
        let accepted = self.links.get(&conn).is_some_and(|slot| {
            if slot.cancel.load(Ordering::SeqCst)
                || slot.queued.fetch_add(1, Ordering::SeqCst) >= MAX_QUEUED_BATCHES
            {
                slot.queued.fetch_sub(1, Ordering::SeqCst);
                return false;
            }
            let sent = slot.commands.send(Command::Write { send, frames }).is_ok();
            if !sent {
                slot.queued.fetch_sub(1, Ordering::SeqCst);
            }
            sent
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
        // Cancel first: the worker abandons any pending OS operation, suppresses callbacks,
        // unsubscribes and closes. The release is announced by whoever arrives last — never
        // before the worker finished closing.
        slot.cancel.store(true, Ordering::SeqCst);
        let _ = slot.commands.send(Command::Close);
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
            slot.cancelled.store(true, Ordering::SeqCst);
            let _ = slot.stop.send(());
        }
        let mut gates = std::mem::take(&mut self.retiring);
        for (_, slot) in self.links.drain() {
            slot.cancel.store(true, Ordering::SeqCst);
            let _ = slot.commands.send(Command::Close);
            gates.push(slot.gate);
        }
        // The submission returns now. The process-wide release is announced only after the
        // coordinator observed every scan worker and every link worker finish; a wedged OS
        // call withholds it and the runtime's watchdog fails the exit closed. If the
        // coordinator cannot even be spawned, nothing is announced for the same reason.
        let events = self.events.clone();
        let _ = spawn_release_coordinator(scan_done, gates, move || {
            events.emit(BackendEvent::ShutdownComplete);
        });
    }
}
