//! Seam between the platform-neutral host core and an OS BLE central (WinRT, simulator, fake).
//!
//! Commands are non-blocking submissions; outcomes return as [`BackendEvent`]s. Backends speak
//! raw GATT: the core owns request correlation, handles, fragmentation and reassembly.

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
    pub platform_hint: String,
    pub label: String,
    pub advertisement: Option<Vec<u8>>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Target {
    Hint(String),
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
    /// GATT discovery, MTU (>= 185) and CCCD subscription completed, in that order.
    Connected {
        conn: Token,
        mtu: usize,
        platform_hint: Option<String>,
    },
    ConnectFailed {
        conn: Token,
        failure: Failure,
    },
    /// One raw GATT notification value (a fragment).
    Fragment {
        conn: Token,
        bytes: Vec<u8>,
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
}

pub trait Backend: Send {
    fn start_scan(&mut self, scan: Token, service_uuid: &str);
    fn stop_scan(&mut self, scan: Token);
    fn connect(&mut self, conn: Token, target: &Target);
    /// Writes every frame in order without response; one completion for the whole batch.
    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Vec<u8>>);
    /// Releases the link. No completion event; late events for `conn` are ignored by the core.
    fn disconnect(&mut self, conn: Token);
    /// Stops everything and releases every OS resource. Bounded; used on EOF/shutdown/failure.
    fn shutdown(&mut self);
}
