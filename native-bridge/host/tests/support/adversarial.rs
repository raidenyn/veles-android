//! Verification-only adversarial model shared by `tests/properties.rs` and the separate
//! `fuzz/` workspace (which includes this file by path). It drives only PUBLIC seams: the strict
//! parser (`parse_strict`, `parse_request`, `read_frame`), the fragment/reassembly layer and the
//! deterministic core against the recording fake backend and the real ingress/outqueue.
//!
//! Everything here is a pure function of its input bytes: no wall clock (the clock is an explicit
//! `Instant` plus an advance), no threads, no randomness except the seeded [`Rng`]. An invariant
//! violation panics with a message naming the invariant; the fuzz harness and the property tests
//! both treat a panic as a finding.
//!
//! The core-sequence input is a tiny op language ([`Op`]) so that hand-written seeds (the five
//! Review Focus cases) and arbitrary fuzz bytes share one decoder. Any byte string decodes to some
//! op sequence (missing parameter bytes read as 0).

#![allow(dead_code)]

use std::collections::{BTreeSet, HashMap, HashSet, VecDeque};
use std::io::Cursor as IoCursor;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use veles_ble_host::backend::{BackendEvent, Discovered, Failure, Target as BackendTarget};
use veles_ble_host::buffer::Payload;
use veles_ble_host::core::Input;
use veles_ble_host::fragment::{
    DiscardReason, GlobalBudget, LinkBudget, LinkCapacity, PushOutcome, Reassembler, fragment,
};
use veles_ble_host::framing::read_frame;
use veles_ble_host::limits::{
    DECODED_RECORD_MAX_BYTES, FRAME_MAX_BYTES, GATT_SEND_RECORD_MAX_BYTES,
};
use veles_ble_host::protocol::{ParseFailure, Phase, RequestBody, parse_request};
use veles_ble_host::strict_json::parse_strict;

use super::{Call, Rig, SCAN_UUID};

// ---- seeded determinism ------------------------------------------------------------------------

/// SplitMix64: tiny, fixed, dependency-free. The recorded seeds are inputs of this exact function.
#[derive(Clone, Debug)]
pub struct Rng(pub u64);

impl Rng {
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    pub fn below(&mut self, n: usize) -> usize {
        (self.next_u64() % n.max(1) as u64) as usize
    }

    pub fn byte(&mut self) -> u8 {
        self.next_u64() as u8
    }

    pub fn bytes(&mut self, len: usize) -> Vec<u8> {
        (0..len).map(|_| self.byte()).collect()
    }
}

/// Reads fuzz bytes; an exhausted input yields zeros so every byte string is a complete program.
pub struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, pos: 0 }
    }

    pub fn exhausted(&self) -> bool {
        self.pos >= self.data.len()
    }

    pub fn u8(&mut self) -> u8 {
        let b = self.data.get(self.pos).copied().unwrap_or(0);
        self.pos = (self.pos + 1).min(self.data.len());
        b
    }

    pub fn u16(&mut self) -> u16 {
        u16::from_le_bytes([self.u8(), self.u8()])
    }

    pub fn take(&mut self, n: usize) -> &'a [u8] {
        let end = (self.pos + n).min(self.data.len());
        let out = &self.data[self.pos..end];
        self.pos = end;
        out
    }
}

/// Test records: deterministic content for a (length, seed) pair.
pub fn record_of(len: usize, seed: u8) -> Vec<u8> {
    (0..len)
        .map(|i| seed.wrapping_add((i % 251) as u8) ^ (i >> 8) as u8)
        .collect()
}

// ---- target 1: strict parser / frame reader -----------------------------------------------------

/// Largest input a parser target accepts (the Native Messaging frame ceiling).
pub const PARSER_INPUT_MAX: usize = FRAME_MAX_BYTES;

/// Strict envelope + framing: no panic, deterministic, bounded results. Returns a coarse outcome
/// code (0 uncorrelatable, 1 rejected, 2 parsed) so callers can build a coverage census.
pub fn strict_envelope(data: &[u8]) -> u8 {
    let data = &data[..data.len().min(PARSER_INPUT_MAX)];

    // Raw JSON layer.
    let json = parse_strict(data);
    assert_eq!(
        json,
        parse_strict(data),
        "parse_strict must be deterministic"
    );

    // Envelope layer, both phases.
    let mut outcome = 0;
    for phase in [Phase::PreReady, Phase::Ready { api_version: 1 }] {
        let first = parse_request(data, phase);
        let second = parse_request(data, phase);
        assert_eq!(first, second, "parse_request must be deterministic");
        match first {
            Ok(request) => {
                outcome = 2;
                assert!(
                    (1..=64).contains(&request.request_id.len()),
                    "accepted request id out of bounds"
                );
                if let RequestBody::Send { record, .. } = &request.body {
                    assert!(
                        (1..=DECODED_RECORD_MAX_BYTES).contains(&record.len()),
                        "accepted record outside 1..=5120: {}",
                        record.len()
                    );
                    // Canonical base64 means the record re-encodes into the original text.
                    let encoded = veles_ble_host::base64::encode(record.as_slice());
                    assert!(
                        std::str::from_utf8(data).is_ok_and(|text| text.contains(&encoded)),
                        "accepted record does not re-encode to the wire text"
                    );
                }
            }
            Err(ParseFailure::Rejected(_)) => outcome = outcome.max(1),
            Err(ParseFailure::Uncorrelatable) => {}
        }
    }
    if json.is_err() {
        assert_ne!(
            outcome, 2,
            "an envelope cannot parse when strict JSON rejects"
        );
    }

    // Frame layer: the same bytes as a raw stdin stream; whatever the header declares.
    let mut stream = IoCursor::new(data.to_vec());
    match read_frame(&mut stream) {
        Ok(Some(payload)) => {
            assert!((1..=FRAME_MAX_BYTES).contains(&payload.len()));
            let declared = u32::from_le_bytes(data[..4].try_into().unwrap()) as usize;
            assert_eq!(payload.len(), declared);
        }
        Ok(None) => assert!(data.is_empty()),
        Err(_) => {}
    }
    outcome
}

// ---- target 2: hostile fragment streams (independent reference model) ----------------------------

/// Mirrors the private reassembly ceilings of `src/fragment.rs` (OTP2-GAT-012). Intentionally
/// restated: if the production constants move, this test must fail.
pub const GLOBAL_INCOMPLETE: usize = 16;
pub const GLOBAL_BYTES: usize = 64 * 1024;
pub const PEER_INCOMPLETE: usize = 2;
pub const PEER_BYTES: usize = 8 * 1024;
const NPEERS: usize = 9;
const INACTIVITY: Duration = Duration::from_secs(5);
const CONFLICT_WINDOW: Duration = Duration::from_secs(60);

/// Expected outcome of one pushed frame, in the model's own vocabulary.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MOut {
    Incomplete,
    Complete(Vec<u8>),
    Ignored,
    Discarded(DiscardReason),
    Disconnect,
}

fn observe(outcome: PushOutcome) -> MOut {
    match outcome {
        PushOutcome::Incomplete => MOut::Incomplete,
        PushOutcome::Complete(p) => MOut::Complete(p.as_slice().to_vec()),
        PushOutcome::Ignored => MOut::Ignored,
        PushOutcome::Discarded(r) => MOut::Discarded(r),
        PushOutcome::Disconnect => MOut::Disconnect,
    }
}

struct MPartial {
    id: u8,
    total: usize,
    bytes: Vec<u8>,
    next: u8,
    touched: Instant,
    /// Harness-side provenance of each accepted fragment (never visible to production).
    tags: BTreeSet<u16>,
}

struct MPeer {
    attribute: usize,
    chunk: usize,
    partials: Vec<MPartial>,
    conflicts: VecDeque<Instant>,
}

/// Independent per-peer model of OTP2-GAT-011/012 written from the protocol text and the Android
/// reference semantics, holding its own live partials, byte totals and conflict window. It is the
/// oracle for exact completed bytes and for per-peer/global reserved-byte conservation. It
/// encodes the owner-approved conflict policy (OTP2-GAT-011, "approach 2"): a start (index 0) for
/// an id that is already live erases that partial and is itself rejected; later nonzero
/// continuations find no live partial and are ignored; a fresh index 0 may start; a second
/// conflicting start inside the rolling 60 s window disconnects the peer. Changing this requires
/// the same owner decision as the Android/reference/fixture contract, never a silent edit.
struct RefModel {
    peers: Vec<MPeer>,
    cap_refused: usize,
    /// Live partials erased by a conflicting start (first conflict, or the disconnecting second).
    conflict_erased: usize,
}

impl RefModel {
    fn new(budgets: &[LinkBudget]) -> Self {
        let peer = |b: LinkBudget| MPeer {
            attribute: b.attribute(),
            chunk: b.attribute() - 4,
            partials: Vec::new(),
            conflicts: VecDeque::new(),
        };
        Self {
            peers: budgets.iter().map(|b| peer(*b)).collect(),
            cap_refused: 0,
            conflict_erased: 0,
        }
    }

    fn global(&self) -> (usize, usize) {
        let all = self.peers.iter().flat_map(|p| p.partials.iter());
        (all.clone().count(), all.map(|p| p.total).sum())
    }

    fn peer_bytes(&self, peer: usize) -> usize {
        self.peers[peer].partials.iter().map(|p| p.total).sum()
    }

    fn expire(&mut self, peer: usize, now: Instant) -> usize {
        let before = self.peers[peer].partials.len();
        self.peers[peer]
            .partials
            .retain(|p| now.saturating_duration_since(p.touched) < INACTIVITY);
        before - self.peers[peer].partials.len()
    }

    fn next_expiry(&self, peer: usize) -> Option<Instant> {
        self.peers[peer]
            .partials
            .iter()
            .map(|p| p.touched + INACTIVITY)
            .min()
    }

    fn clear(&mut self, peer: usize) {
        self.peers[peer].partials.clear();
    }

    /// Returns the expected outcome and, for a completion, the provenance tags of its fragments.
    fn push(&mut self, peer: usize, frame: &[u8], now: Instant, tag: u16) -> (MOut, BTreeSet<u16>) {
        let none = BTreeSet::new();
        let (attribute, chunk) = (self.peers[peer].attribute, self.peers[peer].chunk);
        if frame.len() < 4 || frame.len() > attribute {
            self.clear(peer);
            return (MOut::Discarded(DiscardReason::FrameSize), none);
        }
        let index = frame[0] & 0x7f;
        let last = frame[0] & 0x80 != 0;
        let id = frame[1];
        let declared = usize::from(u16::from_be_bytes([frame[2], frame[3]]));
        if id == 0 {
            self.clear(peer);
            return (MOut::Discarded(DiscardReason::ZeroTransaction), none);
        }
        self.expire(peer, now);
        let live = self.peers[peer].partials.iter().any(|p| p.id == id);
        if index == 0 && live {
            self.peers[peer].partials.retain(|p| p.id != id);
            self.conflict_erased += 1;
            let window = &mut self.peers[peer].conflicts;
            while window
                .front()
                .is_some_and(|at| now.duration_since(*at) >= CONFLICT_WINDOW)
            {
                window.pop_front();
            }
            window.push_back(now);
            if window.len() >= 2 {
                self.clear(peer);
                return (MOut::Disconnect, none);
            }
            return (MOut::Ignored, none);
        }
        if index == 0 {
            if !(1..=GATT_SEND_RECORD_MAX_BYTES).contains(&declared) {
                self.clear(peer);
                return (MOut::Discarded(DiscardReason::TotalLength), none);
            }
            let (n, bytes) = self.global();
            let fits = self.peers[peer].partials.len() < PEER_INCOMPLETE
                && self.peer_bytes(peer) + declared <= PEER_BYTES
                && n < GLOBAL_INCOMPLETE
                && bytes + declared <= GLOBAL_BYTES;
            if !fits {
                self.cap_refused += 1;
                return (MOut::Ignored, none);
            }
            self.peers[peer].partials.push(MPartial {
                id,
                total: declared,
                bytes: Vec::new(),
                next: 0,
                touched: now,
                tags: BTreeSet::new(),
            });
        }
        let Some(pos) = self.peers[peer].partials.iter().position(|p| p.id == id) else {
            return (MOut::Ignored, none);
        };
        let payload = frame.len() - 4;
        let partial = &self.peers[peer].partials[pos];
        let remaining = partial.total - partial.bytes.len();
        let ordered = partial.next == index && (index == 0 || declared == 0);
        let size_ok = if last {
            payload == remaining
        } else {
            payload == chunk && index < 127 && payload <= remaining
        };
        if !ordered {
            self.clear(peer);
            return (MOut::Discarded(DiscardReason::Order), none);
        }
        if !size_ok {
            self.clear(peer);
            return (MOut::Discarded(DiscardReason::Payload), none);
        }
        let partial = &mut self.peers[peer].partials[pos];
        partial.bytes.extend_from_slice(&frame[4..]);
        partial.next += 1;
        partial.touched = now;
        partial.tags.insert(tag);
        if !last {
            return (MOut::Incomplete, none);
        }
        let done = self.peers[peer].partials.remove(pos);
        (MOut::Complete(done.bytes), done.tags)
    }
}

/// Branch counters of one `fragment_stream` run (merged by the property tests).
#[derive(Clone, Debug, Default)]
pub struct FragCensus {
    pub pushes: usize,
    pub incomplete: usize,
    pub complete: usize,
    /// Completions whose bytes came from more than one source. Still possible by design: a bare
    /// next-index continuation (no conflicting START) injected into a live prefix extends it, and
    /// the transport cannot tell it from the peer's own fragment (higher-layer handling is outside
    /// this harness). A conflicting START no longer enables it: it erases the live partial first;
    /// that path is pinned by dedicated tests and by the exact-bytes lockstep with `RefModel`.
    pub spliced: usize,
    /// Live partials erased by a conflicting same-id start.
    pub conflict_erased: usize,
    pub ignored: usize,
    pub disconnect: usize,
    pub discarded: [usize; 5],
    pub expired: usize,
    /// Pushes ignored because a cap (per-peer or global) refused a new transaction.
    pub cap_refused: usize,
    pub peak_global_count: usize,
    pub peak_global_bytes: usize,
    pub peak_peer_bytes: usize,
}

impl FragCensus {
    pub fn merge(&mut self, o: &FragCensus) {
        self.pushes += o.pushes;
        self.incomplete += o.incomplete;
        self.complete += o.complete;
        self.spliced += o.spliced;
        self.conflict_erased += o.conflict_erased;
        self.ignored += o.ignored;
        self.disconnect += o.disconnect;
        for (a, b) in self.discarded.iter_mut().zip(o.discarded) {
            *a += b;
        }
        self.expired += o.expired;
        self.cap_refused += o.cap_refused;
        self.peak_global_count = self.peak_global_count.max(o.peak_global_count);
        self.peak_global_bytes = self.peak_global_bytes.max(o.peak_global_bytes);
        self.peak_peer_bytes = self.peak_peer_bytes.max(o.peak_peer_bytes);
    }
}

fn reason_slot(r: DiscardReason) -> usize {
    match r {
        DiscardReason::FrameSize => 0,
        DiscardReason::ZeroTransaction => 1,
        DiscardReason::TotalLength => 2,
        DiscardReason::Order => 3,
        DiscardReason::Payload => 4,
    }
}

struct Pool {
    tag: u16,
    record: Vec<u8>,
    frames: Vec<Vec<Payload>>,
}

fn make_pool(r: &mut Reader<'_>, budgets: &[LinkBudget], next_tag: &mut u16) -> Pool {
    *next_tag += 1;
    let tag = *next_tag;
    // Half of the records share one length so same-id conflicts and attempted splices can reach an equal declared total.
    let len = if r.u8() & 1 == 0 {
        1000
    } else {
        1 + usize::from(r.u16()) % GATT_SEND_RECORD_MAX_BYTES
    };
    // Many entries share a small id set so same-id conflicts and continuations are common.
    let id = 1 + r.u8() % 3;
    let record = record_of(len, r.u8());
    let frames = budgets
        .iter()
        .map(|b| fragment(&record, *b, id).expect("valid record"))
        .collect();
    Pool {
        tag,
        record,
        frames,
    }
}

/// Interprets `data` as a hostile fragment program over two reassemblers sharing one global
/// budget with an explicit clock, in lockstep with [`RefModel`]. Every outcome (including the exact
/// bytes of every completed record), every expiry count, `next_expiry`, per-peer partial counts and
/// the global reserved count/bytes must equal the model after every operation; per-peer reserved
/// bytes are bounded by the model's independent sums.
pub fn fragment_stream(data: &[u8]) -> FragCensus {
    let mut r = Reader::new(data);
    // Nine peers sharing one budget so the 16-transaction global cap (8 peers x 2) is reachable.
    let budgets: Vec<LinkBudget> = (0..NPEERS).map(|_| pick_budget(&mut r)).collect();
    let global: Arc<GlobalBudget> = GlobalBudget::new();
    let mut peers: Vec<Reassembler> = budgets
        .iter()
        .map(|b| Reassembler::new(*b, global.clone()))
        .collect();
    let mut model = RefModel::new(&budgets);
    let mut next_tag = 0u16;
    let mut pool: Vec<Pool> = (0..4)
        .map(|_| make_pool(&mut r, &budgets, &mut next_tag))
        .collect();
    // Provenance tag -> the exact record that generation of the pool slot fragmented.
    let mut records: HashMap<u16, Vec<u8>> =
        pool.iter().map(|p| (p.tag, p.record.clone())).collect();
    let mut now = Instant::now();
    let mut census = FragCensus::default();
    let mut steps = 0;
    while !r.exhausted() && steps < 4096 {
        steps += 1;
        let peer = usize::from(r.u8()) % NPEERS;
        // Frames to push this step: (bytes, provenance tag).
        let mut frames: Vec<(usize, Vec<u8>, u16)> = Vec::new();
        match r.u8() % 20 {
            // A raw hostile frame of an arbitrary shape.
            0 => {
                let len = usize::from(r.u16()) % 600;
                frames.push((peer, r.take(len).to_vec(), 0));
            }
            // A header-shaped frame (index/last/id/total) with arbitrary body.
            1 => {
                let (index, id) = (r.u8(), r.u8() % 4);
                let total = r.u16() % 5200;
                let body_len = usize::from(r.u8()) * 2 % 520;
                let mut frame = vec![index, id];
                frame.extend_from_slice(&total.to_be_bytes());
                frame.extend_from_slice(r.take(body_len));
                frames.push((peer, frame, 0));
            }
            // One genuine fragment of a pooled record, at any position (reorder/duplicate/skip).
            2..=3 => {
                let j = usize::from(r.u8()) % pool.len();
                let list = &pool[j].frames[peer];
                let k = usize::from(r.u8()) % list.len();
                frames.push((peer, list[k].as_slice().to_vec(), pool[j].tag));
            }
            // A run of genuine fragments of one pooled record, in order, possibly truncated.
            4..=7 => {
                let j = usize::from(r.u8()) % pool.len();
                let list = &pool[j].frames[peer];
                let from = usize::from(r.u8()) % list.len();
                let cut = usize::from(r.u8() % 3).min(list.len() - from - 1);
                for frame in &list[from..list.len() - cut] {
                    frames.push((peer, frame.as_slice().to_vec(), pool[j].tag));
                }
            }
            // Only the opening fragment of a pooled record: builds up live transactions so the
            // per-peer and global caps and same-id conflicts are reached.
            8..=12 | 17 => {
                let j = usize::from(r.u8()) % pool.len();
                frames.push((
                    peer,
                    pool[j].frames[peer][0].as_slice().to_vec(),
                    pool[j].tag,
                ));
            }
            // Explicit clock advancement around the 5 s inactivity and 60 s conflict windows.
            13 | 14 => {
                now += [
                    Duration::ZERO,
                    Duration::from_millis(1),
                    Duration::from_millis(4_999),
                    Duration::from_secs(5),
                    Duration::from_secs(45),
                    Duration::from_secs(60),
                    Duration::from_secs(61),
                ][usize::from(r.u8() % 7)];
                let dropped = peers[peer].expire(now);
                assert_eq!(dropped, model.expire(peer, now), "expired count differs");
                census.expired += dropped;
            }
            15 => {
                if r.u8() & 3 == 0 {
                    peers[peer].clear();
                    model.clear(peer);
                } else {
                    let j = usize::from(r.u8()) % pool.len();
                    pool[j] = make_pool(&mut r, &budgets, &mut next_tag);
                    records.insert(pool[j].tag, pool[j].record.clone());
                }
            }
            // Same-id conflict then continuation: open record j, open record j2 (a conflicting
            // start when ids match, which erases j's partial), then deliver j2's remaining
            // fragments in order (ignored when j2's start was a conflict; no splice can result).
            18 | 19 => {
                let j = usize::from(r.u8()) % pool.len();
                let j2 = usize::from(r.u8()) % pool.len();
                frames.push((
                    peer,
                    pool[j].frames[peer][0].as_slice().to_vec(),
                    pool[j].tag,
                ));
                for frame in &pool[j2].frames[peer] {
                    frames.push((peer, frame.as_slice().to_vec(), pool[j2].tag));
                }
            }
            // Fill burst: open one transaction of each distinct id on EVERY peer (drives the
            // 16-transaction global cap).
            _ => {
                for p in 0..NPEERS {
                    let mut seen = BTreeSet::new();
                    for (j, entry) in pool.iter().enumerate() {
                        if seen.insert(entry.frames[p][0].as_slice()[1]) {
                            frames.push((p, entry.frames[p][0].as_slice().to_vec(), pool[j].tag));
                        }
                    }
                }
            }
        }
        for (peer, frame, tag) in frames {
            census.pushes += 1;
            let (expected, tags) = model.push(peer, &frame, now, tag);
            let got = observe(peers[peer].push(&frame, now));
            assert_eq!(got, expected, "outcome/bytes differ for frame {frame:02x?}");
            match &got {
                MOut::Incomplete => census.incomplete += 1,
                MOut::Complete(bytes) => {
                    assert!((1..=GATT_SEND_RECORD_MAX_BYTES).contains(&bytes.len()));
                    census.complete += 1;
                    let sources: BTreeSet<u16> = tags.iter().copied().filter(|t| *t != 0).collect();
                    if sources.len() > 1 || tags.contains(&0) {
                        census.spliced += 1;
                    }
                    // Genuine single-source completions are exactly their pooled record.
                    if tags.len() == 1
                        && let Some(&t) = tags.iter().next()
                        && t != 0
                    {
                        assert_eq!(
                            bytes, &records[&t],
                            "single-source completion differs from its record"
                        );
                    }
                }
                MOut::Ignored => census.ignored += 1,
                MOut::Discarded(reason) => census.discarded[reason_slot(*reason)] += 1,
                MOut::Disconnect => census.disconnect += 1,
            }
            check_state(&peers, &model, &global, &mut census);
        }
        let expiry_got = peers[peer].next_expiry();
        assert_eq!(expiry_got, model.next_expiry(peer), "next_expiry differs");
        check_state(&peers, &model, &global, &mut census);
    }
    census.cap_refused = model.cap_refused;
    census.conflict_erased = model.conflict_erased;
    for (i, p) in peers.iter_mut().enumerate() {
        p.clear();
        model.clear(i);
    }
    assert_eq!(
        global.usage(),
        (0, 0),
        "budget not returned after clearing every peer"
    );
    drop(peers);
    assert_eq!(global.usage(), (0, 0));
    census
}

fn check_state(peers: &[Reassembler], model: &RefModel, global: &GlobalBudget, c: &mut FragCensus) {
    let (n, bytes) = model.global();
    assert_eq!(
        global.usage(),
        (n, bytes),
        "global reserved count/bytes differ from the model"
    );
    assert!(
        n <= GLOBAL_INCOMPLETE && bytes <= GLOBAL_BYTES,
        "global budget exceeded"
    );
    for (i, peer) in peers.iter().enumerate() {
        assert_eq!(
            peer.incomplete(),
            model.peers[i].partials.len(),
            "peer {i} partial count"
        );
        assert!(
            peer.incomplete() <= PEER_INCOMPLETE,
            "per-peer incomplete cap exceeded"
        );
        assert!(
            model.peer_bytes(i) <= PEER_BYTES,
            "per-peer byte cap exceeded"
        );
        c.peak_peer_bytes = c.peak_peer_bytes.max(model.peer_bytes(i));
    }
    c.peak_global_count = c.peak_global_count.max(n);
    c.peak_global_bytes = c.peak_global_bytes.max(bytes);
    if n == 0 {
        assert_eq!(bytes, 0);
    }
}

fn pick_budget(r: &mut Reader<'_>) -> LinkBudget {
    match r.u8() % 3 {
        0 => LinkBudget::from_att_mtu(185 + usize::from(r.u16()) % 333).unwrap(),
        1 => LinkBudget::from_max_write_without_response(512 + usize::from(r.u8())).unwrap(),
        _ => LinkBudget::from_att_mtu(185).unwrap(),
    }
}

// ---- target 3: core request/backend/cancel sequences --------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Op {
    Hello,
    /// Answer the oldest unanswered backend submission positively (scan started / connected at a
    /// valid capacity / write done); `pick` rotates which category is served first.
    Auto {
        pick: u8,
    },
    StartScan,
    StopScan {
        pick: u8,
    },
    Connect {
        hint: u8,
    },
    Disconnect {
        pick: u8,
    },
    Send {
        pick: u8,
        len: u16,
        dup_live_id: bool,
    },
    /// A backend completion: `kind` selects the event, `pick` the (possibly stale) token.
    Backend {
        kind: u8,
        pick: u8,
        arg: u16,
    },
    /// A fragment on a (possibly stale) link: `hostile` selects a malformed shape.
    Inbound {
        pick: u8,
        len: u16,
        txn: u8,
        hostile: u8,
    },
    /// Advance the explicit clock by `centis * 10 ms` and tick.
    Advance {
        centis: u16,
    },
    Shutdown,
    /// Process-level terminals: 0 EOF, 1 stdout closed, 2 framing, 3 stdin I/O, 4 overflow latch.
    Terminate {
        kind: u8,
    },
}

pub const HINTS: [&str; 6] = [
    "phone-a",
    "phone-b",
    "phone-a#k2",
    "phone-b#k1",
    "",
    "phone-c",
];

/// Wire value of record length 4,096 in [`Op::Send::len`] (see [`send_len`]).
pub const LEN_4096: u16 = 0x1004;
/// Wire value of a 4,097-byte record (first oversize byte) and of 5,120.
pub const LEN_4097: u16 = 0x2000;
pub const LEN_5120: u16 = 0x3000;

/// `len` classes: `<4096` is the exact length (0 -> 1); `0x1xxx` boundary list; `0x2xxx` 4,097;
/// `0x3xxx` 5,120; the rest a pseudo length from the low 12 bits.
pub fn send_len(len: u16) -> usize {
    let low = usize::from(len & 0x0FFF);
    match len >> 12 {
        0 => low.max(1),
        1 => [1, 181, 182, 183, GATT_SEND_RECORD_MAX_BYTES][low % 5],
        2 => GATT_SEND_RECORD_MAX_BYTES + 1,
        3 => DECODED_RECORD_MAX_BYTES,
        _ => 1 + low,
    }
}

impl Op {
    pub fn decode(r: &mut Reader<'_>) -> Op {
        match r.u8() {
            0xFD => Op::Hello,
            // Rare process-level terminals/shutdown so random programs reach deep states.
            0xFF => match r.u8() {
                kind @ 0..=15 => Op::Terminate { kind },
                _ => Op::Hello,
            },
            0xFE => match r.u8() {
                0..=15 => Op::Shutdown,
                _ => Op::Hello,
            },
            b => match b % 12 {
                0 => Op::StartScan,
                1 => Op::StopScan { pick: r.u8() },
                2 | 11 => Op::Connect { hint: r.u8() },
                3 => Op::Disconnect { pick: r.u8() },
                4 | 10 => Op::Send {
                    pick: r.u8(),
                    len: r.u16(),
                    dup_live_id: r.u8() == 0xFF,
                },
                5 | 9 => Op::Backend {
                    kind: r.u8(),
                    pick: r.u8(),
                    arg: r.u16(),
                },
                6 => Op::Inbound {
                    pick: r.u8(),
                    len: r.u16(),
                    txn: r.u8(),
                    hostile: r.u8(),
                },
                7 => Op::Advance { centis: r.u16() },
                8 => Op::Auto { pick: r.u8() },
                _ => Op::Hello,
            },
        }
    }

    pub fn encode(&self, out: &mut Vec<u8>) {
        let le = |v: u16| v.to_le_bytes();
        match self {
            Op::StartScan => out.push(0),
            Op::StopScan { pick } => out.extend([1, *pick]),
            Op::Connect { hint } => out.extend([2, *hint]),
            Op::Disconnect { pick } => out.extend([3, *pick]),
            Op::Send {
                pick,
                len,
                dup_live_id,
            } => out.extend([
                4,
                *pick,
                le(*len)[0],
                le(*len)[1],
                if *dup_live_id { 0xFF } else { 0 },
            ]),
            Op::Backend { kind, pick, arg } => {
                out.extend([5, *kind, *pick, le(*arg)[0], le(*arg)[1]])
            }
            Op::Inbound {
                pick,
                len,
                txn,
                hostile,
            } => out.extend([6, *pick, le(*len)[0], le(*len)[1], *txn, *hostile]),
            Op::Advance { centis } => out.extend([7, le(*centis)[0], le(*centis)[1]]),
            Op::Hello => out.push(0xFD),
            Op::Auto { pick } => out.extend([8, *pick]),
            Op::Shutdown => out.extend([0xFE, 0]),
            Op::Terminate { kind } => out.extend([0xFF, *kind % 16]),
        }
    }
}

pub fn encode_ops(ops: &[Op]) -> Vec<u8> {
    let mut out = Vec::new();
    for op in ops {
        op.encode(&mut out);
    }
    out
}

/// Backend-event kinds of [`Op::Backend`].
pub mod kind {
    pub const SCAN_STARTED: u8 = 0;
    pub const SCAN_FAILED: u8 = 1;
    pub const SCAN_ENDED: u8 = 2;
    pub const ADVERTISEMENT: u8 = 3;
    pub const CONNECTED: u8 = 4;
    pub const CONNECT_FAILED: u8 = 5;
    pub const CAPACITY_CHANGED: u8 = 6;
    pub const WRITE_DONE: u8 = 7;
    pub const WRITE_FAILED: u8 = 8;
    pub const LINK_LOST: u8 = 9;
    pub const LINK_RELEASED: u8 = 10;
    pub const SHUTDOWN_COMPLETE: u8 = 11;
    pub const COUNT: u8 = 12;
}

/// Decoded sequence cap (ops) — bounds a single fuzz iteration.
pub const MAX_OPS: usize = 512;
/// `Op::Backend` argument that selects `ShutdownComplete` in the shutdown slot.
pub const SHUTDOWN_MAGIC: u16 = 0xBEEF;

pub fn core_sequence(data: &[u8]) -> Summary {
    let mut r = Reader::new(data);
    // Every random program starts ready (an implicit hello): otherwise almost every byte string
    // dies at the first non-hello frame and never reaches a link.
    let mut ops = vec![Op::Hello];
    while !r.exhausted() && ops.len() < MAX_OPS {
        ops.push(Op::decode(&mut r));
    }
    run_ops(&ops)
}

impl Summary {
    pub fn bump(&mut self, key: impl Into<String>) {
        *self.census.entry(key.into()).or_insert(0) += 1;
    }

    pub fn merge(&mut self, other: &Summary) {
        for (k, v) in &other.census {
            *self.census.entry(k.clone()).or_insert(0) += v;
        }
    }

    pub fn count(&self, key: &str) -> usize {
        self.census.get(key).copied().unwrap_or(0)
    }
}

/// What a run exercised (for coverage census; never part of an invariant).
#[derive(Clone, Debug, Default)]
pub struct Summary {
    /// Named branch counters (event types, error codes, ingress statuses, exits, ...). Used by the
    /// property tests to prove random programs actually reach each security-relevant branch.
    pub census: std::collections::BTreeMap<String, usize>,
    pub events: usize,
    pub sent: usize,
    pub connected: usize,
    pub errors: usize,
    pub exit: Option<i32>,
}

enum Effect {
    BrokenCapacity,
    Closing,
    Released,
}

struct Link {
    handle: Option<String>,
    budget: Option<LinkBudget>,
    identity: String,
    released: bool,
    closing: bool,
    broken_capacity: bool,
}

struct Model {
    rig: Rig,
    next_id: u32,
    issued: HashMap<String, &'static str>,
    terminals: HashMap<String, u32>,
    /// request id -> backend token it created (scan / connection).
    token_of_request: HashMap<String, u64>,
    send_target: HashMap<String, String>,
    pending_send_handle: Option<String>,
    scan_handles: Vec<String>,
    conn_handles: Vec<String>,
    all_handles: HashSet<String>,
    handle_token: HashMap<String, u64>,
    links: HashMap<u64, Link>,
    scans: Vec<u64>,
    sends: Vec<(u64, u64)>,
    seen_calls: usize,
    tokens_seen: HashSet<(u8, u64)>,
    shutdown_submissions: usize,
    exited: bool,
    expect_live_dup_exit: bool,
    shutdown_id: Option<String>,
    answered: HashSet<(u8, u64)>,
    summary: Summary,
}

fn is_hex_handle(text: &str) -> bool {
    text.len() == 16 && text.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

pub fn run_ops(ops: &[Op]) -> Summary {
    let mut m = Model {
        rig: Rig::new(),
        next_id: 0,
        issued: HashMap::new(),
        terminals: HashMap::new(),
        token_of_request: HashMap::new(),
        send_target: HashMap::new(),
        pending_send_handle: None,
        scan_handles: Vec::new(),
        conn_handles: Vec::new(),
        all_handles: HashSet::new(),
        handle_token: HashMap::new(),
        links: HashMap::new(),
        scans: Vec::new(),
        sends: Vec::new(),
        seen_calls: 0,
        tokens_seen: HashSet::new(),
        shutdown_submissions: 0,
        exited: false,
        expect_live_dup_exit: false,
        shutdown_id: None,
        answered: HashSet::new(),
        summary: Summary::default(),
    };
    for op in ops {
        let was_running = m.rig.exit.is_none();
        m.step(op);
        m.after_step();
        if was_running && let Some(code) = m.rig.exit {
            let name = format!("{op:?}");
            let name = name.split([' ', '{']).next().unwrap_or("?");
            m.summary.bump(format!("exit_by:{name}:{code}"));
        }
        if std::mem::take(&mut m.expect_live_dup_exit) {
            assert_eq!(
                m.rig.exit,
                Some(2),
                "a duplicate live request id must exit 2"
            );
            m.summary.bump("live_dup_exit2");
        }
    }
    m.finish();
    m.summary
}

impl Model {
    fn id(&mut self, kind: &'static str) -> String {
        self.next_id += 1;
        let id = format!("r{}", self.next_id);
        self.issued.insert(id.clone(), kind);
        id
    }

    fn pick_handle(list: &[String], pick: u8) -> String {
        if pick >= 192 || list.is_empty() {
            // Unknown, uppercase, or malformed.
            ["0000000000000000", "ABCDEF0123456789", "zz"][usize::from(pick) % 3].to_string()
        } else {
            list[usize::from(pick) % list.len()].clone()
        }
    }

    fn pick_token(list: &[u64], pick: u8) -> u64 {
        if pick >= 192 || list.is_empty() {
            u64::from(pick) // arbitrary, usually stale / never allocated
        } else {
            list[usize::from(pick) % list.len()]
        }
    }

    fn conn_tokens(&self) -> Vec<u64> {
        let mut tokens: Vec<u64> = self.links.keys().copied().collect();
        tokens.sort_unstable();
        tokens
    }

    fn step(&mut self, op: &Op) {
        match op {
            Op::Auto { pick } => self.auto(*pick),
            Op::Hello => {
                let id = self.id("hello");
                self.rig.frame(json!({
                    "envelope_version": 1, "type": "hello", "request_id": id, "api_versions": [1]
                }));
            }
            Op::StartScan => {
                let id = self.id("start_scan");
                self.rig
                    .req("start_scan", &id, json!({"service_uuid": SCAN_UUID}));
            }
            Op::StopScan { pick } => {
                let id = self.id("stop_scan");
                let handle = Self::pick_handle(&self.scan_handles, *pick);
                self.rig
                    .req("stop_scan", &id, json!({"scan_handle": handle}));
            }
            Op::Connect { hint } => {
                let id = self.id("connect");
                let hint = HINTS[usize::from(*hint) % HINTS.len()];
                self.rig.req("connect", &id, json!({"platform_hint": hint}));
            }
            Op::Disconnect { pick } => {
                let id = self.id("disconnect");
                let handle = Self::pick_handle(&self.conn_handles, *pick);
                self.rig
                    .req("disconnect", &id, json!({"connection_handle": handle}));
            }
            Op::Send {
                pick,
                len,
                dup_live_id,
            } => {
                let handle = Self::pick_handle(&self.conn_handles, *pick);
                let len = send_len(*len);
                // A duplicated LIVE id: reuse the previous request's id before its terminal.
                let id = if *dup_live_id && self.next_id > 0 {
                    let id = format!("r{}", self.next_id);
                    // Once a shutdown was issued, admission is stopped and other parseable frames are
                    // silently ignored (core.rs `on_frame`), so only the shutdown's own id is still
                    // "live" then.
                    let silent = self.shutdown_id.as_deref().is_some_and(|s| s != id);
                    if self.terminals.remove(&id).is_none() && !self.exited && !silent {
                        // Still live: the host must fail closed (exit 2) with no second response.
                        self.expect_live_dup_exit = true;
                    }
                    id
                } else {
                    self.id("send")
                };
                self.pending_send_handle = Some(handle.clone());
                self.send_target.insert(id.clone(), handle.clone());
                let record = record_of(len, self.next_id as u8);
                self.rig.req(
                    "send",
                    &id,
                    json!({"connection_handle": handle,
                           "record_base64": veles_ble_host::base64::encode(&record)}),
                );
            }
            Op::Backend { kind: k, pick, arg } => self.backend(*k % kind::COUNT, *pick, *arg),
            Op::Inbound {
                pick,
                len,
                txn,
                hostile,
            } => {
                let tokens = self.conn_tokens();
                let conn = Self::pick_token(&tokens, *pick);
                let budget = self
                    .links
                    .get(&conn)
                    .and_then(|l| l.budget)
                    .unwrap_or(LinkBudget::from_att_mtu(185).unwrap());
                let len = 1 + usize::from(*len) % GATT_SEND_RECORD_MAX_BYTES;
                let txn = txn.max(&1);
                let frames: Vec<Payload> = match hostile % 6 {
                    // Well-formed record.
                    0 => fragment(&record_of(len, 7), budget, *txn).unwrap(),
                    // Oversized single frame.
                    1 => vec![Payload::new(
                        vec![0x80, *txn, 0, 1, 1]
                            .into_iter()
                            .chain(std::iter::repeat_n(0xEE, 520))
                            .collect(),
                    )],
                    // Garbage header: zero transaction.
                    2 => vec![Payload::new(vec![0x80, 0, 0, 4, 1, 2, 3, 4])],
                    // Index-0 conflict twice (starts then restarts the same id).
                    3 => {
                        let mut frames =
                            fragment(&record_of(len.max(budget.payload() + 1), 9), budget, *txn)
                                .unwrap();
                        frames.truncate(1);
                        let again = frames[0].clone();
                        frames.push(again.clone());
                        frames.push(again);
                        frames
                    }
                    // Truncated record (drop the final fragment).
                    4 => {
                        let mut frames =
                            fragment(&record_of(len.max(budget.payload() + 1), 3), budget, *txn)
                                .unwrap();
                        frames.pop();
                        frames
                    }
                    // Flood: many index-0 starts of distinct transactions.
                    _ => (1..=24u8)
                        .map(|t| Payload::new(vec![0, t, 0x0F, 0xFF, 0xAA]))
                        .collect(),
                };
                for frame in frames {
                    let status = self
                        .rig
                        .inject_status(BackendEvent::Fragment { conn, bytes: frame });
                    self.summary.bump(format!("inject:{status:?}"));
                    self.rig.pump_ingress();
                    self.drain_and_check(Some(conn));
                }
            }
            Op::Advance { centis } => {
                let by = Duration::from_millis(u64::from(*centis) * 10);
                self.rig.advance(by);
            }
            Op::Shutdown => {
                let id = self.id("shutdown");
                self.shutdown_id.get_or_insert_with(|| id.clone());
                self.rig.req("shutdown", &id, json!({}));
            }
            Op::Terminate { kind } => match kind % 5 {
                0 => self.rig.input(Input::StdinEof),
                1 => self.rig.input(Input::StdoutClosed),
                2 => self.rig.input(Input::StdinFraming),
                3 => self.rig.input(Input::StdinIoError),
                _ => self.rig.input(Input::BackendOverflow),
            },
        }
    }

    fn auto(&mut self, pick: u8) {
        let conns = self.conn_tokens();
        let mut order = [0u8, 1, 2];
        order.rotate_left(usize::from(pick) % 3);
        for category in order {
            match category {
                0 => {
                    let found = conns.iter().position(|c| {
                        let l = &self.links[c];
                        l.budget.is_none()
                            && !l.closing
                            && !l.released
                            && !self.answered.contains(&(1, *c))
                    });
                    if let Some(i) = found {
                        self.answered.insert((1, conns[i]));
                        return self.backend(kind::CONNECTED, i as u8, u16::from(pick) << 2);
                    }
                }
                1 => {
                    let found = self
                        .sends
                        .iter()
                        .position(|(_, s)| !self.answered.contains(&(2, *s)));
                    if let Some(i) = found {
                        self.answered.insert((2, self.sends[i].1));
                        return self.backend(kind::WRITE_DONE, i as u8, 0);
                    }
                }
                _ => {
                    let found = self
                        .scans
                        .iter()
                        .position(|s| !self.answered.contains(&(0, *s)));
                    if let Some(i) = found {
                        self.answered.insert((0, self.scans[i]));
                        return self.backend(kind::SCAN_STARTED, i as u8, 0);
                    }
                }
            }
        }
        self.summary.bump("auto_nothing_pending");
    }

    fn backend(&mut self, k: u8, pick: u8, arg: u16) {
        let conns = self.conn_tokens();
        let scans = self.scans.clone();
        let sends: Vec<u64> = self.sends.iter().map(|s| s.1).collect();
        let mut effect: Option<(u64, Effect)> = None;
        let event = match k {
            kind::SCAN_STARTED => BackendEvent::ScanStarted {
                scan: Self::pick_token(&scans, pick),
            },
            kind::SCAN_FAILED => BackendEvent::ScanFailed {
                scan: Self::pick_token(&scans, pick),
                failure: Failure::new("fuzz_scan", arg & 1 == 0),
            },
            kind::SCAN_ENDED => {
                let scan = Self::pick_token(&scans, pick);
                BackendEvent::ScanEnded {
                    scan,
                    failure: (arg & 1 == 1).then(|| Failure::new("fuzz_end", false)),
                }
            }
            kind::ADVERTISEMENT => BackendEvent::Advertisement {
                scan: Self::pick_token(&scans, pick),
                device: Discovered {
                    platform_hint: HINTS[usize::from(arg) % HINTS.len()].to_string(),
                    label: format!("dev{arg}"),
                    advertisement: (arg & 2 == 2).then(|| vec![0xAB; usize::from(arg % 300)]),
                },
            },
            kind::CONNECTED => {
                let conn = Self::pick_token(&conns, pick);
                let capacity = match arg % 4 {
                    0 => LinkCapacity::AttMtu(185 + usize::from(arg >> 2) % 333),
                    1 => LinkCapacity::AttMtu(100 + usize::from(arg >> 2) % 700),
                    2 => LinkCapacity::MaxWriteWithoutResponse(480 + usize::from(arg >> 2) % 64),
                    _ => LinkCapacity::AttMtu(185),
                };
                if let Some(link) = self.links.get_mut(&conn)
                    && link.budget.is_none()
                {
                    link.budget = LinkBudget::from_capacity(capacity).ok();
                }
                BackendEvent::Connected {
                    conn,
                    capacity,
                    platform_hint: None,
                }
            }
            kind::CONNECT_FAILED => BackendEvent::ConnectFailed {
                conn: Self::pick_token(&conns, pick),
                failure: Failure::new("fuzz_connect", arg & 1 == 0),
            },
            kind::CAPACITY_CHANGED => {
                let conn = Self::pick_token(&conns, pick);
                let capacity = match arg % 3 {
                    0 => LinkCapacity::AttMtu(185 + usize::from(arg >> 2) % 333),
                    1 => LinkCapacity::MaxWriteWithoutResponse(400 + usize::from(arg >> 2) % 200),
                    _ => LinkCapacity::AttMtu(517),
                };
                if let Some(link) = self.links.get(&conn)
                    && let Some(frozen) = link.budget
                    && LinkBudget::from_capacity(capacity) != Ok(frozen)
                {
                    effect = Some((conn, Effect::BrokenCapacity));
                }
                BackendEvent::CapacityChanged { conn, capacity }
            }
            kind::WRITE_DONE | kind::WRITE_FAILED => {
                let send = Self::pick_token(&sends, pick);
                let conn = self
                    .sends
                    .iter()
                    .find(|(_, s)| *s == send)
                    .map_or(u64::from(arg), |(c, _)| *c);
                if k == kind::WRITE_DONE {
                    BackendEvent::WriteDone { conn, send }
                } else {
                    BackendEvent::WriteFailed {
                        conn,
                        send,
                        failure: Failure::new("fuzz_write", false),
                    }
                }
            }
            kind::LINK_LOST => {
                let conn = Self::pick_token(&conns, pick);
                effect = Some((conn, Effect::Closing));
                BackendEvent::LinkLost { conn }
            }
            kind::LINK_RELEASED => {
                let conn = Self::pick_token(&conns, pick);
                effect = Some((conn, Effect::Released));
                BackendEvent::LinkReleased { conn }
            }
            // A stray process-wide terminal fails the host closed (exit 1), so random programs
            // emit it only with the magic argument; otherwise this slot is an advertisement.
            _ if arg == SHUTDOWN_MAGIC => BackendEvent::ShutdownComplete,
            _ => BackendEvent::Advertisement {
                scan: Self::pick_token(&scans, pick),
                device: Discovered {
                    platform_hint: "phone-c".to_string(),
                    label: "x".to_string(),
                    advertisement: None,
                },
            },
        };
        let target_conn = match &event {
            BackendEvent::Fragment { conn, .. }
            | BackendEvent::WriteDone { conn, .. }
            | BackendEvent::WriteFailed { conn, .. } => Some(*conn),
            _ => None,
        };
        let status = self.rig.inject_status(event);
        self.summary.bump(format!("inject:{status:?}"));
        if status == veles_ble_host::ingress::EmitStatus::Accepted
            && let Some((conn, effect)) = effect
            && let Some(link) = self.links.get_mut(&conn)
        {
            match effect {
                Effect::BrokenCapacity => {
                    link.broken_capacity = true;
                    self.summary.bump("capacity_break_accepted");
                }
                Effect::Closing => link.closing = true,
                Effect::Released => link.released = true,
            }
        }
        self.rig.pump_ingress();
        // Deliver, then run the model checks with the injected link as the attribution subject.
        self.drain_and_check(target_conn);
    }

    /// Drains the output queue and validates each event. `subject` is the link a just-injected
    /// backend event belongs to (cross-handle attribution).
    fn drain_and_check(&mut self, subject: Option<u64>) {
        self.consume_calls();
        let events = self.rig.events();
        if self.exited && !events.is_empty() {
            panic!("event emitted after the core exited: {events:?}");
        }
        for event in &events {
            self.check_event(event, subject);
        }
        if self.rig.exit.is_some() {
            self.exited = true;
        }
        if let Some(code) = self.rig.exit
            && self.summary.exit.is_none()
        {
            self.summary.bump(format!("exit:{code}"));
        }
        self.summary.exit = self.rig.exit;
    }

    fn after_step(&mut self) {
        self.drain_and_check(None);
        self.pending_send_handle = None;
        // Bounded queues: whatever happened, the output queue honours its published bounds.
        assert!(self.rig.out.len() <= veles_ble_host::outqueue::MAX_QUEUED_MESSAGES);
        assert!(self.rig.out.queued_bytes() <= veles_ble_host::outqueue::MAX_QUEUED_BYTES);
        let (scans, conns, requests) = self.rig.receiver.reserved_counts();
        assert!(
            scans <= 2 && conns <= 32 && requests <= 64,
            "reservation caps exceeded"
        );
        let (events, bytes) = self.rig.receiver.data_usage();
        assert!(
            events <= 128 && bytes <= 64 * 1024,
            "ingress data budget exceeded"
        );
    }

    /// Validates every new backend submission against the lifecycle model.
    fn consume_calls(&mut self) {
        let calls = self.rig.calls();
        let new = calls[self.seen_calls..].to_vec();
        self.seen_calls = calls.len();
        for call in new {
            match call {
                Call::StartScan(t, _) => {
                    assert!(self.tokens_seen.insert((0, t)), "scan token {t} reused");
                    self.scans.push(t);
                    let id = format!("r{}", self.next_id);
                    self.token_of_request.insert(id, t);
                }
                Call::StopScan(_) => {}
                Call::Connect(t, BackendTarget::Hint(hint)) => {
                    assert!(
                        self.tokens_seen.insert((1, t)),
                        "connection token {t} reused"
                    );
                    let identity = hint.split('#').next().unwrap_or("").to_string();
                    // Review Focus 3: a replacement connection to a target whose earlier link
                    // the OS has not yet released must never be submitted.
                    for (old, link) in &self.links {
                        assert!(
                            link.identity != identity || link.released,
                            "Connect({t}) to {identity:?} submitted while link {old} is unreleased"
                        );
                    }
                    self.links.insert(
                        t,
                        Link {
                            handle: None,
                            budget: None,
                            identity,
                            released: false,
                            closing: false,
                            broken_capacity: false,
                        },
                    );
                    let id = format!("r{}", self.next_id);
                    self.token_of_request.insert(id, t);
                }
                Call::WriteFragments(conn, send, frames) => {
                    assert!(
                        self.tokens_seen.insert((2, send)),
                        "send token {send} reused"
                    );
                    let link = self
                        .links
                        .get(&conn)
                        .unwrap_or_else(|| panic!("write on unknown link {conn}"));
                    assert!(!link.released, "write submitted on released link {conn}");
                    assert!(!link.closing, "write submitted on closing link {conn}");
                    assert!(
                        !link.broken_capacity,
                        "write submitted after a capacity break on {conn}"
                    );
                    let handle = link
                        .handle
                        .clone()
                        .expect("write on a link with no connected handle");
                    // Cross-handle: the write is for the handle of the send request just issued.
                    if let Some(want) = &self.pending_send_handle {
                        assert_eq!(
                            &handle, want,
                            "send for {want} was written to {handle}'s link"
                        );
                    }
                    let budget = link
                        .budget
                        .expect("write on a link with no verified capacity");
                    // Review Focus 4: sizing is the frozen link budget for every frame.
                    check_frames(&frames, budget);
                    self.sends.push((conn, send));
                    self.summary.bump("write_submitted");
                    if frames.len() > 1 {
                        self.summary.bump("write_multi_fragment");
                    }
                }
                Call::Disconnect(conn) => {
                    if let Some(link) = self.links.get_mut(&conn) {
                        link.closing = true;
                    }
                }
                Call::Shutdown => {
                    self.shutdown_submissions += 1;
                    assert!(
                        self.shutdown_submissions <= 1,
                        "Backend::shutdown submitted twice"
                    );
                }
            }
        }
    }

    fn check_event(&mut self, event: &Value, subject: Option<u64>) {
        self.summary.events += 1;
        self.summary
            .bump(format!("ev:{}", event["type"].as_str().unwrap_or("?")));
        let kind = event["type"].as_str().expect("event type");
        for field in ["scan_handle", "connection_handle", "device_handle"] {
            if let Some(h) = event.get(field).and_then(Value::as_str) {
                assert!(is_hex_handle(h), "malformed {field} {h:?}");
            }
        }
        if let Some(id) = event.get("request_id").and_then(Value::as_str) {
            assert!(
                self.issued.contains_key(id),
                "event {kind} carries an id the host never received: {id}"
            );
            let n = self.terminals.entry(id.to_string()).or_insert(0);
            *n += 1;
            // Review Focus 1: exactly one terminal per request id.
            assert_eq!(*n, 1, "duplicate terminal for request {id}: {event}");
        }
        match kind {
            "connected" => {
                self.summary.connected += 1;
                let handle = event["connection_handle"].as_str().unwrap().to_string();
                assert!(
                    self.all_handles.insert(handle.clone()),
                    "handle {handle} issued twice"
                );
                let id = event["request_id"].as_str().unwrap();
                let token = *self
                    .token_of_request
                    .get(id)
                    .expect("connected for unknown connect");
                self.handle_token.insert(handle.clone(), token);
                self.links.get_mut(&token).unwrap().handle = Some(handle.clone());
                self.conn_handles.push(handle);
            }
            "scan_state" => {
                let handle = event["scan_handle"].as_str().unwrap().to_string();
                if event["status"] == "started" && self.all_handles.insert(handle.clone()) {
                    self.scan_handles.push(handle);
                }
            }
            "sent" => {
                self.summary.sent += 1;
                let id = event["request_id"].as_str().unwrap();
                let handle = event["connection_handle"].as_str().unwrap();
                // Cross-handle: a sent completion names the handle of the send it answers.
                assert_eq!(
                    self.send_target.get(id).map(String::as_str),
                    Some(handle),
                    "sent for {id} names a different connection"
                );
                if let Some(conn) = subject {
                    assert_eq!(
                        self.handle_token.get(handle),
                        Some(&conn),
                        "sent for handle {handle} produced by a completion on link {conn}"
                    );
                }
            }
            "message" => {
                let handle = event["connection_handle"].as_str().unwrap();
                assert!(
                    self.all_handles.contains(handle),
                    "message on an unissued handle"
                );
                if let Some(conn) = subject {
                    assert_eq!(
                        self.handle_token.get(handle),
                        Some(&conn),
                        "message for {handle} delivered from fragments of link {conn}"
                    );
                }
            }
            "error" => {
                self.summary.errors += 1;
                let code = event["code"].as_str().unwrap_or("?").to_string();
                self.summary.bump(format!("err:{code}"));
            }
            "device_found" => {
                let handle = event["scan_handle"].as_str().unwrap();
                assert!(
                    self.all_handles.contains(handle),
                    "device_found on an unissued scan"
                );
            }
            _ => {}
        }
        for field in ["scan_handle", "connection_handle"] {
            if !matches!(kind, "scan_state" | "connected" | "error")
                && let Some(h) = event.get(field).and_then(Value::as_str)
            {
                assert!(
                    self.all_handles.contains(h),
                    "{kind} names an unissued {field} {h}"
                );
            }
        }
    }

    /// End of sequence: shut the process down, release everything the adapter was told to
    /// release, and require that every reservation and byte budget returns to zero.
    fn finish(&mut self) {
        if self.rig.exit.is_none() {
            self.rig.input(Input::StdinEof);
            self.drain_and_check(None);
        }
        assert!(self.rig.exit.is_some(), "EOF must exit the core");
        for conn in self.conn_tokens() {
            let _ = self.rig.inject_status(BackendEvent::LinkReleased { conn });
        }
        for scan in self.scans.clone() {
            let _ = self.rig.inject_status(BackendEvent::ScanEnded {
                scan,
                failure: None,
            });
        }
        self.rig.pump_ingress();
        self.drain_and_check(None);
        let (_, conns, _) = self.rig.receiver.reserved_counts();
        assert_eq!(
            conns, 0,
            "connection reservations leaked after full release"
        );
        let (events, bytes) = self.rig.receiver.data_usage();
        assert_eq!(
            (events, bytes),
            (0, 0),
            "ingress data budget not returned after release"
        );
    }
}

/// Fragment frames of one record under the frozen budget: header chain, exact sizes.
pub fn check_frames(frames: &[Payload], budget: LinkBudget) {
    assert!(!frames.is_empty());
    let total = usize::from(u16::from_be_bytes([
        frames[0].as_slice()[2],
        frames[0].as_slice()[3],
    ]));
    assert!((1..=GATT_SEND_RECORD_MAX_BYTES).contains(&total));
    let mut payload_sum = 0;
    for (index, frame) in frames.iter().enumerate() {
        let bytes = frame.as_slice();
        let last = index + 1 == frames.len();
        assert!(
            bytes.len() <= budget.attribute(),
            "frame exceeds the frozen attribute budget"
        );
        assert_eq!(usize::from(bytes[0] & 0x7f), index);
        assert_eq!(bytes[0] & 0x80 != 0, last);
        if !last {
            assert_eq!(
                bytes.len(),
                budget.attribute(),
                "non-final frame is not full-size"
            );
        }
        payload_sum += bytes.len() - 4;
    }
    assert_eq!(
        payload_sum, total,
        "fragment payloads do not add up to the declared record"
    );
}

// ---- named seeds -------------------------------------------------------------------------------

/// Hand-written op programs: the five Review Focus cases plus census seeds. `(name, ops)`.
pub fn seed_programs() -> Vec<(&'static str, Vec<Op>)> {
    use Op::*;
    let bk = |kind, pick, arg| Backend { kind, pick, arg };
    let connect = |hint| vec![Connect { hint }, bk(kind::CONNECTED, 0, 3)];
    let mut f1 = vec![Hello];
    f1.extend(connect(0));
    // Focus 1: a live-id duplicate after a send was admitted.
    f1.extend([
        Send {
            pick: 0,
            len: 7,
            dup_live_id: false,
        },
        Send {
            pick: 0,
            len: 7,
            dup_live_id: true,
        },
    ]);
    // Focus 2: link 0 floods and stalls (no WriteDone); link 1 progresses and disconnects.
    let mut f2 = vec![
        Hello,
        Connect { hint: 0 },
        bk(kind::CONNECTED, 0, 3),
        Connect { hint: 1 },
    ];
    f2.extend([
        bk(kind::CONNECTED, 1, 3),
        Send {
            pick: 0,
            len: 3000,
            dup_live_id: false,
        },
    ]);
    f2.extend((0..40).map(|i| Inbound {
        pick: 0,
        len: 4000 + i,
        txn: 1 + (i as u8 % 5),
        hostile: 5,
    }));
    f2.extend([
        Send {
            pick: 1,
            len: 9,
            dup_live_id: false,
        },
        bk(kind::WRITE_DONE, 1, 0),
        Disconnect { pick: 1 },
    ]);
    // Focus 3: partial-write cancel then immediate reconnect to the same target (and an alias).
    let mut f3 = vec![Hello, Connect { hint: 0 }, bk(kind::CONNECTED, 0, 3)];
    f3.extend([
        Send {
            pick: 0,
            len: LEN_4096,
            dup_live_id: false,
        },
        Disconnect { pick: 0 },
        Connect { hint: 2 },
        bk(kind::WRITE_DONE, 0, 0),
        bk(kind::LINK_RELEASED, 0, 0),
        Connect { hint: 0 },
        bk(kind::CONNECTED, 1, 3),
        bk(kind::WRITE_DONE, 0, 0),
        Send {
            pick: 1,
            len: 5,
            dup_live_id: false,
        },
    ]);
    // Focus 4: capacity changes while sends are queued/in flight.
    let mut f4 = vec![Hello, Connect { hint: 0 }, bk(kind::CONNECTED, 0, 3)];
    f4.extend([
        Send {
            pick: 0,
            len: LEN_4096,
            dup_live_id: false,
        },
        Send {
            pick: 0,
            len: LEN_4096,
            dup_live_id: false,
        },
        bk(kind::CAPACITY_CHANGED, 0, 2),
        bk(kind::WRITE_DONE, 0, 0),
        bk(kind::CAPACITY_CHANGED, 0, 0),
        Send {
            pick: 0,
            len: 100,
            dup_live_id: false,
        },
    ]);
    // Focus 5: stdout closure with active links, a scan and partial inbound records.
    let mut f5 = vec![
        Hello,
        StartScan,
        bk(kind::SCAN_STARTED, 0, 0),
        Connect { hint: 0 },
        bk(kind::CONNECTED, 0, 3),
    ];
    f5.extend([
        Connect { hint: 1 },
        bk(kind::CONNECTED, 1, 3),
        Inbound {
            pick: 0,
            len: 4000,
            txn: 1,
            hostile: 4,
        },
        Send {
            pick: 1,
            len: LEN_4096,
            dup_live_id: false,
        },
        Terminate { kind: 1 },
        bk(kind::LINK_RELEASED, 0, 0),
        bk(kind::LINK_RELEASED, 1, 0),
        bk(kind::SCAN_ENDED, 0, 0),
        bk(kind::SHUTDOWN_COMPLETE, 0, SHUTDOWN_MAGIC),
    ]);
    let census = vec![
        Hello,
        StartScan,
        bk(kind::SCAN_STARTED, 0, 0),
        bk(kind::ADVERTISEMENT, 0, 7),
        Connect { hint: 0 },
        bk(kind::CONNECTED, 0, 3),
        Send {
            pick: 0,
            len: 5,
            dup_live_id: false,
        },
        bk(kind::WRITE_DONE, 0, 0),
        Inbound {
            pick: 0,
            len: 900,
            txn: 2,
            hostile: 0,
        },
        Advance { centis: 700 },
        Disconnect { pick: 0 },
        bk(kind::LINK_RELEASED, 0, 0),
        Shutdown,
        bk(kind::SHUTDOWN_COMPLETE, 0, SHUTDOWN_MAGIC),
    ];
    vec![
        ("focus1-live-id-reuse", f1),
        ("focus2-flood-and-stall", f2),
        ("focus3-cancel-then-reconnect", f3),
        ("focus4-capacity-change", f4),
        ("focus5-stdout-closed-active-links", f5),
        ("census-happy-shutdown", census),
    ]
}
