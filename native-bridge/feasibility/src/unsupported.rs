//! Fail-closed backend for platforms without a BLE adapter implementation in this harness.

use crate::backend::{Backend, BackendEvent, Failure, Target, Token};
use crate::runtime::BackendEvents;

pub struct UnsupportedBackend {
    events: BackendEvents,
}

impl UnsupportedBackend {
    pub fn new(events: BackendEvents) -> Self {
        Self { events }
    }
}

impl Backend for UnsupportedBackend {
    fn start_scan(&mut self, scan: Token, _service_uuid: &str) {
        self.events.emit(BackendEvent::ScanFailed {
            scan,
            failure: Failure::new("unsupported_platform", false),
        });
    }

    fn stop_scan(&mut self, _scan: Token) {}

    fn connect(&mut self, conn: Token, _target: &Target) {
        self.events.emit(BackendEvent::ConnectFailed {
            conn,
            failure: Failure::new("unsupported_platform", false),
        });
    }

    fn write_fragments(&mut self, conn: Token, send: Token, _frames: Vec<Vec<u8>>) {
        self.events.emit(BackendEvent::WriteFailed {
            conn,
            send,
            failure: Failure::new("unsupported_platform", false),
        });
    }

    fn disconnect(&mut self, _conn: Token) {}

    fn shutdown(&mut self) {}
}
