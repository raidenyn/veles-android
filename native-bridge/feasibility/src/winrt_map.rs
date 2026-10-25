//! Platform-neutral helpers for the WinRT backend: hint format and failure classification.
//! Kept free of `windows` types so the mappings are unit-tested on any host.

use crate::backend::Failure;

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
    let address = u64::from_str_radix(address, 16).ok()?;
    let kind = kind.parse::<u8>().ok().filter(|k| *k <= 2)?;
    Some((address, kind))
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
