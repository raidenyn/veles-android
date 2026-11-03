//! OTP-24 production Native Messaging BLE bridge — portable library.
//!
//! Task 1 (2026-10-31-otp24-production-bridge) establishes the strict portable parsing layer:
//! frame bounds, strict JSON intake, canonical base64, opaque payload ownership and the
//! request/event envelope vocabulary of `docs/protocol/veles-v1.md` section 10. Task 2 adds
//! opaque GATT fragmentation and owned-buffer budgets. Task 3 adds the bounded runtime
//! ingress (`ingress.rs`: reader frame staging with priority terminals and backend event
//! staging with reserved controls per accepted token), the writer-aware output queue
//! (`outqueue.rs`) and the backend seam vocabulary (`backend.rs`). Task 4 adds the
//! deterministic coordinator (`core.rs`) that consumes parsed frames and drained backend
//! events through the seams above. Task 5 adds the concurrent headless process runtime
//! (`runtime.rs`: one reader, one writer, the core loop on the calling thread, bounded
//! release observation and stdout drain within a five-second inclusive exit deadline).
//! Task 5b adds the process entry (`host.rs`: the strict Chrome launch contract, the
//! build-time extension-origin allowlist, the platform-gated dispatch, raw stdio ownership
//! and bounded typed stderr diagnostics) together with the production binary and the
//! test-only driver example. Task 6 adds the Windows WinRT adapter (`winrt.rs`, compiled on
//! Windows only) with its portable mappings (`winrt_map.rs`) and release bookkeeping
//! (`closejoin.rs`), the ordered-callback admission gate (`admission.rs`) and the partial-setup
//! cleanup stack (`cleanup.rs`). Task 7 adds the macOS CoreBluetooth adapter (`macos.rs`,
//! compiled on macOS only) with its portable mappings (`macos_map.rs`) and link-lifecycle logic (`macos_link.rs`).

pub mod admission;
pub mod backend;
pub mod base64;
pub mod buffer;
pub mod cleanup;
pub mod closejoin;
pub mod core;
pub mod fragment;
pub mod framing;
pub mod host;
pub mod ingress;
pub mod limits;
#[cfg(target_os = "macos")]
pub mod macos;
pub mod macos_link;
pub mod macos_map;
pub mod outqueue;
pub mod protocol;
pub mod runtime;
pub mod strict_json;
#[cfg(windows)]
pub mod winrt;
pub mod winrt_map;
