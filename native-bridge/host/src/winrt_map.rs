//! WinRT hint format, target identity and failure classification (pure mappings, no OS
//! calls). Kept free of `windows` types so the mappings are unit-tested on any host.
//!
//! Selectively adapted from the reviewed feasibility `winrt_map.rs`. Production changes:
//! `parse_hint` is strict (the std radix parser accepts a leading `+`, which the reviewed
//! format never contains), the target identity ([`normalize_hint`]) is the 48-bit address
//! alone (the address-type digit and hex case are observation variants, not identity), and
//! the scan-precheck classification that lived inline in the feasibility adapter is a pure,
//! tested function ([`scan_precheck`]).

use crate::backend::{Failure, NormalizedTarget};

/// `winrt:<12 hex digit address>:<0 public | 1 random | 2 unspecified>`.
pub fn format_hint(address: u64, kind: u8) -> String {
    format!("winrt:{:012x}:{}", address & 0xffff_ffff_ffff, kind)
}

pub fn parse_hint(hint: &str) -> Option<(u64, u8)> {
    let mut parts = hint.split(':');
    if parts.next()? != "winrt" {
        return None;
    }
    let address = parts.next()?;
    let kind = parts.next()?;
    if parts.next().is_some() || address.len() != 12 || kind.len() != 1 {
        return None;
    }
    // Strict: exactly twelve ASCII hex digits (no sign, no whitespace); either hex case.
    if !address.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return None;
    }
    let address = u64::from_str_radix(address, 16).ok()?;
    let kind = match kind.as_bytes()[0] {
        digit @ b'0'..=b'2' => digit - b'0',
        _ => return None,
    };
    Some((address, kind))
}

/// The OS identity behind a WinRT hint: the 48-bit Bluetooth address in its one canonical
/// lowercase text form. The address-type digit and the hex case fold into one identity; any
/// hint [`parse_hint`] refuses has no identity.
pub fn normalize_hint(hint: &str) -> Option<NormalizedTarget> {
    let (address, _kind) = parse_hint(hint)?;
    Some(NormalizedTarget::new(format!("winrt:{address:012x}")))
}

/// Display label when the advertisement carries no local name (Android's does not).
pub fn fallback_label(address: u64) -> String {
    format!("BLE device ..{:04x}", address & 0xffff)
}

/// `Windows.Devices.Bluetooth.BluetoothError`.
pub fn bluetooth_error(code: i32) -> Option<Failure> {
    Some(match code {
        0 => return None, // Success
        1 => Failure::new("radio_off", true),
        2 => Failure::new("resource_in_use", true),
        3 => Failure::new("device_not_connected", true),
        5 => Failure::new("disabled_by_policy", false),
        6 | 9 => Failure::new("unsupported", false),
        7 => Failure::new("disabled_by_user", true),
        8 => Failure::new("consent_required", true),
        _ => Failure::new("os_error", false),
    })
}

/// `Windows.Devices.Enumeration.DeviceAccessStatus` (Unspecified and Allowed are not failures).
pub fn access_status(code: i32) -> Option<Failure> {
    match code {
        2 => Some(Failure::new("access_denied_user", true)), // DeniedByUser
        3 => Some(Failure::new("access_denied_system", false)), // DeniedBySystem
        _ => None,
    }
}

/// `GattCommunicationStatus`: 0 Success, 1 Unreachable, 2 ProtocolError, 3 AccessDenied.
pub fn gatt_status(code: i32, stage: &'static str) -> Option<Failure> {
    match code {
        0 => None,
        1 => Some(Failure::new("unreachable", true)),
        3 => Some(Failure::new("access_denied", false)),
        _ => Some(Failure::new(stage, false)),
    }
}

/// `RadioState`: 0 Unknown, 1 On, 2 Off, 3 Disabled.
pub fn radio_state(code: i32) -> Option<Failure> {
    match code {
        1 => None,
        2 => Some(Failure::new("radio_off", true)),
        3 => Some(Failure::new("radio_disabled", false)),
        _ => Some(Failure::new("radio_unknown", true)),
    }
}

/// What the scan precheck learned about the default Bluetooth adapter.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AdapterProbe {
    /// `BluetoothAdapter::GetDefaultAsync` failed or produced no adapter.
    Missing,
    /// An adapter exists; `le_supported` is `IsLowEnergySupported`, `radio` is the
    /// `RadioState` code or `None` when the radio object could not be obtained.
    Present {
        le_supported: bool,
        radio: Option<i32>,
    },
}

/// The reviewed scan-start classification: no adapter is fatal, LE-unsupported is fatal,
/// radio-off is retryable (a fresh request after the radio is restored succeeds — nothing here
/// retries), an administratively disabled radio is fatal and an unobtainable radio is
/// retryable-unknown. `None` means the scan may start.
pub fn scan_precheck(probe: AdapterProbe) -> Option<Failure> {
    match probe {
        AdapterProbe::Missing => Some(Failure::new("no_adapter", false)),
        AdapterProbe::Present { le_supported, .. } if !le_supported => {
            Some(Failure::new("le_unsupported", false))
        }
        AdapterProbe::Present { radio: None, .. } => Some(Failure::new("radio_unknown", true)),
        AdapterProbe::Present {
            radio: Some(state), ..
        } => radio_state(state),
    }
}

/// `BluetoothLEAdvertisementWatcherStatus`: 0 Created, 1 Started, 2 Stopping, 3 Stopped,
/// 4 Aborted. A watcher has *observably* stopped scanning only in a terminal state; `Stopping`
/// (and `Started`) are not terminal. `Created` counts: a watcher never started has no scan.
pub fn watcher_status_is_terminal(code: i32) -> bool {
    matches!(code, 0 | 3 | 4)
}
