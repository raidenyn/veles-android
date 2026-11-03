# OTP-24 Inbound Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Locate missing BLE replies with correlated, bounded, payload-free physical evidence.

**Architecture:** Extend opt-in debug telemetry at actual Android GATT and reassembly boundaries; correlate it against host and fixture evidence before selecting a production fix.

**Tech Stack:** Kotlin/JUnit/Android instrumentation, Rust Windows native host, ADB, Windows physical BLE.

**Spec:** `docs/superpowers/specs/2026-11-02-otp24-inbound-diagnostics-design.md`

## Global Constraints

- Never log payload bytes or raw Bluetooth addresses. Bound event storage and report truncation.
- Debug-only, explicitly enabled instrumentation must not change transport behavior.
- Reuse `config/android_keys/otp23-fixture.keystore`; compare installed and replacement certificates before in-place updates. Build APKs on Linux.
- No retries or pacing changes without identifying the root cause; write a reproducing regression test before a fix.
- No commit, push, or PR without authorization.

## Review Focus

- Malformed, out-of-order and conflicting fragments: assert diagnostic outcomes match the transport decision.
- Missing peer, missing MTU and exhausted allocation: assert distinguishable outcomes without changing acceptance.
- Invalid operations, oversized input and dropped posted work: report the appropriate boundary rather than asserting transport receipt.
- Reply generation returning null or queue refusal: record decisions distinctly and never log payloads.
- Evidence caps or asynchronous shutdown: expose truncation and avoid treating incomplete evidence as loss.

### Task 1: Capture inbound and reply decisions

**Files:** `app/src/main/java/me/nagaev/veles/sharing/GattTransport.kt`, `SharingGattServer.kt`, `SyntheticTransportFixture.kt`; `app/src/androidTest/java/me/nagaev/veles/sharing/SharingInteropPhysicalTest.kt`; corresponding tests under `app/src/test/java/me/nagaev/veles/sharing/`.

- [ ] Write tests demonstrating that successful multi-fragment records, out-of-order fragments, missing partials, invalid frame lengths and duplicate starts yield distinguishable diagnostic outcomes without changing existing receive results.
- [ ] Run the focused tests and confirm they fail for missing diagnostic behavior.
- [ ] Add opt-in bounded telemetry for callback arrival, transport receive outcome, completed record hash, reply produced/null, and queue accepted/rejected. Retain only size, header metadata, hashed identifiers and timestamps; represent any evidence loss explicitly.
- [ ] Serialize the new data into the physical fixture output, including truncation and asynchronous boundary losses.
- [ ] Run focused and full Android JVM tests; build both fixture APKs on Linux and compare their signers with the installed APKs before updating in place.

### Task 2: Controlled physical comparison

**Files:** existing Windows acceptance tooling under `native-bridge/host/`, evidence under the local OTP-24 progress directory.

- [ ] Run matched one-link, two-link idle, and two-link failing trials with the same survivor phone, record count, timing and globally unique synthetic records, starting slower then repeating at 200 ms.
- [ ] Correlate host write outcomes, each phone's fragment/reassembly/reply decisions, notification outcomes and host-received messages. Mark any unobservable or truncated evidence as such.
- [ ] Record observations, limitations and a falsifiable root-cause hypothesis; perform additional targeted observation if no boundary is isolated.

### Task 3: Root-cause fix and verification

**Files:** determined by Task 2 evidence.

- [ ] Write and run a failing regression test reproducing the identified failure before changing production behavior.
- [ ] Implement the minimal root-cause fix and run relevant automated tests.
- [ ] Verify with a native Windows/MSVC build and repeat the controlled physical cases; obtain independent evidence review and document results.
