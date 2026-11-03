//! Actual (allocator-measured) memory of the approved ingress bounds: 48 events / 16 KiB per
//! link and 1,536 events / 512 KiB global, with all 32 links at their cap at once.
//!
//! This file deliberately contains exactly ONE test and installs a counting global allocator,
//! so the measured deltas are not polluted by concurrently running tests. It measures live heap
//! bytes (allocations minus frees) around the ingress and the 34 callback admission gates
//! (32 links + 2 scans) and asserts the documented bound:
//!
//! - payload bytes <= 512 KiB (the global byte ceiling, exactly what the counters admit);
//! - fixed capacity: the ingress' in-transit lane array plus each gate's lane array;
//! - bookkeeping: per-event queue nodes (bounded by the event ceiling).
//!
//! Nothing here is a hardware claim.

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};

use veles_ble_host::admission::AdmissionGate;
use veles_ble_host::backend::BackendEvent;
use veles_ble_host::buffer::Payload;
use veles_ble_host::ingress::{
    BackendEvents, BackendPop, DATA_BYTES_GLOBAL_MAX, DATA_EVENTS_GLOBAL_MAX,
    DATA_EVENTS_PER_LINK_MAX, MAX_RESERVED_CONNECTIONS, MAX_RESERVED_SCANS,
};

static LIVE: AtomicUsize = AtomicUsize::new(0);

struct Counting;

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        LIVE.fetch_add(layout.size(), Ordering::SeqCst);
        unsafe { System.alloc(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        LIVE.fetch_sub(layout.size(), Ordering::SeqCst);
        unsafe { System.dealloc(ptr, layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        LIVE.fetch_add(new_size, Ordering::SeqCst);
        LIVE.fetch_sub(layout.size(), Ordering::SeqCst);
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

fn live() -> usize {
    LIVE.load(Ordering::SeqCst)
}

/// One link's cap: 48 fragments totalling exactly 16 KiB (16 x 342 B + 32 x 341 B).
fn fragment_len(index: usize) -> usize {
    if index < 16 { 342 } else { 341 }
}

#[test]
fn all_thirty_two_links_at_cap_stay_inside_the_accounted_memory_bound() {
    let base = live();

    // Fixed-capacity structures: the ingress with its in-transit lane array, and one admission
    // gate (with its lane array) per link/scan.
    let (events, receiver) = BackendEvents::new();
    let ingress_fixed = live() - base;
    let before_gates = live();
    let gates: Vec<_> = (0..MAX_RESERVED_CONNECTIONS + MAX_RESERVED_SCANS)
        .map(|_| AdmissionGate::new(events.clone()))
        .collect();
    let gates_fixed = live() - before_gates;

    for conn in 1..=MAX_RESERVED_CONNECTIONS as u64 {
        receiver.reserve_connection(conn).unwrap();
    }
    let before_data = live();
    for conn in 1..=MAX_RESERVED_CONNECTIONS as u64 {
        for index in 0..DATA_EVENTS_PER_LINK_MAX {
            let bytes = Payload::new(vec![0x5A; fragment_len(index)]);
            events.emit(BackendEvent::Fragment { conn, bytes });
        }
    }
    let data_delta = live() - before_data;
    assert_eq!(
        receiver.data_usage(),
        (DATA_EVENTS_GLOBAL_MAX, DATA_BYTES_GLOBAL_MAX),
        "all 32 links sit exactly at their cap"
    );

    // Beyond the cap nothing more is retained: every further fragment is refused (and erased).
    let at_cap = live();
    for conn in 1..=MAX_RESERVED_CONNECTIONS as u64 {
        for _ in 0..10 {
            events.emit(BackendEvent::Fragment {
                conn,
                bytes: Payload::new(vec![0x5A; 300]),
            });
        }
    }
    assert!(
        live() <= at_cap + 64 * 1024,
        "refused fragments must not accumulate: {} -> {}",
        at_cap,
        live()
    );

    let overhead = data_delta - DATA_BYTES_GLOBAL_MAX;
    eprintln!(
        "ingress fixed = {ingress_fixed} B, gates fixed (34) = {gates_fixed} B, \
         32 links at cap = {data_delta} B (payload {DATA_BYTES_GLOBAL_MAX} + bookkeeping {overhead} B)"
    );
    // The documented bound (see the design spec): payload <= 512 KiB, plus bookkeeping of at
    // most 256 B per event node and the fixed lane arrays; total well under 1.5 MiB.
    assert!(
        data_delta >= DATA_BYTES_GLOBAL_MAX,
        "payload must be fully counted"
    );
    assert!(
        overhead <= DATA_EVENTS_GLOBAL_MAX * 256,
        "per-event bookkeeping stays bounded: {overhead} B"
    );
    assert!(
        ingress_fixed + gates_fixed + data_delta <= 3 * 512 * 1024,
        "total ingress memory {} B",
        ingress_fixed + gates_fixed + data_delta
    );

    // Everything is released on drain: no phantom retention.
    let mut drained = 0;
    while let BackendPop::Event(_) = receiver.pop_event(Some(std::time::Duration::ZERO)) {
        drained += 1;
    }
    assert!(drained >= DATA_EVENTS_GLOBAL_MAX);
    assert_eq!(receiver.data_usage(), (0, 0));
    // Payload bytes are gone after the drain; only the (bounded) queue capacity remains, and
    // releasing the reservations returns that too.
    assert!(
        live() <= before_data + overhead + 64 * 1024,
        "payloads released after drain: {} vs {}",
        live(),
        before_data
    );
    for conn in 1..=MAX_RESERVED_CONNECTIONS as u64 {
        receiver.release_connection(conn);
    }
    assert!(
        live() <= before_data + 64 * 1024,
        "queue capacity released with the reservations: {} vs {}",
        live(),
        before_data
    );
    drop(gates);
}
