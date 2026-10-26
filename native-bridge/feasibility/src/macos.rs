//! CoreBluetooth BLE central (macOS only), selected by `host::run_process`. Speaks raw GATT only:
//! request correlation, fragmentation and reassembly stay in the platform-neutral core.
//!
//! Structure mirrors `winrt.rs`: the synchronous [`Backend`] methods submit commands to workers on
//! a small Tokio multi-thread runtime that drives the async `bluest` (CoreBluetooth) transport;
//! outcomes return as [`BackendEvent`]s. One adapter instance is shared process-wide; one worker
//! per scan and per link; every OS wait is bounded; a setup that got the connection up always
//! releases it again, including on cancel.
//!
//! The negotiated ATT MTU is **inferred** on macOS: CoreBluetooth only reports
//! `maxWriteValueLengthForType(WithoutResponse)` (`max_write = mtu - 3`), so
//! `macos_map::effective_mtu` derives `min(max_write + 3, 517)` and refuses anything implying less
//! than the 185 floor; nothing is hardcoded. A reported 512 yields an effective MTU of 515 and a
//! capped fragment payload of 508, byte-identical to an Android phone that negotiated 517.
//!
//! Two bluest/CoreBluetooth facts shape the link loop: `Adapter::device_connection_events` emits
//! nothing on macOS (iOS only), so link loss is detected through the notify stream's
//! `NotConnected` error and a periodic `Device::is_connected()` poll; and CoreBluetooth exposes
//! just the peripheral identifier (an `NSUUID`, no Bluetooth address), so hints carry that
//! identifier and bluest's opaque `DeviceId` round-trips through its serde uuid-string form.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use bluest::error::ErrorKind;
use bluest::{Adapter, Characteristic, Device, DeviceId, Service, Uuid};
use futures_lite::StreamExt;
use objc2_core_bluetooth::CBManager;
use tokio::runtime::{Builder, Runtime};
use tokio::sync::{mpsc, watch};
use tokio::time::Instant as TokioInstant;

use crate::backend::{Backend, BackendEvent, Discovered, Failure, Target, Token};
use crate::fragment::MAX_MTU;
use crate::macos_map::{
    Authorization, BoundedSet, GateState, ScanGate, effective_mtu, fallback_label, format_hint,
    parse_hint, unavailable_timeout,
};
use crate::runtime::BackendEvents;
// `crate::trace` is both a module and the `#[macro_export]`-ed `trace!` macro: importing the
// module path only (as `use crate::trace::redact_hint;`) does not bring the macro into scope.
use crate::{trace, trace::redact_hint};

const SERVICE_UUID: &str = "8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51";
const WRITE_UUID: &str = "8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51";
const NOTIFY_UUID: &str = "8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51";
const ADAPTER_WAIT: Duration = Duration::from_secs(10);
/// Total budget for one link's connect + discovery + MTU + subscription, like the WinRT backend.
const SETUP_BUDGET: Duration = Duration::from_secs(20);
const WRITE_BUDGET: Duration = Duration::from_secs(10);
const DISCONNECT_BUDGET: Duration = Duration::from_secs(3);
const SHUTDOWN_JOIN_BUDGET: Duration = Duration::from_secs(2);
const LINK_LOSS_POLL: Duration = Duration::from_secs(1);
/// Mirrors the WinRT backend's scan-side first-seen cap (the core applies its own tighter caps).
const MAX_SEEN_PER_SCAN: usize = 2048;
/// The core keeps at most one write batch in flight per connection; slack only.
const COMMAND_CHANNEL: usize = 8;

// ---- async runtime and adapter singletons ---------------------------------------------------------

/// A small Tokio runtime (timers only, no I/O driver) shared by every scan/link worker.
fn runtime() -> &'static Runtime {
    static RUNTIME: OnceLock<Runtime> = OnceLock::new();
    RUNTIME.get_or_init(|| {
        Builder::new_multi_thread()
            .worker_threads(2)
            .enable_time()
            .build()
            .expect("tokio BLE worker runtime")
    })
}

/// Current process-wide Bluetooth authorization (class property; does not create a manager and
/// never prompts).
fn authorization() -> Authorization {
    // SAFETY: `+[CBManager authorization]` is a thread-safe class getter with no arguments.
    Authorization::from_raw(unsafe { CBManager::authorization_class() }.0)
}

/// One CoreBluetooth central manager for the whole process; creating it is synchronous.
fn shared_adapter() -> Result<Adapter, Failure> {
    static ADAPTER: OnceLock<Option<Adapter>> = OnceLock::new();
    let adapter = ADAPTER
        .get_or_init(|| {
            trace!(
                "bluetooth authorization before adapter init: {}",
                authorization().name()
            );
            let adapter = futures_lite::future::block_on(Adapter::default());
            trace!(
                "adapter init: created={} authorization={}",
                adapter.is_some(),
                authorization().name()
            );
            adapter
        })
        .clone();
    adapter.ok_or(Failure::new("no_adapter", false))
}

/// Bounded failure classification for a bluest error. Unknown kinds follow the WinRT convention:
/// an OS-level error at a stage maps to `(stage, retryable = true)`. The error kind name is
/// traced; payloads never are.
fn bluest_failure(error: &bluest::Error, stage: &'static str) -> Failure {
    trace!("bluest error at {stage}: kind={:?}", error.kind());
    match error.kind() {
        ErrorKind::AdapterUnavailable => Failure::new("radio_off", true),
        ErrorKind::NotAuthorized => Failure::new("access_denied", true),
        ErrorKind::NotSupported => Failure::new("le_unsupported", false),
        ErrorKind::AlreadyScanning => Failure::new("resource_in_use", true),
        ErrorKind::NotFound => Failure::new(stage, false),
        ErrorKind::NotConnected | ErrorKind::ConnectionFailed => Failure::new(stage, true),
        ErrorKind::Timeout => Failure::new("setup_timeout", true),
        _ => Failure::new(stage, true),
    }
}

/// Rebuilds the opaque `DeviceId` from a hint identifier. `DeviceId` has no public constructor on
/// macOS; its serde form is the inner uuid string (lowercase, hyphenated), parsed from JSON text.
fn device_id(id: &str) -> Result<DeviceId, Failure> {
    serde_json::from_str::<DeviceId>(&format!("\"{id}\""))
        .map_err(|_| Failure::new("device_lookup", false))
}

/// Bounded, cancel-aware waiter for watch channels (also fires when the sender was dropped).
async fn changed(mut receiver: watch::Receiver<()>) {
    let _ = receiver.changed().await;
}

/// One bounded OS call against a shared deadline. Timeout = the OS call never finished in budget;
/// bluest errors classify through [`bluest_failure`].
async fn bounded<T>(
    deadline: TokioInstant,
    future: impl Future<Output = Result<T, bluest::Error>>,
    stage: &'static str,
) -> Result<T, Failure> {
    match tokio::time::timeout_at(deadline, future).await {
        Ok(Ok(value)) => Ok(value),
        Ok(Err(error)) => Err(bluest_failure(&error, stage)),
        Err(_) => Err(Failure::new("setup_timeout", true)),
    }
}

// ---- scans ----------------------------------------------------------------------------------------

struct ScanSlot {
    gate: Arc<Mutex<ScanGate>>,
    stop: watch::Sender<()>,
    task: tokio::task::JoinHandle<()>,
}

/// Runs one service-UUID-filtered scan. `stop_scan` signals through `stop_rx`; the worker exits
/// without emitting (the core confirms the stop itself and drops the scan stream, which stops the
/// OS scan). A worker whose OS scan ends on its own emits `ScanEnded` exactly once.
async fn scan_main(
    scan: Token,
    service_uuid: String,
    events: BackendEvents,
    gate: Arc<Mutex<ScanGate>>,
    stop_rx: watch::Receiver<()>,
) {
    // Failures only surface while the scan has not been externally stopped (the gate is Done):
    // the core already confirmed a stop itself and would ignore a late event.
    let fail = |failure: Failure| {
        if gate.lock().expect("scan gate").state() != GateState::Done {
            events.emit(BackendEvent::ScanFailed { scan, failure });
        }
    };
    let uuid = match Uuid::parse_str(&service_uuid) {
        Ok(uuid) => uuid,
        Err(_) => return fail(Failure::new("scan_start", false)),
    };
    let adapter = match shared_adapter() {
        Ok(adapter) => adapter,
        Err(failure) => return fail(failure),
    };
    // CoreBluetooth cannot do anything before the central is powered on; a central that stays
    // off/unavailable for the whole budget fails closed, classified by the observed authorization.
    let deadline = TokioInstant::from_std(Instant::now() + ADAPTER_WAIT);
    let powered = tokio::time::timeout_at(deadline, adapter.wait_available()).await;
    if let Err(failure) = match powered {
        Ok(Ok(())) => Ok(()),
        Ok(Err(error)) => Err(bluest_failure(&error, "scan_start")),
        Err(_) => {
            let auth = authorization();
            let (stage, retryable) = unavailable_timeout(auth);
            trace!(
                "adapter wait timed out: authorization={} -> {stage}",
                auth.name()
            );
            Err(Failure::new(stage, retryable))
        }
    } {
        return fail(failure);
    }
    let services = [uuid];
    let mut stream = tokio::select! {
        stream = adapter.scan(&services) => match stream {
            Ok(stream) => stream,
            Err(error) => return fail(bluest_failure(&error, "scan_start")),
        },
        _ = changed(stop_rx.clone()) => return, // externally stopped: no event, core confirmed
    };
    // Confirm start exactly once; `Done` wins over a start that has not been confirmed yet.
    if !gate.lock().expect("scan gate").try_run() {
        return; // dropping the stream stops the OS scan
    }
    events.emit(BackendEvent::ScanStarted { scan });
    let mut seen = BoundedSet::new(MAX_SEEN_PER_SCAN);
    loop {
        let device = tokio::select! {
            next = stream.next() => match next {
                Some(device) => device,
                None => break, // central powered off mid-scan: ended on its own
            },
            _ = changed(stop_rx.clone()) => return, // stop_scan; dropping the stream stops it
        };
        let id = device.device.id().to_string();
        let Some(hint) = format_hint(&id) else {
            continue;
        };
        if !seen.insert(&hint) {
            continue; // duplicate or beyond the scan-side cap
        }
        let label = device
            .adv_data
            .local_name
            .filter(|name| !name.is_empty())
            .unwrap_or_else(|| fallback_label(&hint));
        events.emit(BackendEvent::Advertisement {
            scan,
            device: Discovered {
                platform_hint: hint,
                label,
                advertisement: None,
            },
        });
    }
    if gate.lock().expect("scan gate").finish_naturally() {
        events.emit(BackendEvent::ScanEnded {
            scan,
            failure: None,
        });
    }
}

// ---- links ----------------------------------------------------------------------------------------

enum LinkCommand {
    Write { send: Token, frames: Vec<Vec<u8>> },
}

struct LinkSlot {
    commands: mpsc::Sender<LinkCommand>,
    cancel: watch::Sender<()>,
    task: tokio::task::JoinHandle<()>,
}

/// Every OS object of one live link, held for the link's lifetime and released by teardown.
struct Link {
    device: Device,
    /// Never read: held so the GATT service object outlives the characteristics derived from it.
    _service: Service,
    write: Characteristic,
    notify: Characteristic,
    max_write: usize,
    mtu: usize,
}

/// A setup that already established an OS connection cannot just drop it: failures carry the
/// `device` for an explicit bounded disconnect; `failure: None` means the link was cancelled
/// mid-setup (no event; the core handled the request already).
struct SetupError {
    failure: Option<Failure>,
    device: Option<Device>,
}

/// Connects, discovers the Veles service/write/notify characteristics and measures the ATT MTU
/// (inferred; see the module docs). Subscription is deliberately NOT part of setup: the notify
/// stream must live in the link worker (bluest streams borrow their characteristic), which emits
/// `Connected` only once the subscription is actually in place.
async fn set_up_link(
    conn: Token,
    hint: &str,
    deadline: TokioInstant,
    cancel_rx: &watch::Receiver<()>,
) -> Result<Link, SetupError> {
    let started = Instant::now();
    let step = |name: &str| trace!("conn {conn} {name} at {}ms", started.elapsed().as_millis());
    let cancelled = || cancel_rx.has_changed().unwrap_or(true);
    let soft = |failure: Failure| SetupError {
        failure: Some(failure),
        device: None, // no OS connection was established; nothing to release
    };

    // Connect order follows the OTP-06 peripheral contract: the ATT MTU must be in effect before
    // the subscription (the phone rejects a CCCD write that arrives before its MTU callback).
    let id = match parse_hint(hint)
        .ok_or_else(|| Failure::new("device_lookup", false))
        .and_then(device_id)
    {
        Ok(id) => id,
        Err(failure) => return Err(soft(failure)),
    };
    let adapter = shared_adapter().map_err(soft)?;
    if cancelled() {
        return Err(SetupError {
            failure: None,
            device: None,
        });
    }
    let device = match bounded(deadline, adapter.open_device(&id), "device_lookup").await {
        Ok(device) => device,
        Err(failure) => return Err(soft(failure)),
    };
    step("device");
    // The one OS wait whose cancellation behaviour matters: dropping this future makes bluest's
    // connect guard cancel the OS connection, so the cancelled branch needs no explicit release.
    let connected = tokio::select! {
        connected = bounded(deadline, adapter.connect_device(&device), "gatt_connect") => connected,
        _ = changed(cancel_rx.clone()) => {
            trace!("conn {conn} cancelled during connect");
            return Err(SetupError { failure: None, device: None });
        }
    };
    if let Err(failure) = connected {
        return Err(SetupError {
            failure: Some(failure),
            device: None,
        });
    }
    if cancelled() {
        return Err(SetupError {
            failure: None,
            device: Some(device),
        });
    }
    step("connected");

    // From here the OS connection is live: every failure path releases it explicitly.
    let device = device.clone();
    let live = |failure: Failure| SetupError {
        failure: Some(failure),
        device: Some(device.clone()),
    };
    let mid_cancel = |device: &Device| {
        cancelled().then(|| SetupError {
            failure: None,
            device: Some(device.clone()),
        })
    };

    let expected = Uuid::parse_str(SERVICE_UUID).expect("constant UUID");
    let services = match bounded(
        deadline,
        device.discover_services_with_uuid(expected),
        "gatt_profile",
    )
    .await
    {
        Ok(services) => services,
        Err(failure) => return Err(live(failure)),
    };
    if let Some(error) = mid_cancel(&device) {
        return Err(error);
    }
    step("service");
    let Some(service) = services
        .into_iter()
        .find(|service| service.uuid() == expected)
    else {
        return Err(live(Failure::new("gatt_profile", false)));
    };
    let characteristics =
        match bounded(deadline, service.discover_characteristics(), "gatt_profile").await {
            Ok(characteristics) => characteristics,
            Err(failure) => return Err(live(failure)),
        };
    if let Some(error) = mid_cancel(&device) {
        return Err(error);
    }
    step("characteristics");
    let wanted = |uuid: &str| {
        let uuid = Uuid::parse_str(uuid).expect("constant UUID");
        characteristics
            .iter()
            .find(|characteristic| characteristic.uuid() == uuid)
            .cloned()
    };
    let Some(write) = wanted(WRITE_UUID) else {
        return Err(live(Failure::new("gatt_profile", false)));
    };
    let Some(notify) = wanted(NOTIFY_UUID) else {
        return Err(live(Failure::new("gatt_profile", false)));
    };
    if !bounded(deadline, write.properties(), "gatt_profile")
        .await
        .is_ok_and(|properties| properties.write_without_response)
        || !bounded(deadline, notify.properties(), "gatt_profile")
            .await
            .is_ok_and(|properties| properties.notify)
    {
        return Err(live(Failure::new("gatt_profile", false)));
    }

    // CoreBluetooth hides the negotiated ATT MTU; infer it from the measured maximum
    // write-without-response payload. The inferred value is traced, never hardcoded.
    let max_write = write
        .max_write_len()
        .map_err(|_| live(Failure::new("mtu_unknown", false)))?;
    let mtu = effective_mtu(max_write).map_err(|_| live(Failure::new("mtu_too_small", false)))?;
    trace!(
        "conn {conn} max_write_without_response={max_write} inferred effective att mtu={mtu} \
         (max write + 3, capped {MAX_MTU})"
    );
    Ok(Link {
        device,
        _service: service,
        write,
        notify,
        max_write,
        mtu,
    })
}

/// Releases an OS connection with a hard bound; best effort, teardown must never wedge the host.
async fn release_device(device: Device) {
    if let Ok(adapter) = shared_adapter() {
        let _ = tokio::time::timeout(DISCONNECT_BUDGET, adapter.disconnect_device(&device)).await;
    }
}

/// One link worker: setup, subscription, then a select loop over notifications, write commands,
/// link-loss polls and the cancel signal. Every break releases the OS connection with a bound.
async fn link_main(
    conn: Token,
    hint: String,
    events: BackendEvents,
    mut commands: mpsc::Receiver<LinkCommand>,
    cancel_rx: watch::Receiver<()>,
) {
    let cancelled = || cancel_rx.has_changed().unwrap_or(true);
    let deadline = TokioInstant::from_std(Instant::now() + SETUP_BUDGET);
    let link = match set_up_link(conn, &hint, deadline, &cancel_rx).await {
        Ok(link) => link,
        Err(setup) => {
            if let Some(failure) = setup.failure {
                trace!(
                    "conn {conn} setup failed: stage={} retryable={}",
                    failure.stage, failure.retryable
                );
                if !cancelled() {
                    events.emit(BackendEvent::ConnectFailed { conn, failure });
                }
            } else {
                trace!("conn {conn} setup cancelled");
            }
            if let Some(device) = setup.device {
                release_device(device).await;
            }
            return;
        }
    };
    // Subscribe (CCCD-equivalent) before Connected, bounded by the same total setup budget.
    let mut updates = tokio::select! {
        updates = tokio::time::timeout_at(deadline, link.notify.notify()) => match updates {
            Ok(Ok(updates)) => updates,
            Ok(Err(error)) => {
                let failure = bluest_failure(&error, "subscribe");
                trace!("conn {conn} subscribe failed: stage={}", failure.stage);
                if !cancelled() {
                    events.emit(BackendEvent::ConnectFailed { conn, failure });
                }
                return release_device(link.device).await;
            }
            Err(_) => {
                trace!("conn {conn} subscribe timed out");
                if !cancelled() {
                    events.emit(BackendEvent::ConnectFailed {
                        conn,
                        failure: Failure::new("setup_timeout", true),
                    });
                }
                return release_device(link.device).await;
            }
        },
        _ = changed(cancel_rx.clone()) => {
            trace!("conn {conn} cancelled before subscribe");
            return release_device(link.device).await;
        }
    };
    if cancelled() {
        return release_device(link.device).await;
    }
    trace!("conn {conn} subscribed; hint={}", redact_hint(&hint));
    events.emit(BackendEvent::Connected {
        conn,
        mtu: link.mtu,
        platform_hint: Some(hint.clone()),
    });
    let mut lost = false;
    if !link.device.is_connected().await {
        lost = true; // the OS dropped the link between subscription and the Connected emission
        events.emit(BackendEvent::LinkLost { conn });
    }

    let mut poll = tokio::time::interval(LINK_LOSS_POLL);
    poll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut closed = false;
    while !lost && !closed {
        tokio::select! {
            update = updates.next() => {
                match update {
                    Some(Ok(bytes)) => events.emit(BackendEvent::Fragment { conn, bytes }),
                    Some(Err(error)) => {
                        trace!("conn {conn} notify error: kind={:?}", error.kind());
                        lost = true;
                    }
                    None => {
                        trace!("conn {conn} notify stream ended");
                        lost = true;
                    }
                }
            }
            command = commands.recv() => {
                match command {
                    Some(LinkCommand::Write { send, frames }) => {
                        write_all(&link, conn, send, frames, &events, &cancel_rx).await;
                    }
                    // Closing is the cancel signal; a dropped command sender means the same.
                    None => {
                        trace!("conn {conn} command channel closed");
                        closed = true;
                    }
                }
            }
            _ = poll.tick() => {
                if cancelled() {
                    closed = true;
                } else if !link.device.is_connected().await {
                    trace!("conn {conn} poll observes a disconnected device");
                    lost = true;
                }
            }
        }
        if !lost && !closed && cancelled() {
            closed = true;
        }
    }
    // Dropping `updates` unsubscribes (bluest's guard); the OS connection is released with a bound.
    drop(updates);
    if lost {
        events.emit(BackendEvent::LinkLost { conn });
    }
    release_device(link.device).await;
    trace!("conn {conn} released");
}

/// Writes a whole fragment batch in order, one completion event for the batch, cancel-aware.
async fn write_all(
    link: &Link,
    conn: Token,
    send: Token,
    frames: Vec<Vec<u8>>,
    events: &BackendEvents,
    cancel_rx: &watch::Receiver<()>,
) {
    let deadline = TokioInstant::from_std(Instant::now() + WRITE_BUDGET);
    for frame in &frames {
        // Defense in depth: the core already caps the chunk below the measured OS write length.
        if frame.len() > link.max_write {
            trace!(
                "conn {conn} send {send} rejects a frame of {} bytes (max {})",
                frame.len(),
                link.max_write
            );
            events.emit(BackendEvent::WriteFailed {
                conn,
                send,
                failure: Failure::new("write", false),
            });
            return;
        }
        let written = tokio::select! {
            written = tokio::time::timeout_at(deadline, link.write.write_without_response(frame)) => written,
            _ = changed(cancel_rx.clone()) => return, // cancelled: the core already dropped the link
        };
        match written {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                trace!("conn {conn} send {send} failed: kind={:?}", error.kind());
                events.emit(BackendEvent::WriteFailed {
                    conn,
                    send,
                    failure: bluest_failure(&error, "write"),
                });
                return;
            }
            Err(_) => {
                events.emit(BackendEvent::WriteFailed {
                    conn,
                    send,
                    failure: Failure::new("write_timeout", true),
                });
                return;
            }
        }
    }
    events.emit(BackendEvent::WriteDone { conn, send });
}

// ---- backend --------------------------------------------------------------------------------------

pub struct MacosBackend {
    events: BackendEvents,
    scans: HashMap<Token, ScanSlot>,
    links: HashMap<Token, LinkSlot>,
}

impl MacosBackend {
    pub fn new(events: BackendEvents) -> Self {
        Self {
            events,
            scans: HashMap::new(),
            links: HashMap::new(),
        }
    }
}

impl Backend for MacosBackend {
    fn start_scan(&mut self, scan: Token, service_uuid: &str) {
        // CoreBluetooth's central scans serially: a second concurrent scan fails with
        // `resource_in_use` (the core caps concurrent scans at 2, the UI uses one).
        let (stop, stop_rx) = watch::channel(());
        let gate = Arc::new(Mutex::new(ScanGate::new()));
        let task = runtime().spawn(scan_main(
            scan,
            service_uuid.to_string(),
            self.events.clone(),
            gate.clone(),
            stop_rx,
        ));
        self.scans.insert(scan, ScanSlot { gate, stop, task });
    }

    fn stop_scan(&mut self, scan: Token) {
        if let Some(slot) = self.scans.remove(&scan) {
            let _ = slot.gate.lock().expect("scan gate").stop();
            let _ = slot.stop.send(());
        }
    }

    fn connect(&mut self, conn: Token, target: &Target) {
        let Target::Hint(hint) = target;
        if parse_hint(hint).is_none() {
            self.events.emit(BackendEvent::ConnectFailed {
                conn,
                failure: Failure::new("device_lookup", false),
            });
            return;
        }
        let (commands, commands_rx) = mpsc::channel(COMMAND_CHANNEL);
        let (cancel, cancel_rx) = watch::channel(());
        let task = runtime().spawn(link_main(
            conn,
            hint.clone(),
            self.events.clone(),
            commands_rx,
            cancel_rx,
        ));
        self.links.insert(
            conn,
            LinkSlot {
                commands,
                cancel,
                task,
            },
        );
    }

    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Vec<u8>>) {
        let delivered = self.links.get(&conn).is_some_and(|slot| {
            slot.commands
                .try_send(LinkCommand::Write { send, frames })
                .is_ok()
        });
        if !delivered {
            self.events.emit(BackendEvent::WriteFailed {
                conn,
                send,
                failure: Failure::new("write", false),
            });
        }
    }

    fn disconnect(&mut self, conn: Token) {
        if let Some(slot) = self.links.remove(&conn) {
            let _ = slot.cancel.send(());
        }
    }

    fn shutdown(&mut self) {
        for scan in self.scans.keys().copied().collect::<Vec<Token>>() {
            self.stop_scan(scan);
        }
        for slot in self.links.values() {
            let _ = slot.cancel.send(());
        }
        // Bounded wait so the workers can release their OS links before the exit; then force.
        let deadline = Instant::now() + SHUTDOWN_JOIN_BUDGET;
        let mut tasks: Vec<tokio::task::JoinHandle<()>> =
            self.links.drain().map(|(_, slot)| slot.task).collect();
        tasks.extend(self.scans.drain().map(|(_, slot)| slot.task));
        for task in tasks {
            while !task.is_finished() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(10));
            }
            task.abort();
        }
    }
}

impl Drop for MacosBackend {
    fn drop(&mut self) {
        self.shutdown();
    }
}
