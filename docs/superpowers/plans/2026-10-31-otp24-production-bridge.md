# OTP-24 Production BLE Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and freshly verify an on-demand plain-Rust Native Messaging host for independent opaque BLE transport to Windows/macOS-connected phones.

**Architecture:** A separate production crate selectively ports reviewed OTP-23 modules. One deterministic coordinator owns protocol state and event ordering; bounded stdio/backend ingress and independent per-phone workers isolate failures. External synthetic drivers establish production-binary evidence.

**Tech Stack:** Rust 1.98.0, exact retained serde/serde_json/WinRT/bluest/Tokio pins from feasibility, Gradle, existing Node protocol checks, Android GATT revision 2.

**Spec:** `docs/superpowers/specs/2026-10-31-otp24-production-bridge-design.md` (approved); read with protocol sections 9–10 and issue #87.

## Global Constraints

- Coding model: `ollama-cloud/glm-5.3-flash`; review model: `kimi-code-plan-global/k3.1-pro`. Use explicit model selection through `opencode run`; default task-role routing does not satisfy the user's request.
- Do not commit, push, create PRs, or modify project status without user authorization. Task checkpoints below mean inspect changes and obtain review, not automatic commits.
- Separate crate `native-bridge/host/`, package `veles-ble-host`, binary `veles-native-host`; no production dependency on feasibility, Tauri, Node, frontend assets or crypto core.
- JSON frame 1–65,536 bytes; decoded envelope ceiling 5,120 bytes; GATT records 1–4,096 bytes. Reject 4,097–5,120-byte sends with correlated `oversize_record` before fragment creation/write; zero rejects.
- Outstanding requests ≤64; open scans ≤2; connections including pending/closing ownership ≤32; request timeout 30 s, scans exempt; scan cap 300 s.
- Discovery ≤64 per rolling second per scan, ≤1,024 per scan; cache ≤1,024, FIFO eviction excludes connected/pending targets.
- Output ≤128 messages / 256 KiB including writer-held frame; request IDs remain reserved through complete framed delivery. Overflow exits 4 and never drop-and-continues.
- Send storage 16 records / 16 KiB per link, 128 / 128 KiB global, including in-flight records; incomplete inbound state 2 / 8 KiB per peer, 16 / 64 KiB global; inactivity 5 s.
- Input frames ≤64 / 256 KiB; backend data ≤1,536 / 512 KiB global and ≤48 / 16 KiB per link (user-approved 2026-11-01; ingress staging only, distinct from the frozen 2 / 8 KiB and 16 / 64 KiB reassembly caps), fragment ≤512 bytes. Bounded control slots cannot be displaced by data; link data overflow fails that link.
- Terminal input to process exit ≤5 s; stdout drain ≤2 s within that same deadline. Cleanup exhaustion exits 1, not successful shutdown.
- Revision-2 fragment budget `min(ATT_MTU - 3, 512) - 4`; ATT MTU 185–517. macOS write capacity below 512 is undetermined, refused; never infer MTU by adding three.
- No payload/trust persistence, payload logging, product UI, listener, network connection, autostart or transport replay. `sent` is successful local native write submission, not radio/peer acknowledgement.
- New production binary tested in the existing approved macOS signed test bundle; final production signing identity/TCC migration remains outside OTP-24. Preserve signing identities and installed Android data.
- Products/caches/reports under `build/`; root clean guard covers them. Fresh physical evidence, SBOM, checksums and explicit adjudication are mandatory; missing hardware/signing/permission evidence is blocked, not waived.

## Review Focus

1. A request ID reused while its completion is in the writer must terminate silently rather than emit duplicate terminals (Tasks 3–5).
2. A phone that floods notifications or stalls a write must not starve terminal signals or the other phone (Tasks 3, 4, 6, 7).
3. Partial-write cancellation followed by immediate reconnect must not permit an old worker to transmit onto a replacement connection (Tasks 4, 6, 7).
4. Capacity changes during queued/in-flight sends must not change fragment sizing mid-record (Tasks 4, 6, 7).
5. Chrome pipe closure/frozen stdout with active links must release resources and exit within the global deadline (Tasks 5, 10).

## File Structure and Shared Interfaces

Production files below live in `native-bridge/host/`:

- `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `.cargo/config.toml`: standalone workspace, pins, target directory directed to root `build/native-bridge/host/target`.
- `src/{lib,limits,buffer,framing,strict_json,base64,protocol}.rs`: portable exports, bounded owned buffers, framing and strict envelope contract.
- `src/{fragment,outqueue,ingress}.rs`: GATT framing, writer-aware output and byte-aware ingress.
- `src/{backend,core}.rs`: nonblocking adapter seam and deterministic lifecycle coordinator. Retain coherent core port before splitting it further.
- `src/{runtime,host,diagnostics}.rs`, `src/bin/veles-native-host.rs`: stdio wiring, entry validation, safe bounded diagnostics and platform selection.
- `src/{winrt,winrt_map,macos,macos_map,closejoin}.rs`: selectively adapted reviewed OS modules.
- `tests/{protocol,fragmentation,queues,core,process,winrt_map,macos_map,properties}.rs`, `tests/support/{mod,fake_backend}.rs`: fake-clock, deterministic adapter and external-process tests. No simulator shipping flag.
- `examples/host_test_driver.rs`: verification-only simulated process driver; explicitly selected by tests, never a production fallback.
- `fuzz/`: separate verification-only Cargo workspace with parser and state-sequence fuzz targets.
- `scripts/{physical-matrix,validate-evidence}.py`, `scripts/macos-test-bundle.sh`, `acceptance/{cases,matrix}.json`, `README.md`: production-targeted external acceptance tools and evidence index.

Use existing feasibility protocol/fragment type names unless changed below. Never re-export/import feasibility code into the production crate. Exact shared seams:

```rust
pub type Token = u64;
pub struct Payload(Vec<u8>); // no Debug exposing contents; Drop erases owned storage
impl Payload { pub fn new(bytes: Vec<u8>) -> Self; pub fn as_slice(&self) -> &[u8]; pub fn len(&self) -> usize; }
pub fn parse_request(raw: &[u8], phase: Phase) -> Result<Request, ParseFailure>;
pub fn read_frame<R: std::io::Read>(reader: &mut R) -> Result<Option<Payload>, FrameError>;
pub fn write_frame<W: std::io::Write>(writer: &mut W, payload: &[u8]) -> Result<(), FrameError>;
pub fn fragment(record: &[u8], budget: LinkBudget, transaction_id: u8) -> Result<Vec<Payload>, FragmentError>;
```

`Request`, `RequestBody`, `Phase`, `ParseFailure`, `ErrorCode`, `Event`, `LinkCapacity`, `LinkBudget`, `Reassembler` retain the feasibility conceptual API; replace payload `Vec<u8>` fields with `Payload`, and remove payload-bearing `Debug` derives. Define all public types where first used, not test-only placeholders.

The backend retains `Failure`, `Discovered`, `Target`, commands and events from `feasibility/src/backend.rs`, with these deliberate changes:

```rust
pub trait Backend: Send {
    fn start_scan(&mut self, scan: Token, service_uuid: &str);
    fn stop_scan(&mut self, scan: Token);
    fn connect(&mut self, conn: Token, target: &Target);
    fn write_fragments(&mut self, conn: Token, send: Token, frames: Vec<Payload>);
    fn disconnect(&mut self, conn: Token);
    fn shutdown(&mut self);
}
// Every method returns immediately. Every accepted command has reserved bounded
// control-event capacity; inability to submit produces a typed failure event.
// Additional BackendEvent variants:
// LinkReleased { conn: Token }, ScanReleased { scan: Token }, ShutdownComplete,
// CapacityChanged { conn: Token, capacity: LinkCapacity }.
// Fragment bytes: Payload. LinkReleased means OS close completion, not just cancellation.
// ScanReleased is the scan counterpart: the core keeps a scan counted (pending/active/closing)
// against the 2-scan ceiling until it, and a failed scan cleanup never emits it.
```

`BackendEvents` is a cloneable nonblocking ingress producer. `Core<B: Backend>` retains `new(backend, out, config)`, `handle(input, now) -> Option<i32>`, `next_deadline() -> Option<Instant>`. Add `Input::LinkDataOverflow(Token)` and terminal/control variants needed by the runtime. Use explicit `Instant` throughout; closing resources remain capacity-accounted until released. Later tasks consume these signatures unchanged.

## Task 1: Strict Production Envelope and Record-Size Contract

**Files:** Create crate metadata and `src/{lib,limits,buffer,framing,strict_json,base64,protocol}.rs`, `tests/protocol.rs`. Modify `docs/protocol/veles-v1.md`, `protocol/v1/limits.json` descriptions only for size distinction, and `native-bridge/test/protocol-fixtures.test.ts`.

**Interfaces:** Produces `Payload`, parser/framing signatures above and the ported request/event types and serialization API. Use exact dependency pins from `native-bridge/feasibility/Cargo.toml`, no Tauri features. Existing protocol fixtures remain authoritative.

- [ ] Write `protocol.rs` integration tests for duplicate/nested keys, UTF-8 byte limits, null/unknown fields, integer ranges, canonical base64, version/hello negotiation, each request shape and each serialized event shape. Use existing fixture vectors rather than inventing a second contract.
- [ ] Add explicit boundary assertions: parser accepts 4,096 and 5,120 decoded bytes, rejects 0 and 5,121; empty/65,537-byte frames reject before allocation; truncated frame is distinct from clean EOF. Add the shared TypeScript fixture assertions for these shapes.
- [ ] Run `cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test protocol` after minimal metadata/test setup; confirm failure points to missing contract implementation.
- [ ] Selectively port parsing/framing and add `Payload` erase-on-drop ownership. Ensure raw JSON and decoded records do not acquire payload-printing Debug output. Correct protocol prose to distinguish envelope admission from the 4,096-byte GATT send limit and local native submission semantics of `sent`.
- [ ] Rerun the Rust target and `./gradlew protocolCheck`; both pass, including Kotlin/Rust/TypeScript consumers. Inspect the lockfile for unintended runtime dependencies.
- [ ] Checkpoint: review only Task 1 changes with Kimi; resolve findings before dependent coding.

## Task 2: Opaque GATT Fragmentation and Owned-Buffer Budgets

**Files:** Create `src/fragment.rs`, `tests/fragmentation.rs`. Read Android `GattTransport.kt`, `SharingGattServer.kt` and existing fragment fixtures; modify shared fixtures only when a missing production case needs a new vector.

**Interfaces:** Consumes `Payload`; produces `LinkCapacity`, `LinkBudget`, `fragment`, `Reassembler`, `GlobalBudget`, `PushOutcome` using reviewed revision-2 behavior.

- [ ] Write `revision2_boundaries_round_trip`: sizes 1, 177, 178, 179, 508, 509, 1,016, 1,017, 4,095, 4,096 at MTUs 185, 247, 514, 515, 517; assert byte equality, frames ≤`min(mtu-3,512)`, exact nonfinal sizes and no empty final trailer.
- [ ] Write rejection tests for 0/4,097/5,120 bytes, transaction 0, repeated first fragment, malformed total/index/final length, out-of-order fragments, timeout at 5 s, two conflicts in 60 s, peer/global reservation exhaustion and cleanup. Assert released budget counters return to zero on completion/drop.
- [ ] Run `cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test fragmentation`; confirm failure before porting the fragmenter.
- [ ] Port the reviewed budget/reassembly algorithm with owned buffers; preserve macOS undetermined capacity behavior (<512), MTU refusal outside 185–517 and section 9 collision/discard policy. Account allocated capacity, not just used lengths.
- [ ] Rerun fragmentation tests and relevant Android unit tests with `./gradlew testDebugUnitTest --tests '*GattTransportTest' --tests '*GattTransportBudgetTest'`; confirm real test discovery and passing results.
- [ ] Checkpoint: Kimi review of wire equivalence, erasure paths and budget-release behavior.

## Task 3: Bounded Ingress and Writer-Aware Output

**Files:** Create `src/{ingress,outqueue}.rs`, `tests/queues.rs`.

**Interfaces:** Port `OutQueue::{new,push,pop,finish_write,holds_request_id,close,wait_drained}` and `Pop`, replacing payload ownership with `Payload`. Produce nonblocking `BackendEvents` and runtime ingress receivers; `BackendEvents::emit(BackendEvent)` routes reserved controls separately from data. Return overflow ownership to a dropping/erasing path.

- [ ] Write `writer_held_frame_keeps_bytes_and_request_id_reserved`: pop one frame, fill remaining 127 slots, assert another push overflows, ID remains held until `finish_write`. Independently saturate 256 KiB before message count.
- [ ] Write ingress tests saturating 64 frames/256 KiB, per-link 48 events/16 KiB and global 1,536/512 KiB (all 32 links at cap). Assert noisy-link overflow emits one retained control signal, survivor callbacks and EOF/shutdown remain observable, and advertisements coalesce without unbounded sets.
- [ ] Run `cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test queues`; confirm failing behaviors.
- [ ] Implement byte-aware ingress, bounded control reservations and fair per-link consumption; backpressure stdin without unbounded local staging. Port output accounting already proven in feasibility rather than losing its writer-held protection. Include at most one explicitly budgeted reader/writer-local frame.
- [ ] Rerun queues tests; use controlled producers/consumers, not sleep-based scheduling assumptions.
- [ ] Checkpoint: Kimi review of all retained memory and terminal-signal saturation.

## Task 4: Deterministic Request, Connection and Cancellation Core

**Files:** Create `src/{backend,core}.rs`, `tests/core.rs`, `tests/support/{mod,fake_backend}.rs`.

**Interfaces:** Consumes Tasks 1–3; produces shared `Backend`, `BackendEvent`, `Core`, `Config`, `Input` interfaces above. Fake backend records submissions, injects completions and advances a supplied clock without BLE.

- [ ] Write exact-order traces for every request success/error; hello-only pre-ready, silent malformed input, duplicate live ID, 65th request, 3rd scan, 33rd connection, 300 s scan expiry, 30 s request timeout, stale handle/callback, pending duplicate-target connect and cache eviction.
- [ ] Write `oversize_send_never_calls_backend`: 4,097 and 5,120 yield one correlated fatal `oversize_record`; live handle remains usable and fake backend records zero fragment submissions. Assert full send storage gives retryable `cap_exceeded` without cancelling earlier sends.
- [ ] Write `cancel_partial_send_fences_reconnect` and `capacity_change_never_refragments`: errors precede disconnected; closing ownership stays capacity-accounted; same target cannot reconnect until `LinkReleased`; late write completions cannot emit `sent`; equivalent clamped capacity is harmless.
- [ ] Write two-phone flood/stall/timeout tests: survivor send/message progress continues; per-link overflow closes only that link; shutdown errors all pending requests, then lifecycle events, then final completion only after `ShutdownComplete`.
- [ ] Run `cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test core`; confirm missing behavior, then port reviewed core incrementally with nonblocking teardown and bounded bookkeeping. Add documented ingress ceiling entries to `limits.json` and constant-conformance checks.
- [ ] Rerun core/protocol/queue/fragmentation tests; inspect every request terminal and resource accounting path, including counter exhaustion (fail closed before token/handle reuse).
- [ ] Checkpoint: fresh Kimi review against protocol sections 9–10 and approved lifecycle semantics.

## Task 5: Headless Process Runtime and Deadline Enforcement

**Files:** Create `src/{runtime,host,diagnostics}.rs`, `src/bin/veles-native-host.rs`, `examples/host_test_driver.rs`, `tests/process.rs`, `README.md` initial usage.

**Interfaces:** Produces `runtime::Io { stdin: Box<dyn Read + Send>, stdout: Box<dyn Write + Send> }`, `runtime::run<B,F>(make_backend: F, io: Io, config: Config) -> i32` where `F: FnOnce(BackendEvents)->B`, and `host::run_process(args: &[String])->i32`. Production dispatch is platform-gated; test driver is explicitly separate.

- [ ] Write process tests for clean EOF vs truncated header/payload, malformed/oversize frame, no-overlap negotiation exit 3, wrong origin/arguments, broken stdout, explicit shutdown, writer-blocked duplicate request ID, output overflow exit 4 and no stdout diagnostic contamination.
- [ ] Write `stuck_cleanup_exits_with_failure_within_five_seconds`: simulated adapter never reports release; assert exit 1, no `shutdown_complete`. Test successful cleanup plus blocked writer uses ≤2 s drain inside ≤5 s total. Measure child exit independently with a parent watchdog.
- [ ] Run `cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test process`; confirm failures before wiring runtime.
- [ ] Implement raw handle ownership, one writer, control-priority ingress, nonblocking shutdown state and final process-exit deadline. Bounded stderr diagnostics accept only typed status/counts; remove trace-file environment sinks. Production arguments allow Chrome origin and documented platform launcher arguments only, no simulator flags.
- [ ] Bind origin validation to an explicit build-time allowlist matching the extension used for that artifact; no arbitrary runtime origin override. `native-bridge/src/manifest.mjs` currently carries a placeholder ID, so do not call it an established published production origin. External acceptance tooling derives the exact fixture origin, builds the same production source with that recorded allowlist, and records the build configuration/hash. Test wrong-origin rejection. Final published origin/registration policy remains OTP-25.
- [ ] Rerun process tests and all portable Rust checks. Assert Linux production entry refuses unsupported operation without pretending to be Windows; portable tests run through the explicit test driver.
- [ ] Checkpoint: Kimi review of shutdown ordering, pipe ownership, blocked I/O and diagnostics.

## Task 6: Production WinRT Adapter

**Files:** Create `src/{winrt,winrt_map,closejoin}.rs`, `tests/winrt_map.rs`; extend fake-core tests for adapter event traces. Source reference: reviewed feasibility files of the same names.

**Interfaces:** Implements shared `Backend` as `WinrtBackend::new(events: BackendEvents)`. `LinkReleased`/`ShutdownComplete` report observed cleanup; internal OS resources stay owned until completion. Reuse reviewed typed error mapping and setup order.

- [ ] Port classification fixtures for radio-off/retryable, no-adapter/fatal, controlled access-denial/fatal and restoration; add fake closure tests for a slow link not blocking another and stale callback races.
- [ ] On Windows, run adapter test target before implementation to capture failures; Linux mapping tests alone do not establish native adapter compilation.
- [ ] Adapt reviewed watcher/GATT/session/CCCD/readiness/write operations to bounded ingress and per-link workers. Track pending operations, unsubscribe callbacks and cancel writes before closure; never synchronously join all links serially on the core thread.
- [ ] Freeze `LinkBudget`; observe `MaxPduSize` changes and report `CapacityChanged`. Check every native write result before whole-batch `WriteDone`. Fence target reuse until OS resource release.
- [ ] Run Windows `cargo fmt --all -- --check`, `cargo clippy --all-targets --locked -- -D warnings`, `cargo test --locked`; exercise scan/connect/transfer smoke against production binary before Task 10 evidence. Build locally/cross-build here when supported; use the PC only for platform-specific compilation/tooling that cannot run here.
- [ ] Checkpoint: Kimi review of WinRT code and native test output, clearly distinguishing fixtures from physical verdicts.

## Task 7: Production CoreBluetooth Adapter and Signed Test Vehicle

**Files:** Create `src/{macos,macos_map}.rs`, `tests/macos_map.rs`, `scripts/macos-test-bundle.sh`; extend `closejoin.rs` only where platform closure needs it.

**Interfaces:** Implements shared `Backend` as `MacosBackend::new(events: BackendEvents)`. Test bundler embeds the newly built `veles-native-host` in the existing approved PoC identity; it never substitutes production credentials or changes TCC state.

- [ ] Write mapping tests for denied/unavailable/radio states, write lengths 0/181/182/511 (undetermined) and 512+ (clamped), cancellation with OS work in flight and capacity change before a later batch.
- [ ] On macOS, run those tests before porting. Then adapt reviewed bluest/CoreBluetooth worker code with bounded per-link commands/data, notification ownership and native write readiness; `WriteDone` only after successful local submission of every fragment.
- [ ] Implement asynchronous cancellation/release using shared shutdown deadline; partial writes always invalidate the handle. Track closing device identity through actual release, preventing old tasks from racing reconnect.
- [ ] Adapt the existing approved `.app` bundling metadata (Bluetooth usage description, LSUIElement, same bundle ID/team). Record source executable hash and post-signing executable identity separately. A missing approved signing identity blocks the physical step; no ad-hoc fallback.
- [ ] Run macOS formatting/Clippy/all tests and bundle strict-signature verification. Request user action only if the OS requires permission approval; do not reset TCC. Preserve evidence labels that final production identity is unvalidated.
- [ ] Checkpoint: Kimi review of adapter, signing boundary and native test output.

## Task 8: Build, CI, SBOM and Clean Integration

**Files:** Modify `build.gradle.kts`, `.github/workflows/ci.yml`, `verify/{generate-sboms,verify-supply-chain}.sh`, `rust/scripts/assert-clean.sh`, `docs/reproducible-builds.md`, `CLAUDE.md`; create `.github/workflows/build-native-host.yml`. Inspect additional SBOM/license consumers before changing their input list.

**Interfaces:** Adds approved `bridgeHostToolchainCheck`, `bridgeHostFormat`, `bridgeHostLint`, `bridgeHostTest`, `bridgeHostBuild` tasks. Host build emits platform executable + SHA256SUMS under `build/native-bridge/host/`; distinct CI artifacts are named `native-host-<platform>` and cannot be mistaken for existing installer artifacts.

- [ ] Add `native-bridge/host/scripts/test_build_contract.py` (Python standard-library unittest) to check the dedicated task graph/workflow invokes locked Cargo without Tauri/Node, outputs stay under build, SBOM references the production lockfile and supported OS jobs build the actual adapters. Negative check rejects missing production artifact rather than silently using scaffold output. Run with `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'`.
- [ ] Run checks against existing task graph to confirm the production integration is absent, then implement dedicated Gradle tasks and reusable host verification/build workflow (Linux portable, windows-2025, macos-26 with repository Xcode pin). Reuse existing SHA-pinned setup actions.
- [ ] Wire CI host checks and SBOM/license/supply-chain consumers. Preserve existing scaffold packaging ownership; docs and artifact names explicitly distinguish scaffold from production host. No installer registration migration.
- [ ] Run `./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest` and supported-platform `bridgeHostBuild`; rerun affected npm orchestration tests and supply-chain policy with documented pinned prerequisites.
- [ ] Run `./gradlew clean` followed by `rust/scripts/assert-clean.sh`; rebuild the host and assert products are fresh. Check no `native-bridge/host/target` or generated SBOM remains in source tree.
- [ ] Checkpoint: Kimi review of CI, dependency coverage, artifact provenance and clean behavior.

## Task 9: Property/Fuzz and Adversarial Regression Evidence

**Files:** Create `tests/properties.rs`, `fuzz/{Cargo.toml,Cargo.lock,fuzz_targets/strict_envelope.rs,fuzz_targets/fragment_stream.rs,fuzz_targets/core_sequence.rs}`; update `README.md` with exact commands/seeds; reports under `build/native-bridge/host/verification/`.

**Interfaces:** Drives public parser/fragment/core seams, not private OS implementations. Prefer existing dependency pins/frameworks; verification-only dependencies cannot enter the shipping binary graph.

- [ ] Write deterministic seeded properties: arbitrary payloads length 1–4,096 round-trip; bounded malformed input never panics/overallocates; state sequences never cross handles, reuse live IDs or emit two terminals. Include seeds for all five Review Focus cases.
- [ ] Run properties before adding missing production safeguards; preserve minimized regressions for each discovered failure rather than fixing only the corpus.
- [ ] Add separate fuzz targets for raw parser frames, hostile fragments with explicit clock advancement and request/backend/cancel sequences. Limit corpus file sizes to protocol/ingress ceilings and assert budget conservation.
- [ ] Run `cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test properties`; run each fuzz target for a recorded 60 s smoke budget with a pinned documented fuzz toolchain and explicit seed. Save tool/source identity, commands, budgets, exit status and findings; do not call bounded fuzzing proof of absence.
- [ ] Checkpoint: Kimi review of properties, actual reports and release dependency separation.

## Task 10: Fresh Production Physical/Lifecycle Acceptance

**Files:** Create `scripts/{physical-matrix,validate-evidence}.py`, `acceptance/{cases,matrix}.json`; adapt external feasibility Chrome/inspection drivers by reference, not by linking the harness. Evidence under `native-bridge/host/evidence/<run>/` only after review for synthetic-data hygiene. Modify Android synthetic fixture/tests only for demonstrated evidence gaps.

**Interfaces:** `physical-matrix.py --case <id> --host <absolute-executable> --output <dir>` validates executable/source identities before execution; `validate-evidence.py <run-dir>` rejects incomplete identity/checksum/capture/adjudication records. Case IDs enumerate platform, two-phone source bindings and action, not implicit current device state.

- [ ] Create `scripts/test_evidence.py` using Python standard-library unittest. Validator fixture tests assert missing hashes, evaluator revision, phone binding, Chrome provenance, OS context, return codes or adjudication cannot yield PASS. Known failed setups remain indexed. Run `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_evidence.py'` before and after implementing the validator, observing FAIL then PASS.
- [ ] Implement the external production-binary driver and adapt existing exact-origin disposable-Chrome manifests. Build Android fixtures here; compare actual installed/new APK certificates before replacement. Stop if the approved key is missing.
- [ ] Before PC device work call `homepc_ensure`; enumerate physical phones and hold a lease for long runs. Before Mac work call `homepc_status(host=mac)`; asleep is blocked, not a retry loop. Use PC/Mac-local tooling only for required native inspection/Chrome/Bluetooth operations, never personal browser profiles.
- [ ] Run each platform at ready, scanning, two-phone transfer and teardown; record no-window/no-endpoint and filesystem/registry/network observations. Record only the narrow Windows never-visible Chrome wrapper exception. Test byte-exact minimum/chunk boundaries/4,096-byte transfers and 0/4,097/5,120 rejection; connector never sees fragments.
- [ ] With active scans and two byte-proven links run literal `port.disconnect()`, tab close, Chrome-owned pipe closure and explicit shutdown as separate cases. Run clean EOF and truncated-frame EOF as separate direct-launch diagnostics. Observe last JSON, stdout close, process/wrapper exit, OS link release, pending cancellation and survivor progress for per-phone failures. **Amended 2026-11-02 (owner option 2):** for the three abrupt-EOF triggers the individual pending request outcome is recorded as unknown/not observable (no host cancellation required or claimed; synthesized `port_closed` is local only); explicit shutdown keeps the correlated host error before `shutdown_complete`, per-phone failure keeps `send_failed`/`link_lost`.
- [ ] Repeat deliberate radio-off, no-adapter and controlled synthetic-service denial, then restore and submit fresh requests. Record native codes, safe classifications and source/phone timestamps; do not reinterpret historical incidental AccessDenied failures.
- [ ] Validate run catalogs and request a Kimi physical-evidence review. Acceptance matrix distinguishes PASS/FAIL/BLOCKED; final production identity/TCC remains explicitly deferred. Always end device sessions via `homepc_session_end` for each used host, even after failure.
- [ ] Run final touched-component checks, `./gradlew spotlessCheck detekt lintDebug`, the raw `android.util.Log` guard copied verbatim from `.github/workflows/ci.yml`, and `./gradlew protocolCheck`. Record blockers rather than declaring OTP-24 complete if any required physical/check evidence is unavailable.
- [ ] Request a fresh Kimi whole-change review against this plan, approved design and issue #87 before reporting readiness. Any commit/PR/project update still requires user authorization.

## Execution Handoff

Implement sequentially through the preserved coding/review model pair. Independent physical Windows/macOS runs may overlap only after shared code is stable and device ownership is explicit. Each coding delegation includes its task, shared interfaces, approved spec, required checks and instruction not to spawn further agents. Inspect changed files after each task, obtain fresh review, resolve findings and only then start dependent work.

CLI invocation pattern (explicit model overrides; no reliance on task role defaults):

```text
opencode run --model ollama-cloud/glm-5.3-flash --format json <self-contained coding task>
opencode run --model kimi-code-plan-global/k3.1-pro --format json <bounded review task>
```

Use fresh sessions per task/review, retain returned session IDs for fixes, and use bounded prompts to prevent a reviewer from launching its own workflow or exploring archived evidence indiscriminately. If a run is interrupted, inspect work/results before resuming; never assume completion from a timeout.

### Self-review record

- Spec coverage: contract and size correction Task 1; transport Task 2; bounds Task 3; correlation/concurrency Task 4; runtime Task 5; native adapters Tasks 6–7; build/SBOM/clean Task 8; fuzz Task 9; fresh evidence and final gates Task 10.
- Shared types/signatures are declared once above and consumed by name; payload ownership and asynchronous cleanup events are explicit.
- Every Review Focus condition has named tests in its owning tasks; no hardware verdict can be substituted with simulation.
- No implementation, dependency installation, device work or commit has occurred as part of writing this plan.
