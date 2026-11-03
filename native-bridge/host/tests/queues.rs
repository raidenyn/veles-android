//! Task 3 queue saturation and ordering tests (brief: "Write tests RED before code; prove real
//! caps and writer-held ID semantics").
//!
//! Every test is a controlled producer/consumer sequence on one machine thread: pushes, emits
//! and pops happen synchronously in order. The single rendezvous test wakes a co-producer
//! through a condvar and joins it through a `recv_timeout` safety net — never through sleeps
//! or wall-clock scheduling gaps.

use std::time::{Duration, Instant};

use veles_ble_host::backend::{BackendEvent, Discovered, Failure};
use veles_ble_host::buffer::Payload;
use veles_ble_host::fragment::LinkCapacity;
use veles_ble_host::ingress::{
    ArmedShutdown, BackendEvents, BackendPop, BackendReceiver, DATA_BYTES_GLOBAL_MAX,
    DATA_BYTES_PER_LINK_MAX, DATA_EVENTS_GLOBAL_MAX, DATA_EVENTS_PER_LINK_MAX, EmitStatus,
    InputFrames, InputPop, InputPushError, InputTerminal, ReserveError,
};
use veles_ble_host::outqueue::{
    MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Pop as OutPop, PushError,
};

const ZERO: Duration = Duration::from_secs(0);

fn frame_of(len: usize, byte: u8) -> Payload {
    Payload::new(vec![byte; len])
}

fn frag(conn: u64, len: usize, byte: u8) -> BackendEvent {
    BackendEvent::Fragment {
        conn,
        bytes: Payload::new(vec![byte; len]),
    }
}

fn out_frame(q: &OutQueue) -> Payload {
    match q.pop(ZERO) {
        OutPop::Frame(payload) => payload,
        OutPop::Timeout => panic!("pending frame vanished from the queue"),
        OutPop::Closed => panic!("queue closed with frames pending"),
    }
}

fn expect_event(receiver: &BackendReceiver) -> BackendEvent {
    match receiver.pop_event(Some(ZERO)) {
        BackendPop::Event(event) => event,
        BackendPop::Timeout => panic!("expected a queued event, got timeout"),
    }
}

fn expect_timeout(receiver: &BackendReceiver) {
    assert_eq!(
        receiver.pop_event(Some(ZERO)),
        BackendPop::Timeout,
        "expected the empty receiver to time out"
    );
}

/// Drains the receiver into a Vec: the exact tool for the order/count assertions below.
fn drain(receiver: &BackendReceiver) -> Vec<BackendEvent> {
    let mut events = Vec::new();
    loop {
        match receiver.pop_event(Some(ZERO)) {
            BackendPop::Event(event) => events.push(event),
            BackendPop::Timeout => return events,
        }
    }
}

/// Saturates the global data *byte* budget to exactly (1,024 events, 524,288 bytes): 32 reserved
/// connections, each holding 32 fragments of 512 bytes = its own 16 KiB per-link byte cap.
/// 32 links x 16 KiB = 512 KiB is the global byte ceiling, so every link sits at its cap at once.
fn saturate_global_data(events: &BackendEvents, receiver: &BackendReceiver) {
    for conn in 1..=32u64 {
        assert_eq!(receiver.reserve_connection(conn), Ok(()));
        for _ in 0..32 {
            assert_eq!(events.emit(frag(conn, 512, 0x21)), EmitStatus::Accepted);
        }
    }
    assert_eq!(receiver.data_usage(), (1024, 524_288));
}

// ---------------------------------------------------------------------------
// OutQueue: writer-held accounting and saturation (brief item 1)
// ---------------------------------------------------------------------------

#[test]
fn writer_held_frame_keeps_bytes_and_request_id_reserved() {
    let q = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
    assert_eq!(q.push(frame_of(10, 0xA1), Some("r1".to_owned())), Ok(()));
    assert_eq!(q.queued_bytes(), 10);
    // The writer pops the frame; its bytes and request id stay accounted.
    let held = out_frame(&q);
    assert_eq!(held.as_slice().len(), 10);
    assert_eq!(
        q.queued_bytes(),
        10,
        "the writer-held frame stays accounted"
    );
    assert_eq!(q.len(), 1);
    assert!(q.holds_request_id("r1"), "the held id must stay reserved");

    // Fill the remaining 127 slots (1 held + 127 queued = 128 = the message cap).
    for index in 0..127usize {
        let id = format!("r{}", index + 2);
        assert_eq!(
            q.push(frame_of(1, u8::try_from(index).unwrap()), Some(id)),
            Ok(())
        );
    }
    assert_eq!(q.len(), 128);
    assert_eq!(q.queued_bytes(), 10 + 127);
    // Another push overflows while the held frame keeps its accounting slot.
    assert_eq!(
        q.push(frame_of(1, 0xFF), Some("r129".to_owned())),
        Err(PushError::Overflow),
        "128 messages count the writer-held frame"
    );
    assert!(
        q.holds_request_id("r1"),
        "the id is held until finish_write"
    );
    assert!(
        q.holds_request_id("r50"),
        "queued completion ids stay reserved"
    );

    q.finish_write();
    assert!(!q.holds_request_id("r1"), "the held id must be released");
    assert!(q.holds_request_id("r50"), "queued ids remain reserved");
    assert_eq!(q.queued_bytes(), 127);
}

#[test]
fn byte_budget_saturates_independently_of_message_count() {
    let q = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
    // 126 * 2,080 + 64 = 262,144 = 256 KiB with only 127 messages (one under the cap).
    for index in 0..126 {
        assert_eq!(
            q.push(frame_of(2_080, u8::try_from(index).unwrap()), None),
            Ok(())
        );
    }
    assert_eq!(q.push(frame_of(64, 0xEE), None), Ok(()));
    assert_eq!(q.len(), 127);
    assert_eq!(q.queued_bytes(), MAX_QUEUED_BYTES);
    // The next 1-byte frame fits the message cap (127 + 1 = 128) but not the byte budget.
    assert_eq!(q.push(frame_of(1, 0x01), None), Err(PushError::Overflow));
    assert_eq!(q.len(), 127);
    // Freeing one frame admits the next.
    drop(out_frame(&q));
    q.finish_write();
    assert_eq!(q.push(frame_of(1, 0x01), None), Ok(()));
}

#[test]
fn message_count_cap_rejects_the_129th_frame() {
    let q = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
    for index in 0..128 {
        assert_eq!(
            q.push(frame_of(1, u8::try_from(index).unwrap()), None),
            Ok(())
        );
    }
    assert_eq!(q.queued_bytes(), 128);
    assert_eq!(q.push(frame_of(1, 0xFF), None), Err(PushError::Overflow));
    assert_eq!(q.queued_bytes(), 128);
}

#[test]
fn overflow_and_close_reject_pushes_without_queue_damage() {
    let q = OutQueue::new(2, 64);
    assert_eq!(q.push(frame_of(10, 0x01), None), Ok(()));
    assert_eq!(q.push(frame_of(10, 0x02), None), Ok(()));
    // Overflow consumes the pushed payload into the queue's dropping path (erasure on drop).
    assert_eq!(q.push(frame_of(10, 0x03), None), Err(PushError::Overflow));
    assert_eq!(
        q.queued_bytes(),
        20,
        "a rejected push leaves accounting intact"
    );

    q.close();
    assert_eq!(
        q.push(frame_of(10, 0x04), None),
        Err(PushError::Closed),
        "a closed queue is Closed, not an overflow"
    );
    assert_eq!(out_frame(&q).as_slice(), &[0x01; 10]);
    assert_eq!(out_frame(&q).as_slice(), &[0x02; 10]);
    assert_eq!(
        q.pop(ZERO),
        OutPop::Closed,
        "closed yields Closed once drained"
    );
}

#[test]
fn zero_timeout_pop_and_drain_never_block() {
    let q = OutQueue::new(4, 64);
    assert_eq!(q.pop(ZERO), OutPop::Timeout);
    assert_eq!(q.push(frame_of(8, 0x0A), None), Ok(()));
    assert!(
        !q.wait_drained(Instant::now()),
        "a queued frame is not drained"
    );
    drop(out_frame(&q));
    assert!(
        !q.wait_drained(Instant::now()),
        "the popped frame is still held by the writer"
    );
    q.finish_write();
    assert!(
        q.wait_drained(Instant::now()),
        "no held frame: drained immediately"
    );
    assert!(q.is_empty());
}

// ---------------------------------------------------------------------------
// Ingress: reader -> core input frames
// ---------------------------------------------------------------------------

#[test]
fn input_frames_saturate_sixty_four_frames_and_256kib() {
    let q = InputFrames::new();
    for index in 0..64 {
        assert_eq!(
            q.try_push_frame(frame_of(16, u8::try_from(index).unwrap())),
            Ok(())
        );
    }
    assert_eq!(q.frame_usage(), (64, 1_024));
    assert_eq!(
        q.try_push_frame(frame_of(1, 0xFF)),
        Err(InputPushError::Overflow),
        "the 65th frame must overflow"
    );
    assert_eq!(q.frame_usage(), (64, 1_024));

    // 256 KiB saturates independently of the frame count: 16 * 16 KiB = the cap with only
    // 16 frames (well under 64), and one further byte overflows on bytes alone.
    let q = InputFrames::new();
    for index in 0..15 {
        assert_eq!(
            q.try_push_frame(frame_of(16_384, u8::try_from(index).unwrap())),
            Ok(())
        );
    }
    assert_eq!(q.frame_usage(), (15, 245_760));
    assert_eq!(q.try_push_frame(frame_of(16_384, 0x00)), Ok(()));
    assert_eq!(q.frame_usage(), (16, 256 * 1024));
    assert_eq!(
        q.try_push_frame(frame_of(1, 0x01)),
        Err(InputPushError::Overflow)
    );

    // A frame of the protocol maximum is admissible on an empty queue.
    let q = InputFrames::new();
    assert_eq!(q.try_push_frame(frame_of(65_536, 0x77)), Ok(()));
    assert_eq!(q.frame_usage(), (1, 65_536));
}

#[test]
fn input_terminal_survives_saturation_and_delivers_after_frames() {
    let q = InputFrames::new();
    for index in 0..64 {
        assert_eq!(
            q.try_push_frame(frame_of(16, u8::try_from(index).unwrap())),
            Ok(())
        );
    }
    // The full queue cannot displace the terminal flag, and the first kind wins.
    q.set_terminal(InputTerminal::Eof);
    q.set_terminal(InputTerminal::IoError);
    assert_eq!(
        q.try_push_frame(frame_of(1, 0x00)),
        Err(InputPushError::TerminalSet),
        "no frames may enter after the reader terminal"
    );
    for index in 0..64 {
        let frame = match q.pop_frame(Some(ZERO)) {
            InputPop::Frame(payload) => payload,
            other => panic!("expected frame {index}, got {other:?}"),
        };
        assert_eq!(frame.as_slice().len(), 16);
    }
    assert_eq!(
        q.pop_frame(Some(ZERO)),
        InputPop::Terminal(InputTerminal::Eof)
    );
    assert_eq!(
        q.pop_frame(Some(ZERO)),
        InputPop::Terminal(InputTerminal::Eof)
    );
    assert_eq!(q.frame_usage(), (0, 0));

    for kind in [InputTerminal::TruncatedFrame, InputTerminal::IoError] {
        let q = InputFrames::new();
        q.set_terminal(kind);
        assert_eq!(q.pop_frame(Some(ZERO)), InputPop::Terminal(kind));
    }
}

#[test]
fn input_backpressure_push_completes_after_a_drain() {
    let q = InputFrames::new();
    for index in 0..64 {
        assert_eq!(
            q.try_push_frame(frame_of(8, u8::try_from(index).unwrap())),
            Ok(())
        );
    }
    let (tx, rx) = std::sync::mpsc::channel();
    let q2 = q.clone();
    let pusher = std::thread::spawn(move || {
        let result = q2.push_frame_for(frame_of(1, 0x01), Some(Duration::from_secs(10)));
        let _ = tx.send(result);
    });
    // The queue is full: draining one frame frees the slot (or unblocks the parked push).
    let popped = match q.pop_frame(Some(Duration::from_secs(5))) {
        InputPop::Frame(payload) => payload,
        other => panic!("expected a frame, got {other:?}"),
    };
    assert_eq!(popped.as_slice().len(), 8);
    let result = rx
        .recv_timeout(Duration::from_secs(5))
        .expect("push_frame_for must finish once capacity frees");
    assert_eq!(result, Ok(()));
    assert_eq!(q.frame_usage(), (64, 505));
    pusher.join().expect("pusher thread must not panic");
}

#[test]
fn zero_timeout_input_pop_on_empty_times_out() {
    let q = InputFrames::new();
    assert_eq!(q.pop_frame(Some(ZERO)), InputPop::Timeout);
}

// ---------------------------------------------------------------------------
// Ingress: backend data, controls, adverts
// ---------------------------------------------------------------------------

#[test]
fn backend_data_caps_are_real_per_link_and_global() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    // 512-byte fragments reach the independent 16 KiB byte cap at 32 events (< the 48-event cap).
    for _ in 0..32 {
        assert_eq!(events.emit(frag(1, 512, 0xA1)), EmitStatus::Accepted);
    }
    assert_eq!(receiver.link_data_usage(1), Some((32, 16_384)));
    assert_eq!(receiver.data_usage(), (32, 16_384));
    assert_eq!(
        events.emit(frag(1, 1, 0xAB)),
        EmitStatus::DataOverflow,
        "one more byte overflows the 16 KiB per-link byte budget"
    );
    assert_eq!(receiver.link_data_usage(1), Some((32, 16_384)));
    assert_eq!(receiver.data_usage(), (32, 16_384));

    // Survivors keep their own budget: a second link is unaffected by link 1's overflow.
    assert_eq!(receiver.reserve_connection(2), Ok(()));
    for _ in 0..32 {
        assert_eq!(events.emit(frag(2, 512, 0xB2)), EmitStatus::Accepted);
    }
    assert_eq!(receiver.data_usage(), (64, 32_768));

    // The 512-byte physical cap applies before any copy: nothing is stored or accounted,
    // and the oversize fragment deterministically fails the link (one retained marker, so no
    // later data is admitted as though the stream were intact).
    assert_eq!(receiver.reserve_connection(3), Ok(()));
    assert_eq!(
        events.emit(frag(3, 513, 0xC3)),
        EmitStatus::FragmentOversize
    );
    assert_eq!(receiver.link_data_usage(3), Some((0, 0)));
    assert_eq!(receiver.data_usage(), (64, 32_768));
    assert_eq!(
        events.emit(frag(3, 512, 0xC3)),
        EmitStatus::DataOverflow,
        "the oversize fragment failed link 3: valid data is no longer admitted as intact"
    );

    // Every one of the 32 reservable links can sit at its own cap at once: that is exactly the
    // global ceiling (32 x 16 KiB = 512 KiB), reached with the links 4..=32 filled too.
    for conn in 4..=32u64 {
        assert_eq!(receiver.reserve_connection(conn), Ok(()));
        for _ in 0..32 {
            assert_eq!(events.emit(frag(conn, 512, 0x44)), EmitStatus::Accepted);
        }
    }
    // Link 3 contributes nothing (failed by its oversize fragment).
    assert_eq!(receiver.data_usage(), (31 * 32, 31 * 16_384));
    assert_eq!(
        events.emit(frag(4, 1, 0x99)),
        EmitStatus::DataOverflow,
        "a link at its own cap rejects further data"
    );

    // The event Debug never prints fragment content.
    let secret = BackendEvent::Fragment {
        conn: 1,
        bytes: Payload::new(b"secret".to_vec()),
    };
    let text = format!("{secret:?}");
    assert!(text.contains("6 bytes"), "redacted length only: {text}");
    assert!(!text.contains("secret"));
}

/// The global ceilings are exactly 32 links x the per-link ceilings, on BOTH axes, so the
/// independent global event ceiling (1,536) is reached by 32 links of 48 small fragments and
/// the global byte ceiling (512 KiB) by 32 links of 16 KiB, simultaneously.
#[test]
fn all_thirty_two_links_at_their_event_and_byte_cap_hit_the_global_ceilings_exactly() {
    assert_eq!(DATA_EVENTS_GLOBAL_MAX, 32 * DATA_EVENTS_PER_LINK_MAX);
    assert_eq!(DATA_BYTES_GLOBAL_MAX, 32 * DATA_BYTES_PER_LINK_MAX);
    let (events, receiver) = BackendEvents::new();
    for conn in 1..=32u64 {
        assert_eq!(receiver.reserve_connection(conn), Ok(()));
        // 48 fragments totalling exactly 16 KiB: 16 of 342 bytes + 32 of 341 bytes.
        for index in 0..48 {
            let len = if index < 16 { 342 } else { 341 };
            assert_eq!(events.emit(frag(conn, len, 0x5A)), EmitStatus::Accepted);
        }
        assert_eq!(receiver.link_data_usage(conn), Some((48, 16_384)));
    }
    assert_eq!(receiver.data_usage(), (1_536, 524_288));
    for conn in [1u64, 17, 32] {
        assert_eq!(
            events.emit(frag(conn, 1, 0x5A)),
            EmitStatus::DataOverflow,
            "link {conn} is at both of its caps"
        );
    }
    // Draining every fragment returns the accounting to zero (no phantom usage).
    let popped = drain(&receiver);
    assert_eq!(
        popped
            .iter()
            .filter(|event| matches!(event, BackendEvent::Fragment { .. }))
            .count(),
        32 * 48
    );
    assert_eq!(receiver.data_usage(), (0, 0));
}

#[test]
fn per_link_event_cap_binds_before_byte_budget_with_small_fragments() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    for _ in 0..48 {
        assert_eq!(events.emit(frag(1, 100, 0x11)), EmitStatus::Accepted);
    }
    assert_eq!(receiver.link_data_usage(1), Some((48, 4_800)));
    assert_eq!(events.emit(frag(1, 100, 0x11)), EmitStatus::DataOverflow);
    assert_eq!(receiver.link_data_usage(1), Some((48, 4_800)));
}

#[test]
fn noisy_link_overflow_retains_one_marker_and_survivors_progress() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(receiver.reserve_connection(2), Ok(()));
    for _ in 0..48 {
        assert_eq!(events.emit(frag(1, 100, 0x01)), EmitStatus::Accepted);
    }
    assert_eq!(events.emit(frag(1, 1, 0x01)), EmitStatus::DataOverflow);
    for _ in 0..3 {
        assert_eq!(events.emit(frag(2, 100, 0x02)), EmitStatus::Accepted);
    }
    assert_eq!(receiver.reserve_request(50), Ok(()));
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 1, send: 50 }),
        EmitStatus::Accepted
    );
    // A further flood never adds a second marker.
    for _ in 0..30 {
        assert_eq!(events.emit(frag(1, 1, 0x01)), EmitStatus::DataOverflow);
    }

    let popped = drain(&receiver);
    let link1: Vec<&BackendEvent> = popped
        .iter()
        .filter(|event| {
            matches!(
                event,
                BackendEvent::Fragment { conn: 1, .. } | BackendEvent::LinkLost { conn: 1 }
            )
        })
        .collect();
    assert_eq!(link1.len(), 49, "48 fragments plus exactly one marker");
    assert_eq!(
        link1[48],
        &BackendEvent::LinkLost { conn: 1 },
        "the marker trails the retained fragments in link order"
    );
    let markers = popped
        .iter()
        .filter(|event| matches!(event, BackendEvent::LinkLost { conn: 1 }))
        .count();
    assert_eq!(markers, 1, "exactly one failure marker per link, ever");

    // Survivors progress and keep their own order.
    let link2 = popped
        .iter()
        .filter(|event| matches!(event, BackendEvent::Fragment { conn: 2, .. }))
        .count();
    assert_eq!(link2, 3);
    // The queued control still arrives after the marker (emit order is link order).
    assert!(
        popped
            .iter()
            .any(|event| matches!(event, BackendEvent::WriteDone { conn: 1, send: 50 })),
        "controls of a flooded link must stay observable"
    );
    // 30 dropped fragments left nothing behind for link 1.
    assert_eq!(
        drain(&receiver).len(),
        0,
        "post-overflow link data is rejected, not queued"
    );
}

#[test]
fn link_marker_fails_subsequent_data_of_that_link_only() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(receiver.reserve_connection(2), Ok(()));
    for _ in 0..32 {
        assert_eq!(events.emit(frag(1, 512, 0x01)), EmitStatus::Accepted);
    }
    assert_eq!(events.emit(frag(1, 1, 0x01)), EmitStatus::DataOverflow);
    assert_eq!(receiver.link_data_usage(1), Some((32, 16_384)));
    // Link 2 streams while link 1 is barred from further data admission.
    for _ in 0..10 {
        assert_eq!(events.emit(frag(2, 100, 0x02)), EmitStatus::Accepted);
    }
    assert_eq!(receiver.link_data_usage(2), Some((10, 1_000)));
    let after_marker = drain(&receiver)
        .iter()
        .filter(|event| matches!(event, BackendEvent::Fragment { conn: 2, .. }))
        .count();
    assert_eq!(
        after_marker, 10,
        "survivor data unaffected by link 1's marker"
    );
    assert_eq!(
        drain(&receiver).len(),
        0,
        "the marker must be the only terminal for link 1"
    );
}

#[test]
fn emission_order_preserved_within_link_and_scans_precede_adverts() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(1), Ok(()));
    assert_eq!(receiver.reserve_connection(10), Ok(()));
    assert_eq!(receiver.reserve_request(60), Ok(()));

    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 1 }),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(BackendEvent::Advertisement {
            scan: 1,
            device: Discovered {
                platform_hint: "aa:bb".to_owned(),
                label: "P1".to_owned(),
                advertisement: None,
            },
        }),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(BackendEvent::Connected {
            conn: 10,
            capacity: LinkCapacity::AttMtu(517),
            platform_hint: Some("aa:bb".to_owned()),
        }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(frag(10, 8, 0x01)), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(10, 8, 0x02)), EmitStatus::Accepted);
    assert_eq!(
        events.emit(BackendEvent::LinkLost { conn: 10 }),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 10, send: 60 }),
        EmitStatus::Accepted
    );

    let expected = [
        "ScanStarted",
        "Connected",
        "Advertisement",
        "Fragment",
        "Fragment",
        "LinkLost",
        "WriteDone",
    ];
    let actual: Vec<&'static str> = expected
        .iter()
        .map(|_| match expect_event(&receiver) {
            BackendEvent::ScanStarted { .. } => "ScanStarted",
            BackendEvent::Advertisement { .. } => "Advertisement",
            BackendEvent::Connected { .. } => "Connected",
            BackendEvent::Fragment { .. } => "Fragment",
            BackendEvent::LinkLost { .. } => "LinkLost",
            BackendEvent::WriteDone { .. } => "WriteDone",
            _ => panic!("unexpected event shape while walking the exact order"),
        })
        .collect();
    assert_eq!(
        actual, expected,
        "per-zone emission order preserved and the cross-zone sweep fair: scan 1 serves \
         ScanStarted then its advert, connection 10 serves Connected, its fragments, the \
         loss marker and the completion — alternating between the two non-empty zones"
    );
    expect_timeout(&receiver);
}

#[test]
fn controls_survive_full_data_queues_and_shutdown_completes_last() {
    let (events, receiver) = BackendEvents::new();
    saturate_global_data(&events, &receiver);
    // Data admits nothing anywhere now; controls still enqueue.
    assert_eq!(events.emit(frag(1, 1, 0x01)), EmitStatus::DataOverflow);
    assert_eq!(events.emit(frag(2, 1, 0x02)), EmitStatus::DataOverflow);
    for send in 50..53u64 {
        assert_eq!(receiver.reserve_request(send), Ok(()));
    }
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 1, send: 50 }),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(BackendEvent::WriteFailed {
            conn: 2,
            send: 51,
            failure: Failure::new("gatt_write", false),
        }),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(BackendEvent::CapacityChanged {
            conn: 3,
            capacity: LinkCapacity::MaxWriteWithoutResponse(512),
        }),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    assert!(!receiver.control_overflow(), "nothing was dropped");

    // The pipe-side terminal is observable while the backend queues stay saturated.
    let input = InputFrames::new();
    for _ in 0..64 {
        assert_eq!(input.try_push_frame(frame_of(16, 0x0F)), Ok(()));
    }
    input.set_terminal(InputTerminal::Eof);
    assert_eq!(
        input.pop_frame(Some(ZERO)),
        InputPop::Frame(frame_of(16, 0x0F))
    );

    // Drain everything: data first in per-link order, each link's controls after its data,
    // and ShutdownComplete only once everything else has been delivered.
    let drained = drain(&receiver);
    let data_events = drained
        .iter()
        .filter(|event| matches!(event, BackendEvent::Fragment { .. }))
        .count();
    assert_eq!(data_events, 1024, "all saturated data is delivered in full");
    assert!(
        drained
            .iter()
            .any(|event| matches!(event, BackendEvent::WriteDone { conn: 1, send: 50 }))
    );
    assert!(drained.iter().any(|event| matches!(
        event,
        BackendEvent::WriteFailed {
            conn: 2,
            send: 51,
            ..
        }
    )));
    assert!(
        drained
            .iter()
            .any(|event| matches!(event, BackendEvent::CapacityChanged { conn: 3, .. }))
    );
    // Markers from the initial saturation are delivered exactly once per link.
    let markers = drained
        .iter()
        .filter(|event| matches!(event, BackendEvent::LinkLost { .. }))
        .count();
    assert_eq!(markers, 2, "links 1 and 2 each retained exactly one marker");
    assert_eq!(
        drained.last(),
        Some(&BackendEvent::ShutdownComplete),
        "the final event delivers first once every other zone drained"
    );
    // Delivered exactly once: no second delivery, second emit collapses silently.
    expect_timeout(&receiver);
    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    expect_timeout(&receiver);
}

#[test]
fn reservation_bounds_scan_conn_request_and_reuse_after_release() {
    let (_events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(1), Ok(()));
    assert_eq!(receiver.reserve_scan(2), Ok(()));
    assert_eq!(receiver.reserve_scan(3), Err(ReserveError::Bounded));
    assert_eq!(
        receiver.reserve_scan(1),
        Ok(()),
        "duplicate reservations are idempotent"
    );
    assert_eq!(receiver.reserved_counts(), (2, 0, 0));

    for conn in 10..42u64 {
        assert_eq!(receiver.reserve_connection(conn), Ok(()));
    }
    assert_eq!(receiver.reserve_connection(42), Err(ReserveError::Bounded));
    for send in 100..164u64 {
        assert_eq!(receiver.reserve_request(send), Ok(()));
    }
    assert_eq!(receiver.reserve_request(164), Err(ReserveError::Bounded));
    assert_eq!(receiver.reserved_counts(), (2, 32, 64));

    receiver.release_connection(20);
    assert_eq!(receiver.reserved_counts(), (2, 31, 64));
    assert_eq!(
        receiver.reserve_connection(99),
        Ok(()),
        "a released slot frees capacity"
    );
    receiver.release_scan(1);
    assert_eq!(receiver.reserve_scan(77), Ok(()));
    receiver.release_request(100);
    assert_eq!(receiver.reserve_request(200), Ok(()));
    assert_eq!(
        receiver.reserve_request(100),
        Err(ReserveError::Reused),
        "a released request token is retired at the high-water mark: no reuse, ever"
    );
    assert_eq!(receiver.reserved_counts(), (2, 32, 64));
    // Releasing an absent token is a no-op beyond retiring its value (monotonic mark).
    receiver.release_connection(1234);
    receiver.release_scan(1234);
    receiver.release_request(1234);
    assert_eq!(receiver.reserved_counts(), (2, 32, 64));
}

#[test]
fn unknown_tokens_drop_without_allocating() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(events.emit(frag(7, 100, 0x0F)), EmitStatus::StaleToken);
    assert_eq!(
        events.emit(BackendEvent::Connected {
            conn: 7,
            capacity: LinkCapacity::AttMtu(517),
            platform_hint: None,
        }),
        EmitStatus::StaleToken
    );
    assert_eq!(
        events.emit(BackendEvent::LinkLost { conn: 7 }),
        EmitStatus::StaleToken
    );
    assert_eq!(
        events.emit(BackendEvent::LinkReleased { conn: 7 }),
        EmitStatus::StaleToken
    );
    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 9 }),
        EmitStatus::StaleToken
    );
    assert_eq!(
        events.emit(BackendEvent::ScanEnded {
            scan: 9,
            failure: Some(Failure::new("bluetooth_off", false)),
        }),
        EmitStatus::StaleToken
    );
    assert_eq!(
        events.emit(BackendEvent::Advertisement {
            scan: 9,
            device: Discovered {
                platform_hint: "h".to_owned(),
                label: "l".to_owned(),
                advertisement: None,
            },
        }),
        EmitStatus::AdvertisementDropped
    );
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 7, send: 200 }),
        EmitStatus::StaleToken
    );
    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );

    assert_eq!(receiver.data_usage(), (0, 0));
    assert_eq!(receiver.reserved_counts(), (0, 0, 0));
    assert_eq!(
        receiver.pop_event(Some(ZERO)),
        BackendPop::Event(BackendEvent::ShutdownComplete),
        "stale events create no slots; only the global terminal is storable"
    );
    expect_timeout(&receiver);
}

#[test]
fn control_burst_beyond_link_cap_sets_process_level_overflow_flag() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    // 32 conn-scoped controls fit: Connected plus 31 capacity changes.
    assert_eq!(
        events.emit(BackendEvent::Connected {
            conn: 1,
            capacity: LinkCapacity::AttMtu(517),
            platform_hint: None,
        }),
        EmitStatus::Accepted
    );
    for _ in 0..31 {
        assert_eq!(
            events.emit(BackendEvent::CapacityChanged {
                conn: 1,
                capacity: LinkCapacity::AttMtu(517),
            }),
            EmitStatus::Accepted
        );
    }
    assert!(!receiver.control_overflow());
    assert_eq!(
        events.emit(BackendEvent::LinkLost { conn: 1 }),
        EmitStatus::ControlOverflow,
        "at most 32 control events per link may queue"
    );
    assert!(
        receiver.control_overflow(),
        "the process-level control overflow was recorded"
    );
    assert_eq!(
        events.emit(BackendEvent::LinkReleased { conn: 1 }),
        EmitStatus::ControlOverflow,
        "further controls stay rejected while the flag is latched"
    );
    let drained = drain(&receiver);
    assert_eq!(
        drained.len(),
        32,
        "exactly the 32 accepted controls are delivered"
    );
    assert!(receiver.control_overflow(), "the latch is sticky");
}

#[test]
fn advertisements_coalesce_boundedly_with_bounded_metadata() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(1), Ok(()));

    let device = |hint: &str, label: String, advertisement: Option<Vec<u8>>| Discovered {
        platform_hint: hint.to_owned(),
        label,
        advertisement,
    };
    // Ten observations of the same device coalesce into one staged entry; metadata is latest.
    for index in 0..10 {
        assert_eq!(
            events.emit(BackendEvent::Advertisement {
                scan: 1,
                device: device("aa:bb:cc:dd:ee:ff", format!("device v{index}"), None),
            }),
            EmitStatus::Accepted,
        );
    }
    // 69 further distinct devices: 63 fill the staging bound, the remaining 6 are dropped.
    for index in 0..69usize {
        let hint = format!("d{index}");
        let status = events.emit(BackendEvent::Advertisement {
            scan: 1,
            device: device(&hint, format!("l{index}"), None),
        });
        if index < 63 {
            assert_eq!(status, EmitStatus::Accepted);
        } else {
            assert_eq!(status, EmitStatus::AdvertisementDropped);
        }
    }

    let mut delivered = Vec::new();
    loop {
        match receiver.pop_event(Some(ZERO)) {
            BackendPop::Event(BackendEvent::Advertisement { device, .. }) => delivered.push(device),
            BackendPop::Event(other) => panic!("unexpected event while draining: {other:?}"),
            BackendPop::Timeout => break,
        }
    }
    assert_eq!(
        delivered.len(),
        64,
        "one coalesced entry plus 63 distinct fill the bound"
    );
    assert_eq!(delivered[0].platform_hint, "aa:bb:cc:dd:ee:ff");
    assert_eq!(
        delivered[0].label, "device v9",
        "the latest observation wins"
    );
    assert_eq!(delivered[63].platform_hint, "d62");

    // Bounded metadata: the opaque routing hint is preserved verbatim (dedupe/connect key) or
    // the whole device is rejected — never rewritten; advertisement data is a bounded copy at
    // the binding 256-byte ceiling; display labels are sanitized separately.
    let hint_256 = "h".repeat(256);
    assert_eq!(
        events.emit(BackendEvent::Advertisement {
            scan: 1,
            device: device(
                &hint_256,
                format!("l\u{7}{}\u{1}", "n".repeat(300)),
                Some(vec![0xEE; 1_000]),
            ),
        }),
        EmitStatus::Accepted,
        "staging refills after a drain"
    );
    match expect_event(&receiver) {
        BackendEvent::Advertisement { ref device, .. } => {
            assert_eq!(
                device.platform_hint, hint_256,
                "a representable opaque hint must be preserved byte-for-byte"
            );
            assert!(device.label.len() <= 256);
            assert!(
                !device.label.contains('\u{7}') && !device.label.contains('\u{1}'),
                "control characters must not survive in the display label"
            );
            assert!(device.label.contains(&"n".repeat(100)));
            assert_eq!(
                device.advertisement.as_ref().map(Vec::<u8>::len),
                Some(256),
                "the binding advertisement ceiling is 256 bytes"
            );
        }
        other => panic!("expected advertisement, got {other:?}"),
    }

    // Unrepresentable opaque hints are rejected whole, never truncated or sanitized.
    for bad_hint in ["y".repeat(257), "a\u{7}b".to_owned(), String::new()] {
        assert_eq!(
            events.emit(BackendEvent::Advertisement {
                scan: 1,
                device: device(&bad_hint, "kept?".to_owned(), None),
            }),
            EmitStatus::AdvertisementDropped,
            "hint of {} bytes with controls={} must not be staged rewritten",
            bad_hint.len(),
            bad_hint.chars().any(char::is_control),
        );
    }
    expect_timeout(&receiver);

    // Advertisement data beyond the binding 256-byte ceiling is a bounded copy, never staged
    // unboundedly.
    assert_eq!(
        events.emit(BackendEvent::Advertisement {
            scan: 1,
            device: device("z", "y".to_owned(), Some(vec![0xEE; 1_000]),),
        }),
        EmitStatus::Accepted
    );
    match expect_event(&receiver) {
        BackendEvent::Advertisement { ref device, .. } => {
            assert_eq!(device.advertisement.as_ref().map(Vec::<u8>::len), Some(256));
        }
        other => panic!("expected advertisement, got {other:?}"),
    }
    expect_timeout(&receiver);

    assert_eq!(
        events.emit(BackendEvent::Advertisement {
            scan: 1,
            device: device("x", "y".to_owned(), None),
        }),
        EmitStatus::Accepted
    );
    expect_event(&receiver);
    expect_timeout(&receiver);

    // Advertisements of an unreserved scan are dropped.
    assert_eq!(receiver.reserve_scan(2), Ok(()));
    assert_eq!(
        events.emit(BackendEvent::Advertisement {
            scan: 3,
            device: device("q", "r".to_owned(), None),
        }),
        EmitStatus::AdvertisementDropped
    );
    expect_timeout(&receiver);
}

#[test]
fn round_robin_is_fair_across_links() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(receiver.reserve_connection(2), Ok(()));
    assert_eq!(events.emit(frag(1, 4, 0x01)), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(2, 4, 0x02)), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(1, 4, 0x03)), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(2, 4, 0x04)), EmitStatus::Accepted);

    let conns: Vec<u64> = (0..4)
        .map(|_| match expect_event(&receiver) {
            BackendEvent::Fragment { conn, .. } => conn,
            other => panic!("expected fragment, got {other:?}"),
        })
        .collect();
    assert_eq!(
        conns,
        vec![1, 2, 1, 2],
        "equal queue lengths strictly alternate"
    );
}

/// Fix2 (shutdown handshake arming): the core's arm of the shutdown handshake and a
/// backend process terminal are ordered under the same ingress state lock — a terminal
/// observed before `arm_shutdown` permanently makes the handshake unexpected, and only a
/// terminal observed after the arm can complete it.
#[test]
fn shutdown_handshake_arm_is_atomically_ordered_with_process_terminal() {
    // A fresh ingress: the arm succeeds and can be (idempotently) taken again
    // only while no terminal was observed.
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.arm_shutdown(), ArmedShutdown::Armed);
    assert_eq!(receiver.arm_shutdown(), ArmedShutdown::Armed);

    // A terminal STAGED before any further arm makes that arm unexpected
    // (the handshake must never complete on a pre-existing terminal).
    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    assert_eq!(receiver.arm_shutdown(), ArmedShutdown::UnexpectedTerminal);

    // Delivered terminals likewise: any arm after the observation is too late.
    assert_eq!(drain(&receiver), vec![BackendEvent::ShutdownComplete]);
    assert_eq!(receiver.arm_shutdown(), ArmedShutdown::UnexpectedTerminal);

    // A second staged terminal after the first one collapsed is still an
    // unexpected basis for a later arm.
    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    assert_eq!(receiver.arm_shutdown(), ArmedShutdown::UnexpectedTerminal);
}

#[test]
fn pop_event_zero_timeout_on_empty_times_out() {
    let (_events, receiver) = BackendEvents::new();
    assert_eq!(receiver.pop_event(Some(ZERO)), BackendPop::Timeout);
}

// ---------------------------------------------------------------------------
// Fix1A regressions: release accounting/cancellation, oversize link failure,
// opaque metadata, retained-allocation bounds, cloneable producers, push
// ownership documentation
// ---------------------------------------------------------------------------

/// Critical finding 1: releasing a connection with queued fragment data must subtract that
/// slot's per-link accounting from the global totals (no phantom usage) and leave the token
/// fully reusable.
#[test]
fn release_connection_with_queued_data_restores_global_accounting() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(receiver.reserve_connection(2), Ok(()));
    for _ in 0..16 {
        assert_eq!(events.emit(frag(1, 512, 0x11)), EmitStatus::Accepted);
    }
    for _ in 0..2 {
        assert_eq!(events.emit(frag(2, 100, 0x22)), EmitStatus::Accepted);
    }
    assert_eq!(receiver.data_usage(), (18, 8_392));

    receiver.release_connection(1);
    assert_eq!(
        receiver.data_usage(),
        (2, 200),
        "link 1's queued data must not remain as phantom global usage"
    );
    assert_eq!(
        receiver.link_data_usage(1),
        None,
        "the released slot's per-link accounting is gone with it"
    );
    receiver.release_connection(2);
    assert_eq!(receiver.data_usage(), (0, 0));

    // The released token is fully reusable against restored global capacity.
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(events.emit(frag(1, 512, 0x11)), EmitStatus::Accepted);
    assert_eq!(
        events.emit(frag(1, 512, 0x11)),
        EmitStatus::Accepted,
        "the full 64-KiB budget must be available again after the release"
    );
    assert_eq!(receiver.data_usage(), (2, 1_024));
}

/// Important finding 9: releasing a request must cancel completions of that token already
/// queued per-link (they must not deliver after the release — and monotonic tokens, review
/// round 5 N2, keep a retired value from ever being re-reserved, so no later reservation can
/// receive them).
#[test]
fn release_request_cancels_queued_completions_suppressing_stale_delivery() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(10), Ok(()));
    assert_eq!(receiver.reserve_request(100), Ok(()));
    assert_eq!(receiver.reserve_request(101), Ok(()));

    // Completions queued before the release...
    assert_eq!(
        events.emit(BackendEvent::WriteDone {
            conn: 10,
            send: 100
        }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(frag(10, 8, 0x01)), EmitStatus::Accepted);
    assert_eq!(
        events.emit(BackendEvent::WriteFailed {
            conn: 10,
            send: 101,
            failure: Failure::new("gatt_write", false),
        }),
        EmitStatus::Accepted
    );

    // ...are cancelled by the release; post-release emissions are stale at admission.
    receiver.release_request(100);
    receiver.release_request(101);
    assert_eq!(
        events.emit(BackendEvent::WriteDone {
            conn: 10,
            send: 100
        }),
        EmitStatus::StaleToken,
        "completions for a released token are rejected at admission"
    );
    assert_eq!(
        events.emit(frag(10, 8, 0x02)),
        EmitStatus::Accepted,
        "cancel removes only that token's completions, never link data"
    );

    // Fresh, strictly increasing tokens reserve normally after the release (the retired
    // values 100/101 themselves are rejected — review round 5, N2's monotonic tokens).
    assert_eq!(receiver.reserve_request(102), Ok(()));
    assert_eq!(receiver.reserve_request(103), Ok(()));
    assert_eq!(
        events.emit(BackendEvent::WriteFailed {
            conn: 10,
            send: 102,
            failure: Failure::new("gatt_write", false),
        }),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(BackendEvent::WriteDone {
            conn: 10,
            send: 103
        }),
        EmitStatus::Accepted
    );

    let drained = drain(&receiver);
    assert_eq!(
        drained,
        vec![
            BackendEvent::Fragment {
                conn: 10,
                bytes: Payload::new(vec![0x01; 8]),
            },
            BackendEvent::Fragment {
                conn: 10,
                bytes: Payload::new(vec![0x02; 8]),
            },
            BackendEvent::WriteFailed {
                conn: 10,
                send: 102,
                failure: Failure::new("gatt_write", false),
            },
            BackendEvent::WriteDone {
                conn: 10,
                send: 103,
            },
        ],
        "both pre-release completions (WriteDone{{100}}, WriteFailed{{101}}) must be cancelled; \
         link data and each fresh strictly increasing token's own completion remain in link order"
    );
    let done: Vec<u64> = drained
        .iter()
        .filter_map(|event| match event {
            BackendEvent::WriteDone { send, .. } => Some(*send),
            _ => None,
        })
        .collect();
    assert_eq!(done, vec![103], "no stale WriteDone of the cancelled token");
    let failed: Vec<u64> = drained
        .iter()
        .filter_map(|event| match event {
            BackendEvent::WriteFailed { send, .. } => Some(*send),
            _ => None,
        })
        .collect();
    assert_eq!(failed, vec![102]);
    expect_timeout(&receiver);
}

/// Review round 5, N2: request tokens are monotonic and never reused. A released token is
/// retired at the bounded high-water mark — re-reserving it, or any lower out-of-order
/// value, is rejected even while its late completion is in flight, so the late old
/// completion can never be delivered to a (re)reservation of the same value. No retired-set
/// is kept; a token still reserved stays idempotently reservable.
#[test]
fn request_token_reuse_after_release_is_rejected_and_late_completion_stays_undeliverable() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(receiver.reserve_request(100), Ok(()));
    assert_eq!(
        receiver.reserve_request(100),
        Ok(()),
        "re-reserving a token that is still reserved stays idempotent (preserved behavior)"
    );

    // The old request's completion is queued when the token is released...
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 1, send: 100 }),
        EmitStatus::Accepted
    );
    receiver.release_request(100);

    // ...and both the attempted re-reservation and the late old completion are rejected.
    assert_eq!(
        receiver.reserve_request(100),
        Err(ReserveError::Reused),
        "a released token value is retired: no reuse, even after release"
    );
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 1, send: 100 }),
        EmitStatus::StaleToken,
        "the late old completion of the retired token is stale at admission"
    );
    // Out-of-order too: a lower never-reserved value sits below the high-water mark.
    assert_eq!(
        receiver.reserve_request(50),
        Err(ReserveError::Reused),
        "out-of-order reservations are rejected against the monotonic high-water mark"
    );
    assert_eq!(receiver.reserved_counts(), (0, 1, 0));
    assert_eq!(
        drain(&receiver).len(),
        0,
        "no stale delivery of any retired value"
    );

    // A fresh, strictly increasing token reserves normally and delivers its own completion.
    assert_eq!(receiver.reserve_request(101), Ok(()));
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 1, send: 101 }),
        EmitStatus::Accepted
    );
    assert_eq!(
        drain(&receiver),
        vec![BackendEvent::WriteDone { conn: 1, send: 101 }],
        "the fresh token's own completion delivers exactly once"
    );
    expect_timeout(&receiver);
}

/// Important finding 11: a fragment above the physical 512-byte ceiling is rejected pre-copy
/// AND deterministically fails its link with exactly one retained marker — the core must see
/// a guaranteed terminal, and no later data of the link may be admitted as though the stream
/// were intact.
#[test]
fn oversize_fragment_fails_link_with_one_retained_marker() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(5), Ok(()));
    for _ in 0..2 {
        assert_eq!(events.emit(frag(5, 64, 0x55)), EmitStatus::Accepted);
    }
    assert_eq!(
        events.emit(frag(5, 513, 0xC3)),
        EmitStatus::FragmentOversize
    );
    assert_eq!(
        events.emit(frag(5, 64, 0x55)),
        EmitStatus::DataOverflow,
        "after the oversize fragment the stream is broken: no later data is admitted intact"
    );
    assert_eq!(
        receiver.link_data_usage(5),
        Some((2, 128)),
        "the oversize fragment is accounted nowhere"
    );
    let drained = drain(&receiver);
    assert_eq!(
        drained,
        vec![
            frag(5, 64, 0x55),
            frag(5, 64, 0x55),
            BackendEvent::LinkLost { conn: 5 },
        ],
        "the retained fragments precede exactly one retained failure marker"
    );
    assert_eq!(receiver.data_usage(), (0, 0));
    assert_eq!(
        events.emit(frag(5, 10, 0x55)),
        EmitStatus::DataOverflow,
        "the failed link stays failed until its reservation is released"
    );
    expect_timeout(&receiver);
}

/// Critical finding 2 (queues side): payloads are retained by `len()` only —
/// `Payload::new` normalizes excess `Vec` capacity, so a one-byte fragment from a huge
/// spare-capacity buffer passes the budget exactly as a one-byte fragment (buffer-level proof
/// lives in `buffer.rs`'s tests).
#[test]
fn excess_capacity_payload_is_admitted_by_len_not_capacity() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    let mut raw = Vec::with_capacity(65_536);
    raw.push(0x77);
    assert!(raw.capacity() >= 65_536, "precondition: excess capacity");
    assert_eq!(
        events.emit(BackendEvent::Fragment {
            conn: 1,
            bytes: Payload::new(raw),
        }),
        EmitStatus::Accepted
    );
    assert_eq!(receiver.data_usage(), (1, 1));
    assert_eq!(drain(&receiver).len(), 1);
    expect_timeout(&receiver);
}

/// Critical finding 2: the `Connected` control's optional platform hint is bounded at
/// admission — a representable hint (≤ 256 bytes, control-free, non-empty) is preserved
/// verbatim; an unrepresentable one degrades the metadata to `None` (bounded, explicit
/// absence, never a rewritten partial value) while the lifecycle event itself is never
/// dropped over metadata.
#[test]
fn connected_platform_hint_is_bounded_and_degraded_when_unrepresentable() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(30), Ok(()));
    let hint_256 = "h".repeat(256);
    assert_eq!(
        events.emit(BackendEvent::Connected {
            conn: 30,
            capacity: LinkCapacity::AttMtu(517),
            platform_hint: Some(hint_256.clone()),
        }),
        EmitStatus::Accepted
    );
    match expect_event(&receiver) {
        BackendEvent::Connected {
            ref platform_hint, ..
        } => {
            assert_eq!(
                platform_hint.as_deref(),
                Some(hint_256.as_str()),
                "a representable hint must be preserved byte-for-byte"
            );
        }
        other => panic!("expected Connected, got {other:?}"),
    }
    for bad in [String::new(), "y".repeat(257), "a\u{7}b".to_owned()] {
        assert_eq!(
            events.emit(BackendEvent::Connected {
                conn: 30,
                capacity: LinkCapacity::AttMtu(517),
                platform_hint: Some(bad),
            }),
            EmitStatus::Accepted,
            "the lifecycle event itself must never be dropped over metadata"
        );
        match expect_event(&receiver) {
            BackendEvent::Connected {
                ref platform_hint, ..
            } => {
                assert_eq!(
                    platform_hint.as_ref(),
                    None,
                    "an unrepresentable hint degrades to None, never to a rewritten value"
                );
            }
            other => panic!("expected Connected, got {other:?}"),
        }
    }
    expect_timeout(&receiver);
}

/// Minor finding 12: the advertised cloneable producer API — clones share the same staging
/// state, so an adapter keeps its clone for its OS callback path.
#[test]
fn backend_event_producers_are_cloneable() {
    let (events, receiver) = BackendEvents::new();
    let clone = events.clone();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(events.emit(frag(1, 8, 0x01)), EmitStatus::Accepted);
    assert_eq!(clone.emit(frag(1, 8, 0x02)), EmitStatus::Accepted);
    let conns: Vec<u64> = drain(&receiver)
        .iter()
        .filter_map(|event| match event {
            BackendEvent::Fragment { conn, .. } => Some(*conn),
            _ => None,
        })
        .collect();
    assert_eq!(
        conns,
        vec![1, 1],
        "both producer handles stage into the queue"
    );
}

/// Minor finding 13: both rejection kinds of `try_push_frame`/`push_frame_for` consume the
/// pushed payload (it drops and erases here); the queue is left intact and no stale frame
/// remains — callers re-parse for a retry, nothing is retained for them.
#[test]
fn rejected_input_pushes_consume_the_payload_and_leave_queue_intact() {
    let q = InputFrames::new();
    for _ in 0..64 {
        assert_eq!(q.try_push_frame(frame_of(1, 0x11)), Ok(()));
    }
    assert_eq!(
        q.try_push_frame(frame_of(1, 0xFF)),
        Err(InputPushError::Overflow)
    );
    assert_eq!(
        q.frame_usage(),
        (64, 64),
        "the rejected push left nothing behind in the queue"
    );
    // The timeout path of the backpressure push consumes the payload too.
    assert_eq!(
        q.push_frame_for(frame_of(256, 0x33), Some(ZERO)),
        Err(InputPushError::Overflow)
    );
    assert_eq!(q.frame_usage(), (64, 64));
    q.set_terminal(InputTerminal::Eof);
    assert_eq!(
        q.try_push_frame(frame_of(1, 0xFF)),
        Err(InputPushError::TerminalSet)
    );
    assert_eq!(
        q.frame_usage(),
        (64, 64),
        "a terminal-set-rejected push also leaves the queue intact"
    );
}

// ---------------------------------------------------------------------------
// Fix1B-order regressions: real FIFO positions for the failure marker (finding
// 7) and per-scan sequencing of terminals versus staged adverts (finding 8)
// ---------------------------------------------------------------------------

fn advert(scan: u64, hint: &str) -> BackendEvent {
    BackendEvent::Advertisement {
        scan,
        device: Discovered {
            platform_hint: hint.to_owned(),
            label: "device".to_owned(),
            advertisement: None,
        },
    }
}

/// Important finding 7: the data-budget overflow marker holds its exact per-link FIFO
/// position — after the retained data and the control emitted before the failure, before the
/// control emitted after the failure. The marker must never be deferred behind controls that
/// were admitted later, and it consumes no reserved control slot.
#[test]
fn overflow_marker_holds_its_fifo_position_before_later_controls() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    assert_eq!(receiver.reserve_request(70), Ok(()));

    // A control emitted before the failure precedes everything queued after it.
    assert_eq!(
        events.emit(BackendEvent::CapacityChanged {
            conn: 1,
            capacity: LinkCapacity::AttMtu(517),
        }),
        EmitStatus::Accepted
    );
    // Exactly 48 events of 64 bytes fill the per-link event budget; the 49th overflows,
    // synthesizing the marker at its stream position, and the link stays failed for data.
    for index in 0..48 {
        assert_eq!(
            events.emit(frag(1, 64, u8::try_from(index).unwrap())),
            EmitStatus::Accepted
        );
    }
    assert_eq!(events.emit(frag(1, 64, 0x10)), EmitStatus::DataOverflow);
    // A control emitted after the failure is still admitted (no reserved slot is displaced
    // by the marker) and must deliver after the earlier synthesized LinkLost.
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 1, send: 70 }),
        EmitStatus::Accepted
    );

    let mut expected = vec![BackendEvent::CapacityChanged {
        conn: 1,
        capacity: LinkCapacity::AttMtu(517),
    }];
    for index in 0..48 {
        expected.push(frag(1, 64, u8::try_from(index).unwrap()));
    }
    expected.push(BackendEvent::LinkLost { conn: 1 });
    expected.push(BackendEvent::WriteDone { conn: 1, send: 70 });
    assert_eq!(
        drain(&receiver),
        expected,
        "exact per-link emission order: earlier control, data, marker, later control"
    );
    expect_timeout(&receiver);
}

/// Important finding 7 (the oversize path synthesizes the same marker): the
/// [`EmitStatus::FragmentOversize`] failure marker also occupies its exact FIFO position
/// between the link's retained data and a completion control emitted after the failure.
#[test]
fn oversize_marker_holds_its_fifo_position_before_later_controls() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(2), Ok(()));
    assert_eq!(receiver.reserve_request(71), Ok(()));

    assert_eq!(events.emit(frag(2, 8, 0x22)), EmitStatus::Accepted);
    assert_eq!(
        events.emit(frag(2, 513, 0xC3)),
        EmitStatus::FragmentOversize
    );
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 2, send: 71 }),
        EmitStatus::Accepted
    );

    assert_eq!(
        drain(&receiver),
        vec![
            frag(2, 8, 0x22),
            BackendEvent::LinkLost { conn: 2 },
            BackendEvent::WriteDone { conn: 2, send: 71 },
        ],
        "the oversize failure marker waits behind the retained fragment and ahead of the \
         later completion"
    );
    expect_timeout(&receiver);
}

/// Important finding 8 (terminal variant `ScanEnded`): scan controls and staged discoveries
/// share one per-scan FIFO, so every earlier staged advert delivers before the scan's end
/// confirmation — never after it.
#[test]
fn scan_ended_does_not_overtake_earlier_staged_adverts() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(1), Ok(()));

    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 1 }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(advert(1, "aa:bb")), EmitStatus::Accepted);
    assert_eq!(events.emit(advert(1, "cc:dd")), EmitStatus::Accepted);
    assert_eq!(
        events.emit(BackendEvent::ScanEnded {
            scan: 1,
            failure: None
        }),
        EmitStatus::Accepted
    );

    assert_eq!(
        drain(&receiver),
        vec![
            BackendEvent::ScanStarted { scan: 1 },
            advert(1, "aa:bb"),
            advert(1, "cc:dd"),
            BackendEvent::ScanEnded {
                scan: 1,
                failure: None
            },
        ],
        "the end confirmation must not overtake adverts staged during the scan"
    );
    expect_timeout(&receiver);
}

/// Important finding 8 (terminal variant `ScanFailed`): a failed scan's staged adverts
/// deliver before the failure terminal — the core never observes a failure whose scan history
/// contains undelivered discoveries.
#[test]
fn scan_failed_does_not_overtake_earlier_staged_adverts() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(4), Ok(()));

    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 4 }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(advert(4, "aa:bb")), EmitStatus::Accepted);
    let failure = Failure::new("bluetooth_off", false);
    assert_eq!(
        events.emit(BackendEvent::ScanFailed { scan: 4, failure }),
        EmitStatus::Accepted
    );

    assert_eq!(
        drain(&receiver),
        vec![
            BackendEvent::ScanStarted { scan: 4 },
            advert(4, "aa:bb"),
            BackendEvent::ScanFailed { scan: 4, failure },
        ],
        "the failure terminal must not overtake earlier staged adverts"
    );
    expect_timeout(&receiver);
}

/// Important finding 8 (coalescing): the latest-wins replacement must keep the staged
/// position inside the per-scan FIFO — the coalesced advert stays ahead of the scan
/// terminal, and only its metadata is replaced.
#[test]
fn coalesced_advert_keeps_its_stage_position_before_the_scan_terminal() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(1), Ok(()));

    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 1 }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(advert(1, "aa:bb")), EmitStatus::Accepted);
    let latest = BackendEvent::Advertisement {
        scan: 1,
        device: Discovered {
            platform_hint: "aa:bb".to_owned(),
            label: "latest".to_owned(),
            advertisement: Some(vec![0xE1]),
        },
    };
    // Same opaque hint: the second observation replaces the staged one in place.
    assert_eq!(events.emit(latest.clone()), EmitStatus::Accepted);
    assert_eq!(
        events.emit(BackendEvent::ScanEnded {
            scan: 1,
            failure: None
        }),
        EmitStatus::Accepted
    );

    assert_eq!(
        drain(&receiver),
        vec![
            BackendEvent::ScanStarted { scan: 1 },
            latest,
            BackendEvent::ScanEnded {
                scan: 1,
                failure: None
            },
        ],
        "coalescing must keep the staged position: one advert, latest metadata, then the \
         end confirmation"
    );
    expect_timeout(&receiver);
}

// ---------------------------------------------------------------------------
// Fix1B-fair regressions: reserved-control progress under continuously
// refillable advertisement staging (Critical 4) and closed admission plus
// terminal progress after shutdown (Critical 5)
// ---------------------------------------------------------------------------

fn advert_label(scan: u64, hint: &str, label: &str) -> BackendEvent {
    BackendEvent::Advertisement {
        scan,
        device: Discovered {
            platform_hint: hint.to_owned(),
            label: label.to_owned(),
            advertisement: None,
        },
    }
}

/// Critical finding 4: two scans continuously re-stage advertisements (same opaque hint
/// coalesces in place, so both scan zones stay non-empty forever), while one connection
/// holds queued fragments and a completion. Fair scheduling serves every non-empty zone
/// exactly once per sweep of the rotating cursor — the connection's data and control
/// progress within bounded pops and keep their per-link FIFO order, each scan keeps its
/// own FIFO (its start control before its staged adverts), and the latest-wins metadata
/// still wins. Pre-fix, the scan phase was always served first, so
/// `Advertisement{scan 1, ..}` re-delivered every pop and the connection events never
/// appeared while the staging continued.
#[test]
fn sustained_advertisements_never_starve_connection_progress() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(1), Ok(()));
    assert_eq!(receiver.reserve_scan(2), Ok(()));
    assert_eq!(receiver.reserve_connection(10), Ok(()));
    assert_eq!(receiver.reserve_request(60), Ok(()));

    // Setup: scan control plus one staged advert per scan, and four connection events.
    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 1 }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(advert(1, "aa:bb")), EmitStatus::Accepted);
    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 2 }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(advert(2, "cc:dd")), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(10, 8, 0x01)), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(10, 8, 0x02)), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(10, 8, 0x03)), EmitStatus::Accepted);
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 10, send: 60 }),
        EmitStatus::Accepted
    );

    // Four bounded sweeps with continuous re-staging: after every pop of an advertising
    // scan the producer re-observes that scan's device under the same opaque hint (latest
    // wins, coalesced in place) — the advert class is continuously refillable, yet every
    // zone advances exactly once per sweep of the rotating cursor. The exact sequence is:
    // zone A, zone B, zone C, zone A, zone B, zone C, ... with each scan's control before
    // its first advert and the link's fragments before its completion.
    let kinds = [
        "ScanStarted",
        "ScanStarted",
        "Fragment", // sweep 1
        "Advertisement",
        "Advertisement",
        "Fragment", // sweep 2
        "Advertisement",
        "Advertisement",
        "Fragment", // sweep 3
        "Advertisement",
        "Advertisement",
        "WriteDone", // sweep 4
    ];
    let refill = |step: usize| -> Option<BackendEvent> {
        match step {
            0 => Some(advert_label(1, "aa:bb", "v1")),
            1 => Some(advert_label(2, "cc:dd", "v1")),
            3 => Some(advert_label(1, "aa:bb", "v2")),
            4 => Some(advert_label(2, "cc:dd", "v2")),
            6 => Some(advert_label(1, "aa:bb", "v3")),
            7 => Some(advert_label(2, "cc:dd", "v3")),
            9 => Some(advert_label(1, "aa:bb", "v4")),
            10 => Some(advert_label(2, "cc:dd", "v4")),
            _ => None,
        }
    };
    for (index, kind) in kinds.iter().enumerate() {
        let actual = match expect_event(&receiver) {
            BackendEvent::ScanStarted { .. } => "ScanStarted",
            BackendEvent::Advertisement { .. } => "Advertisement",
            BackendEvent::Fragment { .. } => "Fragment",
            BackendEvent::WriteDone { .. } => "WriteDone",
            other => panic!("unexpected event shape while sweeping: {other:?}"),
        };
        assert_eq!(
            &actual,
            kind,
            "sweep step {index}: continuous staging must not starve the connection phase \
             (pre-fix, scan {} was re-served every pop)",
            index % 3 + 1
        );
        if let Some(event) = refill(index) {
            assert_eq!(
                events.emit(event),
                EmitStatus::Accepted,
                "the adversarial producer keeps staging; staging must stay accepted"
            );
        }
    }

    // The re-staged tail drains in per-scan registration order, latest metadata first-wins
    // replaced: both "v4" observations, then the receiver is empty.
    assert!(matches!(
        expect_event(&receiver),
        BackendEvent::Advertisement { device, .. } if device.label == *"v4"
    ));
    assert!(matches!(
        expect_event(&receiver),
        BackendEvent::Advertisement { device, .. } if device.label == *"v4"
    ));
    assert_eq!(receiver.data_usage(), (0, 0));
    expect_timeout(&receiver);
}

/// Critical finding 5: once the backend has reported process shutdown (terminal latched
/// pending), admission is closed — every further emission is a typed rejected status and
/// nothing new is staged — while the already-retained work drains in per-zone FIFO order
/// and `ShutdownComplete` delivers last, exactly once. Post-delivery, the terminal
/// collapses silently and emissions stay rejected.
#[test]
fn post_shutdown_admission_is_closed_and_retained_work_drains_to_terminal() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_scan(1), Ok(()));
    assert_eq!(receiver.reserve_connection(10), Ok(()));
    assert_eq!(receiver.reserve_request(60), Ok(()));

    // Retained work before the terminal is latched.
    assert_eq!(
        events.emit(BackendEvent::ScanStarted { scan: 1 }),
        EmitStatus::Accepted
    );
    assert_eq!(events.emit(advert(1, "aa:bb")), EmitStatus::Accepted);
    assert_eq!(events.emit(frag(10, 8, 0x01)), EmitStatus::Accepted);
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 10, send: 60 }),
        EmitStatus::Accepted
    );
    assert_eq!(receiver.data_usage(), (1, 8));

    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted,
        "the terminal latches pending"
    );

    // Admission is closed: even reserved tokens are rejected, nothing staged, nothing
    // latched as control overflow (the typed rejection is the bounded behavior).
    let rejected = [
        events.emit(BackendEvent::ScanStarted { scan: 1 }),
        events.emit(BackendEvent::ScanEnded {
            scan: 1,
            failure: None,
        }),
        events.emit(advert(1, "aa:bb")),
        events.emit(frag(10, 8, 0x02)),
        events.emit(BackendEvent::WriteDone { conn: 10, send: 60 }),
        events.emit(BackendEvent::WriteFailed {
            conn: 10,
            send: 60,
            failure: Failure::new("gatt_write", false),
        }),
        events.emit(BackendEvent::Connected {
            conn: 10,
            capacity: LinkCapacity::AttMtu(517),
            platform_hint: None,
        }),
        events.emit(BackendEvent::CapacityChanged {
            conn: 10,
            capacity: LinkCapacity::AttMtu(517),
        }),
        events.emit(BackendEvent::LinkLost { conn: 10 }),
        events.emit(BackendEvent::LinkReleased { conn: 10 }),
    ];
    for (index, status) in rejected.iter().enumerate() {
        assert_eq!(
            status,
            &EmitStatus::Shutdown,
            "post-shutdown emission #{index} must be rejected as closed admission"
        );
    }
    assert_eq!(
        receiver.data_usage(),
        (1, 8),
        "a rejected admission stages nothing"
    );
    assert_eq!(receiver.reserved_counts(), (1, 1, 1));
    assert!(
        !receiver.control_overflow(),
        "the closed-admission rejection is typed, not a control overflow"
    );

    // The retained work drains in per-zone FIFO order (fair sweep over the two zones),
    // then the terminal, exactly once — a subsequent pop only times out.
    assert_eq!(
        drain(&receiver),
        vec![
            BackendEvent::ScanStarted { scan: 1 },
            frag(10, 8, 0x01),
            advert(1, "aa:bb"),
            BackendEvent::WriteDone { conn: 10, send: 60 },
            BackendEvent::ShutdownComplete,
        ],
        "retained zones drain first (fair sweep), then the terminal, exactly once"
    );
    assert_eq!(receiver.data_usage(), (0, 0));
    expect_timeout(&receiver);

    // Post-delivery: the terminal collapses silently; everything else stays rejected.
    assert_eq!(
        events.emit(BackendEvent::ShutdownComplete),
        EmitStatus::Accepted
    );
    assert_eq!(
        events.emit(frag(10, 8, 0x03)),
        EmitStatus::Shutdown,
        "even after delivery, admission stays closed"
    );
    expect_timeout(&receiver);
}

// ---------------------------------------------------------------------------
// Fix1B-capacity regressions: guaranteed per-link completion storage for
// every admitted request (Important 6), independent of the reserved ordinary
// control slots, with bounded capacity and cancellation accounting
// ---------------------------------------------------------------------------

/// Important finding 6 (minimal legitimate burst): 33 reserved requests complete on one link
/// — one more than the 32 ordinary control slots. Every admitted request has guaranteed
/// per-link completion storage, so no completion drops and nothing latches process-level
/// control overflow; each completion delivers exactly once, in link emission order.
#[test]
fn completion_burst_beyond_thirty_two_delivers_without_overflow() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    let mut expected = Vec::new();
    for send in 100..133u64 {
        assert_eq!(receiver.reserve_request(send), Ok(()));
        let event = BackendEvent::WriteDone { conn: 1, send };
        assert_eq!(
            events.emit(event.clone()),
            EmitStatus::Accepted,
            "reserved request {send}'s completion must always be storable"
        );
        expected.push(event);
    }
    assert!(
        !receiver.control_overflow(),
        "a legitimate completion burst must never latch control overflow"
    );
    assert_eq!(receiver.data_usage(), (0, 0), "completions are not data");
    assert_eq!(
        drain(&receiver),
        expected,
        "all 33 admitted completions deliver exactly once, in link order"
    );
    expect_timeout(&receiver);
}

/// Important finding 6 (full bounds coincide): 64 admitted requests — the global outstanding
/// bound — all complete on one link whose 32 reserved ordinary control slots are already
/// full (Connected plus 31 CapacityChanged). Send completions stage in their own guaranteed
/// per-link region of the link FIFO, so nothing overflows and all 96 events deliver in
/// per-link FIFO order: ordinary controls first, then token-ordered completions.
#[test]
fn sixty_four_completions_on_one_link_alongside_full_ordinary_controls() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));

    // The link's ordinary control tail is exactly full (32 reserved slots).
    let connected = BackendEvent::Connected {
        conn: 1,
        capacity: LinkCapacity::AttMtu(517),
        platform_hint: None,
    };
    assert_eq!(events.emit(connected.clone()), EmitStatus::Accepted);
    let mut expected = vec![connected];
    for _ in 0..31 {
        let event = BackendEvent::CapacityChanged {
            conn: 1,
            capacity: LinkCapacity::AttMtu(517),
        };
        assert_eq!(events.emit(event.clone()), EmitStatus::Accepted);
        expected.push(event);
    }

    // The completion burst: every globally admitted request token (64) completes here.
    for send in 100..164u64 {
        assert_eq!(receiver.reserve_request(send), Ok(()));
        let event = if send % 2 == 0 {
            BackendEvent::WriteDone { conn: 1, send }
        } else {
            BackendEvent::WriteFailed {
                conn: 1,
                send,
                failure: Failure::new("gatt_write", false),
            }
        };
        assert_eq!(
            events.emit(event.clone()),
            EmitStatus::Accepted,
            "the 64 admitted requests' completions must never overflow one link"
        );
        expected.push(event);
    }
    assert!(
        !receiver.control_overflow(),
        "64 reserved completions plus a full ordinary control tail must never overflow"
    );
    assert_eq!(receiver.data_usage(), (0, 0), "completions are not data");
    assert_eq!(receiver.reserved_counts(), (0, 1, 64));

    assert_eq!(
        drain(&receiver),
        expected,
        "per-link FIFO order: ordinary controls first, then token-ordered completions"
    );
    assert_eq!(
        receiver.reserved_counts(),
        (0, 1, 64),
        "deliveries do not consume reservations"
    );
    expect_timeout(&receiver);
}

/// Important finding 6 (cancellation accounting): releasing a request cancels its already
/// queued completion and returns that guaranteed per-link completion slot, immediately
/// reusable by a fresh reservation — the cancelled completion never delivers, the fresh
/// request's completion does.
#[test]
fn cancelled_completion_frees_its_guaranteed_per_link_slot() {
    let (events, receiver) = BackendEvents::new();
    assert_eq!(receiver.reserve_connection(1), Ok(()));
    for send in 200..264u64 {
        assert_eq!(receiver.reserve_request(send), Ok(()));
    }
    for send in 200..264u64 {
        assert_eq!(
            events.emit(BackendEvent::WriteDone { conn: 1, send }),
            EmitStatus::Accepted
        );
    }
    assert!(!receiver.control_overflow());

    // Token 200 is cancelled before its queued completion is consumed; the freed guaranteed
    // slot immediately carries a freshly reserved token's completion on the same link.
    receiver.release_request(200);
    assert_eq!(receiver.reserve_request(300), Ok(()));
    assert_eq!(
        events.emit(BackendEvent::WriteDone { conn: 1, send: 300 }),
        EmitStatus::Accepted,
        "cancelled storage must be reusable without control overflow"
    );
    assert!(!receiver.control_overflow());

    let mut expected = Vec::new();
    for send in 201..264u64 {
        expected.push(BackendEvent::WriteDone { conn: 1, send });
    }
    expected.push(BackendEvent::WriteDone { conn: 1, send: 300 });
    assert_eq!(
        drain(&receiver),
        expected,
        "the cancelled completion never delivers; 63 cancelled-out plus 1 fresh = 64 deliver"
    );
    expect_timeout(&receiver);
}
