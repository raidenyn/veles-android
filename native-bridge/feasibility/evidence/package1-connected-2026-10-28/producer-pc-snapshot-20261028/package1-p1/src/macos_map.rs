//! Platform-neutral helpers for the CoreBluetooth (macOS) backend: hint format, the link-budget
//! policy and the bounded scan bookkeeping. Kept free of `bluest` types so every
//! mapping is unit-tested on any host (mirrors `winrt_map.rs` for the WinRT backend).
//!
//! CoreBluetooth never exposes the negotiated ATT MTU. It only reports the maximum
//! write-without-response value length `W` of a connected characteristic. `W` is a backend limit:
//! at most `MTU - 3`, but the platform may cap it, so equality is never assumed and no MTU is
//! invented (the old "512 -> 515" inference). A shared budget is established only for `W >= 512`
//! (then `MTU - 3 >= 512` and both ends use 512); a smaller `W` is refused (fail closed) because the
//! peer's exact non-final size `min(MTU - 3, 512) - 4` cannot be determined without guessing.

use crate::fragment::{BudgetError, LinkBudget};
use std::collections::HashSet;

/// `macos:<36-char canonical lowercase UUID>` of the CoreBluetooth peripheral identifier
/// (an `NSUUID`); CoreBluetooth applications never see Bluetooth addresses.
const PREFIX: &str = "macos";
const UUID_LEN: usize = 36;

#[derive(Debug, PartialEq, Eq)]
pub enum MtuError {
    /// The measured write length is below 512: it may be a platform cap rather than `MTU - 3`, so
    /// no common fragment budget with the peer can be established.
    Undetermined,
}

/// Canonical 36-char lowercase UUID: `8-4-4-4-12` hex digits with hyphens at 8/13/18/23.
pub fn valid_uuid(text: &str) -> bool {
    let bytes = text.as_bytes();
    bytes.len() == UUID_LEN
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => *byte == b'-',
            _ => matches!(byte, b'0'..=b'9' | b'a'..=b'f'),
        })
}

/// Builds the opaque search hint from the peripheral identifier `Display` string.
/// `None` means the OS handed back something unusable; the caller fails closed.
pub fn format_hint(device_id: &str) -> Option<String> {
    valid_uuid(device_id).then(|| format!("{PREFIX}:{device_id}"))
}

/// Recovers the peripheral identifier text embedded by [`format_hint`].
pub fn parse_hint(hint: &str) -> Option<&str> {
    let rest = hint.strip_prefix(PREFIX)?.strip_prefix(':')?;
    valid_uuid(rest).then_some(rest)
}

/// Display label when the advertisement carries no local name (Android's does not).
pub fn fallback_label(device_id: &str) -> String {
    let hex = device_id
        .bytes()
        .rev()
        .take_while(|byte| byte.is_ascii_hexdigit())
        .count();
    let start = device_id.len().saturating_sub(hex.min(4));
    format!("BLE device ..{}", &device_id[start..])
}

/// Link budget from the measured CoreBluetooth `maximumWriteValueLength(WithoutResponse)`.
///
/// - `W >= 512` gives attribute 512 / payload 508, the same link as an Android phone at MTU 515..517.
/// - `W < 512` is `Undetermined` and the link is refused; it is neither rounded nor extrapolated.
pub fn link_budget(max_write_without_response: usize) -> Result<LinkBudget, MtuError> {
    LinkBudget::from_max_write_without_response(max_write_without_response).map_err(|error| {
        match error {
            BudgetError::TooSmall | BudgetError::TooLarge | BudgetError::Undetermined => {
                MtuError::Undetermined
            }
        }
    })
}

/// `CBManagerAuthorization` as raw `NSInteger` (0..=3), kept numeric so classification is portable.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Authorization {
    NotDetermined,
    Restricted,
    Denied,
    AllowedAlways,
    /// Any value outside the documented range (future OS); never treated as granted.
    Unknown,
}

impl Authorization {
    pub fn from_raw(raw: isize) -> Self {
        match raw {
            0 => Self::NotDetermined,
            1 => Self::Restricted,
            2 => Self::Denied,
            3 => Self::AllowedAlways,
            _ => Self::Unknown,
        }
    }

    /// Fixed trace-safe name.
    pub fn name(self) -> &'static str {
        match self {
            Self::NotDetermined => "not_determined",
            Self::Restricted => "restricted",
            Self::Denied => "denied",
            Self::AllowedAlways => "allowed_always",
            Self::Unknown => "unknown",
        }
    }
}

/// Failure `(stage, retryable)` when the central never became available within the budget.
/// `radio_off` is only claimed when authorization is settled as allowed; an undecided or
/// unrecognised authorization never blames the radio.
pub fn unavailable_timeout(auth: Authorization) -> (&'static str, bool) {
    match auth {
        Authorization::Denied | Authorization::Restricted => ("access_denied", true),
        Authorization::NotDetermined => ("permission_pending", true),
        Authorization::AllowedAlways => ("radio_off", true),
        Authorization::Unknown => ("adapter_unavailable", true),
    }
}

/// Bounded first-seen dedupe for scan discoveries (never grows without limit).
#[derive(Debug)]
pub struct BoundedSet {
    inner: HashSet<String>,
    cap: usize,
}

impl BoundedSet {
    pub fn new(cap: usize) -> Self {
        Self {
            inner: HashSet::new(),
            cap,
        }
    }

    /// Inserts `hint`; `false` for a duplicate or once `cap` entries are held (drop further
    /// discoveries; a device seen before the cap remains connectable).
    pub fn insert(&mut self, hint: &str) -> bool {
        self.inner.len() < self.cap && self.inner.insert(hint.to_string())
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }
}

/// Scan lifecycle gate shared by the backend and its scan worker.
///
/// Transitions are linear: `Pending -> Running` happens exactly once when the OS scan confirmed
/// start, and `-> Done` happens exactly once, either by an external `stop_scan` (no
/// `ScanEnded`/`ScanStarted` emissions after that) or by the worker's own natural end.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum GateState {
    #[default]
    Pending,
    Running,
    Done,
}

#[derive(Debug, Default)]
pub struct ScanGate {
    state: GateState,
}

impl ScanGate {
    pub fn new() -> Self {
        Self {
            state: GateState::Pending,
        }
    }

    pub fn state(&self) -> GateState {
        self.state
    }

    /// `Pending -> Running`. `false` (no state change) when already running or externally done:
    /// the worker must not emit `ScanStarted`.
    pub fn try_run(&mut self) -> bool {
        if self.state != GateState::Pending {
            return false;
        }
        self.state = GateState::Running;
        true
    }

    /// Any -> `Done`. `true` only on the first stop; `false` means already stopped/ended.
    pub fn stop(&mut self) -> bool {
        (self.state != GateState::Done)
            .then(|| self.state = GateState::Done)
            .is_some()
    }

    /// Marks the natural end of a worker that started (or was silently dropped while pending)
    /// without emitting: only a running gate confirms an unsolicited end.
    pub fn finish_naturally(&mut self) -> bool {
        (self.state == GateState::Running)
            .then(|| self.state = GateState::Done)
            .is_some()
    }
}
