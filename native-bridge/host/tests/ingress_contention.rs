//! OTP-24 whole-change review finding C1: routine lock contention between ingress producers and
//! the core thread must never fail the host, lose or reorder a fragment/control, nor exceed the
//! bounded budgets. These tests drive the *public* seams from real threads (no timing sleeps in
//! the assertions): several link producers plus a scan producer race one consumer that also
//! takes the shared ingress lock through its admission seams, exactly like the core thread.
//!
//! Flow control mirrors the real system: each producer keeps at most [`WINDOW`] events
//! outstanding per link (a credit returned when the consumer pops the event), so the per-link
//! data/control ceilings can never legitimately overflow — any latch or loss observed here is
//! therefore caused by contention handling alone.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use veles_ble_host::backend::{BackendEvent, Discovered, Token};
use veles_ble_host::buffer::Payload;
use veles_ble_host::fragment::LinkCapacity;
use veles_ble_host::ingress::{BackendEvents, BackendPop, EmitStatus};

const LINKS: usize = 6;
const EVENTS_PER_LINK: u32 = 4_000;
/// Per-link outstanding events (well under the 16-fragment / 32-control ceilings).
const WINDOW: usize = 8;
/// How many emissions took the contended (deferred) path — reported, not asserted: it depends
/// on the scheduler, but proves in the log that the lane was really exercised.
static DEFERRED: AtomicUsize = AtomicUsize::new(0);

fn fragment_event(conn: Token, seq: u32) -> BackendEvent {
    let mut bytes = seq.to_be_bytes().to_vec();
    bytes.resize(64, 0xA5);
    BackendEvent::Fragment {
        conn,
        bytes: Payload::new(bytes),
    }
}

/// A link control that carries the sequence number in its capacity value.
fn control_event(conn: Token, seq: u32) -> BackendEvent {
    BackendEvent::CapacityChanged {
        conn,
        capacity: LinkCapacity::AttMtu(seq as usize),
    }
}

fn seq_of(event: &BackendEvent) -> Option<(Token, u32)> {
    match event {
        BackendEvent::Fragment { conn, bytes } => Some((
            *conn,
            u32::from_be_bytes(bytes.as_slice()[..4].try_into().unwrap()),
        )),
        BackendEvent::CapacityChanged {
            conn,
            capacity: LinkCapacity::AttMtu(seq),
        } => Some((*conn, *seq as u32)),
        _ => None,
    }
}

#[test]
fn multi_producer_contention_loses_nothing_and_never_latches_a_host_failure() {
    let (events, receiver) = BackendEvents::new();
    let scan: Token = 1_000;
    receiver.reserve_scan(scan).unwrap();
    for link in 0..LINKS {
        receiver.reserve_connection(link as Token + 1).unwrap();
    }
    let credits: Arc<Vec<AtomicUsize>> =
        Arc::new((0..LINKS).map(|_| AtomicUsize::new(0)).collect());
    let stop_ads = Arc::new(AtomicBool::new(false));

    // One producer thread per link (each link's callbacks are serialised, as the admission
    // gate guarantees), mixing fragments and controls; they all race the consumer's lock use.
    let mut producers = Vec::new();
    for link in 0..LINKS {
        let events = events.clone();
        let credits = Arc::clone(&credits);
        producers.push(thread::spawn(move || {
            let conn = link as Token + 1;
            for seq in 0..EVENTS_PER_LINK {
                while credits[link].load(Ordering::Acquire) >= WINDOW {
                    thread::yield_now();
                }
                credits[link].fetch_add(1, Ordering::AcqRel);
                let event = if seq % 7 == 3 {
                    control_event(conn, seq)
                } else {
                    fragment_event(conn, seq)
                };
                let status = events.emit(event);
                if status == EmitStatus::Deferred {
                    DEFERRED.fetch_add(1, Ordering::Relaxed);
                }
                assert_ne!(
                    status,
                    EmitStatus::LockBusy,
                    "link {link} seq {seq}: contention must not reject"
                );
                assert!(
                    !matches!(
                        status,
                        EmitStatus::DataOverflow | EmitStatus::ControlOverflow
                    ),
                    "link {link} seq {seq}: {status:?}"
                );
            }
        }));
    }
    // A lossy advertisement producer hammering the same lock.
    let ads = {
        let events = events.clone();
        let stop = Arc::clone(&stop_ads);
        thread::spawn(move || {
            let mut n = 0u32;
            while !stop.load(Ordering::Acquire) {
                events.emit(BackendEvent::Advertisement {
                    scan,
                    device: Discovered {
                        platform_hint: format!("dev-{}", n % 32),
                        label: "x".into(),
                        advertisement: None,
                    },
                });
                n = n.wrapping_add(1);
            }
        })
    };

    // The consumer behaves like the core thread: pops AND hits the admission/latch seams
    // (each of which takes the same ingress lock) between pops.
    let mut next_seq = vec![0u32; LINKS];
    let mut total = 0usize;
    let started = Instant::now();
    while next_seq.iter().any(|&n| n < EVENTS_PER_LINK) {
        assert!(
            started.elapsed() < Duration::from_secs(60),
            "stalled at {next_seq:?}"
        );
        assert!(!receiver.control_overflow(), "control overflow latched");
        assert!(
            !receiver.emit_lock_failure(),
            "routine contention latched a sticky host failure after {total} events"
        );
        let _ = receiver.link_data_usage(1);
        match receiver.pop_event(Some(Duration::from_millis(1))) {
            BackendPop::Event(event) => {
                if let Some((conn, seq)) = seq_of(&event) {
                    let link = conn as usize - 1;
                    // Exact per-link ordering, no gap, no duplicate, no loss.
                    assert_eq!(seq, next_seq[link], "link {link}: out-of-order/lost");
                    next_seq[link] += 1;
                    total += 1;
                    credits[link].fetch_sub(1, Ordering::AcqRel);
                }
            }
            BackendPop::Timeout => {}
        }
    }
    stop_ads.store(true, Ordering::Release);
    for producer in producers {
        producer.join().unwrap();
    }
    ads.join().unwrap();
    eprintln!(
        "contended emissions that took the ordered lane: {}",
        DEFERRED.load(Ordering::Relaxed)
    );
    assert!(!receiver.emit_lock_failure());
    assert!(!receiver.control_overflow());
    // Bounded memory: nothing of the links is left staged.
    assert_eq!(receiver.data_usage().0, 0);
}

/// Review follow-up #1 under real concurrency: producers blast unthrottled (so the per-link
/// budgets genuinely overflow, on both the direct and the deferred path) while a slow
/// consumer frees capacity in between. For every link the delivered fragments must be a
/// gap-free prefix `0..k` followed by at most one `LinkLost`, and nothing of the link after it:
/// never data behind the gap.
#[test]
fn overflowing_links_deliver_a_gap_free_prefix_then_exactly_one_link_lost() {
    const BLAST_LINKS: usize = 12; // each link overflows its own 48-event per-link cap (12 x 48 stays under the global 1,536)
    const BLAST: u32 = 400;
    let (events, receiver) = BackendEvents::new();
    for link in 0..BLAST_LINKS {
        receiver.reserve_connection(link as Token + 1).unwrap();
    }
    let producers: Vec<_> = (0..BLAST_LINKS)
        .map(|link| {
            let events = events.clone();
            thread::spawn(move || {
                for seq in 0..BLAST {
                    events.emit(fragment_event(link as Token + 1, seq));
                    if seq % 16 == 15 {
                        thread::yield_now();
                    }
                }
            })
        })
        .collect();
    let mut prefix = [0u32; BLAST_LINKS];
    let mut lost = [0usize; BLAST_LINKS];
    let mut idle = 0;
    while idle < 200 {
        match receiver.pop_event(Some(Duration::from_millis(1))) {
            BackendPop::Event(BackendEvent::LinkLost { conn }) => lost[conn as usize - 1] += 1,
            BackendPop::Event(event) => {
                idle = 0;
                let (conn, seq) = seq_of(&event).expect("fragments only");
                let link = conn as usize - 1;
                assert_eq!(lost[link], 0, "link {link}: data after LinkLost");
                assert_eq!(seq, prefix[link], "link {link}: data behind a gap");
                prefix[link] += 1;
            }
            BackendPop::Timeout => idle += 1,
        }
    }
    for producer in producers {
        producer.join().unwrap();
    }
    // Drain whatever the producers' tail left.
    while let BackendPop::Event(event) = receiver.pop_event(Some(Duration::from_millis(5))) {
        match event {
            BackendEvent::LinkLost { conn } => lost[conn as usize - 1] += 1,
            other => {
                let (conn, seq) = seq_of(&other).unwrap();
                let link = conn as usize - 1;
                assert_eq!(lost[link], 0, "link {link}: data after LinkLost");
                assert_eq!(seq, prefix[link], "link {link}: data behind a gap");
                prefix[link] += 1;
            }
        }
    }
    for link in 0..BLAST_LINKS {
        assert!(lost[link] <= 1, "link {link}: {} markers", lost[link]);
        assert!(
            prefix[link] == BLAST || lost[link] == 1,
            "link {link}: lost data without a LinkLost ({} of {BLAST})",
            prefix[link]
        );
    }
    assert!(!receiver.emit_lock_failure() && !receiver.control_overflow());
    assert_eq!(receiver.data_usage(), (0, 0));
}
