//! Deterministic simulated BLE central for process-level tests: two synthetic "phones" that
//! implement the Android peripheral's receive/fragment rules independently per link. Selected only
//! with `--backend sim` (never reachable from a Chrome-launched host, which gets no such argument).
//!
//! Synthetic behaviour (never OTP data): record `56 45 4c 01` answers `56 45 4c 02`; a record that
//! starts with `ECHO` is echoed whole; a record equal to `DROP` makes the link vanish.

use std::collections::HashMap;
use std::time::Instant;

use crate::backend::{Backend, BackendEvent, Discovered, Failure, Target, Token};
use crate::fragment::{GlobalBudget, PushOutcome, Reassembler, fragment};
use crate::runtime::BackendEvents;

pub const PHONE_HINTS: [&str; 2] = ["sim:phone-1", "sim:phone-2"];
const MTU: usize = 185;

struct Link {
    hint: String,
    reassembler: Reassembler,
    next_transaction: u8,
}

pub struct SimBackend {
    events: BackendEvents,
    budget: std::sync::Arc<GlobalBudget>,
    links: HashMap<Token, Link>,
}

impl SimBackend {
    pub fn new(events: BackendEvents) -> Self {
        Self {
            events,
            budget: GlobalBudget::new(),
            links: HashMap::new(),
        }
    }
}

impl Backend for SimBackend {
    fn start_scan(&mut self, scan: Token, _service_uuid: &str) {
        self.events.emit(BackendEvent::ScanStarted { scan });
        for (index, hint) in PHONE_HINTS.iter().enumerate() {
            self.events.emit(BackendEvent::Advertisement {
                scan,
                device: Discovered {
                    platform_hint: (*hint).to_string(),
                    label: format!("Synthetic phone {}", index + 1),
                    advertisement: None,
                },
            });
        }
    }

    fn stop_scan(&mut self, _scan: Token) {}

    fn connect(&mut self, conn: Token, target: &Target) {
        let Target::Hint(hint) = target;
        if PHONE_HINTS.contains(&hint.as_str()) {
            self.links.insert(
                conn,
                Link {
                    hint: hint.clone(),
                    reassembler: Reassembler::new(MTU, self.budget.clone()),
                    next_transaction: 1,
                },
            );
            self.events.emit(BackendEvent::Connected {
                conn,
                mtu: MTU,
                platform_hint: Some(hint.clone()),
            });
        } else {
            self.events.emit(BackendEvent::ConnectFailed {
                conn,
                failure: Failure::new("device_lookup", false),
            });
        }
    }

    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Vec<u8>>) {
        let Some(link) = self.links.get_mut(&conn) else {
            self.events.emit(BackendEvent::WriteFailed {
                conn,
                send,
                failure: Failure::new("write", false),
            });
            return;
        };
        let mut record = None;
        for frame in &frames {
            match link.reassembler.push(frame, Instant::now()) {
                PushOutcome::Complete(bytes) => record = Some(bytes),
                PushOutcome::Incomplete => {}
                _ => {
                    self.events.emit(BackendEvent::WriteFailed {
                        conn,
                        send,
                        failure: Failure::new("write", false),
                    });
                    return;
                }
            }
        }
        self.events.emit(BackendEvent::WriteDone { conn, send });
        let Some(record) = record else {
            return;
        };
        let response = if record == [0x56, 0x45, 0x4c, 0x01] {
            Some(vec![0x56, 0x45, 0x4c, 0x02])
        } else if record.starts_with(b"ECHO") {
            Some(record.clone())
        } else {
            None
        };
        if record == b"DROP" {
            self.links.remove(&conn);
            self.events.emit(BackendEvent::LinkLost { conn });
            return;
        }
        if let Some(response) = response {
            let transaction = link.next_transaction;
            link.next_transaction = link.next_transaction % 255 + 1;
            for frame in fragment(&response, MTU, transaction).expect("synthetic response fits") {
                self.events
                    .emit(BackendEvent::Fragment { conn, bytes: frame });
            }
        }
        let _ = &link.hint;
    }

    fn disconnect(&mut self, conn: Token) {
        self.links.remove(&conn);
    }

    fn shutdown(&mut self) {
        self.links.clear();
    }
}
