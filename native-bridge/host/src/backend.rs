//! Shared event/trait vocabulary of the BLE backend seam (OTP-24 production host).
//!
//! Ported from the reviewed feasibility `native-bridge/feasibility/src/backend.rs` (design
//! disposition table: "Typed bounded submission, cancellation and cleanup completion seams"),
//! with three deliberate production changes the controller pinned in the task dispatch:
//!
//! - fragment bytes are owned erased [`Payload`] buffers, not `Vec<u8>`;
//! - [`Backend::write_fragments`] hands over `Vec<Payload>`, one owned erased buffer per frame;
//! - three lifecycle events the production core needs exist here: [`BackendEvent::LinkReleased`]
//!   (cleanup observed), [`BackendEvent::ShutdownComplete`] (process-wide release observed) and
//!   [`BackendEvent::CapacityChanged`] (a later observed capacity change on a live link).
//!
//! Everything else retains the reviewed safe, bounded concepts: commands are non-blocking
//! submissions, outcomes return as [`BackendEvent`]s, and nothing carries device payloads in
//! failures — only a bounded `&'static str` stage. Backends speak raw GATT; the core (later
//! task) owns request correlation, handles, fragmentation and reassembly and will consume this
//! vocabulary through `ingress`/`outqueue`, never by calling OS backends directly.

use crate::buffer::Payload;
use crate::fragment::LinkCapacity;

/// Host-assigned opaque id for a scan, connection or send attempt. Never shown to Chrome.
pub type Token = u64;

/// Safe, bounded classification of a backend failure. Never carries device payloads.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Failure {
    /// `[a-z0-9_]{1,32}` stage reported in the envelope `error`.
    pub stage: &'static str,
    pub retryable: bool,
}

impl Failure {
    pub const fn new(stage: &'static str, retryable: bool) -> Self {
        Self { stage, retryable }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Discovered {
    /// Opaque OS search hint; identifies the device for `connect` and for per-scan dedupe.
    /// The production ingress bounds it to the 256-byte ceiling of the protocol hint field.
    pub platform_hint: String,
    pub label: String,
    pub advertisement: Option<Vec<u8>>,
}

/// OS routing target for `connect` — an opaque platform hint, never trusted identity.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Target {
    Hint(String),
}

/// The OS-normalized identity behind a connect target, produced by
/// [`Backend::normalize_target`] — the backend's own typed addressing, never a
/// transformation of the opaque hint text:
///
/// - Windows (WinRT): the 48-bit Bluetooth address of the reviewed
///   `winrt:<12 hex digits>:<0|1|2>` hint format. The device identity is the
///   address; the observed address-type digit and the hex case are not
///   identity, so their variants fold into one identity.
/// - macOS (CoreBluetooth): the peripheral identifier UUID value of the
///   reviewed `macos:<36-char canonical lowercase UUID>` hint format (the one
///   canonical text form of that UUID, compared as the UUID value).
///
/// No generic casing/Unicode normalization is applied anywhere: only the
/// backend's typed address/UUID mapping produces an identity, and a hint the
/// backend cannot resolve maps to `None`. The core keys pending/open/closing
/// connections by this identity and refuses a `connect` whose target resolves
/// to none before any backend side effect; the opaque hint itself stays the
/// routing input (`Backend::connect` receives it unchanged).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NormalizedTarget(String);

impl NormalizedTarget {
    /// Builds the identity from the backend's canonical form of its own
    /// platform mapping.
    pub fn new(identity: impl Into<String>) -> Self {
        Self(identity.into())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BackendEvent {
    ScanStarted {
        scan: Token,
    },
    ScanFailed {
        scan: Token,
        failure: Failure,
    },
    /// Watcher ended on its own or after `stop_scan` (stop confirmation).
    ScanEnded {
        scan: Token,
        failure: Option<Failure>,
    },
    Advertisement {
        scan: Token,
        device: Discovered,
    },
    /// The OS release of the scan has been observed: the scan worker finished and every
    /// resource it acquired (watcher, subscriptions, stream) was closed **cleanly**. The
    /// scan counterpart of [`BackendEvent::LinkReleased`]: the core keeps a scan counted
    /// against the 2-scan ceiling (pending / active / closing) until this event, however
    /// the scan ended (core `stop_scan`, 300 s cap, OS-ended, failed start). Emitted at most
    /// once per scan, after any `ScanFailed`/`ScanEnded` of the same scan (per-scan FIFO),
    /// and **never** for a scan whose cleanup failed or wedged — that scan stays counted
    /// and the process-wide `ShutdownComplete` is withheld too.
    ScanReleased {
        scan: Token,
    },
    /// GATT discovery, link capacity (>= the 185-byte MTU floor) and CCCD subscription completed,
    /// in that order. The core derives the one [`crate::fragment::LinkBudget`] from `capacity`.
    Connected {
        conn: Token,
        capacity: LinkCapacity,
        platform_hint: Option<String>,
    },
    ConnectFailed {
        conn: Token,
        failure: Failure,
    },
    /// A later observed capacity change on a live link. The core rechecks the frozen budget; an
    /// invalidated budget cancels affected work, an identical derived budget changes nothing.
    CapacityChanged {
        conn: Token,
        capacity: LinkCapacity,
    },
    /// One raw GATT notification value (a fragment). Owned; erased on release.
    Fragment {
        conn: Token,
        bytes: Payload,
    },
    WriteDone {
        conn: Token,
        send: Token,
    },
    WriteFailed {
        conn: Token,
        send: Token,
        failure: Failure,
    },
    LinkLost {
        conn: Token,
    },
    /// The OS release of the link has been observed (after `disconnect`): the production
    /// counterpart of the reviewed "no completion event" policy.
    LinkReleased {
        conn: Token,
    },
    /// The process-wide OS release has been observed (after `shutdown`): the production
    /// counterpart of the reviewed bounded-close seam. Emitted exactly once.
    ShutdownComplete,
}

pub trait Backend: Send {
    fn start_scan(&mut self, scan: Token, service_uuid: &str);
    /// Requests the scan to stop. Non-blocking; the backend reports the observed release as
    /// a [`BackendEvent::ScanReleased`], and the core keeps the scan counted until then.
    fn stop_scan(&mut self, scan: Token);
    fn connect(&mut self, conn: Token, target: &Target);
    /// The OS-normalized identity behind the opaque platform hint, or `None`
    /// when this backend cannot resolve the hint to an OS target (unsupported
    /// or malformed): the core refuses such a `connect` before any OS side
    /// effect. Deterministic and synchronous — a pure function of the hint
    /// bytes, never an OS call (the connect path stays nonblocking), and the
    /// hint itself is still routed verbatim to [`Backend::connect`]. The
    /// per-platform identity semantics are pinned in [`NormalizedTarget`]'s
    /// docs; the production adapters (Tasks 6/7) implement them with the OS
    /// address/UUID mappings.
    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget>;
    /// Writes every frame in order without response; one completion for the whole batch.
    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Payload>);
    /// Releases the link. The backend reports the observed release as a
    /// [`BackendEvent::LinkReleased`]; late events for `conn` are ignored by the core.
    fn disconnect(&mut self, conn: Token);
    /// Stops everything and releases every OS resource. Bounded; used on EOF/shutdown/failure.
    /// The backend reports the observed process-wide release as [`BackendEvent::ShutdownComplete`].
    fn shutdown(&mut self);
}
