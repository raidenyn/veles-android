//! Bounded pending-output queue: 128 messages / 256 KiB, no silent drops, request-id reservation.

use std::time::{Duration, Instant};

use veles_otp23_feasibility::outqueue::{
    MAX_QUEUED_BYTES, MAX_QUEUED_MESSAGES, OutQueue, Overflow, Pop,
};

const NOW: Duration = Duration::from_millis(0);

#[test]
fn limits_are_the_frozen_ceilings() {
    assert_eq!((MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES), (128, 262_144));
}

#[test]
fn preserves_order_and_reports_depth() {
    let q = OutQueue::new(8, 1024);
    for i in 0..3u8 {
        q.push(vec![i; 10], None).unwrap();
    }
    assert_eq!((q.len(), q.queued_bytes()), (3, 30));
    for i in 0..3u8 {
        assert_eq!(q.pop(NOW), Pop::Frame(vec![i; 10]));
        q.finish_write();
    }
    assert_eq!(q.pop(NOW), Pop::Timeout);
    assert!(q.is_empty());
}

#[test]
fn message_ceiling_overflows_without_dropping_or_reordering() {
    let q = OutQueue::new(MAX_QUEUED_MESSAGES, MAX_QUEUED_BYTES);
    for i in 0..MAX_QUEUED_MESSAGES {
        q.push(vec![(i % 256) as u8; 4], None).unwrap();
    }
    assert_eq!(q.push(vec![9; 4], None), Err(Overflow));
    assert_eq!(q.len(), MAX_QUEUED_MESSAGES);
    assert_eq!(
        q.pop(NOW),
        Pop::Frame(vec![0; 4]),
        "the oldest message is still first"
    );
}

#[test]
fn byte_ceiling_overflows_at_the_boundary() {
    let q = OutQueue::new(128, 1000);
    q.push(vec![1; 600], None).unwrap();
    q.push(vec![2; 400], None).unwrap(); // exactly 1000
    assert_eq!(q.push(vec![3; 1], None), Err(Overflow));
    assert_eq!(q.queued_bytes(), 1000);
}

#[test]
fn a_popped_frame_stays_accounted_until_its_write_finishes() {
    let q = OutQueue::new(1, 100);
    q.push(vec![1; 10], Some("r1".into())).unwrap();
    assert_eq!(q.pop(NOW), Pop::Frame(vec![1; 10]));
    assert_eq!(
        q.push(vec![2; 10], None),
        Err(Overflow),
        "still being written to a frozen stdout"
    );
    q.finish_write();
    assert!(q.push(vec![2; 10], None).is_ok());
}

#[test]
fn request_ids_stay_reserved_while_queued_or_being_written() {
    let q = OutQueue::new(8, 1024);
    q.push(b"a".to_vec(), Some("r1".into())).unwrap();
    q.push(b"b".to_vec(), None).unwrap();
    assert!(q.holds_request_id("r1"));
    assert!(!q.holds_request_id("r2"));
    assert_eq!(q.pop(NOW), Pop::Frame(b"a".to_vec()));
    assert!(q.holds_request_id("r1"), "popped but not yet written");
    q.finish_write();
    assert!(
        !q.holds_request_id("r1"),
        "released once delivered to stdout"
    );
}

#[test]
fn close_rejects_pushes_but_drains_what_is_queued() {
    let q = OutQueue::new(8, 1024);
    q.push(b"last".to_vec(), None).unwrap();
    q.close();
    assert_eq!(q.push(b"late".to_vec(), None), Err(Overflow));
    assert_eq!(q.pop(NOW), Pop::Frame(b"last".to_vec()));
    q.finish_write();
    assert_eq!(q.pop(NOW), Pop::Closed);
}

#[test]
fn wait_drained_observes_the_writer_and_respects_its_deadline() {
    let q = OutQueue::new(8, 1024);
    assert!(
        q.wait_drained(Instant::now() + Duration::from_millis(10)),
        "empty queue is drained"
    );
    q.push(b"x".to_vec(), None).unwrap();
    let start = Instant::now();
    assert!(
        !q.wait_drained(start + Duration::from_millis(60)),
        "nobody is writing"
    );
    assert!(start.elapsed() >= Duration::from_millis(50));
    let writer = {
        let q = q.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(30));
            assert!(matches!(q.pop(Duration::from_secs(1)), Pop::Frame(_)));
            q.finish_write();
        })
    };
    assert!(q.wait_drained(Instant::now() + Duration::from_secs(5)));
    writer.join().unwrap();
}

#[test]
fn pop_blocks_until_data_arrives() {
    let q = OutQueue::new(8, 1024);
    let producer = {
        let q = q.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(40));
            q.push(b"hi".to_vec(), None).unwrap();
        })
    };
    assert_eq!(q.pop(Duration::from_secs(5)), Pop::Frame(b"hi".to_vec()));
    producer.join().unwrap();
}
