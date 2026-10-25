//! Whole-record GATT fragmentation and reassembly mirroring the Android peripheral
//! (`GattTransport.kt`, OTP2-GAT-010..012). Transport only: records are opaque bytes.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const MIN_MTU: usize = 185;
pub const MAX_MTU: usize = 517;
pub const MAX_RECORD: usize = 4096;

const HEADER: usize = 4;
const INACTIVITY: Duration = Duration::from_secs(5);
const CONFLICT_WINDOW: Duration = Duration::from_secs(60);
const PEER_INCOMPLETE: usize = 2;
const PEER_BYTES: usize = 8 * 1024;
const GLOBAL_INCOMPLETE: usize = 16;
const GLOBAL_BYTES: usize = 64 * 1024;

/// Best-effort erase of a payload buffer before it is released.
pub fn wipe(bytes: &mut [u8]) {
    for byte in bytes.iter_mut() {
        // Volatile so the compiler cannot elide the clearing of a buffer that is about to be freed.
        unsafe { std::ptr::write_volatile(byte, 0) };
    }
    std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
}

#[derive(Debug, PartialEq, Eq)]
pub enum FragmentError {
    EmptyRecord,
    OversizeRecord,
    BadMtu,
    BadTransactionId,
}

/// Splits one record into uniform-header fragments. Every non-final payload is exactly
/// `mtu - 7` bytes; the last chunk is flagged final (no trailing zero-length fragment).
pub fn fragment(
    record: &[u8],
    mtu: usize,
    transaction_id: u8,
) -> Result<Vec<Vec<u8>>, FragmentError> {
    if record.is_empty() {
        return Err(FragmentError::EmptyRecord);
    }
    if record.len() > MAX_RECORD {
        return Err(FragmentError::OversizeRecord);
    }
    if !(MIN_MTU..=MAX_MTU).contains(&mtu) {
        return Err(FragmentError::BadMtu);
    }
    if transaction_id == 0 {
        return Err(FragmentError::BadTransactionId);
    }
    let chunk = mtu - 3 - HEADER;
    let count = record.len().div_ceil(chunk);
    let frames = record
        .chunks(chunk)
        .enumerate()
        .map(|(index, payload)| {
            let last = index + 1 == count;
            let total = if index == 0 { record.len() as u16 } else { 0 };
            let mut frame = Vec::with_capacity(HEADER + payload.len());
            frame.push(index as u8 | if last { 0x80 } else { 0 });
            frame.push(transaction_id);
            frame.extend_from_slice(&total.to_be_bytes());
            frame.extend_from_slice(payload);
            frame
        })
        .collect();
    Ok(frames)
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum DiscardReason {
    FrameSize,
    ZeroTransaction,
    TotalLength,
    Order,
    Payload,
}

#[derive(Debug, PartialEq, Eq)]
pub enum PushOutcome {
    Incomplete,
    Complete(Vec<u8>),
    /// Stray fragment, duplicate of a live transaction, or an over-cap new transaction: no state change.
    Ignored,
    /// Malformed input: all partial transactions of the peer were dropped (transport-only loss).
    Discarded(DiscardReason),
    /// Repeated conflicting transaction starts within 60 s: drop only this peer.
    Disconnect,
}

/// Process-wide reassembly ceilings: 16 incomplete transactions and 64 KiB.
#[derive(Debug, Default)]
pub struct GlobalBudget {
    inner: Mutex<(usize, usize)>,
}

impl GlobalBudget {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// (incomplete transactions, buffered bytes).
    pub fn usage(&self) -> (usize, usize) {
        *self.inner.lock().expect("budget lock")
    }

    fn try_reserve(&self, bytes: usize) -> bool {
        let mut state = self.inner.lock().expect("budget lock");
        if state.0 + 1 > GLOBAL_INCOMPLETE || state.1 + bytes > GLOBAL_BYTES {
            return false;
        }
        state.0 += 1;
        state.1 += bytes;
        true
    }

    fn release(&self, bytes: usize) {
        let mut state = self.inner.lock().expect("budget lock");
        state.0 = state.0.saturating_sub(1);
        state.1 = state.1.saturating_sub(bytes);
    }
}

struct Partial {
    id: u8,
    total: usize,
    bytes: Vec<u8>,
    next_index: u8,
    touched: Instant,
}

/// Per-connection reassembler: 2 incomplete transactions and 8 KiB, 5 s inactivity timeout.
pub struct Reassembler {
    mtu: usize,
    budget: Arc<GlobalBudget>,
    partials: Vec<Partial>,
    conflicts: VecDeque<Instant>,
}

impl Reassembler {
    pub fn new(mtu: usize, budget: Arc<GlobalBudget>) -> Self {
        Self {
            mtu,
            budget,
            partials: Vec::new(),
            conflicts: VecDeque::new(),
        }
    }

    pub fn push(&mut self, frame: &[u8], now: Instant) -> PushOutcome {
        if frame.len() < HEADER || frame.len() > self.mtu - 3 {
            return self.discard(DiscardReason::FrameSize);
        }
        let index = frame[0] & 0x7f;
        let last = frame[0] & 0x80 != 0;
        let id = frame[1];
        let length = usize::from(u16::from_be_bytes([frame[2], frame[3]]));
        if id == 0 {
            return self.discard(DiscardReason::ZeroTransaction);
        }
        self.expire(now);
        if index == 0 && self.partials.iter().any(|p| p.id == id) {
            return self.conflict(now);
        }
        if index == 0 {
            if !(1..=MAX_RECORD).contains(&length) {
                return self.discard(DiscardReason::TotalLength);
            }
            if !self.can_allocate(length) {
                return PushOutcome::Ignored;
            }
            self.partials.push(Partial {
                id,
                total: length,
                bytes: Vec::with_capacity(length),
                next_index: 0,
                touched: now,
            });
        }
        let Some(position) = self.partials.iter().position(|p| p.id == id) else {
            return PushOutcome::Ignored;
        };
        let payload = frame.len() - HEADER;
        let partial = &self.partials[position];
        let remaining = partial.total - partial.bytes.len();
        let ordered = partial.next_index == index && (index == 0 || length == 0);
        let valid_size = if last {
            payload == remaining
        } else {
            payload == self.mtu - 3 - HEADER && index < 127 && payload <= remaining
        };
        if !ordered {
            return self.discard(DiscardReason::Order);
        }
        if !valid_size {
            return self.discard(DiscardReason::Payload);
        }
        let partial = &mut self.partials[position];
        partial.bytes.extend_from_slice(&frame[HEADER..]);
        partial.next_index += 1;
        partial.touched = now;
        if !last {
            return PushOutcome::Incomplete;
        }
        let done = self.partials.remove(position);
        self.budget.release(done.total);
        PushOutcome::Complete(done.bytes)
    }

    fn can_allocate(&self, length: usize) -> bool {
        self.partials.len() < PEER_INCOMPLETE
            && self.partials.iter().map(|p| p.total).sum::<usize>() + length <= PEER_BYTES
            && self.budget.try_reserve(length)
    }

    fn conflict(&mut self, now: Instant) -> PushOutcome {
        while self
            .conflicts
            .front()
            .is_some_and(|at| now.duration_since(*at) >= CONFLICT_WINDOW)
        {
            self.conflicts.pop_front();
        }
        self.conflicts.push_back(now);
        if self.conflicts.len() >= 2 {
            self.clear();
            PushOutcome::Disconnect
        } else {
            PushOutcome::Ignored
        }
    }

    fn discard(&mut self, reason: DiscardReason) -> PushOutcome {
        self.clear();
        PushOutcome::Discarded(reason)
    }

    /// Drops transactions idle for at least five seconds; returns how many were dropped.
    pub fn expire(&mut self, now: Instant) -> usize {
        let mut dropped = 0;
        let mut index = 0;
        while index < self.partials.len() {
            if now.saturating_duration_since(self.partials[index].touched) >= INACTIVITY {
                let mut partial = self.partials.remove(index);
                wipe(&mut partial.bytes);
                self.budget.release(partial.total);
                dropped += 1;
            } else {
                index += 1;
            }
        }
        dropped
    }

    /// Earliest instant at which an incomplete transaction times out.
    pub fn next_expiry(&self) -> Option<Instant> {
        self.partials.iter().map(|p| p.touched + INACTIVITY).min()
    }

    pub fn incomplete(&self) -> usize {
        self.partials.len()
    }

    /// Wipes and releases every partial transaction.
    pub fn clear(&mut self) {
        for mut partial in self.partials.drain(..) {
            wipe(&mut partial.bytes);
            self.budget.release(partial.total);
        }
    }
}

impl Drop for Reassembler {
    fn drop(&mut self) {
        self.clear();
    }
}
