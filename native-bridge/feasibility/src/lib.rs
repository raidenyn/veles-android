//! OTP-23 disposable Native Messaging + BLE feasibility harness.
//!
//! Synthetic bytes only: no Veles cryptography, trust, identity or OTP data. The crate is split
//! into a platform-neutral core (framing, strict envelope parsing, GATT fragmentation, the host
//! state machine) and platform backends behind the [`backend::Backend`] seam.

pub mod backend;
pub mod base64;
pub mod core;
pub mod fragment;
pub mod framing;
pub mod identity;
pub mod outqueue;
pub mod protocol;
pub mod runtime;
pub mod sim;
pub mod strict_json;
pub mod trace;
pub mod unsupported;
#[cfg(windows)]
pub mod winrt;
pub mod winrt_map;

pub mod host;
