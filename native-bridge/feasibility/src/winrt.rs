//! WinRT BLE central: advertisement watcher, per-link GATT worker threads and cancel-aware
//! waits. Speaks raw GATT only (the core owns fragmentation); synthetic transport, no Veles
//! semantics.
//!
//! Connect order follows the Android peripheral contract and the OTP-06 follow-ups: ATT MTU
//! (>= 185) must be in effect **before** the CCCD write, because the phone rejects a CCCD write
//! that arrives before its MTU callback. Every OS object of a link is closed on disconnect.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use windows::Devices::Bluetooth::Advertisement::{
    BluetoothLEAdvertisementReceivedEventArgs, BluetoothLEAdvertisementWatcher,
    BluetoothLEAdvertisementWatcherStatus, BluetoothLEAdvertisementWatcherStoppedEventArgs,
    BluetoothLEScanningMode,
};
use windows::Devices::Bluetooth::GenericAttributeProfile::{
    GattCharacteristic, GattCharacteristicProperties,
    GattClientCharacteristicConfigurationDescriptorValue, GattCommunicationStatus,
    GattDeviceService, GattSession, GattValueChangedEventArgs, GattWriteOption,
};
use windows::Devices::Bluetooth::{
    BluetoothAdapter, BluetoothAddressType, BluetoothCacheMode, BluetoothConnectionStatus,
    BluetoothLEDevice,
};
use windows::Devices::Radios::RadioState;
use windows::Foundation::TypedEventHandler;
use windows::Storage::Streams::{DataReader, DataWriter, IBuffer};
use windows::Win32::System::WinRT::{RO_INIT_MULTITHREADED, RoInitialize};
use windows::core::{GUID, Interface, RuntimeType};
use windows_future::{AsyncStatus, IAsyncOperation};

use crate::backend::{Backend, BackendEvent, Discovered, Failure, Target, Token};
use crate::fragment::{LinkCapacity, MAX_MTU, MIN_MTU};
use crate::runtime::BackendEvents;
use crate::trace;
use crate::winrt_map::{
    access_status, bluetooth_error, fallback_label, format_hint, gatt_status, parse_hint,
    radio_state,
};

const WRITE_UUID: &str = "8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51";
const NOTIFY_UUID: &str = "8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51";
const SETUP_BUDGET: Duration = Duration::from_secs(20);
const MTU_WAIT: Duration = Duration::from_secs(3);
const WRITE_BUDGET: Duration = Duration::from_secs(10);
const SCAN_START_WAIT: Duration = Duration::from_millis(1500);
const SHUTDOWN_JOIN_BUDGET: Duration = Duration::from_secs(2);
const MAX_SEEN_PER_SCAN: usize = 2048;

fn init_apartment() {
    // MTA; the call is harmless if the thread is already initialised.
    let _ = unsafe { RoInitialize(RO_INIT_MULTITHREADED) };
}

fn guid(text: &str) -> GUID {
    GUID::try_from(text).expect("constant GUID")
}

#[derive(Debug)]
enum WaitError {
    Cancelled,
    TimedOut,
    Os(windows::core::Error),
}

/// Polls an async operation so a stuck OS call can always be abandoned on cancel or deadline.
fn wait<T: RuntimeType + 'static>(
    op: IAsyncOperation<T>,
    cancel: &AtomicBool,
    deadline: Instant,
) -> Result<T, WaitError> {
    loop {
        match op.Status().map_err(WaitError::Os)? {
            AsyncStatus::Completed => return op.GetResults().map_err(WaitError::Os),
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
            _ => return op.GetResults().map_err(WaitError::Os),
        }
    }
}

fn buffer_bytes(buffer: &IBuffer) -> windows::core::Result<Vec<u8>> {
    let reader = DataReader::FromBuffer(buffer)?;
    let mut bytes = vec![0u8; reader.UnconsumedBufferLength()? as usize];
    reader.ReadBytes(&mut bytes)?;
    Ok(bytes)
}

fn make_buffer(bytes: &[u8]) -> windows::core::Result<IBuffer> {
    let writer = DataWriter::new()?;
    writer.WriteBytes(bytes)?;
    writer.DetachBuffer()
}

/// Diagnostic only (trace): the optional ATT protocol-error byte attached to a GATT discovery result.
fn protocol_error_text(
    error: windows::core::Result<windows::Foundation::IReference<u8>>,
) -> String {
    match error.and_then(|reference| reference.Value()) {
        Ok(value) => format!("0x{value:02x}"),
        // A null reference and an accessor failure are not told apart here, so the trace says so.
        Err(_) => "none_or_unavailable".to_string(),
    }
}

fn wait_failure(error: WaitError, stage: &'static str) -> Failure {
    match error {
        WaitError::Cancelled => Failure::new("cancelled", true),
        WaitError::TimedOut => Failure::new("setup_timeout", true),
        WaitError::Os(os) => {
            trace!("os error at {stage}: 0x{:08x}", os.code().0);
            Failure::new(stage, true)
        }
    }
}

// ---- scanning ---------------------------------------------------------------------------------

struct ScanSlot {
    cancelled: Arc<AtomicBool>,
    watcher: Arc<Mutex<Option<BluetoothLEAdvertisementWatcher>>>,
}

fn prepare_and_start_scan(
    scan: Token,
    service_uuid: &str,
    events: &BackendEvents,
    cancelled: &Arc<AtomicBool>,
    slot: &Arc<Mutex<Option<BluetoothLEAdvertisementWatcher>>>,
) -> Result<(), Failure> {
    let deadline = Instant::now() + Duration::from_secs(10);
    let never = AtomicBool::new(false);
    let adapter = BluetoothAdapter::GetDefaultAsync()
        .map_err(|_| Failure::new("no_adapter", false))
        .and_then(|op| wait(op, &never, deadline).map_err(|_| Failure::new("no_adapter", false)))?;
    if !adapter.IsLowEnergySupported().unwrap_or(false) {
        return Err(Failure::new("le_unsupported", false));
    }
    let radio = adapter
        .GetRadioAsync()
        .map_err(|_| Failure::new("radio_unknown", true))
        .and_then(|op| {
            wait(op, &never, deadline).map_err(|_| Failure::new("radio_unknown", true))
        })?;
    let state = radio.State().map_or(0, |s| s.0);
    if let Some(failure) = radio_state(state) {
        return Err(failure);
    }
    debug_assert_eq!(RadioState::On.0, 1);

    let watcher =
        BluetoothLEAdvertisementWatcher::new().map_err(|_| Failure::new("scan_start", false))?;
    watcher
        .SetScanningMode(BluetoothLEScanningMode::Active)
        .map_err(|_| Failure::new("scan_start", false))?;
    let diagnostic = std::env::var_os("VELES_OTP23_DIAG_SCAN_ANY").is_some();
    if !diagnostic {
        let uuid = GUID::try_from(service_uuid).map_err(|_| Failure::new("scan_start", false))?;
        let filter = watcher
            .AdvertisementFilter()
            .map_err(|_| Failure::new("scan_start", false))?;
        filter
            .Advertisement()
            .and_then(|advertisement| advertisement.ServiceUuids())
            .and_then(|uuids| uuids.Append(uuid))
            .map_err(|_| Failure::new("scan_start", false))?;
    }

    let seen: Arc<Mutex<HashSet<u64>>> = Arc::new(Mutex::new(HashSet::new()));
    let received_events = events.clone();
    let received_cancel = cancelled.clone();
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
            let mut seen = seen.lock().expect("seen lock");
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
        received_events.emit(BackendEvent::Advertisement {
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
    watcher
        .Received(&received)
        .map_err(|_| Failure::new("scan_start", false))?;

    let stop_error = Arc::new(Mutex::new(None::<i32>));
    let stopped_events = events.clone();
    let stopped_cancel = cancelled.clone();
    let stopped_error = stop_error.clone();
    let stopped = TypedEventHandler::<
        BluetoothLEAdvertisementWatcher,
        BluetoothLEAdvertisementWatcherStoppedEventArgs,
    >::new(move |_, args| {
        let code = args
            .as_ref()
            .and_then(|a| a.Error().ok())
            .map_or(4, |e| e.0);
        *stopped_error.lock().expect("stop error lock") = Some(code);
        if !stopped_cancel.load(Ordering::SeqCst) {
            trace!("watcher stopped by the OS: BluetoothError={code}");
            stopped_events.emit(BackendEvent::ScanEnded {
                scan,
                failure: bluetooth_error(code),
            });
        }
        Ok(())
    });
    watcher
        .Stopped(&stopped)
        .map_err(|_| Failure::new("scan_start", false))?;

    watcher
        .Start()
        .map_err(|_| Failure::new("scan_start", false))?;
    let started_at = Instant::now();
    loop {
        match watcher.Status() {
            Ok(BluetoothLEAdvertisementWatcherStatus::Started) => break,
            Ok(BluetoothLEAdvertisementWatcherStatus::Aborted) => {
                let code = stop_error.lock().expect("stop error lock").unwrap_or(4);
                return Err(bluetooth_error(code).unwrap_or(Failure::new("scan_start", false)));
            }
            _ => {}
        }
        if started_at.elapsed() > SCAN_START_WAIT {
            let _ = watcher.Stop();
            return Err(Failure::new("scan_start", true));
        }
        thread::sleep(Duration::from_millis(20));
    }
    *slot.lock().expect("watcher slot lock") = Some(watcher);
    Ok(())
}

// ---- connections ------------------------------------------------------------------------------

enum Command {
    Write { send: Token, frames: Vec<Vec<u8>> },
    Close,
}

struct LinkSlot {
    commands: Sender<Command>,
    cancel: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

fn disconnect_link(links: &mut HashMap<Token, LinkSlot>, conn: Token) {
    if let Some(mut slot) = links.remove(&conn) {
        slot.cancel.store(true, Ordering::SeqCst);
        let _ = slot.commands.send(Command::Close);
        // The worker observes cancellation within its bounded OS wait and closes its Link.
        // Join already-finished workers; detach active ones rather than blocking the core.
        if let Some(worker) = slot.thread.take()
            && worker.is_finished()
        {
            let _ = worker.join();
        }
    }
}

struct Link {
    device: BluetoothLEDevice,
    service: GattDeviceService,
    session: GattSession,
    write: GattCharacteristic,
    notify: GattCharacteristic,
    value_changed: i64,
    status_changed: i64,
}

impl Link {
    fn close(self) {
        let _ = self.notify.RemoveValueChanged(self.value_changed);
        let _ = self
            .device
            .RemoveConnectionStatusChanged(self.status_changed);
        let _ = self.service.Close();
        let _ = self.session.SetMaintainConnection(false);
        let _ = self.session.Close();
        let _ = self.device.Close();
    }
}

fn set_up_link(
    conn: Token,
    hint: &str,
    events: &BackendEvents,
    cancel: &Arc<AtomicBool>,
    lost: &Arc<AtomicBool>,
) -> Result<(Link, usize), Failure> {
    let started = Instant::now();
    let deadline = started + SETUP_BUDGET;
    let step = |name: &str| trace!("conn {conn} {name} at {}ms", started.elapsed().as_millis());
    let (address, kind) = parse_hint(hint).ok_or(Failure::new("device_lookup", false))?;

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
    step("device");
    if let Ok(op) = device.RequestAccessAsync()
        && let Ok(status) = wait(op, cancel, deadline)
        && let Some(failure) = access_status(status.0)
    {
        let _ = device.Close();
        return Err(failure);
    }

    let fail = |device: &BluetoothLEDevice, failure: Failure| {
        let _ = device.Close();
        failure
    };

    // A maintained GATT session is what keeps the LE link up and lets Windows exchange the MTU.
    let session = match BluetoothLEDevice::BluetoothDeviceId(&device)
        .and_then(|id| GattSession::FromDeviceIdAsync(&id))
    {
        Ok(op) => wait(op, cancel, deadline)
            .map_err(|e| fail(&device, wait_failure(e, "gatt_connect")))?,
        Err(_) => return Err(fail(&device, Failure::new("gatt_connect", false))),
    };
    let abandon = |device: &BluetoothLEDevice, session: &GattSession, failure: Failure| {
        let _ = session.SetMaintainConnection(false);
        let _ = session.Close();
        let _ = device.Close();
        failure
    };
    session
        .SetMaintainConnection(true)
        .map_err(|_| abandon(&device, &session, Failure::new("gatt_connect", false)))?;
    step("session");

    // Uncached discovery: a stale cache must not hide a phone whose service was just enabled.
    let services = device
        .GetGattServicesForUuidWithCacheModeAsync(
            guid("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"),
            BluetoothCacheMode::Uncached,
        )
        .map_err(|_| abandon(&device, &session, Failure::new("gatt_connect", false)))
        .and_then(|op| {
            wait(op, cancel, deadline)
                .map_err(|e| abandon(&device, &session, wait_failure(e, "gatt_connect")))
        })?;
    let status = services.Status().map_or(2, |s| s.0);
    trace!(
        "conn {conn} services GattCommunicationStatus={status} protocol_error={}",
        protocol_error_text(services.ProtocolError())
    );
    if let Some(failure) = gatt_status(status, "gatt_connect") {
        return Err(abandon(&device, &session, failure));
    }
    let service = services
        .Services()
        .ok()
        .and_then(|list| list.GetAt(0).ok())
        .ok_or_else(|| abandon(&device, &session, Failure::new("gatt_profile", false)))?;
    step("service");

    // MTU first (OTP-06: the phone refuses CCCD before its MTU callback). Windows normally
    // exchanges on connect; wait for a usable value, never subscribe below the floor.
    let mtu_deadline = Instant::now() + MTU_WAIT;
    let mtu = loop {
        let mtu = usize::from(session.MaxPduSize().unwrap_or(0));
        if mtu >= MIN_MTU {
            break mtu.min(MAX_MTU);
        }
        if cancel.load(Ordering::SeqCst) || Instant::now() >= mtu_deadline {
            trace!("conn {conn} mtu stuck at {mtu}");
            let _ = service.Close();
            return Err(abandon(
                &device,
                &session,
                Failure::new("mtu_too_small", false),
            ));
        }
        thread::sleep(Duration::from_millis(25));
    };
    step(&format!("mtu={mtu}"));

    let characteristic = |uuid: &str,
                          needed: GattCharacteristicProperties,
                          name: &'static str|
     -> Result<GattCharacteristic, Failure> {
        let op = service
            .GetCharacteristicsForUuidWithCacheModeAsync(guid(uuid), BluetoothCacheMode::Uncached)
            .map_err(|_| Failure::new("gatt_profile", false))?;
        let result = wait(op, cancel, deadline).map_err(|e| wait_failure(e, "gatt_profile"))?;
        trace!(
            "conn {conn} {name} characteristics GattCommunicationStatus={} protocol_error={}",
            result.Status().map_or(-1, |s| s.0),
            protocol_error_text(result.ProtocolError())
        );
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
        trace!("conn {conn} {name} properties=0x{:x}", properties.0);
        if !properties.contains(needed) {
            return Err(Failure::new("gatt_profile", false));
        }
        Ok(found)
    };
    let write = characteristic(
        WRITE_UUID,
        GattCharacteristicProperties::WriteWithoutResponse,
        "write",
    )
    .map_err(|f| {
        let _ = service.Close();
        abandon(&device, &session, f)
    })?;
    let notify = characteristic(NOTIFY_UUID, GattCharacteristicProperties::Notify, "notify")
        .map_err(|f| {
            let _ = service.Close();
            abandon(&device, &session, f)
        })?;
    step("characteristics");

    // Handler first, then CCCD, so the first notification cannot be missed.
    let value_events = events.clone();
    let value_handler =
        TypedEventHandler::<GattCharacteristic, GattValueChangedEventArgs>::new(move |_, args| {
            if let Some(args) = args.as_ref()
                && let Ok(bytes) = args.CharacteristicValue().and_then(|b| buffer_bytes(&b))
            {
                value_events.emit(BackendEvent::Fragment { conn, bytes });
            }
            Ok(())
        });
    let value_changed = notify
        .ValueChanged(&value_handler)
        .map_err(|_| abandon(&device, &session, Failure::new("subscribe", false)))?;
    let cccd = notify
        .WriteClientCharacteristicConfigurationDescriptorWithResultAsync(
            GattClientCharacteristicConfigurationDescriptorValue::Notify,
        )
        .map_err(|_| Failure::new("subscribe", false))
        .and_then(|op| wait(op, cancel, deadline).map_err(|e| wait_failure(e, "subscribe")));
    let cccd = match cccd {
        Ok(result) => result,
        Err(failure) => {
            let _ = notify.RemoveValueChanged(value_changed);
            return Err(abandon(&device, &session, failure));
        }
    };
    let cccd_status = cccd.Status().map_or(2, |s| s.0);
    if cccd_status != GattCommunicationStatus::Success.0 {
        let _ = notify.RemoveValueChanged(value_changed);
        let failure =
            gatt_status(cccd_status, "subscribe").unwrap_or(Failure::new("subscribe", false));
        trace!("conn {conn} CCCD refused status={cccd_status}");
        return Err(abandon(
            &device,
            &session,
            Failure::new("subscribe", failure.retryable),
        ));
    }
    step("subscribed");

    let lost_events = events.clone();
    let lost_flag = lost.clone();
    let status_handler = TypedEventHandler::<BluetoothLEDevice, windows::core::IInspectable>::new(
        move |sender, _| {
            if let Some(device) = sender.as_ref()
                && device
                    .ConnectionStatus()
                    .unwrap_or(BluetoothConnectionStatus::Disconnected)
                    == BluetoothConnectionStatus::Disconnected
                && !lost_flag.swap(true, Ordering::SeqCst)
            {
                lost_events.emit(BackendEvent::LinkLost { conn });
            }
            Ok(())
        },
    );
    let status_changed = device
        .ConnectionStatusChanged(&status_handler)
        .map_err(|_| abandon(&device, &session, Failure::new("gatt_connect", false)))?;
    Ok((
        Link {
            device,
            service,
            session,
            write,
            notify,
            value_changed,
            status_changed,
        },
        mtu,
    ))
}

fn connection_main(
    conn: Token,
    hint: String,
    events: BackendEvents,
    commands: Receiver<Command>,
    cancel: Arc<AtomicBool>,
) {
    init_apartment();
    let lost = Arc::new(AtomicBool::new(false));
    let (link, mtu) = match set_up_link(conn, &hint, &events, &cancel, &lost) {
        Ok(ok) => ok,
        Err(failure) => {
            trace!(
                "conn {conn} setup failed: stage={} retryable={}",
                failure.stage, failure.retryable
            );
            if !cancel.load(Ordering::SeqCst) {
                events.emit(BackendEvent::ConnectFailed { conn, failure });
            }
            return;
        }
    };
    if cancel.load(Ordering::SeqCst) {
        link.close();
        return;
    }
    events.emit(BackendEvent::Connected {
        conn,
        capacity: LinkCapacity::AttMtu(mtu),
        platform_hint: Some(hint.clone()),
    });
    if link
        .device
        .ConnectionStatus()
        .unwrap_or(BluetoothConnectionStatus::Disconnected)
        == BluetoothConnectionStatus::Disconnected
        && !lost.swap(true, Ordering::SeqCst)
    {
        events.emit(BackendEvent::LinkLost { conn });
    }

    while let Ok(command) = commands.recv() {
        match command {
            Command::Close => break,
            Command::Write { send, frames } => {
                let outcome = write_all(&link, &frames, &cancel);
                match outcome {
                    Ok(()) => events.emit(BackendEvent::WriteDone { conn, send }),
                    Err(failure) => {
                        trace!("conn {conn} write failed: stage={}", failure.stage);
                        events.emit(BackendEvent::WriteFailed {
                            conn,
                            send,
                            failure,
                        });
                    }
                }
            }
        }
        if cancel.load(Ordering::SeqCst) {
            break;
        }
    }
    link.close();
    trace!("conn {conn} released");
}

fn write_all(link: &Link, frames: &[Vec<u8>], cancel: &AtomicBool) -> Result<(), Failure> {
    let deadline = Instant::now() + WRITE_BUDGET;
    for frame in frames {
        let buffer = make_buffer(frame).map_err(|_| Failure::new("write", false))?;
        let op = link
            .write
            .WriteValueWithResultAndOptionAsync(&buffer, GattWriteOption::WriteWithoutResponse)
            .map_err(|_| Failure::new("write", false))?;
        let result = wait(op, cancel, deadline).map_err(|e| wait_failure(e, "write"))?;
        let status = result.Status().map_or(2, |s| s.0);
        if let Some(failure) = gatt_status(status, "write") {
            return Err(failure);
        }
    }
    Ok(())
}

// ---- backend ----------------------------------------------------------------------------------------

pub struct WinrtBackend {
    events: BackendEvents,
    scans: HashMap<Token, ScanSlot>,
    links: HashMap<Token, LinkSlot>,
}

impl WinrtBackend {
    pub fn new(events: BackendEvents) -> Self {
        init_apartment();
        Self {
            events,
            scans: HashMap::new(),
            links: HashMap::new(),
        }
    }
}

impl Backend for WinrtBackend {
    fn start_scan(&mut self, scan: Token, service_uuid: &str) {
        let cancelled = Arc::new(AtomicBool::new(false));
        let watcher = Arc::new(Mutex::new(None));
        self.scans.insert(
            scan,
            ScanSlot {
                cancelled: cancelled.clone(),
                watcher: watcher.clone(),
            },
        );
        let events = self.events.clone();
        let uuid = service_uuid.to_string();
        thread::Builder::new()
            .name("winrt-scan-start".into())
            .spawn(move || {
                init_apartment();
                match prepare_and_start_scan(scan, &uuid, &events, &cancelled, &watcher) {
                    Ok(()) if cancelled.load(Ordering::SeqCst) => {
                        if let Some(watcher) = watcher.lock().expect("watcher slot lock").take() {
                            let _ = watcher.Stop();
                        }
                    }
                    Ok(()) => events.emit(BackendEvent::ScanStarted { scan }),
                    Err(failure) => {
                        trace!("scan {scan} failed: stage={}", failure.stage);
                        if !cancelled.load(Ordering::SeqCst) {
                            events.emit(BackendEvent::ScanFailed { scan, failure });
                        }
                    }
                }
            })
            .expect("spawn scan starter");
    }

    fn stop_scan(&mut self, scan: Token) {
        if let Some(slot) = self.scans.remove(&scan) {
            slot.cancelled.store(true, Ordering::SeqCst);
            if let Some(watcher) = slot.watcher.lock().expect("watcher slot lock").take() {
                let _ = watcher.Stop();
            }
        }
    }

    fn connect(&mut self, conn: Token, target: &Target) {
        let Target::Hint(hint) = target;
        let (commands, receiver) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(false));
        let events = self.events.clone();
        let (hint, worker_cancel) = (hint.clone(), cancel.clone());
        let thread = thread::Builder::new()
            .name(format!("winrt-link-{conn}"))
            .spawn(move || connection_main(conn, hint, events, receiver, worker_cancel))
            .expect("spawn link worker");
        self.links.insert(
            conn,
            LinkSlot {
                commands,
                cancel,
                thread: Some(thread),
            },
        );
    }

    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Vec<u8>>) {
        let delivered = self
            .links
            .get(&conn)
            .is_some_and(|slot| slot.commands.send(Command::Write { send, frames }).is_ok());
        if !delivered {
            self.events.emit(BackendEvent::WriteFailed {
                conn,
                send,
                failure: Failure::new("write", false),
            });
        }
    }

    fn disconnect(&mut self, conn: Token) {
        disconnect_link(&mut self.links, conn);
    }

    fn shutdown(&mut self) {
        let scans: Vec<Token> = self.scans.keys().copied().collect();
        for scan in scans {
            self.stop_scan(scan);
        }
        for slot in self.links.values() {
            slot.cancel.store(true, Ordering::SeqCst);
            let _ = slot.commands.send(Command::Close);
        }
        // Bounded wait so a wedged OS call cannot keep the process alive; exit releases the rest.
        let deadline = Instant::now() + SHUTDOWN_JOIN_BUDGET;
        for (_, mut slot) in self.links.drain() {
            if let Some(thread) = slot.thread.take() {
                while !thread.is_finished() && Instant::now() < deadline {
                    thread::sleep(Duration::from_millis(10));
                }
                if thread.is_finished() {
                    let _ = thread.join();
                } else {
                    trace!("link worker did not finish within the shutdown budget");
                }
            }
        }
    }
}

#[allow(dead_code)]
fn _assert_interface_bound<T: Interface>() {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disconnected_link_slots_are_released_during_churn() {
        let mut links = HashMap::new();
        for conn in 1..=128 {
            let (commands, rx) = mpsc::channel();
            let cancel = Arc::new(AtomicBool::new(false));
            links.insert(
                conn,
                LinkSlot {
                    commands,
                    cancel: cancel.clone(),
                    thread: None,
                },
            );
            disconnect_link(&mut links, conn);
            assert!(links.is_empty());
            assert!(cancel.load(Ordering::SeqCst));
            assert!(matches!(rx.try_recv(), Ok(Command::Close)));
        }
    }
}
