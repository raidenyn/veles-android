//! Opaque whole-record GATT fragmentation and reassembly, mirroring the Android peripheral
//! (`GattTransport.kt`, profile revision 2, OTP2-GAT-010..012). Transport only: records are
//! opaque bytes.
//!
//! Adapted from the reviewed OTP-23 feasibility fragmenter
//! (`native-bridge/feasibility/src/fragment.rs`, design disposition table: "Preserve revision 2;
//! explicit buffer ownership/erase and budget checks"). Wire behavior is unchanged; the
//! production adaptation stores record bytes only in owned [`Payload`] buffers — fragment frames,
//! partial reassembly state and completed records — so every released byte passes through
//! `buffer::erase`, and no `Debug`/trace formatting can print record content.
//!
//! One formula, written once ([`LinkBudget`]): a fragment frame (4-byte header + payload) may not
//! exceed `min(ATT_MTU - 3, 512)` bytes in either direction, so every non-final payload is exactly
//! `attribute - 4` bytes and the final payload exactly what remains. No backend hardcodes an MTU
//! or a chunk size; each reports what its OS can observe ([`LinkCapacity`]).

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::buffer::Payload;
use crate::limits::GATT_SEND_RECORD_MAX_BYTES;

/// Smallest negotiated ATT MTU a link may report (OTP2-GAT-003).
pub const MIN_MTU: usize = 185;
/// Largest negotiated ATT MTU accepted (the Android `requestMtu` ceiling).
pub const MAX_MTU: usize = 517;
/// ATT Handle Value Notification / Write Command header (opcode + handle).
pub const ATT_OVERHEAD: usize = 3;
/// Bluetooth ATT maximum attribute value length.
pub const ATTRIBUTE_VALUE_MAX: usize = 512;
/// Uniform fragment header (index/final byte, transaction id, total length).
pub const HEADER: usize = 4;

/// What a backend can observe about the link's size limit. Windows reports the ATT MTU
/// (`GattSession::MaxPduSize`); CoreBluetooth never exposes the MTU, only the maximum
/// write-without-response value length, which already equals `MTU - 3`.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum LinkCapacity {
    AttMtu(usize),
    MaxWriteWithoutResponse(usize),
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum BudgetError {
    /// The link reports less than the 185-byte MTU floor (or the equivalent 182-byte write).
    TooSmall,
    /// An ATT MTU above the 517-byte ceiling.
    TooLarge,
    /// Only a backend write limit is known and it is below the 512 clamp: that limit may be capped
    /// by the platform, so it cannot be equated with `MTU - 3` and no common budget can be
    /// established.
    Undetermined,
}

/// The attribute/payload budget of one link, derived once from its [`LinkCapacity`] and frozen
/// for the connection (a later capacity change never refragments an in-flight record).
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub struct LinkBudget {
    attribute: usize,
}

impl LinkBudget {
    /// `attribute = min(mtu - 3, 512)`; MTUs outside `185..=517` are refused.
    pub fn from_att_mtu(mtu: usize) -> Result<Self, BudgetError> {
        if mtu < MIN_MTU {
            return Err(BudgetError::TooSmall);
        }
        if mtu > MAX_MTU {
            return Err(BudgetError::TooLarge);
        }
        Ok(Self {
            attribute: (mtu - ATT_OVERHEAD).min(ATTRIBUTE_VALUE_MAX),
        })
    }

    /// A measured maximum write-without-response length `W` (CoreBluetooth) is a *backend* limit:
    /// it is at most `MTU - 3` but the platform may cap it, so `W == MTU - 3` cannot be assumed.
    /// The peer's non-final fragments are exactly `min(MTU - 3, 512) - 4` bytes, so a budget is
    /// only established when `W >= 512`: then `MTU - 3 >= W >= 512` and the shared budget is 512
    /// whatever the MTU is. Below 512 the common budget is unknown and the link is refused (fail
    /// closed); no value is guessed. 512 is the ATT attribute maximum, not a tuning constant.
    pub fn from_max_write_without_response(write: usize) -> Result<Self, BudgetError> {
        if write >= ATTRIBUTE_VALUE_MAX {
            Ok(Self {
                attribute: ATTRIBUTE_VALUE_MAX,
            })
        } else {
            Err(BudgetError::Undetermined)
        }
    }

    pub fn from_capacity(capacity: LinkCapacity) -> Result<Self, BudgetError> {
        match capacity {
            LinkCapacity::AttMtu(mtu) => Self::from_att_mtu(mtu),
            LinkCapacity::MaxWriteWithoutResponse(write) => {
                Self::from_max_write_without_response(write)
            }
        }
    }

    /// Largest fragment frame (header + payload) on this link.
    pub fn attribute(self) -> usize {
        self.attribute
    }

    /// Payload of every non-final fragment on this link.
    pub fn payload(self) -> usize {
        self.attribute - HEADER
    }
}

/// Largest whole record on the GATT link: the frozen `protocol/v1/limits.json` ceiling
/// (`record_max_bytes`). The Native Messaging envelope admits up to 5,120 decoded bytes; a
/// `send` in the 4,097..=5,120 band is refused with correlated `oversize_record` before any
/// fragment is created or written. Zero bytes reject.
pub const MAX_RECORD: usize = GATT_SEND_RECORD_MAX_BYTES;

#[derive(Debug, PartialEq, Eq)]
pub enum FragmentError {
    EmptyRecord,
    OversizeRecord,
    BadTransactionId,
}

/// Splits one record into uniform-header fragments. Every non-final payload is exactly
/// `budget.payload()` bytes; the last chunk (`1..=payload`) is flagged final, so an exact
/// multiple ends in a full final fragment and a sender never emits a trailing zero-length
/// fragment. Each frame is an owned buffer that is erased when released.
pub fn fragment(
    record: &[u8],
    budget: LinkBudget,
    transaction_id: u8,
) -> Result<Vec<Payload>, FragmentError> {
    if record.is_empty() {
        return Err(FragmentError::EmptyRecord);
    }
    if record.len() > MAX_RECORD {
        return Err(FragmentError::OversizeRecord);
    }
    if transaction_id == 0 {
        return Err(FragmentError::BadTransactionId);
    }
    let chunk = budget.payload();
    let count = record.len().div_ceil(chunk);
    Ok(record
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
            Payload::new(frame)
        })
        .collect())
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
    Complete(Payload),
    /// Stray fragment or an over-cap new transaction: no state change; or the first conflicting
    /// start of a live transaction id, which erased that live partial and released its budget.
    Ignored,
    /// Malformed input: all partial transactions of the peer were dropped (transport-only loss).
    Discarded(DiscardReason),
    /// Repeated conflicting transaction starts within 60 s: drop only this peer.
    Disconnect,
}

const INACTIVITY: Duration = Duration::from_secs(5);
const CONFLICT_WINDOW: Duration = Duration::from_secs(60);
const PEER_INCOMPLETE: usize = 2;
const PEER_BYTES: usize = 8 * 1024;
const GLOBAL_INCOMPLETE: usize = 16;
const GLOBAL_BYTES: usize = 64 * 1024;

/// Process-wide reassembly ceilings: 16 incomplete transactions and 64 KiB, reserved by each
/// transaction's declared total (its full allocated capacity) at the first fragment.
#[derive(Debug, Default)]
pub struct GlobalBudget {
    inner: Mutex<(usize, usize)>,
}

impl GlobalBudget {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// (incomplete transactions, reserved bytes).
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

/// One incomplete inbound transaction. Every accepted fragment's payload is an owned buffer; on
/// any release path (completion assembly, discard, expiry, clear) those buffers erase themselves,
/// and the assembled record is handed over as a fresh owned buffer.
struct Partial {
    id: u8,
    total: usize,
    chunks: Vec<Payload>,
    filled: usize,
    next_index: u8,
    touched: Instant,
}

/// Per-connection reassembler: 2 incomplete transactions and 8 KiB, 5 s inactivity timeout.
pub struct Reassembler {
    link: LinkBudget,
    budget: Arc<GlobalBudget>,
    partials: Vec<Partial>,
    conflicts: VecDeque<Instant>,
}

impl Reassembler {
    pub fn new(link: LinkBudget, budget: Arc<GlobalBudget>) -> Self {
        Self {
            link,
            budget,
            partials: Vec::new(),
            conflicts: VecDeque::new(),
        }
    }

    pub fn push(&mut self, frame: &[u8], now: Instant) -> PushOutcome {
        if frame.len() < HEADER || frame.len() > self.link.attribute() {
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
        if index == 0
            && let Some(live) = self.partials.iter().position(|p| p.id == id)
        {
            return self.conflict(live, now);
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
                chunks: Vec::new(),
                filled: 0,
                next_index: 0,
                touched: now,
            });
        }
        let Some(position) = self.partials.iter().position(|p| p.id == id) else {
            return PushOutcome::Ignored;
        };
        let payload = frame.len() - HEADER;
        let partial = &self.partials[position];
        let remaining = partial.total - partial.filled;
        let chunk = self.link.payload();
        let ordered = partial.next_index == index && (index == 0 || length == 0);
        let valid_size = if last {
            payload == remaining
        } else {
            payload == chunk && index < 127 && payload <= remaining
        };
        if !ordered {
            return self.discard(DiscardReason::Order);
        }
        if !valid_size {
            return self.discard(DiscardReason::Payload);
        }
        let partial = &mut self.partials[position];
        partial.chunks.push(Payload::new(frame[HEADER..].to_vec()));
        partial.filled += payload;
        partial.next_index += 1;
        partial.touched = now;
        if !last {
            return PushOutcome::Incomplete;
        }
        let done = self.partials.remove(position);
        self.budget.release(done.total);
        PushOutcome::Complete(Self::assemble(done))
    }

    /// Concatenates the fragment payloads of a complete transaction into one owned record
    /// buffer; the fragment buffers erase themselves as they are released here.
    fn assemble(mut partial: Partial) -> Payload {
        let mut bytes = Vec::with_capacity(partial.filled);
        for chunk in &partial.chunks {
            bytes.extend_from_slice(chunk.as_slice());
        }
        partial.chunks.clear();
        Payload::new(bytes)
    }

    fn can_allocate(&self, length: usize) -> bool {
        self.partials.len() < PEER_INCOMPLETE
            && self.partials.iter().map(|p| p.total).sum::<usize>() + length <= PEER_BYTES
            && self.budget.try_reserve(length)
    }

    /// Conflict rule (OTP2-GAT-011): a start for an id that is already live erases that partial
    /// (its buffers erase themselves; its peer and global budget are released) and is itself
    /// rejected, so no live prefix can be completed by a foreign suffix. Later nonzero
    /// continuations of the id find no partial and are ignored; a later fresh index 0 may start.
    /// A second conflicting start within the rolling 60 s window disconnects the peer.
    fn conflict(&mut self, live: usize, now: Instant) -> PushOutcome {
        let erased = self.partials.remove(live);
        self.budget.release(erased.total);
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
                let partial = self.partials.remove(index);
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

    /// Releases every partial transaction; its fragment buffers erase themselves on drop.
    pub fn clear(&mut self) {
        for partial in self.partials.drain(..) {
            self.budget.release(partial.total);
        }
    }
}

impl Drop for Reassembler {
    fn drop(&mut self) {
        self.clear();
    }
}
