//! CoreBluetooth hint format, target identity, capacity proof, failure classification and the
//! release-settle decision (pure mappings, no OS calls). Kept free of `bluest`/Objective-C
//! types so every mapping is unit-tested on any host (the macOS adapter, `src/macos.rs`, is
//! compiled on macOS only).
//!
//! Selectively adapted from the reviewed feasibility `macos_map.rs`. Production changes:
//!
//! - the hint identity ([`normalize_hint`]) is the peripheral identifier UUID *value*: the one
//!   canonical lowercase text form of the UUID, so hex-case variants of one hint fold into one
//!   identity while any other spelling (braces, `urn:uuid:`, missing hyphens, whitespace,
//!   a sign) has no identity and is refused before any OS side effect;
//! - the capacity proof is a typed [`LinkCapacity`] ([`proven_capacity`]): CoreBluetooth only
//!   reports the maximum write-without-response length `W` (never the ATT MTU), `W < 512` is
//!   **refused** (it may be a platform cap, so no shared fragment budget can be proven) and
//!   `W >= 512` is clamped to the 512-byte attribute maximum so the host never reports an
//!   unbounded OS number;
//! - the native layer has no capacity-change callback, so the adapter re-reads `W` before every
//!   write batch ([`check_before_batch`]): a changed value is reported as a capacity change and
//!   the batch is refused instead of being written under a stale proof;
//! - failure classification is a closed set of bounded stage tokens ([`classify`]); OS error
//!   text is never carried;

use std::collections::HashSet;

use crate::backend::{Failure, NormalizedTarget};
use crate::fragment::{ATTRIBUTE_VALUE_MAX, BudgetError, LinkBudget, LinkCapacity};

/// `macos:<36-char canonical UUID>` of the CoreBluetooth peripheral identifier (an `NSUUID`);
/// CoreBluetooth applications never see Bluetooth addresses.
const PREFIX: &str = "macos";
const UUID_LEN: usize = 36;

/// Strict `8-4-4-4-12` hyphenated hex UUID text (either hex case); nothing else.
fn is_uuid_text(text: &str) -> bool {
    let bytes = text.as_bytes();
    bytes.len() == UUID_LEN
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => *byte == b'-',
            _ => byte.is_ascii_hexdigit(),
        })
}

/// Canonical 36-char lowercase UUID: `8-4-4-4-12` hex digits with hyphens at 8/13/18/23.
pub fn valid_uuid(text: &str) -> bool {
    is_uuid_text(text) && !text.bytes().any(|byte| byte.is_ascii_uppercase())
}

/// Builds the opaque search hint from the peripheral identifier `Display` string. `None`
/// means the OS handed back something unusable; the caller fails closed.
pub fn format_hint(device_id: &str) -> Option<String> {
    valid_uuid(device_id).then(|| format!("{PREFIX}:{device_id}"))
}

/// Recovers the peripheral identifier embedded by [`format_hint`] in its canonical lowercase
/// form. Accepts either hex case (the identity is the UUID value); anything that is not
/// exactly `macos:` plus a hyphenated 36-character UUID is refused.
pub fn parse_hint(hint: &str) -> Option<String> {
    let rest = hint.strip_prefix(PREFIX)?.strip_prefix(':')?;
    is_uuid_text(rest).then(|| rest.to_ascii_lowercase())
}

/// The OS identity behind a CoreBluetooth hint: the peripheral UUID value in its one
/// canonical lowercase text form. Hex-case variants fold into one identity; every hint
/// [`parse_hint`] refuses has no identity.
pub fn normalize_hint(hint: &str) -> Option<NormalizedTarget> {
    parse_hint(hint).map(|uuid| NormalizedTarget::new(format!("{PREFIX}:{uuid}")))
}

/// Display label when the advertisement carries no local name (Android's does not).
pub fn fallback_label(hint: &str) -> String {
    let hex = hint
        .bytes()
        .rev()
        .take_while(|byte| byte.is_ascii_hexdigit())
        .count();
    let start = hint.len().saturating_sub(hex.min(4));
    format!("BLE device ..{}", &hint[start..])
}

// ---- capacity -------------------------------------------------------------------------------------

/// Why a link capacity could not be proven.
#[derive(Debug, PartialEq, Eq)]
pub enum CapacityError {
    /// The measured write length is below 512: it may be a platform cap rather than `MTU - 3`,
    /// so no common fragment budget with the peer can be established.
    Undetermined,
}

/// Proves the link capacity from the measured CoreBluetooth
/// `maximumWriteValueLengthForType(WithoutResponse)` (`W`).
///
/// - `W >= 512` → [`LinkCapacity::MaxWriteWithoutResponse`] clamped to 512 (the shared budget is
///   512 whatever the MTU is; the clamp also keeps an absurd OS value from leaving this layer).
/// - `W < 512` (including 0, the MTU-floor equivalent 182 and 511) → refused. Nothing is
///   rounded, extrapolated or guessed.
pub fn proven_capacity(max_write_without_response: usize) -> Result<LinkCapacity, CapacityError> {
    // The shared fragment layer owns the 512 rule; this only maps its refusal.
    match LinkBudget::from_max_write_without_response(max_write_without_response) {
        Ok(_) => Ok(LinkCapacity::MaxWriteWithoutResponse(
            max_write_without_response.min(ATTRIBUTE_VALUE_MAX),
        )),
        Err(BudgetError::TooSmall | BudgetError::TooLarge | BudgetError::Undetermined) => {
            Err(CapacityError::Undetermined)
        }
    }
}

/// The failure a refused capacity proof reports (`ConnectFailed` before `Connected`).
pub fn capacity_failure(error: &CapacityError) -> Failure {
    match error {
        CapacityError::Undetermined => Failure::new("write_capacity", false),
    }
}

/// What the adapter does with one write batch given a fresh read of `W`.
#[derive(Debug, PartialEq, Eq)]
pub enum BatchCheck {
    /// The proven capacity still holds and every frame fits: write the batch.
    Proceed,
    /// `W` is no longer a proven capacity (or changed): refuse the batch (nothing is written)
    /// and report this capacity to the core, which tears the link down.
    CapacityChanged(LinkCapacity),
    /// `W` could not be read: refuse the batch with a bounded failure.
    Unreadable,
    /// A frame exceeds the 512-byte attribute maximum or the freshly read `W`: refuse.
    FrameTooLarge,
}

/// Decides one batch before any frame is submitted. `announced` is the clamped capacity last
/// handed to the core; `observed` is a fresh read of `W` (`None` = the read failed).
pub fn check_before_batch(
    announced: usize,
    observed: Option<usize>,
    frame_lengths: impl IntoIterator<Item = usize>,
) -> BatchCheck {
    let Some(observed) = observed else {
        return BatchCheck::Unreadable;
    };
    // Compare the *clamped* values: a change inside the 512 clamp changes nothing.
    let clamped = observed.min(ATTRIBUTE_VALUE_MAX);
    if clamped != announced || proven_capacity(observed).is_err() {
        return BatchCheck::CapacityChanged(LinkCapacity::MaxWriteWithoutResponse(clamped));
    }
    if frame_lengths.into_iter().any(|len| len > clamped) {
        return BatchCheck::FrameTooLarge;
    }
    BatchCheck::Proceed
}

// ---- authorization, radio and OS error classification ---------------------------------------------

/// `CBManagerAuthorization` as raw `NSInteger` (0..=3), kept numeric so classification is
/// portable.
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
}

/// A settled refusal known *before* waiting for the central: a denied or restricted
/// authorization cannot become available by waiting, so the scan fails at once (retryable: a
/// person can grant access and the next fresh request succeeds — nothing here retries). An
/// undecided or unrecognised authorization is not a refusal (the OS prompt may still be
/// answered).
pub fn scan_precheck(auth: Authorization) -> Option<Failure> {
    match auth {
        Authorization::Denied | Authorization::Restricted => {
            Some(Failure::new("access_denied", true))
        }
        Authorization::NotDetermined | Authorization::AllowedAlways | Authorization::Unknown => {
            None
        }
    }
}

/// Failure when the central never became available within the budget. `radio_off` is only
/// claimed when authorization is settled as allowed; an undecided or unrecognised
/// authorization never blames the radio.
pub fn unavailable_timeout(auth: Authorization) -> Failure {
    match auth {
        Authorization::Denied | Authorization::Restricted => Failure::new("access_denied", true),
        Authorization::NotDetermined => Failure::new("permission_pending", true),
        Authorization::AllowedAlways => Failure::new("radio_off", true),
        Authorization::Unknown => Failure::new("adapter_unavailable", true),
    }
}

/// The bounded set of OS error kinds the adapter distinguishes (a mirror of the `bluest`
/// `ErrorKind` variants it maps; anything else is [`OsError::Other`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OsError {
    AdapterUnavailable,
    NotAuthorized,
    NotSupported,
    AlreadyScanning,
    NotFound,
    NotConnected,
    ConnectionFailed,
    Timeout,
    Other,
}

/// Bounded failure classification for an OS error at `stage` (a `[a-z0-9_]` token). Unknown
/// kinds are retryable stage failures; the error text is never carried.
pub fn classify(error: OsError, stage: &'static str) -> Failure {
    match error {
        OsError::AdapterUnavailable => Failure::new("radio_off", true),
        OsError::NotAuthorized => Failure::new("access_denied", true),
        OsError::NotSupported => Failure::new("le_unsupported", false),
        OsError::AlreadyScanning => Failure::new("resource_in_use", true),
        OsError::NotFound => Failure::new(stage, false),
        OsError::NotConnected | OsError::ConnectionFailed | OsError::Other => {
            Failure::new(stage, true)
        }
        OsError::Timeout => Failure::new("setup_timeout", true),
    }
}

// ---- scan bookkeeping ------------------------------------------------------------------------------

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
