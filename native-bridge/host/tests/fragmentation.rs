//! Task 2 contract tests (OTP-24 production crate): opaque GATT fragmentation and owned-buffer
//! budgets. Wire rules mirror the Android peripheral (`GattTransport.kt`, profile revision 2,
//! OTP2-GAT-010..012) and the committed `protocol/v1/fixtures/fragmentation.json` vectors.

use std::sync::Arc;
use std::time::{Duration, Instant};

use veles_ble_host::buffer::Payload;
use veles_ble_host::fragment::{
    ATTRIBUTE_VALUE_MAX, BudgetError, DiscardReason, FragmentError, GlobalBudget, LinkBudget,
    LinkCapacity, MAX_MTU, MIN_MTU, PushOutcome, Reassembler, fragment,
};
use veles_ble_host::limits::{DECODED_RECORD_MAX_BYTES, GATT_SEND_RECORD_MAX_BYTES};

fn hex_decode(text: &str) -> Vec<u8> {
    assert!(text.len().is_multiple_of(2), "odd hex length");
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).expect("hex digit"))
        .collect()
}

/// Deterministic synthetic record bytes (never OTP data); the reviewed fixture generator.
fn synthetic(len: usize, seed: u8) -> Vec<u8> {
    (0..len)
        .map(|i| seed.wrapping_add((i % 251) as u8))
        .collect()
}

fn link(mtu: usize) -> LinkBudget {
    LinkBudget::from_att_mtu(mtu).expect("valid ATT MTU")
}

fn reassembler(mtu: usize) -> (Reassembler, Arc<GlobalBudget>) {
    let budget = GlobalBudget::new();
    (Reassembler::new(link(mtu), budget.clone()), budget)
}

fn feed_all(frames: &[Payload], mtu: usize) -> PushOutcome {
    let (mut r, _) = reassembler(mtu);
    let now = Instant::now();
    let mut last = PushOutcome::Incomplete;
    for (i, frame) in frames.iter().enumerate() {
        last = r.push(frame.as_slice(), now);
        if i + 1 < frames.len() {
            assert_eq!(
                last,
                PushOutcome::Incomplete,
                "fragment {i} must be incomplete"
            );
        }
    }
    last
}

/// Literal, independent statement of the payload budget (the Android test's table):
/// unclamped up to ATT MTU 515, 508 above.
fn literal_payload(mtu: usize) -> usize {
    if mtu <= 515 { mtu - 7 } else { 508 }
}

// ---- sender boundaries ----------------------------------------------------------------------

#[test]
fn revision2_boundaries_round_trip() {
    for mtu in [MIN_MTU, 247, 514, 515, MAX_MTU] {
        let budget = link(mtu);
        let attribute = (mtu - 3).min(ATTRIBUTE_VALUE_MAX);
        let chunk = budget.payload();
        for size in [1usize, 177, 178, 179, 508, 509, 1016, 1017, 4095, 4096] {
            let record = synthetic(size, (mtu % 251) as u8);
            let frames = fragment(&record, budget, 42).unwrap();
            for (index, frame) in frames.iter().enumerate() {
                let last = index + 1 == frames.len();
                assert!(
                    frame.len() <= attribute,
                    "mtu {mtu} size {size}: frame within min(mtu-3, 512)"
                );
                assert!(
                    frame.len() > 4,
                    "mtu {mtu} size {size}: sender never emits an empty trailer"
                );
                if !last {
                    assert_eq!(
                        frame.len(),
                        4 + chunk,
                        "mtu {mtu} size {size}: non-final payload is exactly the budget"
                    );
                }
                let bytes = frame.as_slice();
                assert_eq!(bytes[0] & 0x7f, index as u8, "index byte");
                assert_eq!(bytes[0] & 0x80 != 0, last, "final flag");
                assert_eq!(bytes[1], 42, "transaction id");
                let total = u16::from_be_bytes([bytes[2], bytes[3]]);
                assert_eq!(total, if index == 0 { size as u16 } else { 0 });
            }
            let joined: Vec<u8> = frames
                .iter()
                .flat_map(|f| f.as_slice()[4..].to_vec())
                .collect();
            assert_eq!(joined, record, "mtu {mtu} size {size}: byte equality");
            match feed_all(&frames, mtu) {
                PushOutcome::Complete(reassembled) => assert_eq!(
                    reassembled.as_slice(),
                    record.as_slice(),
                    "mtu {mtu} size {size}: round trip"
                ),
                other => panic!("mtu {mtu} size {size}: {other:?}"),
            }
        }
    }
}

#[test]
fn send_rejections_zero_4097_5120_and_transaction_zero() {
    assert_eq!(
        fragment(&[], link(MIN_MTU), 1),
        Err(FragmentError::EmptyRecord)
    );
    assert_eq!(
        fragment(&synthetic(4097, 1), link(MIN_MTU), 1),
        Err(FragmentError::OversizeRecord)
    );
    assert_eq!(
        fragment(&synthetic(5120, 1), link(MIN_MTU), 1),
        Err(FragmentError::OversizeRecord),
        "the envelope-admitted band 4097..={} is refused before any frame or write",
        DECODED_RECORD_MAX_BYTES
    );
    assert_eq!(
        fragment(&synthetic(10, 1), link(MIN_MTU), 0),
        Err(FragmentError::BadTransactionId)
    );
    assert!(fragment(&synthetic(1, 1), link(MIN_MTU), 1).is_ok());
    assert!(fragment(&synthetic(4096, 1), link(MIN_MTU), 1).is_ok());
    assert!(fragment(&synthetic(10, 1), link(MIN_MTU), 255).is_ok());
    assert_eq!(GATT_SEND_RECORD_MAX_BYTES, 4096, "frozen GATT ceiling");
}

#[test]
fn link_capacity_domains_and_the_literal_budget_table() {
    for mtu in [0, 23, MIN_MTU - 1] {
        assert_eq!(LinkBudget::from_att_mtu(mtu), Err(BudgetError::TooSmall));
    }
    for mtu in [MAX_MTU + 1, 1000, usize::MAX] {
        assert_eq!(LinkBudget::from_att_mtu(mtu), Err(BudgetError::TooLarge));
    }
    assert_eq!(
        LinkBudget::from_capacity(LinkCapacity::AttMtu(MIN_MTU - 1)),
        Err(BudgetError::TooSmall)
    );
    for mtu in MIN_MTU..=MAX_MTU {
        let budget = link(mtu);
        assert_eq!(budget.payload(), literal_payload(mtu), "mtu {mtu}");
        assert_eq!(budget.attribute(), literal_payload(mtu) + 4, "mtu {mtu}");
        assert!(budget.attribute() <= ATTRIBUTE_VALUE_MAX);
    }
    // macOS: only a backend write limit is known; below the 512 clamp the platform may cap
    // it, so no common budget can be established and the link is refused (fail closed).
    for write in [0, 1, 511] {
        assert_eq!(
            LinkBudget::from_max_write_without_response(write),
            Err(BudgetError::Undetermined)
        );
        assert_eq!(
            LinkBudget::from_capacity(LinkCapacity::MaxWriteWithoutResponse(write)),
            Err(BudgetError::Undetermined)
        );
    }
    for write in [512, 1024] {
        let budget = LinkBudget::from_max_write_without_response(write).unwrap();
        assert_eq!(budget.attribute(), 512);
    }
}

// ---- receiver rules -------------------------------------------------------------------------

#[test]
fn started_then_malformed_fragments_discard_the_partial_and_release_budgets() {
    let started = |(r, budget): (Reassembler, Arc<GlobalBudget>), now: Instant| {
        let mut r = r;
        let mut first = vec![0x00, 5, 0x01, 0x90];
        first.extend(synthetic(178, 1));
        assert_eq!(r.push(&first, now), PushOutcome::Incomplete);
        assert_eq!(r.incomplete(), 1);
        assert_eq!(
            budget.usage(),
            (1, 400),
            "capacity is reserved in full up front"
        );
        (r, budget, now)
    };
    let payload = |n: usize| synthetic(n, 2);
    let cases: Vec<(&str, Vec<u8>, DiscardReason)> = vec![
        (
            "zero transaction id",
            [vec![0x01, 0, 0, 0], payload(178)].concat(),
            DiscardReason::ZeroTransaction,
        ),
        (
            "index gap",
            [vec![0x02, 5, 0, 0], payload(178)].concat(),
            DiscardReason::Order,
        ),
        (
            "short non-final payload",
            [vec![0x01, 5, 0, 0], payload(100)].concat(),
            DiscardReason::Payload,
        ),
        (
            "final payload too short",
            [vec![0x81, 5, 0, 0], payload(100)].concat(),
            DiscardReason::Payload,
        ),
        (
            "final payload too long (frame above the attribute cap)",
            [vec![0x81, 5, 0, 0], payload(179)].concat(),
            DiscardReason::FrameSize,
        ),
        (
            "frame shorter than header",
            vec![0x01, 5, 0],
            DiscardReason::FrameSize,
        ),
        ("empty frame", vec![], DiscardReason::FrameSize),
        (
            "frame above mtu-3 with a valid header",
            [vec![0x01, 5, 0, 0], payload(179)].concat(),
            DiscardReason::FrameSize,
        ),
    ];
    for (name, frame, reason) in cases {
        let (mut r, budget, now) = started(reassembler(MIN_MTU), Instant::now());
        assert_eq!(
            r.push(&frame, now),
            PushOutcome::Discarded(reason),
            "{name}"
        );
        assert_eq!(r.incomplete(), 0, "{name}: partial state dropped");
        assert_eq!(budget.usage(), (0, 0), "{name}: budget released");
    }
}

#[test]
fn first_fragment_total_must_be_1_to_4096() {
    for (name, total) in [
        ("zero", 0u16),
        ("4097", 4097),
        ("5120", 5120),
        ("max u16", 65535),
    ] {
        let (mut r, budget) = reassembler(MIN_MTU);
        let mut frame = vec![0x00, 9, (total >> 8) as u8, total as u8];
        frame.extend(synthetic(178, 1));
        assert_eq!(
            r.push(&frame, Instant::now()),
            PushOutcome::Discarded(DiscardReason::TotalLength),
            "{name}"
        );
        assert_eq!(budget.usage(), (0, 0));
    }
    let (mut r, _) = reassembler(MIN_MTU);
    let mut frame = vec![0x00, 9, 0x10, 0x00];
    frame.extend(synthetic(178, 1));
    assert_eq!(
        r.push(&frame, Instant::now()),
        PushOutcome::Incomplete,
        "4096 is the maximum"
    );
}

#[test]
fn out_of_order_and_stray_fragments_never_corrupt_live_transactions() {
    let (mut r, _) = reassembler(MIN_MTU);
    let now = Instant::now();
    let mut first = vec![0x00, 5, 0x01, 0x90];
    first.extend(synthetic(178, 1));
    assert_eq!(r.push(&first, now), PushOutcome::Incomplete);
    // Out of order: index 3 while index 1 is expected.
    let mut skip = vec![0x03, 5, 0, 0];
    skip.extend(synthetic(178, 3));
    assert_eq!(
        r.push(&skip, now),
        PushOutcome::Discarded(DiscardReason::Order)
    );
    // Stray fragment of an unknown transaction: no state change.
    let (mut r, budget) = reassembler(MIN_MTU);
    let now = Instant::now();
    let mut stray = vec![0x01, 77, 0, 0];
    stray.extend(synthetic(178, 2));
    assert_eq!(r.push(&stray, now), PushOutcome::Ignored);
    assert_eq!(budget.usage(), (0, 0));
    // Nothing follows a completed final fragment.
    let mut final_only = vec![0x80, 5, 0x00, 3];
    final_only.extend(synthetic(3, 1));
    assert!(matches!(r.push(&final_only, now), PushOutcome::Complete(_)));
    let mut after = vec![0x81, 5, 0, 0];
    after.extend(synthetic(3, 2));
    assert_eq!(r.push(&after, now), PushOutcome::Ignored);
}

#[test]
fn exact_size_rules_reject_every_off_by_one_at_mtu_517() {
    let p = 508usize;
    let m = MAX_MTU;
    let frame = |index: u8, last: bool, tid: u8, total: usize, payload: usize| {
        let mut frame = vec![
            index | if last { 0x80 } else { 0 },
            tid,
            (total >> 8) as u8,
            total as u8,
        ];
        frame.extend(synthetic(payload, index));
        frame
    };
    let accepts = |frames: &[Vec<u8>]| {
        let (mut r, _) = reassembler(m);
        let now = Instant::now();
        matches!(
            frames.iter().map(|f| r.push(f, now)).last(),
            Some(PushOutcome::Complete(_))
        )
    };
    // Non-final short by one, with a final that would otherwise make the total match.
    assert!(!accepts(&[
        frame(0, false, 5, 1016, p - 1),
        frame(1, true, 5, 0, p + 1)
    ]));
    // Non-final carrying more than remains.
    assert!(!accepts(&[frame(0, false, 5, 507, p)]));
    // Final short of / beyond the declared total.
    assert!(!accepts(&[
        frame(0, false, 5, 1000, p),
        frame(1, true, 5, 0, 491)
    ]));
    assert!(!accepts(&[
        frame(0, false, 5, 1000, p),
        frame(1, true, 5, 0, 493)
    ]));
    // Empty final while bytes remain.
    assert!(!accepts(&[
        frame(0, false, 5, 1000, p),
        frame(1, true, 5, 0, 0)
    ]));
    // Total 0 and 4097 are refused.
    assert!(!accepts(&[frame(0, true, 5, 0, 0)]));
    assert!(!accepts(&[frame(0, false, 5, 4097, p)]));
    // Gap and duplicate indices.
    assert!(!accepts(&[
        frame(0, false, 5, 1017, p),
        frame(2, true, 5, 0, 509 - 1)
    ]));
    assert!(!accepts(&[
        frame(0, false, 5, 1017, p),
        frame(0, false, 5, 0, p)
    ]));
    // The index field is 7 bits: a non-final index 127 has no live transaction to belong to
    // (stray), and against a live transaction it is out of order for any correct sender.
    assert!(!accepts(&[frame(127, false, 5, 4096, p)]));
    let (mut r, _) = reassembler(m);
    let now = Instant::now();
    assert_eq!(
        r.push(&frame(0, false, 5, 4096, p), now),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.push(&frame(127, false, 5, 0, p), now),
        PushOutcome::Discarded(DiscardReason::Order)
    );
    // The exact forms are accepted.
    assert!(accepts(&[
        frame(0, false, 5, 1016, p),
        frame(1, true, 5, 0, p)
    ]));
    assert!(accepts(&[frame(0, true, 5, p, p)]));
    // Nothing may follow a final fragment.
    let (mut r, _) = reassembler(m);
    let now = Instant::now();
    assert!(matches!(
        r.push(&frame(0, true, 5, p, p), now),
        PushOutcome::Complete(_)
    ));
    assert!(!matches!(
        r.push(&frame(1, true, 5, 0, 0), now),
        PushOutcome::Complete(_)
    ));
}

#[test]
fn receiver_accepts_empty_final_after_an_exact_multiple() {
    // Receiver tolerance only; the sender never emits it.
    let frame = |index: u8, last: bool, tid: u8, total: usize, payload: usize| {
        let mut frame = vec![
            index | if last { 0x80 } else { 0 },
            tid,
            (total >> 8) as u8,
            total as u8,
        ];
        frame.extend(synthetic(payload, index));
        frame
    };
    let accepts = |mtu: usize, frames: &[Vec<u8>]| {
        let (mut r, _) = reassembler(mtu);
        let now = Instant::now();
        matches!(
            frames.iter().map(|f| r.push(f, now)).last(),
            Some(PushOutcome::Complete(_))
        )
    };
    for mtu in [MIN_MTU, 514, 516, MAX_MTU] {
        let p = literal_payload(mtu);
        let tid = 9u8;
        let total = 2 * p;
        let mut tolerated = vec![frame(0, false, tid, total, p), frame(1, false, tid, 0, p)];
        tolerated.push(frame(2, true, tid, 0, 0));
        assert!(accepts(mtu, &tolerated), "mtu {mtu}");
        // Not an exact multiple: the same empty final is a length mismatch.
        let short = [
            frame(0, false, tid, total + 1, p),
            frame(1, false, tid, 0, p),
            frame(2, true, tid, 0, 0),
        ];
        assert!(!accepts(mtu, &short), "mtu {mtu}");
    }
}

#[test]
fn inactivity_timeout_at_five_seconds_and_timer_refresh() {
    let (mut r, budget) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    let mut first = vec![0x00, 5, 0x01, 0x90];
    first.extend(synthetic(178, 1));
    assert_eq!(r.push(&first, t0), PushOutcome::Incomplete);
    assert_eq!(r.expire(t0 + Duration::from_millis(4_999)), 0);
    assert_eq!(r.incomplete(), 1);
    assert_eq!(r.expire(t0 + Duration::from_secs(5)), 1);
    assert_eq!(r.incomplete(), 0);
    assert_eq!(budget.usage(), (0, 0));
    // The late remainder is no longer matched to a transaction.
    let mut late = vec![0x01, 5, 0, 0];
    late.extend(synthetic(178, 2));
    assert_eq!(
        r.push(&late, t0 + Duration::from_secs(6)),
        PushOutcome::Ignored
    );
}

#[test]
fn fragment_activity_refreshes_the_inactivity_timer() {
    let (mut r, _) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    let mut first = vec![0x00, 5, 0x01, 0x90];
    first.extend(synthetic(178, 1));
    assert_eq!(r.push(&first, t0), PushOutcome::Incomplete);
    let mut second = vec![0x01, 5, 0, 0];
    second.extend(synthetic(178, 2));
    assert_eq!(
        r.push(&second, t0 + Duration::from_secs(4)),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.expire(t0 + Duration::from_secs(8)),
        0,
        "only 4 s since the last fragment"
    );
    assert_eq!(r.expire(t0 + Duration::from_secs(9)), 1);
}

#[test]
fn repeated_first_fragment_two_conflicts_in_60s_disconnect_older_conflicts_forgotten() {
    let tid = 5u8;
    let start = |total: u16| {
        let mut frame = vec![0x00, tid, (total >> 8) as u8, total as u8];
        frame.extend(synthetic(178, 1));
        frame
    };
    let (mut r, budget) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    assert_eq!(r.push(&start(400), t0), PushOutcome::Incomplete);
    assert_eq!(
        r.push(&start(400), t0),
        PushOutcome::Ignored,
        "first conflict is rejected"
    );
    assert_eq!(
        budget.usage(),
        (0, 0),
        "the conflicting start erased the live partial and released its budget"
    );
    // A fresh start of the same id is live again; the second conflict inside the 60 s window
    // (still within the 5 s inactivity window of the fresh partial) disconnects.
    assert_eq!(
        r.push(&start(400), t0 + Duration::from_secs(1)),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.push(&start(400), t0 + Duration::from_secs(3)),
        PushOutcome::Disconnect
    );
    assert_eq!(
        r.incomplete(),
        0,
        "the conflicting link's partial state is dropped"
    );
    assert_eq!(
        budget.usage(),
        (0, 0),
        "the conflicting link's budget is released"
    );
    // Conflicts older than the 60 s window are forgotten: after the original transaction times
    // out, a fresh transaction with the same id is kept alive and a repeated start at +61 s is
    // only the first recorded conflict again (Ignored, not Disconnect).
    let (mut r, _) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    assert_eq!(r.push(&start(400), t0), PushOutcome::Incomplete);
    assert_eq!(r.push(&start(400), t0), PushOutcome::Ignored);
    let mut keep_alive = vec![0x01, tid, 0, 0];
    keep_alive.extend(synthetic(178, 2));
    assert_eq!(
        r.push(&start(400), t0 + Duration::from_secs(57)),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.push(&keep_alive, t0 + Duration::from_secs(60)),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.push(&start(400), t0 + Duration::from_secs(61)),
        PushOutcome::Ignored,
        "the conflict recorded at t0 is older than 60 s and no longer counts"
    );
}

#[test]
fn peer_and_global_reservation_exhaustion() {
    let budget = GlobalBudget::new();
    let now = Instant::now();
    let mut peers: Vec<Reassembler> = (0..9)
        .map(|_| Reassembler::new(link(MIN_MTU), budget.clone()))
        .collect();
    for (p, peer) in peers.iter_mut().enumerate().take(8) {
        for tx in [1u8, 2] {
            let mut frame = vec![0x00, tx, 0x10, 0x00];
            frame.extend(synthetic(178, p as u8));
            assert_eq!(
                peer.push(&frame, now),
                PushOutcome::Incomplete,
                "peer {p} tx {tx}"
            );
        }
    }
    assert_eq!(
        budget.usage(),
        (16, 65_536),
        "16 incomplete and 64 KiB are the global ceilings"
    );
    let mut frame = vec![0x00, 1, 0x00, 0x10];
    frame.extend(synthetic(178, 1));
    assert_eq!(
        peers[8].push(&frame, now),
        PushOutcome::Ignored,
        "17th incomplete transaction"
    );
    assert_eq!(budget.usage(), (16, 65_536));
    peers.remove(0); // dropping a peer releases its share
    assert_eq!(budget.usage(), (14, 57_344));
}

#[test]
fn only_two_incomplete_transactions_per_peer() {
    let (mut r, budget) = reassembler(MIN_MTU);
    let now = Instant::now();
    for tx in [41u8, 42] {
        let mut frame = vec![0x00, tx, 0x10, 0x00];
        frame.extend(synthetic(178, tx));
        assert_eq!(r.push(&frame, now), PushOutcome::Incomplete);
    }
    assert_eq!(
        budget.usage(),
        (2, 8192),
        "two maximum-size incomplete records fit 8 KiB exactly"
    );
    let third = [vec![0x80, 43, 0, 1], vec![0xab]].concat();
    assert_eq!(r.push(&third, now), PushOutcome::Ignored);
    assert_eq!(budget.usage(), (2, 8192));
    assert_eq!(r.incomplete(), 2);
}

#[test]
fn budget_counters_return_to_zero_on_completion_and_drop() {
    // Completion.
    let budget = GlobalBudget::new();
    let mut r = Reassembler::new(link(MIN_MTU), budget.clone());
    let mut first = vec![0x00, 5, 0x01, 0x64];
    first.extend(synthetic(178, 1));
    let mut last = vec![0x81, 5, 0, 0];
    last.extend(synthetic(178, 2));
    assert_eq!(r.push(&first, Instant::now()), PushOutcome::Incomplete);
    assert_eq!(budget.usage(), (1, 356));
    assert!(matches!(
        r.push(&last, Instant::now()),
        PushOutcome::Complete(_)
    ));
    assert_eq!(
        budget.usage(),
        (0, 0),
        "completion releases the reservation"
    );
    // Drop.
    {
        let mut r = Reassembler::new(link(MIN_MTU), budget.clone());
        let mut first = vec![0x00, 7, 0x01, 0x90];
        first.extend(synthetic(178, 3));
        r.push(&first, Instant::now());
        assert_eq!(budget.usage(), (1, 400));
        drop(r);
    }
    assert_eq!(budget.usage(), (0, 0), "drop releases the reservation");
}

#[test]
fn payload_bytes_never_reach_debug_output() {
    let record = synthetic(16, 0x5A);
    // All frames and completed records are opaque Payloads with redacted Debug.
    let frames = fragment(&record, link(MIN_MTU), 9).unwrap();
    for frame in &frames {
        assert_eq!(
            format!("{frame:?}"),
            format!("Payload({} bytes)", frame.len())
        );
    }
    let (mut r, _) = reassembler(MIN_MTU);
    let now = Instant::now();
    let mut outcome = PushOutcome::Incomplete;
    for frame in &frames {
        outcome = r.push(frame.as_slice(), now);
    }
    match outcome {
        PushOutcome::Complete(payload) => {
            assert_eq!(
                payload.as_slice(),
                record.as_slice(),
                "reassembly is byte exact"
            );
            assert_eq!(
                format!("{payload:?}"),
                format!("Payload({} bytes)", payload.len())
            );
        }
        other => panic!("{other:?}"),
    }
}

// ---- committed protocol fixtures ------------------------------------------------------------

/// Per-frame outcome expected from the frozen revision-2 receiver. `Complete(n)` completes with
/// the concatenated payloads of the transaction's first `n` frames (`n == frames.len()` is the
/// canonical completion, `n < frames.len()` marks the completing frame before trailing strays).
#[derive(Debug, Clone, Copy, PartialEq)]
enum Expect {
    Incomplete,
    Ignored,
    Discarded(DiscardReason),
    Complete(usize),
}

/// Live-state ledger after one transaction: `(incomplete transactions, reserved bytes)`.
type Ledger = (usize, usize);
/// One transaction's frozen trace: (fixture stage, per-frame expected outcomes, ledger).
type TxExpect<'a> = (&'static str, &'a [Expect], Ledger);
/// One fixture's frozen traces, keyed to the committed fixture id.
type SpecialExpect<'a> = (&'static str, &'a [TxExpect<'a>]);

fn assert_outcome(outcome: PushOutcome, expect: &Expect, frames: &[Vec<u8>], label: &str) {
    match expect {
        Expect::Incomplete => assert_eq!(outcome, PushOutcome::Incomplete, "{label}: {outcome:?}"),
        Expect::Ignored => assert_eq!(outcome, PushOutcome::Ignored, "{label}: {outcome:?}"),
        Expect::Discarded(reason) => {
            assert_eq!(
                outcome,
                PushOutcome::Discarded(*reason),
                "{label}: {outcome:?}"
            );
        }
        Expect::Complete(upto) => match outcome {
            PushOutcome::Complete(payload) => {
                let record: Vec<u8> = frames[..*upto]
                    .iter()
                    .flat_map(|f| f[4..].to_vec())
                    .collect();
                assert_eq!(payload.as_slice(), record.as_slice(), "{label}");
            }
            other => panic!("{label}: expected Complete, got {other:?}"),
        },
    }
}

#[test]
fn committed_fragmentation_fixtures_behave_as_specified() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../protocol/v1/fixtures/fragmentation.json"
    );
    let document: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).expect("fixture file")).unwrap();
    let fixtures = document["fixtures"].as_array().unwrap();
    assert_eq!(fixtures.len(), 84, "frozen fixture count");
    assert!(
        fixtures.iter().any(|f| f["id"] == "frag-r2-mtu517-len4096"),
        "r2 vectors must be present"
    );

    // Frozen revision-2 per-fragment traces for every fixture whose transactions are not a
    // canonical accepted completion, keyed by fixture id. Each transaction entry is
    // (fixture stage, per-frame expected outcomes, expected live-state ledger after the
    // transaction as `(incomplete transactions, reserved bytes)`). The discard reasons are the
    // frozen receiver's own: the frame-size check precedes index/order checks, index-0 total
    // validation precedes allocation, a first repeated start is a conflict (rejected, the live partial
    // erased and its budget released) and only the second within 60 s disconnects, strays after completion or of
    // unknown transactions are ignored, and a malformed fragment discards the peer's state.
    let legacy_frames: [Expect; 9] = [
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Discarded(DiscardReason::FrameSize),
        Expect::Ignored,
    ];
    let special: &[SpecialExpect] = &[
        // accepted:true fixtures whose transactions stay incomplete, and the duplicate start
        // that is ignored without touching the live transaction
        (
            "frag-two-concurrent-incomplete",
            &[
                ("", &[Expect::Incomplete], (1, 512)),
                ("", &[Expect::Incomplete], (2, 768)),
            ],
        ),
        (
            "frag-live-transaction-duplicate-ignored",
            &[
                ("", &[Expect::Incomplete], (1, 200)),
                ("", &[Expect::Ignored], (0, 0)),
                ("", &[Expect::Incomplete, Expect::Complete(2)], (0, 0)),
            ],
        ),
        // malformed declared length on a LIVE id is still a conflict (precedes length validation):
        // the live partial is erased and its reservation released, then a fresh start completes
        (
            "frag-r2-live-duplicate-start-length-0-erases",
            &[
                ("", &[Expect::Incomplete], (1, 200)),
                ("", &[Expect::Ignored], (0, 0)),
                ("", &[Expect::Incomplete, Expect::Complete(2)], (0, 0)),
            ],
        ),
        // malformed declared length on a LIVE id is still a conflict (precedes length validation):
        // the live partial is erased and its reservation released, then a fresh start completes
        (
            "frag-r2-live-duplicate-start-length-4097-erases",
            &[
                ("", &[Expect::Incomplete], (1, 200)),
                ("", &[Expect::Ignored], (0, 0)),
                ("", &[Expect::Incomplete, Expect::Complete(2)], (0, 0)),
            ],
        ),
        // an over-budget frame is rejected BEFORE conflict handling: no conflict is counted and
        // every partial of the peer (ids 52 and 53) is discarded, so a fresh start of 53 completes
        (
            "frag-r2-over-budget-duplicate-start-discards-peer-state",
            &[
                ("", &[Expect::Incomplete], (1, 200)),
                ("", &[Expect::Incomplete], (2, 400)),
                (
                    "fragment_total",
                    &[Expect::Discarded(DiscardReason::FrameSize)],
                    (0, 0),
                ),
                ("", &[Expect::Incomplete, Expect::Complete(2)], (0, 0)),
            ],
        ),
        // caps: the third start finds both peer slots busy: ignored, state preserved
        (
            "frag-third-transaction-cap",
            &[
                ("", &[Expect::Incomplete], (1, 512)),
                ("", &[Expect::Incomplete], (2, 768)),
                ("fragment_caps", &[Expect::Ignored], (2, 768)),
            ],
        ),
        // fragment_header stage: violations detectable at the header (discard reason below may
        // still be FrameSize/ZeroTransaction/TotalLength/Order — those are distinct header rules)
        (
            "frag-txid-zero",
            &[(
                "fragment_header",
                &[Expect::Discarded(DiscardReason::ZeroTransaction)],
                (0, 0),
            )],
        ),
        (
            "frag-reserved-nonzero",
            &[(
                "fragment_header",
                &[Expect::Incomplete, Expect::Discarded(DiscardReason::Order)],
                (0, 0),
            )],
        ),
        (
            "frag-total-zero",
            &[(
                "fragment_header",
                &[Expect::Discarded(DiscardReason::TotalLength)],
                (0, 0),
            )],
        ),
        (
            "frag-total-over-max",
            &[(
                "fragment_header",
                &[Expect::Discarded(DiscardReason::TotalLength)],
                (0, 0),
            )],
        ),
        (
            "frag-short-header",
            &[(
                "fragment_header",
                &[Expect::Discarded(DiscardReason::FrameSize)],
                (0, 0),
            )],
        ),
        // fragment_total stage: declared-total and payload-length validation
        (
            "frag-payload-over-budget",
            &[(
                "fragment_total",
                &[Expect::Discarded(DiscardReason::FrameSize)],
                (0, 0),
            )],
        ),
        (
            "frag-nonfinal-payload-short",
            &[(
                "fragment_total",
                &[
                    Expect::Discarded(DiscardReason::Payload),
                    Expect::Discarded(DiscardReason::FrameSize),
                ],
                (0, 0),
            )],
        ),
        (
            "frag-sum-mismatch",
            &[(
                "fragment_total",
                &[Expect::Discarded(DiscardReason::Payload)],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-legacy-r1-frames-rejected",
            &[("fragment_total", legacy_frames.as_slice(), (0, 0))],
        ),
        (
            "frag-r2-mtu517-nonfinal-513-attribute",
            &[(
                "fragment_total",
                &[Expect::Discarded(DiscardReason::FrameSize)],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-final-513-attribute",
            &[(
                "fragment_total",
                &[Expect::Discarded(DiscardReason::FrameSize)],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu514-512-attribute-over-budget",
            &[(
                "fragment_total",
                &[Expect::Discarded(DiscardReason::FrameSize)],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-nonfinal-short-507",
            &[(
                "fragment_total",
                &[
                    Expect::Discarded(DiscardReason::Payload),
                    Expect::Discarded(DiscardReason::FrameSize),
                ],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-nonfinal-exceeds-remaining",
            &[(
                "fragment_total",
                &[Expect::Discarded(DiscardReason::Payload)],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-final-short-of-total",
            &[(
                "fragment_total",
                &[
                    Expect::Incomplete,
                    Expect::Discarded(DiscardReason::Payload),
                ],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-empty-final-after-partial",
            &[(
                "fragment_total",
                &[
                    Expect::Incomplete,
                    Expect::Discarded(DiscardReason::Payload),
                ],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-total-4097",
            &[(
                "fragment_header",
                &[Expect::Discarded(DiscardReason::TotalLength)],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-total-0",
            &[(
                "fragment_header",
                &[Expect::Discarded(DiscardReason::TotalLength)],
                (0, 0),
            )],
        ),
        // fragment_index stage: gap against a live transaction, duplicate index-0 start of a live
        // transaction (first conflict: rejected, the live partial erased and its budget released), a stray
        // start with a nonzero first index, and the r2 gap whose second frame is both oversized
        // and out of order (the frozen order rejects it at frame size first)
        (
            "frag-index-gap",
            &[(
                "fragment_index",
                &[Expect::Incomplete, Expect::Discarded(DiscardReason::Order)],
                (0, 0),
            )],
        ),
        (
            "frag-index-duplicate",
            &[(
                "fragment_index",
                &[Expect::Incomplete, Expect::Ignored],
                (0, 0),
            )],
        ),
        (
            "frag-first-fragment-nonzero-index",
            &[("fragment_index", &[Expect::Ignored], (0, 0))],
        ),
        (
            "frag-r2-mtu517-index-gap",
            &[(
                "fragment_index",
                &[
                    Expect::Incomplete,
                    Expect::Discarded(DiscardReason::FrameSize),
                ],
                (0, 0),
            )],
        ),
        // fragment_final stage: the record completes, the trailing fragments are strays
        (
            "frag-final-then-extra-fragment",
            &[(
                "fragment_final",
                &[Expect::Complete(1), Expect::Ignored],
                (0, 0),
            )],
        ),
        (
            "frag-r2-mtu517-full-final-then-extra",
            &[(
                "fragment_final",
                &[Expect::Complete(1), Expect::Ignored],
                (0, 0),
            )],
        ),
    ];
    assert_eq!(
        special.len(),
        30,
        "frozen rejected/special fixture table size"
    );

    let mut classified = [0usize, 0]; // [trace-table, canonical completion]
    for fixture in fixtures {
        let id = fixture["id"].as_str().unwrap();
        let mtu = fixture["input"]["mtu"].as_u64().unwrap() as usize;
        let (mut r, budget) = reassembler(mtu);
        let now = Instant::now();
        let transactions = fixture["input"]["transactions"].as_array().unwrap();
        let expected = fixture["expected"]["transactions"].as_array().unwrap();
        if let Some((_, tx_expectations)) = special.iter().find(|(fid, _)| *fid == id) {
            classified[0] += 1;
            assert_eq!(
                tx_expectations.len(),
                transactions.len(),
                "{id}: transaction count"
            );
            for (ti, transaction) in transactions.iter().enumerate() {
                let (stage, per_frame, end_state) = tx_expectations[ti];
                let want = &expected[ti];
                if let Some(actual) = want.get("stage") {
                    assert_eq!(
                        actual.as_str().unwrap(),
                        stage,
                        "{id} tx{ti}: stage vocabulary"
                    );
                } else {
                    assert_eq!(stage, "", "{id} tx{ti}: fixture has no stage field");
                }
                let frames: Vec<Vec<u8>> = transaction["fragments"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|f| hex_decode(f.as_str().unwrap()))
                    .collect();
                assert_eq!(
                    per_frame.len(),
                    frames.len(),
                    "{id} tx{ti}: frame trace length"
                );
                for (fi, frame) in frames.iter().enumerate() {
                    let outcome = r.push(frame, now);
                    assert_outcome(
                        outcome,
                        &per_frame[fi],
                        &frames,
                        &format!("{id} tx{ti} f{fi}"),
                    );
                }
                assert_eq!(
                    (r.incomplete(), budget.usage()),
                    (end_state.0, end_state),
                    "{id} tx{ti}: live-state ledger"
                );
            }
        } else {
            classified[1] += 1;
            assert_eq!(
                transactions.len(),
                1,
                "{id}: non-canonical fixture missing from the trace table"
            );
            assert_eq!(fixture["expected"]["accepted"], true, "{id}");
            let want = &expected[0];
            assert_eq!(want["completed"], true, "{id}: expected completion");
            let frames: Vec<Vec<u8>> = transactions[0]["fragments"]
                .as_array()
                .unwrap()
                .iter()
                .map(|f| hex_decode(f.as_str().unwrap()))
                .collect();
            assert!(!frames.is_empty(), "{id}");
            let last = frames.len() - 1;
            for (fi, frame) in frames.iter().enumerate() {
                let expect = if fi == last {
                    Expect::Complete(frames.len())
                } else {
                    Expect::Incomplete
                };
                let outcome = r.push(frame, now);
                assert_outcome(outcome, &expect, &frames, &format!("{id} f{fi}"));
            }
            let concatenated: Vec<u8> = frames.iter().flat_map(|f| f[4..].to_vec()).collect();
            assert_eq!(
                concatenated,
                hex_decode(want["reassembled"].as_str().unwrap()),
                "{id}: payloads equal the committed reassembled record"
            );
            assert_eq!(
                (r.incomplete(), budget.usage()),
                (0, (0, 0)),
                "{id}: completion cleanup"
            );
        }
    }
    assert_eq!(
        classified,
        [30, 54],
        "fixture classification partition (30 + 54 = 84)"
    );
}

#[test]
fn sender_reproduces_every_canonical_accepted_fixture_byte_for_byte() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../protocol/v1/fixtures/fragmentation.json"
    );
    let document: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let mut compared = 0;
    let mut empty_final = 0;
    let mut classified = 0;
    for fixture in document["fixtures"].as_array().unwrap() {
        let id = fixture["id"].as_str().unwrap();
        let transactions = fixture["input"]["transactions"].as_array().unwrap();
        let expected = &fixture["expected"]["transactions"][0];
        if fixture["expected"]["accepted"] != true
            || transactions.len() != 1
            || expected["completed"] != true
        {
            continue;
        }
        classified += 1;
        let committed: Vec<Vec<u8>> = transactions[0]["fragments"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| hex_decode(f.as_str().unwrap()))
            .collect();
        // A trailing empty final is receiver tolerance only; the sender never emits it.
        if committed.last().unwrap().len() == 4 {
            empty_final += 1;
            continue;
        }
        let mtu = fixture["input"]["mtu"].as_u64().unwrap() as usize;
        let record = hex_decode(expected["reassembled"].as_str().unwrap());
        let tid = transactions[0]["transaction_id"].as_u64().unwrap() as u8;
        let frames = fragment(&record, link(mtu), tid).unwrap();
        let wire: Vec<Vec<u8>> = frames.iter().map(|f| f.as_slice().to_vec()).collect();
        assert_eq!(wire, committed, "{id}");
        compared += 1;
    }
    // Frozen counts: 54 accepted single-transaction completed fixtures = 51 canonical sender
    // vectors + 3 receiver-only empty-final tolerances. An exact guard so fixture gain/loss
    // can never silently change the asserted coverage.
    assert_eq!(compared, 51, "frozen canonical sender vectors");
    assert_eq!(empty_final, 3, "receiver-only empty-final tolerances");
    assert_eq!(
        classified, 54,
        "accepted single-transaction completed fixtures"
    );
}

// ---------- OTP2-GAT-011 conflict policy (owner ruling, "approach 2") ----------
// A start (index 0) for an id that is already live erases the existing partial, releases its
// peer and global budget and is itself rejected. Later nonzero continuations find no live
// partial and are ignored; a later fresh index 0 may start; the rolling-60 s conflict count
// (second conflicting start disconnects) is preserved.

fn conflict_frame(index: u8, last: bool, id: u8, total: u16, fill: u8, payload: usize) -> Vec<u8> {
    let mut frame = vec![
        index | if last { 0x80 } else { 0 },
        id,
        (total >> 8) as u8,
        total as u8,
    ];
    frame.extend(std::iter::repeat_n(fill, payload));
    frame
}

#[test]
fn same_id_prefix_plus_foreign_suffix_never_completes() {
    // Live prefix A (id 7, total 200: 178 + 22), conflicting start B (same id), then B's
    // would-be final suffix. Under the old policy the suffix completed prefix-A||suffix-B.
    let (mut r, budget) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    let a0 = conflict_frame(0, false, 7, 200, 0xA1, 178);
    let b0 = conflict_frame(0, false, 7, 200, 0xB2, 178);
    let b1 = conflict_frame(1, true, 7, 0, 0xB2, 22);
    assert_eq!(r.push(&a0, t0), PushOutcome::Incomplete);
    assert_eq!(r.push(&b0, t0), PushOutcome::Ignored);
    assert_eq!(
        r.push(&b1, t0),
        PushOutcome::Ignored,
        "suffix has no live partial"
    );
    assert_eq!(r.incomplete(), 0);
    assert_eq!(budget.usage(), (0, 0));
}

#[test]
fn conflicting_start_erases_partial_and_releases_peer_and_global_budget() {
    let budget = GlobalBudget::new();
    let t0 = Instant::now();
    let mut peer = Reassembler::new(link(MIN_MTU), budget.clone());
    let mut other = Reassembler::new(link(MIN_MTU), budget.clone());
    assert_eq!(
        peer.push(&conflict_frame(0, false, 1, 4000, 1, 178), t0),
        PushOutcome::Incomplete
    );
    assert_eq!(
        peer.push(&conflict_frame(0, false, 2, 300, 2, 178), t0),
        PushOutcome::Incomplete
    );
    assert_eq!(
        other.push(&conflict_frame(0, false, 9, 500, 3, 178), t0),
        PushOutcome::Incomplete
    );
    assert_eq!(budget.usage(), (3, 4800));
    // Peer is at its 2-transaction cap; conflicting start of id 1 erases only id 1.
    assert_eq!(
        peer.push(&conflict_frame(0, false, 1, 100, 4, 178), t0),
        PushOutcome::Ignored
    );
    assert_eq!(peer.incomplete(), 1);
    assert_eq!(
        budget.usage(),
        (2, 800),
        "id 1's 4000 bytes released; id 2 and the other peer remain"
    );
    // The freed peer slot and bytes are immediately usable (4000 bytes fit again under 8 KiB).
    assert_eq!(
        peer.push(&conflict_frame(0, false, 3, 4000, 5, 178), t0),
        PushOutcome::Incomplete
    );
    assert_eq!(budget.usage(), (3, 4800));
    drop(peer);
    assert_eq!(budget.usage(), (1, 500));
}

#[test]
fn fresh_start_after_discard_is_accepted_and_completes_exactly() {
    let (mut r, budget) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    assert_eq!(
        r.push(&conflict_frame(0, false, 7, 200, 0xA1, 178), t0),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.push(&conflict_frame(0, false, 7, 200, 0xB2, 178), t0),
        PushOutcome::Ignored
    );
    // Fresh index 0 for the same id starts a new transaction that completes with only its own bytes.
    assert_eq!(
        r.push(&conflict_frame(0, false, 7, 200, 0xC3, 178), t0),
        PushOutcome::Incomplete
    );
    assert_eq!(budget.usage(), (1, 200));
    let PushOutcome::Complete(done) = r.push(&conflict_frame(1, true, 7, 0, 0xC3, 22), t0) else {
        panic!("fresh transaction must complete");
    };
    assert_eq!(done.as_slice(), &vec![0xC3u8; 200][..]);
    assert_eq!(budget.usage(), (0, 0));
}

#[test]
fn conflict_count_spans_first_discard_and_second_conflict_of_a_fresh_start() {
    let (mut r, budget) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    let start = conflict_frame(0, false, 7, 200, 0xA1, 178);
    assert_eq!(r.push(&start, t0), PushOutcome::Incomplete);
    assert_eq!(
        r.push(&start, t0 + Duration::from_secs(1)),
        PushOutcome::Ignored,
        "conflict 1: discard"
    );
    // The id is no longer live, so a repeat start is a fresh start, not a conflict.
    assert_eq!(
        r.push(&start, t0 + Duration::from_secs(2)),
        PushOutcome::Incomplete
    );
    assert_eq!(budget.usage(), (1, 200));
    // Starting another live transaction makes the next same-id start an active second conflict.
    assert_eq!(
        r.push(&start, t0 + Duration::from_secs(3)),
        PushOutcome::Disconnect,
        "conflict 2 within 60 s of conflict 1 disconnects"
    );
    assert_eq!(r.incomplete(), 0);
    assert_eq!(budget.usage(), (0, 0));
}

#[test]
fn conflict_older_than_sixty_seconds_is_forgotten_after_discard() {
    let (mut r, _) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    let start = conflict_frame(0, false, 7, 200, 0xA1, 178);
    assert_eq!(r.push(&start, t0), PushOutcome::Incomplete);
    assert_eq!(r.push(&start, t0), PushOutcome::Ignored);
    let at = t0 + Duration::from_secs(61);
    assert_eq!(r.push(&start, at), PushOutcome::Incomplete);
    assert_eq!(
        r.push(&start, at),
        PushOutcome::Ignored,
        "first conflict of a new window"
    );
}

#[test]
fn malformed_declared_length_on_a_live_id_is_a_conflict_that_erases_the_partial() {
    for bad_total in [0u16, 4097] {
        let (mut r, budget) = reassembler(MIN_MTU);
        let t0 = Instant::now();
        let live = conflict_frame(0, false, 7, 200, 0xA1, 178);
        let dup = conflict_frame(0, false, 7, bad_total, 0xEE, 178);
        let suffix = conflict_frame(1, true, 7, 0, 0xA1, 22);
        assert_eq!(r.push(&live, t0), PushOutcome::Incomplete);
        assert_eq!(
            r.push(&dup, t0),
            PushOutcome::Ignored,
            "total={bad_total}: conflict precedes length validation"
        );
        assert_eq!(budget.usage(), (0, 0), "total={bad_total}: partial erased");
        assert_eq!(r.push(&suffix, t0), PushOutcome::Ignored);
        // Fresh start proceeds and completes with only its own bytes.
        assert_eq!(r.push(&live, t0), PushOutcome::Incomplete);
        let PushOutcome::Complete(done) = r.push(&suffix, t0) else {
            panic!("fresh transaction completes")
        };
        assert_eq!(done.as_slice(), &vec![0xA1u8; 200][..]);
    }
}

#[test]
fn malformed_duplicate_counts_toward_the_window_and_second_conflict_disconnects() {
    for bad_total in [0u16, 4097] {
        let (mut r, budget) = reassembler(MIN_MTU);
        let t0 = Instant::now();
        let live = conflict_frame(0, false, 7, 200, 0xA1, 178);
        let dup = conflict_frame(0, false, 7, bad_total, 0xEE, 178);
        assert_eq!(r.push(&live, t0), PushOutcome::Incomplete);
        assert_eq!(r.push(&dup, t0), PushOutcome::Ignored);
        assert_eq!(
            r.push(&live, t0 + Duration::from_secs(1)),
            PushOutcome::Incomplete
        );
        assert_eq!(
            r.push(&dup, t0 + Duration::from_secs(2)),
            PushOutcome::Disconnect,
            "total={bad_total}"
        );
        assert_eq!(budget.usage(), (0, 0));
    }
}

#[test]
fn over_budget_duplicate_start_is_a_frame_discard_not_a_conflict() {
    // Exact state and counting: the over-budget frame discards every partial of the peer and
    // releases every reservation, and it is NOT recorded in the conflict window. Afterwards one
    // in-budget conflict is only a FIRST conflict (Ignored); a second one disconnects.
    let (mut r, budget) = reassembler(MIN_MTU);
    let t0 = Instant::now();
    let live = |id: u8| conflict_frame(0, false, id, 200, 0xA1, 178);
    let over = conflict_frame(0, false, 52, 200, 0xEE, 179); // 4 + 179 = 183 > 182
    assert_eq!(r.push(&live(52), t0), PushOutcome::Incomplete);
    assert_eq!(r.push(&live(53), t0), PushOutcome::Incomplete);
    assert_eq!(budget.usage(), (2, 400));
    assert_eq!(
        r.push(&over, t0),
        PushOutcome::Discarded(DiscardReason::FrameSize)
    );
    assert_eq!((r.incomplete(), budget.usage()), (0, (0, 0)));
    // 53 is no longer live: a start is plain, not a conflict.
    assert_eq!(r.push(&live(53), t0), PushOutcome::Incomplete);
    assert_eq!(r.push(&live(52), t0), PushOutcome::Incomplete);
    // No conflict was fabricated by the over-budget frame: this is the FIRST conflict.
    assert_eq!(
        r.push(&live(52), t0 + Duration::from_secs(1)),
        PushOutcome::Ignored
    );
    assert_eq!(
        r.push(&live(52), t0 + Duration::from_secs(2)),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.push(&live(52), t0 + Duration::from_secs(3)),
        PushOutcome::Disconnect
    );
    assert_eq!(budget.usage(), (0, 0));
}
