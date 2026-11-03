//! Recording fake backend of the deterministic core tests: it stores every
//! [`Backend`] submission (with the exact owned [`Payload`] frames it received
//! and never any BLE work) so tests can assert on submissions and inject
//! completions through the real ingress producer of the rig.

use std::sync::{Arc, Mutex};

use veles_ble_host::backend::{Backend, NormalizedTarget, Target, Token};
use veles_ble_host::buffer::Payload;

/// One recorded backend submission, in submission order. Frame lists are
/// exact copies (payload content is never logged anywhere else; tests hold
/// these buffers directly).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Call {
    StartScan(Token, String),
    StopScan(Token),
    Connect(Token, Target),
    WriteFragments(Token, Token, Vec<Payload>),
    Disconnect(Token),
    Shutdown,
}

#[derive(Clone, Default)]
pub struct FakeBackend {
    pub calls: Arc<Mutex<Vec<Call>>>,
    sync_release: Option<veles_ble_host::ingress::BackendEvents>,
}

/// One deliberate alias hint of the fake backend's normalization rule: the
/// text before the first `#` is the device's OS identity, the suffix after it
/// is an observation variant (the way the real seam folds the WinRT
/// address-type digit; CoreBluetooth's reviewed format is already canonical
/// and needs no fold). `alias("phone-1", "kind2")` names the same OS device
/// as `"phone-1"`.
pub fn alias(hint: &str, observation: &str) -> String {
    format!("{hint}#{observation}")
}

impl FakeBackend {
    pub fn handle(&self) -> Self {
        Self {
            calls: Arc::clone(&self.calls),
            sync_release: None,
        }
    }

    /// Arms the fake to report the process-wide release SYNCHRONOUSLY inside
    /// its `shutdown()` submission through the real ingress producer — the
    /// way an adapter with no outstanding OS work behaves (the callback the
    /// handshake must still accept, because the core arms the handshake
    /// before the submission).
    pub fn arm_sync_release(&mut self, events: veles_ble_host::ingress::BackendEvents) {
        self.sync_release = Some(events);
    }

    pub fn calls(&self) -> Vec<Call> {
        self.calls.lock().expect("fake backend log").clone()
    }

    pub fn clear(&self) {
        self.calls.lock().expect("fake backend log").clear();
    }

    pub fn write_calls(&self) -> Vec<(Token, Token, Vec<Payload>)> {
        self.calls
            .lock()
            .expect("fake backend log")
            .iter()
            .filter_map(|call| match call {
                Call::WriteFragments(conn, send, frames) => Some((*conn, *send, frames.clone())),
                _ => None,
            })
            .collect()
    }
}

impl Backend for FakeBackend {
    fn normalize_target(&self, hint: &str) -> Option<NormalizedTarget> {
        // The deliberate alias rule (see [`alias`]): the identity is the text
        // before the first `#`, everything else is an observation variant;
        // a hint with no identity text resolves to no OS target at all.
        let identity = hint.split('#').next()?;
        (!identity.is_empty()).then(|| NormalizedTarget::new(identity.to_string()))
    }

    fn start_scan(&mut self, scan: Token, service_uuid: &str) {
        self.calls
            .lock()
            .expect("fake backend log")
            .push(Call::StartScan(scan, service_uuid.to_string()));
    }

    fn stop_scan(&mut self, scan: Token) {
        self.calls
            .lock()
            .expect("fake backend log")
            .push(Call::StopScan(scan));
    }

    fn connect(&mut self, conn: Token, target: &Target) {
        self.calls
            .lock()
            .expect("fake backend log")
            .push(Call::Connect(conn, target.clone()));
    }

    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Payload>) {
        self.calls
            .lock()
            .expect("fake backend log")
            .push(Call::WriteFragments(conn, send, frames));
    }

    fn disconnect(&mut self, conn: Token) {
        self.calls
            .lock()
            .expect("fake backend log")
            .push(Call::Disconnect(conn));
    }

    fn shutdown(&mut self) {
        self.calls
            .lock()
            .expect("fake backend log")
            .push(Call::Shutdown);
        if let Some(events) = self.sync_release.take() {
            // The synchronous observed release of this submission
            // (BackendEvents is Clone; the last handle dies here).
            let _ = events.emit(veles_ble_host::backend::BackendEvent::ShutdownComplete);
        }
    }
}
