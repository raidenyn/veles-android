//! Test-only allocation probe.
//!
//! Installs a global allocator that records, per thread, the largest single
//! allocation (or reallocation target) requested while a closure runs. It exists
//! so a regression test can prove that an untrusted-length read is rejected
//! *before* the oversized buffer is allocated: the observable is peak request
//! size, not the eventual return value (an unbounded `read_to_end` also rejects
//! an oversized document - after buffering all of it).
//!
//! Included with `#[path]` from the integration tests and the library's unit
//! tests; each test binary gets its own allocator instance.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

thread_local! {
    static ARMED: Cell<bool> = const { Cell::new(false) };
    static PEAK: Cell<usize> = const { Cell::new(0) };
}

fn note(size: usize) {
    // `try_with`: the allocator also runs during thread teardown.
    let _ = ARMED.try_with(|armed| {
        if armed.get() {
            let _ = PEAK.try_with(|peak| {
                if size > peak.get() {
                    peak.set(size);
                }
            });
        }
    });
}

pub struct Probe;

// SAFETY: every method forwards to the system allocator unchanged; the probe
// only records the requested size in thread-local, allocation-free cells.
unsafe impl GlobalAlloc for Probe {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        note(layout.size());
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        note(layout.size());
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        note(new_size);
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

#[global_allocator]
static GLOBAL: Probe = Probe;

/// Run `work` and return its value with the largest allocation request this
/// thread made meanwhile.
pub fn largest_allocation<R>(work: impl FnOnce() -> R) -> (R, usize) {
    PEAK.with(|peak| peak.set(0));
    ARMED.with(|armed| armed.set(true));
    let value = work();
    ARMED.with(|armed| armed.set(false));
    let peak = PEAK.with(Cell::get);
    (value, peak)
}
