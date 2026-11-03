//! Task 9 deterministic property and adversarial regression suite (verification only).
//!
//! Every generator is the fixed SplitMix64 of `support::adversarial::Rng` with the seeds recorded
//! below; there is no wall clock and no entropy source, so a failure reproduces from its seed and
//! iteration number alone. The same checkers (`support/adversarial.rs`) are driven by the
//! separate `fuzz/` workspace. Bounded runs are NOT proof of absence — they are regression
//! evidence for the named inputs and a seeded sample of the rest.

#[allow(unused_imports)]
mod support;

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::io::Cursor;
use std::sync::Arc;
use std::time::Instant;

use serde_json::json;

use support::adversarial::{
    FragCensus, Op, PARSER_INPUT_MAX, Rng, Summary, check_frames, core_sequence, encode_ops,
    fragment_stream, record_of, run_ops, seed_programs, strict_envelope,
};
use support::{Rig, SCAN_UUID};

use veles_ble_host::backend::BackendEvent;
use veles_ble_host::base64;
use veles_ble_host::core::Input;
use veles_ble_host::fragment::{
    GlobalBudget, LinkBudget, LinkCapacity, PushOutcome, Reassembler, fragment,
};
use veles_ble_host::framing::read_frame;
use veles_ble_host::ingress::EmitStatus;
use veles_ble_host::protocol::{Phase, RequestBody, parse_request};

// ---- recorded seeds -------------------------------------------------------------------------------

const SEED_PARSER: u64 = 0x0A11_CE24_0001;
const SEED_FRAGMENT: u64 = 0x0A11_CE24_0002;
const SEED_CORE: u64 = 0x0A11_CE24_0003;
const SEED_ROUNDTRIP: u64 = 0x0A11_CE24_0004;

// ---- per-thread allocation accounting ------------------------------------------------------------------
//
// Claim (deliberately narrow): for ONE measured call on the calling thread this records the largest
// single allocation request, the gross bytes requested (allocs + realloc growth, frees ignored) and
// the peak live bytes (requests minus frees made on the same thread during the call). It is a
// per-call guard against input-length-independent or header-driven allocation, NOT a whole-process
// memory proof: other threads, memory freed on another thread, and allocator slack are not seen.

struct Counting;

thread_local! {
    static ARMED: Cell<bool> = const { Cell::new(false) };
    static LARGEST: Cell<usize> = const { Cell::new(0) };
    static GROSS: Cell<usize> = const { Cell::new(0) };
    static LIVE: Cell<isize> = const { Cell::new(0) };
    static PEAK: Cell<isize> = const { Cell::new(0) };
}

fn note_alloc(size: usize, released: usize) {
    // `try_with`: allocations during thread teardown must not panic.
    let _ = ARMED.try_with(|armed| {
        if !armed.get() {
            return;
        }
        let _ = LARGEST.try_with(|l| l.set(l.get().max(size)));
        let _ = GROSS.try_with(|g| g.set(g.get() + size.saturating_sub(released)));
        let _ = LIVE.try_with(|live| {
            live.set(live.get() + size as isize - released as isize);
            let _ = PEAK.try_with(|p| p.set(p.get().max(live.get())));
        });
    });
}

fn note_free(size: usize) {
    let _ = ARMED.try_with(|armed| {
        if armed.get() {
            let _ = LIVE.try_with(|live| live.set(live.get() - size as isize));
        }
    });
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        note_alloc(layout.size(), 0);
        unsafe { System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        note_free(layout.size());
        unsafe { System.dealloc(ptr, layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        note_alloc(new_size, layout.size());
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

#[derive(Clone, Copy, Debug)]
struct AllocUse {
    largest: usize,
    gross: usize,
    peak_live: usize,
}

/// Runs `f` and returns what this thread allocated during it (see the claim above).
fn measure<R>(f: impl FnOnce() -> R) -> (R, AllocUse) {
    for cell in [&LARGEST, &GROSS] {
        cell.with(|c| c.set(0));
    }
    for cell in [&LIVE, &PEAK] {
        cell.with(|c| c.set(0));
    }
    ARMED.with(|a| a.set(true));
    let out = f();
    ARMED.with(|a| a.set(false));
    let used = AllocUse {
        largest: LARGEST.with(Cell::get),
        gross: GROSS.with(Cell::get),
        peak_live: PEAK.with(Cell::get).max(0) as usize,
    };
    (out, used)
}

/// Backwards-compatible: largest single allocation only.
fn largest_allocation<R>(f: impl FnOnce() -> R) -> (R, usize) {
    let (out, used) = measure(f);
    (out, used.largest)
}

// ---- 1. record round trip, every length 1..=4096 ------------------------------------------------------

fn send_json(id: &str, handle: &str, record: &[u8]) -> Vec<u8> {
    serde_json::to_vec(&json!({
        "envelope_version": 1, "api_version": 1, "type": "send", "request_id": id,
        "connection_handle": handle, "record_base64": base64::encode(record),
    }))
    .unwrap()
}

#[test]
fn every_record_length_1_to_4096_round_trips_base64_envelope_and_fragments() {
    let mut rng = Rng(SEED_ROUNDTRIP);
    let budgets = [
        LinkBudget::from_att_mtu(185).unwrap(),
        LinkBudget::from_att_mtu(244).unwrap(),
        LinkBudget::from_att_mtu(517).unwrap(),
        LinkBudget::from_capacity(LinkCapacity::MaxWriteWithoutResponse(512)).unwrap(),
    ];
    for len in 1..=4096usize {
        let record = record_of(len, rng.byte());
        // base64 canonical round trip.
        let text = base64::encode(&record);
        assert_eq!(
            base64::decode(&text, 5120).unwrap(),
            record,
            "base64 len {len}"
        );
        // envelope parse recovers the exact record.
        let parsed = parse_request(
            &send_json("s", "0123456789abcdef", &record),
            Phase::Ready { api_version: 1 },
        )
        .unwrap_or_else(|f| panic!("len {len} rejected: {f:?}"));
        let RequestBody::Send { record: got, .. } = parsed.body else {
            panic!("not a send")
        };
        assert_eq!(got.as_slice(), &record[..], "envelope len {len}");
        // fragment -> reassemble under every capacity.
        for budget in budgets {
            let frames = fragment(&record, budget, 1 + (len % 255) as u8).unwrap();
            check_frames(&frames, budget);
            let global = GlobalBudget::new();
            let mut peer = Reassembler::new(budget, Arc::clone(&global));
            let now = Instant::now();
            let mut done = None;
            for (i, frame) in frames.iter().enumerate() {
                match peer.push(frame.as_slice(), now) {
                    PushOutcome::Incomplete => assert!(i + 1 < frames.len()),
                    PushOutcome::Complete(payload) => done = Some(payload),
                    other => panic!("len {len}: {other:?}"),
                }
            }
            assert_eq!(
                done.expect("completed").as_slice(),
                &record[..],
                "reassembly len {len}"
            );
            assert_eq!(global.usage(), (0, 0));
        }
    }
}

#[test]
fn every_record_length_1_to_4096_round_trips_through_the_core_at_three_capacities() {
    for mtu in [185usize, 244, 517] {
        let mut rig = Rig::ready();
        let (handle, conn) = rig.connect_hint("c", "phone-a", mtu);
        let budget = LinkBudget::from_att_mtu(mtu).unwrap();
        for len in 1..=4096usize {
            let record = record_of(len, (len % 251) as u8);
            let id = format!("s{len}");
            rig.req(
                "send",
                &id,
                json!({"connection_handle": handle, "record_base64": base64::encode(&record)}),
            );
            let (write_conn, send, frames) = rig.last_write();
            assert_eq!(write_conn, conn);
            check_frames(&frames, budget);
            // The frames carry exactly the record bytes in order (transport byte-exactness).
            let mut joined = Vec::new();
            for frame in &frames {
                joined.extend_from_slice(&frame.as_slice()[4..]);
            }
            assert_eq!(joined, record, "mtu {mtu} len {len}");
            rig.inject(BackendEvent::WriteDone { conn, send });
            let events = rig.events();
            assert_eq!(events.len(), 1, "{events:?}");
            assert_eq!(events[0]["type"], "sent");
            assert_eq!(events[0]["request_id"], id);
            assert_eq!(rig.exit, None);
            rig.clear_calls();
        }
    }
}

/// Exact classification at the record-size boundaries (OTP2-BRD-002/031): `(decoded length,
/// expected writes, expected error code or None, whether the error names the connection)`.
/// 0 decodes to nothing => `invalid_base64`; 1 and 4,096 are accepted and written; 4,097..=5,120 are
/// the correlated, connection-scoped `oversize_record` (no fragment created); 5,121 is above the
/// envelope ceiling — the protocol text does not classify it, the observed code is
/// `oversize_record` WITHOUT a connection handle (an envelope-level refusal). Every refusal is
/// fatal-for-the-request, stage `record`, and the process survives (no exit).
#[test]
fn record_size_boundaries_have_exact_classification_and_no_collateral() {
    type Expect<'a> = Option<(&'a str, bool)>;
    let cases: [(usize, usize, Expect); 7] = [
        (0, 0, Some(("invalid_base64", false))),
        (1, 1, None),
        (4096, 1, None),
        (4097, 0, Some(("oversize_record", true))),
        (5119, 0, Some(("oversize_record", true))),
        (5120, 0, Some(("oversize_record", true))),
        (5121, 0, Some(("oversize_record", false))),
    ];
    for (len, writes, error) in cases {
        let mut rig = Rig::ready();
        let (handle, conn) = rig.connect_hint("c", "phone-a", 517);
        rig.clear_calls();
        rig.req(
            "send",
            "b",
            json!({"connection_handle": handle, "record_base64": base64::encode(&record_of(len, 1))}),
        );
        assert_eq!(rig.write_calls().len(), writes, "len {len}: backend writes");
        let events = rig.events();
        match error {
            None => assert!(events.is_empty(), "len {len}: {events:?}"),
            Some((code, scoped)) => {
                assert_eq!(events.len(), 1, "len {len}: {events:?}");
                let e = &events[0];
                assert_eq!(e["type"], "error", "len {len}");
                assert_eq!(e["code"], code, "len {len}");
                assert_eq!(e["recoverability"], "fatal", "len {len}");
                assert_eq!(e["stage"], "record", "len {len}");
                assert_eq!(e["request_id"], "b", "len {len}");
                assert_eq!(
                    e.get("connection_handle").and_then(|h| h.as_str()),
                    scoped.then_some(handle.as_str()),
                    "len {len}: connection scoping"
                );
            }
        }
        assert_eq!(
            rig.exit, None,
            "len {len}: a refused record never ends the process"
        );
        // The link is untouched by a refused record: no teardown submission for it.
        assert!(
            !rig.calls().contains(&support::Call::Disconnect(conn)),
            "len {len}: refused record tore the link down"
        );
    }
}

// ---- 2. bounded malformed input: never panics, never overallocates ------------------------------------

fn valid_corpus() -> Vec<Vec<u8>> {
    let record = record_of(300, 5);
    let handle = "0123456789abcdef";
    vec![
        serde_json::to_vec(&json!({"envelope_version":1,"type":"hello","request_id":"h","api_versions":[1]})).unwrap(),
        serde_json::to_vec(&json!({"envelope_version":1,"type":"hello","request_id":"h","api_version_range":{"min":1,"max":9}})).unwrap(),
        serde_json::to_vec(&json!({"envelope_version":1,"api_version":1,"type":"start_scan","request_id":"a","service_uuid":SCAN_UUID})).unwrap(),
        serde_json::to_vec(&json!({"envelope_version":1,"api_version":1,"type":"connect","request_id":"c","platform_hint":"phone"})).unwrap(),
        serde_json::to_vec(&json!({"envelope_version":1,"api_version":1,"type":"disconnect","request_id":"d","connection_handle":handle})).unwrap(),
        serde_json::to_vec(&json!({"envelope_version":1,"api_version":1,"type":"shutdown","request_id":"z"})).unwrap(),
        send_json("s", handle, &record),
        send_json("s", handle, &record_of(5120, 2)),
    ]
}

/// A parse call may allocate decoded strings and base64 output, bounded by a small multiple of
/// the input. This documented envelope is 4x the input plus 4 KiB: serde's growth doubling plus
/// the decode buffer plus slack. Anything bigger would mean input-length-independent allocation.
fn alloc_bound(input_len: usize) -> usize {
    4 * input_len + 4096
}

/// Gross requested bytes and peak live bytes of one `parse_request`: documented envelopes of 16x
/// and 8x the input plus 16 KiB (serde value tree + owned strings + base64 buffer + payload copy).
fn gross_bound(input_len: usize) -> usize {
    16 * input_len + 16 * 1024
}

fn peak_bound(input_len: usize) -> usize {
    8 * input_len + 8 * 1024
}

#[test]
fn malformed_and_mutated_envelopes_never_panic_and_stay_allocation_bounded() {
    let mut rng = Rng(SEED_PARSER);
    let corpus = valid_corpus();
    let mut census = [0usize; 3];
    let mut worst = 0usize;
    for iteration in 0..30_000 {
        let mut bytes = corpus[rng.below(corpus.len())].clone();
        match rng.below(8) {
            0 => {} // untouched
            1 => {
                let i = rng.below(bytes.len());
                bytes[i] = rng.byte();
            }
            2 => bytes.truncate(rng.below(bytes.len() + 1)),
            3 => {
                let at = rng.below(bytes.len() + 1);
                let n = 1 + rng.below(32);
                let extra = rng.bytes(n);
                bytes.splice(at..at, extra);
            }
            4 => {
                // Pathological nesting.
                let depth = 1 + rng.below(70_000);
                bytes = std::iter::repeat_n(if rng.below(2) == 0 { b'[' } else { b'{' }, depth)
                    .collect();
            }
            5 => {
                let n = rng.below(PARSER_INPUT_MAX + 1);
                bytes = rng.bytes(n);
            }
            6 => {
                // Huge string / base64 field.
                let n = rng.below(60_000);
                let blob = "A".repeat(n - n % 4);
                bytes = format!(
                    "{{\"envelope_version\":1,\"api_version\":1,\"type\":\"send\",\"request_id\":\"x\",\"connection_handle\":\"0123456789abcdef\",\"record_base64\":\"{blob}\"}}"
                )
                .into_bytes();
            }
            _ => {
                let n = rng.below(4);
                for _ in 0..n {
                    let i = rng.below(bytes.len());
                    bytes[i] ^= 1 << rng.below(8);
                }
            }
        }
        bytes.truncate(PARSER_INPUT_MAX);
        let outcome = strict_envelope(&bytes);
        census[usize::from(outcome)] += 1;
        // Production-only measurement of ONE envelope parse (the harness's own re-parses and
        // re-encodes above are deliberately outside the measured window).
        let (_, used) = measure(|| {
            let _ = parse_request(&bytes, Phase::Ready { api_version: 1 });
        });
        worst = worst.max(used.gross * 1000 / (bytes.len() + 1));
        assert!(
            used.largest <= alloc_bound(bytes.len())
                && used.gross <= gross_bound(bytes.len())
                && used.peak_live <= peak_bound(bytes.len()),
            "iteration {iteration} (seed {SEED_PARSER:#x}): {used:?} for a {} byte input",
            bytes.len()
        );
    }
    // The generator must actually reach all three outcomes (it is not vacuous).
    assert!(census.iter().all(|n| *n > 100), "census {census:?}");
    eprintln!("worst gross/input ratio x1000: {worst}");
}

#[test]
fn frame_headers_never_allocate_beyond_the_declared_and_ceiling() {
    for declared in [
        0u32,
        1,
        4,
        65_535,
        65_536,
        65_537,
        1 << 20,
        u32::MAX - 1,
        u32::MAX,
    ] {
        for body in [0usize, 1, 100] {
            let mut data = declared.to_le_bytes().to_vec();
            data.extend(vec![b'x'; body]);
            let (result, largest) =
                largest_allocation(|| read_frame(&mut Cursor::new(data.clone())));
            let ceiling = 65_536 + 256;
            assert!(
                largest <= ceiling,
                "declared {declared}: allocated {largest}"
            );
            match declared {
                0 => assert!(result.is_err()),
                65_537.. => {
                    assert!(result.is_err());
                    assert!(
                        largest < 256,
                        "oversize header {declared} allocated {largest} before rejecting"
                    );
                }
                _ => {
                    if body < declared as usize {
                        assert!(result.is_err(), "truncated payload accepted");
                    }
                }
            }
        }
    }
}

#[test]
fn deeply_nested_and_oversize_json_is_rejected_without_stack_or_heap_blowup() {
    // Run on a deliberately small stack. 256 KiB was enough for the recursive serde_json path
    // on Linux but not for its larger MSVC debug frames, so pin the rejection to a bound that
    // fails if depth rejection scales with the input's nesting.
    std::thread::Builder::new()
        .stack_size(128 * 1024)
        .spawn(|| {
            for open in *b"[{" {
                let nested: Vec<u8> = std::iter::repeat_n(open, 65_536).collect();
                let (_, largest) = largest_allocation(|| strict_envelope(&nested));
                assert!(largest <= alloc_bound(nested.len()));
            }
        })
        .unwrap()
        .join()
        .expect("nested JSON must be rejected, not overflow the stack");
}

// ---- 3. hostile fragments: explicit clock, budget conservation -----------------------------------------

#[test]
fn seeded_hostile_fragment_streams_match_the_reference_model_exactly() {
    let mut rng = Rng(SEED_FRAGMENT);
    let mut total = FragCensus::default();
    for _ in 0..3_000 {
        let len = rng.below(3_000);
        total.merge(&fragment_stream(&rng.bytes(len)));
    }
    eprintln!("FRAG CENSUS {total:?}");
    // Non-vacuity: every branch the model predicts is actually exercised by the seeded sample.
    assert!(total.complete > 5_000, "{total:?}");
    assert!(
        total.conflict_erased > 1_000,
        "same-id conflict erasures: {total:?}"
    );
    assert!(
        total.ignored > 1_000 && total.incomplete > 10_000,
        "{total:?}"
    );
    assert!(
        total.disconnect > 1_000,
        "conflict-window disconnects: {total:?}"
    );
    assert!(total.expired > 1_000, "inactivity expiries: {total:?}");
    assert!(
        total.cap_refused > 1_000,
        "per-peer/global cap refusals: {total:?}"
    );
    for (slot, n) in total.discarded.iter().enumerate() {
        assert!(
            *n >= 10,
            "discard reason slot {slot} reached only {n} times: {total:?}"
        );
    }
    assert_eq!(
        total.peak_global_count, 16,
        "the 16-transaction global cap is reached"
    );
    assert!(total.peak_global_bytes > 32 * 1024 && total.peak_global_bytes <= 64 * 1024);
    assert!(total.peak_peer_bytes > 4096 && total.peak_peer_bytes <= 8 * 1024);
}

#[test]
fn reassembly_expiry_returns_the_global_budget_at_the_exact_deadline() {
    let global = GlobalBudget::new();
    let budget = LinkBudget::from_att_mtu(185).unwrap();
    let mut peer = Reassembler::new(budget, Arc::clone(&global));
    let t0 = Instant::now();
    let frames = fragment(&record_of(1000, 1), budget, 1).unwrap();
    assert!(matches!(
        peer.push(frames[0].as_slice(), t0),
        PushOutcome::Incomplete
    ));
    assert_eq!(global.usage(), (1, 1000));
    peer.expire(t0 + std::time::Duration::from_millis(4_999));
    assert_eq!(
        global.usage(),
        (1, 1000),
        "alive 1 ms before the 5 s deadline"
    );
    peer.expire(t0 + std::time::Duration::from_secs(5));
    assert_eq!(global.usage(), (0, 0), "released exactly at 5 s");
}

/// Regression for the 60 s fragment-smoke finding (Task 9 review, Important 2), under the owner's
/// conflict ruling (OTP2-GAT-011 "approach 2"): a start for a live id erases the live partial and
/// is itself rejected, so the would-be continuation `fb[1..]` finds no live partial and nothing
/// can complete as `a[..178] || b[178..]`. Budgets are returned at once; a fresh start works.
#[test]
fn same_id_conflict_erases_live_partial_and_foreign_suffix_never_completes() {
    let budget = LinkBudget::from_att_mtu(185).unwrap();
    let global = GlobalBudget::new();
    let mut peer = Reassembler::new(budget, Arc::clone(&global));
    let now = Instant::now();
    let a = record_of(400, 1);
    let b = record_of(400, 90);
    let fa = fragment(&a, budget, 1).unwrap();
    let fb = fragment(&b, budget, 1).unwrap();
    assert_eq!(fa.len(), 3);
    assert!(matches!(
        peer.push(fa[0].as_slice(), now),
        PushOutcome::Incomplete
    ));
    assert!(
        matches!(peer.push(fb[0].as_slice(), now), PushOutcome::Ignored),
        "conflicting start rejected"
    );
    assert_eq!(
        global.usage(),
        (0, 0),
        "live partial erased, budget released"
    );
    assert!(matches!(
        peer.push(fb[1].as_slice(), now),
        PushOutcome::Ignored
    ));
    assert!(matches!(
        peer.push(fb[2].as_slice(), now),
        PushOutcome::Ignored
    ));
    assert_eq!(peer.incomplete(), 0);
    // A later fresh index 0 starts, and completes with only its own bytes.
    assert!(matches!(
        peer.push(fb[0].as_slice(), now),
        PushOutcome::Incomplete
    ));
    assert!(matches!(
        peer.push(fb[1].as_slice(), now),
        PushOutcome::Incomplete
    ));
    let PushOutcome::Complete(done) = peer.push(fb[2].as_slice(), now) else {
        panic!("fresh transaction completes")
    };
    assert_eq!(done.as_slice(), &b[..]);
    assert_eq!(global.usage(), (0, 0));
}

// ---- 4. core sequences ----------------------------------------------------------------------------------

#[test]
fn seeded_core_sequences_keep_every_invariant() {
    let mut rng = Rng(SEED_CORE);
    let mut total = Summary::default();
    for _ in 0..1_000 {
        let len = rng.below(1_200);
        total.merge(&core_sequence(&rng.bytes(len)));
    }
    eprintln!("CORE CENSUS {:?}", total.census);
    // Non-vacuity of the security-relevant branches (minimums are far below the observed counts).
    for (key, min) in [
        ("ev:connected", 100),
        ("ev:sent", 50),
        ("ev:message", 10),
        ("ev:disconnected", 50),
        ("ev:device_found", 3),
        ("err:busy", 100),
        ("err:oversize_record", 10),
        ("err:unknown_handle", 100),
        ("capacity_break_accepted", 20),
        ("write_submitted", 200),
        ("write_multi_fragment", 100),
        ("live_dup_exit2", 5),
        ("exit:0", 500),
        ("exit:1", 5),
        ("exit:2", 5),
        ("inject:DataOverflow", 100),
        ("inject:FragmentOversize", 10),
        ("inject:StaleToken", 100),
    ] {
        assert!(
            total.count(key) >= min,
            "census {key} = {} < {min}: {:?}",
            total.count(key),
            total.census
        );
    }
}

#[test]
fn op_language_round_trips_and_seed_programs_run_clean() {
    for (name, ops) in seed_programs() {
        let bytes = encode_ops(&ops);
        let mut reader = support::adversarial::Reader::new(&bytes);
        let decoded: Vec<Op> = (0..ops.len()).map(|_| Op::decode(&mut reader)).collect();
        assert_eq!(decoded, ops, "{name} does not round-trip");
        assert!(reader.exhausted());
        let summary = run_ops(&ops);
        eprintln!("seed {name}: {summary:?}");
    }
}

// ---- 5. the five Review Focus failures, as named regressions -------------------------------------------

/// Focus 1: a request ID reused while its completion sits in the writer terminates silently.
#[test]
fn focus1_reused_id_while_completion_is_in_the_writer_exits_silently() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c", "phone-a", 517);
    rig.req(
        "send",
        "dup",
        json!({"connection_handle": handle, "record_base64": base64::encode(&record_of(9, 1))}),
    );
    let (_, send, _) = rig.last_write();
    rig.inject(BackendEvent::WriteDone { conn, send });
    let held = rig.pop_held(); // the writer holds the `sent` frame; finish_write not called
    assert!(String::from_utf8_lossy(&held).contains("\"sent\""));
    rig.req(
        "send",
        "dup",
        json!({"connection_handle": handle, "record_base64": base64::encode(&record_of(9, 2))}),
    );
    assert_eq!(
        rig.exit,
        Some(2),
        "reuse of an id still held by the writer is fail-closed"
    );
    // Only the writer-held frame itself remains; nothing else was queued for the reused id.
    assert_eq!(
        rig.out.len(),
        1,
        "no second terminal may be queued for the reused id"
    );
    rig.out.finish_write();
    assert!(
        rig.events().is_empty(),
        "the reused id produced a second response"
    );
}

/// Focus 2: one phone floods fragments and stalls its write; the other phone and the terminal
/// signals are not starved.
#[test]
fn focus2_flooding_and_stalled_peer_does_not_starve_the_other_phone() {
    let mut rig = Rig::ready();
    let (a_handle, a) = rig.connect_hint("ca", "phone-a", 185);
    let (b_handle, b) = rig.connect_hint("cb", "phone-b", 517);
    // A stalls: a send with no completion.
    rig.req("send", "a-stall", json!({"connection_handle": a_handle, "record_base64": base64::encode(&record_of(4000, 1))}));
    // A floods well past its per-link data budget without the core draining in between.
    let frame = fragment(
        &record_of(4096, 3),
        LinkBudget::from_att_mtu(185).unwrap(),
        1,
    )
    .unwrap();
    let mut overflowed = false;
    for round in 0..64 {
        let status = rig.inject_status(BackendEvent::Fragment {
            conn: a,
            bytes: frame[round % frame.len()].clone(),
        });
        overflowed |= status == EmitStatus::DataOverflow;
    }
    assert!(overflowed, "the flood must hit the per-link data budget");
    rig.pump_ingress();
    // B still completes a send end to end, and can disconnect, with its terminal delivered.
    rig.req(
        "send",
        "b-send",
        json!({"connection_handle": b_handle, "record_base64": base64::encode(&record_of(50, 1))}),
    );
    let (wc, ws, _) = rig.last_write();
    assert_eq!(wc, b, "B's write must go to B's link");
    rig.inject(BackendEvent::WriteDone { conn: b, send: ws });
    let events = rig.events();
    let sent: Vec<_> = events.iter().filter(|e| e["type"] == "sent").collect();
    assert_eq!(sent.len(), 1, "{events:?}");
    assert_eq!(sent[0]["request_id"], "b-send");
    assert_eq!(sent[0]["connection_handle"], b_handle);
    assert_eq!(rig.exit, None);
    rig.req(
        "disconnect",
        "b-bye",
        json!({"connection_handle": b_handle}),
    );
    let events = rig.events();
    assert!(
        events
            .iter()
            .any(|e| e["type"] == "disconnected" && e["request_id"] == "b-bye"),
        "{events:?}"
    );
}

/// Focus 3: partial-write cancellation followed by an immediate reconnect never lets the old
/// work reach the replacement link.
#[test]
fn focus3_cancelled_write_then_reconnect_never_transmits_on_the_replacement() {
    let mut rig = Rig::ready();
    let (handle, old) = rig.connect_hint("c1", "phone-a", 185);
    rig.req(
        "send",
        "old",
        json!({"connection_handle": handle, "record_base64": base64::encode(&record_of(4096, 1))}),
    );
    let (_, old_send, _) = rig.last_write();
    rig.req("disconnect", "bye", json!({"connection_handle": handle}));
    let _ = rig.events();
    // Immediate reconnect (also by alias) is refused until the OS release is observed.
    for hint in ["phone-a", "phone-a#k2"] {
        rig.clear_calls();
        rig.req(
            "connect",
            &format!("again-{}", hint.len()),
            json!({"platform_hint": hint}),
        );
        let events = rig.events();
        assert_eq!(events[0]["code"], "busy", "{events:?}");
        assert!(
            rig.calls()
                .iter()
                .all(|c| !matches!(c, support::Call::Connect(..)))
        );
    }
    rig.inject(BackendEvent::LinkReleased { conn: old });
    let (new_handle, new) = rig.connect_hint("c2", "phone-a", 517);
    assert_ne!(new, old, "connection tokens are never reused");
    assert_ne!(new_handle, handle, "handles are never reused");
    // The old worker's late completion is stale and cannot become a `sent` on the new link.
    assert_eq!(
        rig.inject_status(BackendEvent::WriteDone {
            conn: old,
            send: old_send
        }),
        EmitStatus::StaleToken
    );
    assert!(rig.events().is_empty());
    rig.clear_calls();
    rig.req(
        "send",
        "fresh",
        json!({"connection_handle": new_handle, "record_base64": base64::encode(&record_of(5, 1))}),
    );
    let writes = rig.write_calls();
    assert_eq!(writes.len(), 1);
    assert_eq!(
        writes[0].0, new,
        "only the replacement link carries the new write"
    );
}

/// Focus 4: a capacity change during queued/in-flight sends never changes sizing mid-record.
#[test]
fn focus4_capacity_change_never_resizes_a_record_in_flight() {
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c", "phone-a", 185);
    let frozen = LinkBudget::from_att_mtu(185).unwrap();
    for id in ["q1", "q2"] {
        rig.req("send", id, json!({"connection_handle": handle, "record_base64": base64::encode(&record_of(4096, 4))}));
    }
    let (_, _, first) = rig.last_write();
    check_frames(&first, frozen);
    // A change to a different derived budget closes the link instead of re-fragmenting.
    rig.inject(BackendEvent::CapacityChanged {
        conn,
        capacity: LinkCapacity::AttMtu(517),
    });
    let before = rig.write_calls().len();
    let events = rig.events();
    assert!(events.iter().any(|e| e["type"] == "error"), "{events:?}");
    assert_eq!(
        rig.write_calls().len(),
        before,
        "no new write after the capacity break"
    );
    for (_, _, frames) in rig.write_calls() {
        check_frames(&frames, frozen);
    }
    // Identical derived budget (MTU 517 -> 515 both give attribute 512) is a no-op.
    let mut rig = Rig::ready();
    let (handle, conn) = rig.connect_hint("c", "phone-a", 517);
    for id in ["q1", "q2"] {
        rig.req("send", id, json!({"connection_handle": handle, "record_base64": base64::encode(&record_of(4096, 4))}));
    }
    rig.inject(BackendEvent::CapacityChanged {
        conn,
        capacity: LinkCapacity::AttMtu(515),
    });
    let (_, send, _) = rig.last_write();
    rig.inject(BackendEvent::WriteDone { conn, send });
    let frozen = LinkBudget::from_att_mtu(517).unwrap();
    for (_, _, frames) in rig.write_calls() {
        check_frames(&frames, frozen);
    }
    assert_eq!(rig.exit, None);
}

/// Focus 5 (portable part): output pipe closure with active links, a scan and partial inbound
/// state releases everything the backend was asked to release. The process-level frozen/broken
/// stdout deadline is covered by `tests/process.rs`.
#[test]
fn focus5_stdout_closed_with_active_work_releases_everything() {
    let mut rig = Rig::ready();
    let (_scan_handle, scan) = rig.open_scan("s1");
    let (ha, a) = rig.connect_hint("ca", "phone-a", 185);
    let (_hb, b) = rig.connect_hint("cb", "phone-b", 185);
    // A partial inbound record on A (first fragment only) holds reassembly state.
    let frames = fragment(
        &record_of(900, 2),
        LinkBudget::from_att_mtu(185).unwrap(),
        1,
    )
    .unwrap();
    rig.inject(BackendEvent::Fragment {
        conn: a,
        bytes: frames[0].clone(),
    });
    assert_eq!(rig.events().len(), 0, "a partial record is not delivered");
    // A multi-fragment send is in flight on A.
    rig.req(
        "send",
        "s-pending",
        json!({"connection_handle": ha, "record_base64": base64::encode(&record_of(4096, 6))}),
    );
    assert_eq!(rig.write_calls().len(), 1);
    let (scans0, conns0, requests0) = rig.receiver.reserved_counts();
    assert_eq!(
        (scans0, conns0),
        (1, 2),
        "precondition: one scan, two links reserved"
    );
    assert_eq!(requests0, 1, "precondition: the pending send is reserved");
    rig.clear_calls();
    rig.input(Input::StdoutClosed);
    assert_eq!(rig.exit, Some(0));
    // Exactly the required release submissions (order among links is unspecified), shutdown last.
    let calls = rig.calls();
    assert_eq!(calls.len(), 4, "{calls:?}");
    assert_eq!(calls.last(), Some(&support::Call::Shutdown), "{calls:?}");
    for want in [
        support::Call::StopScan(scan),
        support::Call::Disconnect(a),
        support::Call::Disconnect(b),
    ] {
        assert_eq!(
            calls.iter().filter(|c| **c == want).count(),
            1,
            "{want:?} in {calls:?}"
        );
    }
    // The writer is gone: nothing is emitted for the pending send, the scan or the links.
    assert!(rig.events().is_empty(), "events after stdout closure");
    // Ingress bookkeeping: every scan/link/request reservation and every staged data byte is
    // already back at zero (including the partial inbound record's staging).
    assert_eq!(rig.receiver.reserved_counts(), (0, 0, 0));
    assert_eq!(rig.receiver.data_usage(), (0, 0));
    assert_eq!(rig.receiver.link_data_usage(a), None);
    // Late completions of the cancelled work are stale and produce nothing.
    let pending = rig.write_calls();
    assert!(pending.is_empty(), "no new write after closure");
    for event in [
        BackendEvent::WriteDone { conn: a, send: 1 },
        BackendEvent::LinkReleased { conn: a },
        BackendEvent::LinkReleased { conn: b },
        BackendEvent::ScanEnded {
            scan,
            failure: None,
        },
    ] {
        let status = rig.inject_status(event);
        assert_eq!(status, EmitStatus::StaleToken, "late completion not stale");
    }
    rig.pump_ingress();
    assert!(rig.events().is_empty());
    // The process-wide release is observed only through the attributed terminal.
    assert!(!rig.core.shutdown_complete_received());
    assert_eq!(
        rig.inject_status(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    rig.pump_ingress();
    assert!(
        rig.core.shutdown_complete_received(),
        "attributed ShutdownComplete not recorded"
    );
    assert!(rig.events().is_empty());
    assert_eq!(rig.exit, Some(0), "exit code unchanged by late completions");
}

// ---- strict census guard: the shared generators reach what they claim -----------------------------------

#[test]
fn census_seed_programs_exercise_connect_send_and_shutdown() {
    let programs = seed_programs();
    let census = programs
        .iter()
        .find(|(n, _)| *n == "census-happy-shutdown")
        .unwrap();
    let summary = run_ops(&census.1);
    assert!(summary.connected >= 1 && summary.sent >= 1, "{summary:?}");
    assert!(summary.exit.is_some());
}
