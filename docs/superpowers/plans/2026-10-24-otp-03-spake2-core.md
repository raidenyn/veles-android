# OTP-03 SPAKE2 Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the frozen first-pairing profile once in Rust, exercised through native, JNI and local WASM.

**Architecture:** RustCrypto P-256, SHA-256, HKDF and HMAC implement RFC 9382 directly (a crate named SPAKE2 does not establish profile compatibility). Rust owns non-cloneable, zeroizing ephemeral state. Both runtime wrappers call the same bounded command dispatcher, which keeps secrets behind process-local opaque handles and enforces role/order/deadline/one-shot consumption. Deterministic scalars and non-Veles identities exist only in unit tests.

**Tech Stack:** Existing Rust 1.98 workspace, exact-pinned RustCrypto dependencies, JNI 0.22, wasm-bindgen, Kotlin instrumentation, Node WASM tests.

**Spec:** `docs/rfcs/2026-08-21-otp-external-devices.md` OTP-03 and `docs/protocol/veles-v1.md` §3 (normative); autonomous approach authorized by user.

## Global Constraints

- Suite `SPAKE2-P256-SHA256-HKDF-HMAC`; Android A, client B; uncompressed 65-byte points.
- Preserve frozen context, identities, 40-byte code transform, LE transcript lengths and ordered confirmations.
- No identity storage, trust activation, Bluetooth or real OTP records.
- Product callers cannot inject randomness or extract scalars/Ka; enrollment seed becomes available once after peer confirmation.
- Use local WASM only. Keep deterministic hooks test-only. Do not commit without an explicit request.

## Review Focus

- Reject on-curve points whose unmasking yields identity before producing confirmations.
- Invalid commands and replayed/expired handles cannot retain reusable secret state.
- Browser entropy failures terminate attempts; random rejection sampling is bounded.
- Context mismatches and role reflection fail generically without shared-secret release.
- Bounded handles and copied inputs cannot grow process memory without limit.

### Task 1: RFC core

**Files:** `rust/veles-crypto/src/{lib,spake2}.rs`, `rust/veles-crypto/Cargo.toml`, `rust/Cargo.lock`.

**Interfaces:** `Pairing::start(role, code, context)`, `exchange(peer_point)`, `confirm(peer_mac)`, `finish()`; all return stable errors; state consumes itself on failure. Test-only deterministic constructor supplies scalars and RFC identities.

- [x] Add failing tests consuming RFC anchors and Veles crypto fixtures; compare pA/pB and confirmation bytes to committed truth.
- [x] Implement validated context/code reduction, rejection-sampled scalars, RFC points/transcript/KDF/MAC, zeroizing state and identity abort.
- [x] Exercise wrong code, context, reflection, malformed points, zero scalar/RNG failure, invalid ordering and one-shot output.
- [x] Run `cargo test --locked` and denied-warning Clippy.

### Task 2: Runtime boundaries

**Files:** `rust/veles-crypto/src/api.rs`, JNI module, `app/src/main/java/me/nagaev/veles/crypto/VelesCrypto.kt`, existing JNI and WASM tests.

**Interfaces:** binary `pairingCommand(input: ByteArray): ByteArray` / `pairing_command(input: &[u8]) -> Vec<u8>`; versioned commands start/exchange/confirm/finish/close, status byte, 64-bit opaque handle. Rust measures elapsed monotonic time; no caller-supplied deadline.

- [x] Add failing round-trip tests through dispatcher for both roles, lifecycle, caps and malformed commands.
- [x] Implement bounded registry, monotonic expiry, generic cryptographic failure mapping and strict lengths.
- [x] Expose identical dispatcher on JNI/WASM, testing round trips, malformed input and consumed handles in each runtime.
- [x] Run host/WASM/Android lint, tests and APK JNI allow-list check. Existing allow-list checks ABI/library paths, so no symbol-list edit was required; `nm` independently confirmed exactly the two intended product JNI exports.

### Task 3: Evidence and review

**Files:** `docs/protocol/reviews/otp-03-evidence.md`, test harness files as needed.

- [x] Run bounded malformed-input fuzz/property tests and release benchmark baseline.
- [x] Record actual command outcomes, shared-vector coverage, dependency hashes and limitations.
- [x] Review full diff against the normative protocol and fix findings with regression tests.
- [x] Run final appropriate checks; report only verified outcomes. GitHub issue remains open pending PR integration; the user authorized committing, pushing and creating the PR after verification.
