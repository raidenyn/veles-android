//! Whole-record fragmentation/reassembly against the Android peripheral rules (OTP2-GAT-010..012),
//! the committed `protocol/v1/fixtures/fragmentation.json` vectors and malformed-fragment cases.

mod common;

use std::sync::Arc;
use std::time::{Duration, Instant};

use common::{budget as link, hex_decode, synthetic};
use veles_otp23_feasibility::fragment::{
    ATTRIBUTE_VALUE_MAX, BudgetError, DiscardReason, FragmentError, GlobalBudget, LinkBudget,
    LinkCapacity, MAX_MTU, MIN_MTU, PushOutcome, Reassembler, fragment,
};

fn reassembler(mtu: usize) -> (Reassembler, Arc<GlobalBudget>) {
    let budget = GlobalBudget::new();
    (Reassembler::new(link(mtu), budget.clone()), budget)
}

fn feed_all(frames: &[Vec<u8>], mtu: usize) -> PushOutcome {
    let (mut r, _) = reassembler(mtu);
    let now = Instant::now();
    let mut last = PushOutcome::Incomplete;
    for (i, frame) in frames.iter().enumerate() {
        last = r.push(frame, now);
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

// ---- sender boundaries ----------------------------------------------------------------------

#[test]
fn record_length_zero_and_above_4096_are_refused_at_fragmentation_time() {
    assert_eq!(
        fragment(&[], link(MIN_MTU), 1),
        Err(FragmentError::EmptyRecord)
    );
    assert_eq!(
        fragment(&synthetic(4097, 1), link(MIN_MTU), 1),
        Err(FragmentError::OversizeRecord)
    );
    assert!(fragment(&synthetic(1, 1), link(MIN_MTU), 1).is_ok());
    assert!(fragment(&synthetic(4096, 1), link(MIN_MTU), 1).is_ok());
}

#[test]
fn transaction_id_domain_and_link_capacity_domains_are_enforced() {
    let record = synthetic(10, 1);
    assert_eq!(
        fragment(&record, link(MIN_MTU), 0),
        Err(FragmentError::BadTransactionId)
    );
    assert!(fragment(&record, link(MIN_MTU), 255).is_ok());
    assert!(fragment(&record, link(MAX_MTU), 1).is_ok());
    // The ATT MTU range is enforced once, where the budget is derived.
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
}

#[test]
fn chunking_at_the_mtu_floor_matches_the_frozen_boundaries() {
    // payload = 185 - 3 - 4 = 178 per non-final fragment.
    let cases = [
        (1usize, 1usize),
        (177, 1),
        (178, 1),
        (179, 2),
        (355, 2),
        (356, 2),
        (357, 3),
        (4095, 24),
        (4096, 24),
    ];
    for (len, fragments) in cases {
        let record = synthetic(len, 3);
        let frames = fragment(&record, link(MIN_MTU), 7).unwrap();
        assert_eq!(frames.len(), fragments, "record length {len}");
        for (index, frame) in frames.iter().enumerate() {
            let last = index == frames.len() - 1;
            assert_eq!(frame[0] & 0x7f, index as u8, "index byte, len {len}");
            assert_eq!(
                frame[0] & 0x80 != 0,
                last,
                "final flag, len {len} frag {index}"
            );
            assert_eq!(frame[1], 7);
            let total = u16::from_be_bytes([frame[2], frame[3]]);
            assert_eq!(
                total,
                if index == 0 { len as u16 } else { 0 },
                "length field"
            );
            if !last {
                assert_eq!(frame.len(), 4 + 178, "non-final payload is exactly MTU-7");
            }
            assert!(frame.len() <= MIN_MTU - 3, "fits one ATT write");
        }
        let joined: Vec<u8> = frames.iter().flat_map(|f| f[4..].to_vec()).collect();
        assert_eq!(
            joined, record,
            "payloads concatenate to the record, len {len}"
        );
    }
}

#[test]
fn max_record_is_23_full_fragments_plus_two_bytes_at_mtu_185() {
    let frames = fragment(&synthetic(4096, 9), link(MIN_MTU), 24).unwrap();
    assert_eq!(frames.len(), 24);
    assert!(frames[..23].iter().all(|f| f.len() == 182));
    assert_eq!(frames[23].len(), 4 + 2);
}

#[test]
fn max_mtu_uses_the_capped_non_final_fragments() {
    // OTP-23 PoC: the 512-byte attribute cap shrinks the 517-MTU non-final payload to 508.
    let frames = fragment(&synthetic(4096, 9), link(MAX_MTU), 1).unwrap();
    assert_eq!(frames.len(), 9); // ceil(4096 / 508)
    assert!(frames[..8].iter().all(|f| f.len() == 4 + 508));
    assert_eq!(frames[8].len(), 4 + 32);
}

#[test]
fn round_trip_every_boundary_length_at_floor_and_ceiling_mtu() {
    for mtu in [MIN_MTU, 247, MAX_MTU] {
        let payload = (mtu - 3).min(ATTRIBUTE_VALUE_MAX) - 4;
        let mut lengths = vec![
            1,
            2,
            payload - 1,
            payload,
            payload + 1,
            2 * payload - 1,
            2 * payload,
            2 * payload + 1,
        ];
        lengths.extend([4095, 4096]);
        for len in lengths {
            let record = synthetic(len, len as u8);
            let frames = fragment(&record, link(mtu), 200).unwrap();
            match feed_all(&frames, mtu) {
                PushOutcome::Complete(bytes) => assert_eq!(bytes, record, "mtu {mtu} len {len}"),
                other => panic!("mtu {mtu} len {len}: {other:?}"),
            }
        }
    }
}

// ---- receiver rules ----------------------------------------------------------------------------

#[test]
fn zero_length_final_fragment_is_accepted_when_the_record_is_an_exact_multiple() {
    // Fixture style: two full non-final fragments then an empty final one (356 bytes).
    let record = synthetic(356, 5);
    let mut frames = fragment(&record, link(MIN_MTU), 25).unwrap();
    let last = frames.pop().unwrap();
    let mut non_final = last.clone();
    non_final[0] &= 0x7f; // the sender's last chunk becomes non-final...
    frames.push(non_final);
    frames.push(vec![0x82, 25, 0, 0]); // ...followed by an empty final fragment, index 2
    assert!(matches!(feed_all(&frames, MIN_MTU), PushOutcome::Complete(b) if b == record));
}

fn started(total: u16) -> (Reassembler, Arc<GlobalBudget>, Instant) {
    let (mut r, budget) = reassembler(MIN_MTU);
    let now = Instant::now();
    let mut first = vec![0x00, 5, (total >> 8) as u8, total as u8];
    first.extend(synthetic(178, 1));
    assert_eq!(r.push(&first, now), PushOutcome::Incomplete);
    assert_eq!(r.incomplete(), 1);
    assert_eq!(budget.usage(), (1, total as usize));
    (r, budget, now)
}

#[test]
fn malformed_fragments_discard_the_transaction_and_free_its_budget() {
    let payload = |n: usize| synthetic(n, 2);
    let cases: Vec<(&str, Vec<u8>, DiscardReason)> = vec![
        (
            "zero transaction id",
            [vec![0x01, 0, 0, 0], payload(178)].concat(),
            DiscardReason::ZeroTransaction,
        ),
        (
            "reserved byte 2 set",
            [vec![0x01, 5, 0, 1], payload(178)].concat(),
            DiscardReason::Order,
        ),
        (
            "reserved byte 3 set",
            [vec![0x01, 5, 1, 0], payload(178)].concat(),
            DiscardReason::Order,
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
            "final payload too long",
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
            "frame above MTU-3",
            [vec![0x01, 5, 0, 0], payload(179)].concat(),
            DiscardReason::FrameSize,
        ),
    ];
    for (name, frame, reason) in cases {
        let (mut r, budget, now) = started(400);
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
    for (name, total) in [("zero", 0u16), ("4097", 4097), ("max u16", 65535)] {
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
fn duplicate_index_and_stray_fragments_of_unknown_transactions() {
    let (mut r, _, now) = started(400);
    let mut second = vec![0x01, 5, 0, 0];
    second.extend(synthetic(178, 2));
    assert_eq!(r.push(&second, now), PushOutcome::Incomplete);
    assert_eq!(
        r.push(&second, now),
        PushOutcome::Discarded(DiscardReason::Order),
        "duplicate index 1"
    );
    let (mut r, _) = reassembler(MIN_MTU);
    let mut stray = vec![0x01, 77, 0, 0];
    stray.extend(synthetic(178, 2));
    assert_eq!(
        r.push(&stray, now),
        PushOutcome::Ignored,
        "no transaction 77 exists"
    );
}

#[test]
fn final_length_mismatch_with_correct_flag_sequence_is_discarded() {
    let (mut r, budget, now) = started(400);
    let mut second = vec![0x01, 5, 0, 0];
    second.extend(synthetic(178, 2));
    assert_eq!(r.push(&second, now), PushOutcome::Incomplete);
    let mut last = vec![0x82, 5, 0, 0];
    last.extend(synthetic(43, 3)); // 178 + 178 + 43 = 399 != 400
    assert_eq!(
        r.push(&last, now),
        PushOutcome::Discarded(DiscardReason::Payload)
    );
    assert_eq!(budget.usage(), (0, 0));
}

#[test]
fn only_two_incomplete_transactions_per_peer_and_the_third_changes_nothing() {
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
fn global_caps_apply_across_peers_before_allocation() {
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
fn inactivity_timeout_is_five_seconds_per_transaction() {
    let (mut r, budget, t0) = started(400);
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
    let (mut r, _, t0) = started(400);
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
fn live_duplicate_start_is_ignored_and_a_repeat_within_sixty_seconds_disconnects() {
    let (mut r, budget, t0) = started(400);
    let mut again = vec![0x00, 5, 0x01, 0x90];
    again.extend(synthetic(178, 1));
    assert_eq!(
        r.push(&again, t0),
        PushOutcome::Ignored,
        "first conflict is ignored"
    );
    assert_eq!(
        budget.usage(),
        (1, 400),
        "the live transaction is untouched"
    );
    // Still inside both the 5 s inactivity window of the live transaction and the 60 s conflict window.
    assert_eq!(
        r.push(&again, t0 + Duration::from_secs(3)),
        PushOutcome::Disconnect
    );
}

#[test]
fn conflicts_older_than_sixty_seconds_are_forgotten() {
    let (mut r, _, t0) = started(400);
    let mut again = vec![0x00, 5, 0x01, 0x90];
    again.extend(synthetic(178, 1));
    assert_eq!(r.push(&again, t0), PushOutcome::Ignored);
    // The original transaction times out; a fresh one with the same id starts at +57 s and is
    // refreshed at +60 s so that a repeated start at +61 s is a genuine conflict again.
    let mut fresh = vec![0x00, 5, 0x01, 0x90];
    fresh.extend(synthetic(178, 1));
    assert_eq!(
        r.push(&fresh, t0 + Duration::from_secs(57)),
        PushOutcome::Incomplete
    );
    let mut keep_alive = vec![0x01, 5, 0, 0];
    keep_alive.extend(synthetic(178, 2));
    assert_eq!(
        r.push(&keep_alive, t0 + Duration::from_secs(60)),
        PushOutcome::Incomplete
    );
    assert_eq!(
        r.push(&again, t0 + Duration::from_secs(61)),
        PushOutcome::Ignored,
        "the conflict recorded at t0 is older than 60 s and no longer counts"
    );
}

#[test]
fn clear_wipes_everything() {
    let (mut r, budget, _) = started(400);
    r.clear();
    assert_eq!((r.incomplete(), budget.usage()), (0, (0, 0)));
}

// ---- committed protocol fixtures --------------------------------------------------------------

#[test]
fn committed_fragmentation_fixtures_behave_as_specified() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../protocol/v1/fixtures/fragmentation.json"
    );
    let document: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).expect("fixture file")).unwrap();
    let fixtures = document["fixtures"].as_array().unwrap();
    assert!(
        fixtures.iter().any(|f| f["id"] == "frag-r2-mtu517-len4096"),
        "r2 vectors must be present"
    );
    for fixture in fixtures {
        let id = fixture["id"].as_str().unwrap();
        let mtu = fixture["input"]["mtu"].as_u64().unwrap() as usize;
        let (mut r, _) = reassembler(mtu);
        let now = Instant::now();
        let expected = fixture["expected"]["transactions"].as_array().unwrap();
        for (index, transaction) in fixture["input"]["transactions"]
            .as_array()
            .unwrap()
            .iter()
            .enumerate()
        {
            let mut outcome = PushOutcome::Incomplete;
            for frame in transaction["fragments"].as_array().unwrap() {
                // Every fragment is delivered; trailing fragments after completion or discard are
                // strays and must not resurrect or corrupt anything.
                outcome = r.push(&hex_decode(frame.as_str().unwrap()), now);
            }
            let want = &expected[index];
            let label = format!("{id} transaction {index}");
            if want["completed"] == true {
                let bytes = hex_decode(want["reassembled"].as_str().unwrap());
                assert_eq!(outcome, PushOutcome::Complete(bytes), "{label}");
            } else if want["ignored"] == true {
                assert_eq!(outcome, PushOutcome::Ignored, "{label}");
            } else if want["incomplete"] == true {
                assert!(
                    matches!(outcome, PushOutcome::Incomplete | PushOutcome::Ignored),
                    "{label}: {outcome:?}"
                );
            } else {
                assert!(
                    want.get("stage").is_some(),
                    "{label}: fixture has an unexpected shape"
                );
                assert!(
                    matches!(outcome, PushOutcome::Discarded(_) | PushOutcome::Ignored),
                    "{label}: expected rejection, got {outcome:?}"
                );
            }
        }
    }
}

#[test]
fn sender_output_matches_the_committed_max_record_vector() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../protocol/v1/fixtures/fragmentation.json"
    );
    let document: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let fixture = document["fixtures"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["id"] == "frag-mtu185-max-total-4096")
        .expect("max-total fixture");
    let transaction = &fixture["input"]["transactions"][0];
    let wanted: Vec<Vec<u8>> = transaction["fragments"]
        .as_array()
        .unwrap()
        .iter()
        .map(|f| hex_decode(f.as_str().unwrap()))
        .collect();
    let record = hex_decode(
        fixture["expected"]["transactions"][0]["reassembled"]
            .as_str()
            .unwrap(),
    );
    assert_eq!(
        fragment(
            &record,
            link(185),
            transaction["transaction_id"].as_u64().unwrap() as u8
        )
        .unwrap(),
        wanted
    );
}

// ---- OTP2-GAT-010 profile revision 2: min(MTU - 3, 512) attribute budget ------------------------

#[test]
fn r2_mtu_517_capped_non_final_payloads_are_exactly_508_bytes() {
    let record = synthetic(4096, 9);
    let frames = fragment(&record, link(MAX_MTU), 1).unwrap();
    assert_eq!(frames.len(), 9, "ceil(4096 / 508)");
    assert!(
        frames[..8].iter().all(|f| f.len() == 4 + 508),
        "non-final payload is exactly min(MTU-3, 512) - 4 = 508"
    );
    assert_eq!(
        frames[8].len(),
        4 + 32,
        "final fragment carries the remainder"
    );
    let joined: Vec<u8> = frames.iter().flat_map(|f| f[4..].to_vec()).collect();
    assert_eq!(joined, record, "payloads concatenate byte-exact");
}

#[test]
fn r2_mtu_517_payload_boundary_508_fits_and_509_needs_two_fragments() {
    let fits = fragment(&synthetic(508, 1), link(MAX_MTU), 1).unwrap();
    assert_eq!(fits.len(), 1, "508 payload fits one capped fragment");
    assert_eq!(fits[0][0], 0x80, "single fragment is final");
    let split = fragment(&synthetic(509, 1), link(MAX_MTU), 1).unwrap();
    assert_eq!(split.len(), 2, "509 payload needs a second fragment");
    assert_eq!(split[0].len(), 4 + 508);
    assert_eq!(split[1].len(), 4 + 1);
    assert!(matches!(
        feed_all(&split, MAX_MTU),
        PushOutcome::Complete(b) if b == synthetic(509, 1)
    ));
}

#[test]
fn r2_mtu_517_rejects_inbound_frames_above_the_512_attribute_cap() {
    let now = Instant::now();
    // Frame sizes 513..516 (payloads 509..512) all exceed min(MTU-3, 512) = 512.
    for payload in [509usize, 510, 511, 512] {
        let (mut r, budget) = reassembler(MAX_MTU);
        let mut frame = vec![0x00, 7, 0x01, 0xFE];
        frame.extend(synthetic(payload, 1));
        assert_eq!(
            r.push(&frame, now),
            PushOutcome::Discarded(DiscardReason::FrameSize),
            "frame of {} bytes exceeds the 512 attribute cap",
            4 + payload
        );
        assert_eq!(budget.usage(), (0, 0), "payload {payload}");
        assert_eq!(r.incomplete(), 0, "payload {payload}");
    }
    // Exactly 512 bytes (508 payload) is still accepted at MTU 517.
    let (mut r, budget) = reassembler(MAX_MTU);
    let mut frame = vec![0x00, 7, 0x03, 0xFA];
    frame.extend(synthetic(508, 1));
    assert_eq!(r.push(&frame, now), PushOutcome::Incomplete);
    assert_eq!(budget.usage(), (1, 1018));
}

#[test]
fn r2_mtu_517_round_trip_byte_exact_at_the_capped_chunk_boundaries() {
    let chunk = 508usize; // min(517 - 3, 512) - 4
    for len in [
        1,
        chunk - 1,
        chunk,
        chunk + 1,
        2 * chunk,
        2 * chunk + 1,
        4095,
        4096,
    ] {
        let record = synthetic(len, len as u8);
        let frames = fragment(&record, link(MAX_MTU), 200).unwrap();
        match feed_all(&frames, MAX_MTU) {
            PushOutcome::Complete(bytes) => assert_eq!(bytes, record, "len {len}"),
            other => panic!("len {len}: {other:?}"),
        }
    }
}

#[test]
fn r2_preserves_the_mtu_185_vectors() {
    let record = synthetic(4096, 9);
    let frames = fragment(&record, link(MIN_MTU), 24).unwrap();
    assert_eq!(frames.len(), 24);
    assert!(frames[..23].iter().all(|f| f.len() == 182));
    assert_eq!(frames[23].len(), 4 + 2);
    match feed_all(&frames, MIN_MTU) {
        PushOutcome::Complete(bytes) => assert_eq!(bytes, record),
        other => panic!("{other:?}"),
    }
}

// Literal, independent statement of the payload budget: unclamped up to ATT MTU 515, 508 above.
fn literal_payload(mtu: usize) -> usize {
    if mtu <= 515 { mtu - 7 } else { 508 }
}

#[test]
fn r2_budget_matches_the_literal_table_for_every_mtu() {
    for mtu in MIN_MTU..=MAX_MTU {
        let budget = link(mtu);
        assert_eq!(budget.payload(), literal_payload(mtu), "mtu {mtu}");
        assert_eq!(budget.attribute(), literal_payload(mtu) + 4, "mtu {mtu}");
        assert!(budget.attribute() <= ATTRIBUTE_VALUE_MAX);
    }
}

#[test]
fn r2_sender_and_receiver_agree_at_every_boundary_of_every_mtu() {
    for mtu in MIN_MTU..=MAX_MTU {
        let p = literal_payload(mtu);
        for len in [
            1,
            2,
            p - 1,
            p,
            p + 1,
            2 * p - 1,
            2 * p,
            2 * p + 1,
            4095,
            4096,
        ] {
            let record = synthetic(len, (mtu % 251) as u8);
            let frames = fragment(&record, link(mtu), 77).unwrap();
            assert_eq!(frames.len(), len.div_ceil(p), "mtu {mtu} len {len}");
            for (index, frame) in frames.iter().enumerate() {
                let last = index + 1 == frames.len();
                assert!(frame.len() <= (mtu - 3).min(512), "mtu {mtu} len {len}");
                assert_eq!(frame[0] & 0x80 != 0, last);
                if !last || len % p == 0 {
                    assert_eq!(frame.len() - 4, p, "mtu {mtu} len {len} frame {index}");
                } else {
                    assert_eq!(frame.len() - 4, len % p, "mtu {mtu} len {len}");
                }
            }
            match feed_all(&frames, mtu) {
                PushOutcome::Complete(bytes) => assert_eq!(bytes, record, "mtu {mtu} len {len}"),
                other => panic!("mtu {mtu} len {len}: {other:?}"),
            }
        }
    }
}

fn frame(index: u8, last: bool, tid: u8, total: usize, payload: usize) -> Vec<u8> {
    let mut frame = vec![
        index | if last { 0x80 } else { 0 },
        tid,
        (total >> 8) as u8,
        total as u8,
    ];
    frame.extend(synthetic(payload, index));
    frame
}

fn accepts(mtu: usize, frames: &[Vec<u8>]) -> bool {
    let (mut r, _) = reassembler(mtu);
    let now = Instant::now();
    matches!(
        frames.iter().map(|f| r.push(f, now)).last(),
        Some(PushOutcome::Complete(_))
    )
}

#[test]
fn r2_receiver_accepts_an_empty_final_only_after_an_exact_multiple() {
    for mtu in [MIN_MTU, 514, 516, MAX_MTU] {
        let p = literal_payload(mtu);
        let tolerated = [
            frame(0, false, 9, 2 * p, p),
            frame(1, false, 9, 0, p),
            frame(2, true, 9, 0, 0),
        ];
        assert!(accepts(mtu, &tolerated), "mtu {mtu}");
        // Not an exact multiple: the same empty final is a length mismatch.
        let short = [
            frame(0, false, 9, 2 * p + 1, p),
            frame(1, false, 9, 0, p),
            frame(2, true, 9, 0, 0),
        ];
        assert!(!accepts(mtu, &short), "mtu {mtu}");
    }
}

#[test]
fn r2_exact_size_rules_reject_every_off_by_one_at_mtu_517() {
    let p = 508;
    let m = MAX_MTU;
    // non-final short by one, with a final that would otherwise make the total match
    assert!(!accepts(
        m,
        &[frame(0, false, 5, 1016, p - 1), frame(1, true, 5, 0, p + 1)]
    ));
    // non-final carrying more than remains
    assert!(!accepts(m, &[frame(0, false, 5, 507, p)]));
    // final short of / beyond the declared total
    assert!(!accepts(
        m,
        &[frame(0, false, 5, 1000, p), frame(1, true, 5, 0, 491)]
    ));
    assert!(!accepts(
        m,
        &[frame(0, false, 5, 1000, p), frame(1, true, 5, 0, 493)]
    ));
    // empty final while bytes remain
    assert!(!accepts(
        m,
        &[frame(0, false, 5, 1000, p), frame(1, true, 5, 0, 0)]
    ));
    // total 0 and 4097
    assert!(!accepts(m, &[frame(0, true, 5, 0, 0)]));
    assert!(!accepts(m, &[frame(0, false, 5, 4097, p)]));
    // gap and duplicate indices
    assert!(!accepts(
        m,
        &[frame(0, false, 5, 1017, p), frame(2, true, 5, 0, 509 - 1)]
    ));
    assert!(!accepts(
        m,
        &[frame(0, false, 5, 1017, p), frame(0, false, 5, 0, p)]
    ));
    // the exact forms are accepted
    assert!(accepts(
        m,
        &[frame(0, false, 5, 1016, p), frame(1, true, 5, 0, p)]
    ));
    assert!(accepts(m, &[frame(0, true, 5, p, p)]));
    // nothing may follow a final fragment
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
fn r2_revision_1_frames_at_mtu_516_and_517_fail_closed() {
    for mtu in [516usize, MAX_MTU] {
        let legacy = mtu - 7; // revision 1: 509 / 510 payload, 513 / 514-byte attribute values
        let record = synthetic(4096, 1);
        let frames: Vec<Vec<u8>> = record
            .chunks(legacy)
            .enumerate()
            .map(|(i, chunk)| {
                let mut f = vec![
                    i as u8
                        | if (i + 1) * legacy >= record.len() {
                            0x80
                        } else {
                            0
                        },
                    9,
                    if i == 0 { 0x10 } else { 0 },
                    0,
                ];
                f.extend_from_slice(chunk);
                f
            })
            .collect();
        let (mut r, budget) = reassembler(mtu);
        assert_eq!(
            r.push(&frames[0], Instant::now()),
            PushOutcome::Discarded(DiscardReason::FrameSize),
            "mtu {mtu}"
        );
        assert_eq!(budget.usage(), (0, 0), "nothing was retained");
    }
}

/// Byte-exact cross-consumer agreement: the Rust sender reproduces every canonical accepted
/// committed vector (including its transaction id), and the receiver reassembles all of them.
#[test]
fn r2_sender_reproduces_every_canonical_accepted_fixture_byte_for_byte() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../protocol/v1/fixtures/fragmentation.json"
    );
    let document: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let mut compared = 0;
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
        let committed: Vec<Vec<u8>> = transactions[0]["fragments"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| hex_decode(f.as_str().unwrap()))
            .collect();
        // A trailing empty final is receiver tolerance only; the sender never emits it.
        if committed.last().unwrap().len() == 4 {
            continue;
        }
        let mtu = fixture["input"]["mtu"].as_u64().unwrap() as usize;
        let record = hex_decode(expected["reassembled"].as_str().unwrap());
        let tid = transactions[0]["transaction_id"].as_u64().unwrap() as u8;
        assert_eq!(
            fragment(&record, link(mtu), tid).unwrap(),
            committed,
            "{id}"
        );
        compared += 1;
    }
    assert!(compared >= 40, "compared only {compared} canonical vectors");
}
