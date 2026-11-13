# OTP-10 SPAKE2 Enrollment and Trust Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phone (Kotlin) and Chrome connector (TypeScript) enroll once with a six-digit SPAKE2 code, activate trust durably, and recover from interrupted activation, with the real Rust core on every test path.

**Architecture:** Each endpoint owns its pairing and activation state machine and storage and calls the OTP-03 core only for SPAKE2. A `host-jni` cargo feature gives the JVM and the CI loopback a host-built native library; the WASM package is bundled into the extension. Recovery reuses the OTP-09 session classes with new OTP2-CRY-019f keys.

**Tech Stack:** Kotlin/Android (JUnit, Robolectric, MockK), TypeScript (Vitest, Vite, Playwright), Rust `veles-crypto` (cargo, JNI, wasm-bindgen), Gradle, GitHub Actions.

**Spec:** `proposal.md`, `design.md` (D1–D7), `specs/*/spec.md`, `tasks.md` in this directory. Protocol: `docs/protocol/veles-v1.md` (§3, §5, §11); fixtures: `protocol/v1/`.

## Global Constraints

- Window 300 s monotonic; `pairing_id` 128-bit; code six digits, uniform by rejection sampling; code never sent, logged, persisted or in `toString`.
- 5 attempts per `pairing_id`; 5 per rolling 300 s; 1 SPAKE2 op in flight; 1 fresh `pA` per connection, 5 per rolling 60 s; inactivity 30 s.
- New limits (`origin: "builder_choice"`): 20 malformed pairing connections per rolling 60 s; 10 `pairing_request` per rolling 60 s. Any malformed pairing frame gets `preauth_reject` and an immediate disconnect.
- Admission order: framing/`pairing_id` → deadline → `P256.requireValidPoint` → atomic charge (never refunded) → deadline again → core `exchange`.
- Core API (`docs/protocol/otp-03-api.md`): `start(role A=1|B=2, six ASCII code bytes, frozen context)` returns `handle u64` and the 65-byte point; then `exchange`/`confirm`/`finish`/`close(handle)`. The core has no handle count, so the wrappers count open handles. Phone and client never compare codes themselves.
- Pending expiry: phone 86 400 s boot-scoped (OTP2-STA-010c), Chrome connector-scoped (010d).
- Duplicate installation ID after confirmation: protected record `0x0003`, code `0x04` `enrollment_rejected` (OTP2-STA-012).
- `device_type`: phone record `0x00`; client `0x01` desktop or `0x02` watch.
- Reconnect `FrameType` and `RecordPolicy` unchanged; pairing frames 0x01–0x05 use their own enum and emit the same `preauth_reject` (0x0F) bytes.
- Robolectric tests never initialize `VelesCrypto`; real-core tests are plain JUnit; every test closes core handles.
- Android `.so`, WASM package and `Cargo.lock` must not change from the `host-jni` feature; if they do, stop and ask.
- `web-extension/package.json` `test` is an explicit file list: every task that creates a TS test file adds it there. Reuse existing constant-time helpers (`MessageDigest.isEqual`, TS `equalConstantTime` in `session/engine.ts`) and the `() -> Long` clock convention (`SystemClock::elapsedRealtime`).
- Every task leaves the tree green and CI-passing on its own commit.
- Conventional Commits; before any PR run `./gradlew spotlessCheck detekt lintDebug` and the raw `android.util.Log` guard. Never uninstall or clear app data on the fixture phone; reuse the existing signing key.

## Review Focus

- Code containing leading zeros (`000042`) must round-trip through both endpoints.
- A `pB` replayed on a fresh connection must be charged again and fail, never reuse an old execution.
- Cancel racing a verified `cB` must leave no half-written record or open window.
- Service shutdown mid-exchange must close the handle and erase the code.
- Connector tab closing mid-pairing must delete the Chrome `pending` record and close the WASM handle.

---

## Task 1: `host-jni` feature, `rustHostJni`, loading and Android/protocol CI

**Files:** Modify `rust/veles-crypto/Cargo.toml`, `rust/veles-crypto/src/lib.rs:19`, `build.gradle.kts`, `app/build.gradle.kts`, `web-extension/test/session/loopback.test.ts`, `.github/workflows/{check-protocol,check-android,check-rust}.yml`.

**Interfaces:** Produces Gradle task `rustHostJni` (output under `build/rust/host-jni/…`) and env var `VELES_HOST_JNI_DIR`, consumed by the loopback spawn as `-Djava.library.path`.

- [ ] **Step 1:** Record baseline sha256 of the Android `.so` (`./gradlew rustJni`), the WASM package (`./gradlew rustWasm`) and `Cargo.lock`.
- [ ] **Step 2:** Add feature `host-jni = ["dep:jni"]` and `[target.'cfg(not(target_os = "android"))'.dependencies] jni = { version = "=0.22.4", optional = true }`; edit the cfg at `lib.rs:19` in place so line numbers do not shift.
- [ ] **Step 3:** Add `rustHostJni` (locked, explicit remap flags, `CARGO_PROFILE_RELEASE_PANIC=unwind`, separate `CARGO_TARGET_DIR`) and a host clippy run (`cargo clippy -p veles-crypto --features host-jni`); `build/rust` is already covered by `assert-clean.sh`.
- [ ] **Step 4:** Set `java.library.path` and `dependsOn rustHostJni` on `testDebugUnitTest` and `:app:protocolJvmTest`; in `loopback.test.ts` add `-Djava.library.path=${process.env.VELES_HOST_JNI_DIR}` to the child JVM args; `sessionLoopbackCheck` sets the env var and depends on `rustHostJni` and `rustWasm`.
- [ ] **Step 5:** Add the Rust toolchain, cache and `rustHostJni` steps to `check-protocol.yml`, the `check-android.yml` unit-tests job and `check-rust.yml` `jni-integration` in this same commit.
- [ ] **Step 6:** Rebuild and compare hashes; expect all three identical. Also run the wasm32 build, `rustLint`, `generate-sboms.sh` (`--all-features`), `verify-apk-jni.sh` and the `rustPackage` file-set check to prove the host library never reaches products. If anything differs, stop and ask the user and flag the diff for the core's external reviewer.
- [ ] **Step 7:** Run `./gradlew rustLint rustTest rustJniConformance :app:protocolJvmTest`; expect PASS.
- [ ] **Step 8:** Commit `build(rust): add host-jni feature and rustHostJni task`.

## Task 2: Extension WASM bundling and extension CI

**Files:** Create `web-extension/src/pairing/core.ts`, `web-extension/test/pairing/core.test.ts`. Modify `web-extension/vite.config.ts`, `web-extension/src/globals.d.ts`, `web-extension/package.json` (test list), `web-extension/test/bundle.test.ts`, `build.gradle.kts` (`dependsOn rustWasm` for `protocolWebTest` and other tasks that run the TS client), `.github/workflows/{check-web-extension,build-web-extension,check-rust}.yml`, `verify/test/workflow-contracts.test.mjs`, docs.

**Interfaces:** Produces `core.ts`: `initPairingCore(source?: BufferSource | URL): Promise<void>` (tests pass the package bytes, as `rust/veles-crypto/tests/pairing-wasm.mjs` does; the connector passes the bundled URL); `class PairingExecution { static start(code: string, context: Uint8Array): { execution: PairingExecution; point: Uint8Array }; exchange(peerPoint: Uint8Array): Uint8Array; confirm(...): Uint8Array; finish(...): Uint8Array; close(): void }` (role is B; `close` idempotent, called from a `finally`); module-level `openHandleCount(): number`.

- [ ] **Step 1:** Write `core.test.ts`: `loads_wasm_and_round_trips_start_close` (init from bytes, start with code `000042`, `close()` twice without throwing, `openHandleCount() === 0`) and `missing_package_gives_clear_error`.
- [ ] **Step 2:** Run `cd web-extension && npm test -- core`; expect FAIL (no module).
- [ ] **Step 3:** Implement `core.ts` importing `rust-wasm/pkg/veles_crypto.js`; add `declare module '*.wasm?url'` to `src/globals.d.ts`; initialize only in the connector tab; Vite bundles the package.
- [ ] **Step 4:** Update `bundle.test.ts` to match the `.wasm` asset by pattern and assert the service worker bundle never references it; add `test/pairing/core.test.ts` to the `npm test` list.
- [ ] **Step 5:** Add Rust toolchain, cache and `rustWasm` steps to `check-web-extension.yml` and `build-web-extension.yml`, and a `rustWasm` step to `check-rust.yml` `jni-integration` before its `npm ci && npm run build` and `assert-clean` steps; update `workflow-contracts.test.mjs`; run `node --test verify/test/workflow-contracts.test.mjs`, `npm run package`, `npm run test:bundle`, `python3 -I scripts/components.py check`; expect PASS.
- [ ] **Step 6:** Update the SBOM note and docs (`docs/reproducible-builds.md`, `docs/ci.md`, `BUILD.md`, `CLAUDE.md`); run `verify/verify-supply-chain.sh` after its prerequisites.
- [ ] **Step 7:** Commit `build(web-extension): bundle the WASM core and wire CI`.

## Task 3: Shared protocol data and scenario inventory

**Files:** Modify `protocol/v1/limits.json`, `app/src/test/java/me/nagaev/veles/session/ScenarioConformanceTest.kt`, `web-extension/test/session/scenarios.test.ts`; create `protocol/conformance/otp-10-scenarios.json`.

**Interfaces:** Produces limit names `pairing_malformed_connections_rolling_max` (20), `pairing_malformed_rolling_interval_seconds` (60), `pairing_request_rolling_max` (10), `pairing_request_rolling_interval_seconds` (60), each with `unit`, `scope`, `origin: "builder_choice"` and a resolvable `source_anchor`; and `otp-10-scenarios.json` in the OTP-09 format (`id -> "execute" | {"engine-effect": {terminate, erase, deliver}} | "deferred:Task N"`). Traces with no `final_state` (`st-neg-same-connection-pb-after-invalid-point` on the phone, `st-neg-commit-without-verified-prepare` on the client; both `accepted: false`, `no_state_mutation: true`) use the `engine-effect` form, and the enrollment runners assert the effect and the absence of state mutation.

- [ ] **Step 1:** Add the four limits; run `./gradlew protocolCheck` (includes `protocol/tools/check-contract-text.mjs`); expect PASS. The phone matches the values in Task 5.
- [ ] **Step 2:** Create `otp-10-scenarios.json` with every OTP-10 id (enrollment traces for a phone/client machine, recovery ids in `records.json` and `reconnect.json`, the ten former `deferred:OTP-10`) as `deferred:Task N`. Leave the five fixture self-check traces in the OTP-02 runner.
- [ ] **Step 3:** Extend the OTP-09 runners (`ScenarioConformanceTest.kt:45-62`, `scenarios.test.ts` ~1120-1130) for the second inventory: an `otp-10-scenarios.json` entry overrides the OTP-09 `deferred:OTP-10` entry of the same id and no other overlap is allowed; `deferred:Task N` is an accepted classification; coverage is checked per fixture file; the OTP-09 runners execute only `records.json`/`reconnect.json` ids (the `21` count stays the OTP-09-file-only count of ids that run there); `enrollment.json` ids belong to `EnrollmentScenarioTest.kt` and `enrollment-scenarios.test.ts`, with the five fixture self-check traces excluded explicitly; the client-id set asserted by `clientRoleScenariosAreDrivenByTheTypeScriptRunner` reads from the inventory instead of a fixed list.
- [ ] **Step 4:** Run `./gradlew :app:protocolJvmTest testDebugUnitTest --tests "*ScenarioConformanceTest*"` and `cd web-extension && npm test`; expect PASS with all OTP-10 ids deferred.
- [ ] **Step 5:** Commit `feat(protocol): add pairing rate limits and OTP-10 scenario inventory`.

## Task 4: Kotlin core seam, pairing-frame codec, window

**Files:** Create `app/src/main/java/me/nagaev/veles/pairing/{PairingCore.kt,PairingFrames.kt,PairingWindow.kt,PairingCode.kt,PairingExecution.kt}`; tests under `app/src/test/java/me/nagaev/veles/pairing/` incl. `PairingTestBase.kt`, `NoVelesCryptoUnderRobolectricTest.kt`, `PairingCryptoVectorsTest.kt`.

**Interfaces:**
- `interface PairingCore { fun start(role: Role, code: ByteArray, context: ByteArray): Started; fun exchange(h: Handle, peerPoint: ByteArray): ByteArray; fun confirm(h: Handle, ...): ByteArray; fun finish(h: Handle, ...): ByteArray; fun close(h: Handle); fun openHandles(): Int }`; `data class Started(val handle: Handle, val point: ByteArray)`. Production `VelesPairingCore` resolves `VelesCrypto` lazily and counts handles itself.
- `enum class PairingFrameType(val code: Int) { REQUEST(1), PA(2), PB(3), CA(4), CB(5) }`; `PairingFrames.encode/decode`.
- `class PairingWindow(clock: () -> Long, random: SecureRandom) { fun open(): Opened; fun cancel(); fun isOpen(): Boolean; val pairingId: ByteArray; fun codeBytes(): ByteArray /* six ASCII digits, handed only to PairingCore.start */ }`; `class PairingCode` with redacting `toString`.
- `class PairingExecution(core: PairingCore, handle: Handle) : AutoCloseable`.

- [ ] **Step 1:** Write failing tests: `window_expires_at_300s`, `second_open_refused`, `window_state_absent_after_restart`, `code_is_six_digits_with_leading_zeros_uniform` (fixed `SecureRandom`), `code_toString_redacts`, `execution_closes_handle_on_exception`, `robolectric_guard_fails_if_VelesCrypto_initialized`.
- [ ] **Step 2:** Run `./gradlew testDebugUnitTest --tests "me.nagaev.veles.pairing.*"`; expect FAIL.
- [ ] **Step 3:** Implement; rejection sampling over `0..999999`; `PairingTestBase` asserts `openHandles() == 0` in `@After`.
- [ ] **Step 4:** Add `PairingCryptoVectorsTest` (`veles-pairing-code-*`, wrong-code, role-swap, role-mismatch, point negatives) as plain JUnit on the real host core; rerun; expect PASS. Run `./gradlew spotlessApply detekt`.
- [ ] **Step 5:** Commit `feat(app): add pairing window, code and core seam`.

## Task 5: Phone admission, machine (through `cB`), routing and the Kotlin trace runner

**Files:** Create `PairingAdmission.kt`, `PhonePairingMachine.kt`, `PairingLimits.kt`, `EnrollmentScenarioTest.kt` in the pairing package; modify `sharing/SessionHost.kt` (per-connection dispatcher), `sharing/SharingService.kt` (`shutdownSessions` cleanup); tests `PairingAdmissionTest.kt`, `PhonePairingMachineTest.kt`, `SessionHostRoutingTest.kt`.

**Interfaces:**
- Defined here: `data class PairingLimits(...)` (checked against `limits.json`), `typealias ConnId = Long`, `sealed interface Decision { Allow; Reject }`, `sealed interface Outgoing { Frame(bytes); Disconnect }`.
- `class PairingAdmission(limits: PairingLimits, clock: () -> Long) { fun requestAllowed(conn: ConnId): Decision; fun paAllowed(conn: ConnId): Decision; fun chargeAttempt(pairingId: ByteArray): Decision /* atomic, no refund */; fun malformed(conn: ConnId); fun beginOp(): Boolean; fun endOp() }`.
- `class PhonePairingMachine(window, admission, coreProvider: () -> PairingCore, ...) { suspend fun onFrame(bytes: ByteArray): List<Outgoing>; fun onDisconnect(); fun cancel() }` (suspend, because later tasks call suspend trust-store functions). This task implements §11.1 states up to `cB` verification.
- `SessionHost` chooses the pairing machine or the reconnect engine from the first frame. The new pairing dependency is an optional constructor parameter whose default rejects `pairing_request` generically, so `SessionHostTest.kt`, `SharingGattServerTest.kt` and `SyntheticProbeHandlerTest.kt` keep compiling; only `SharingService.kt` passes the real machine factory.

- [ ] **Step 1:** Write failing tests (names pin the specs): `sixth_attempt_gets_preauth_reject`, `rolling_limit_across_windows`, `pa_only_connection_consumes_no_attempt`, `invalid_point_is_not_charged`, `valid_pb_charged_before_exchange_even_on_failure` (fake core records call order), `deadline_299999_ok_300000_rejected`, `deadline_rechecked_before_exchange`, `charge_atomic_versus_cancel_and_deadline`, `two_concurrent_charges_one_attempt_left`, `cancel_then_new_window_fresh_budget_rolling_still_applies`, `second_pa_on_connection_rejected_zero_core_calls`, `sixth_pa_per_minute_rejected`, `twenty_first_malformed_connection_blocks_requests`, `eleventh_request_checked_before_pa_limit`, `reconnect_malformed_does_not_use_pairing_budget`, `concurrent_second_request_rejected_without_charge`, `replayed_pb_on_fresh_connection_charged_again_and_fails` (fake core shows a new `start`/`exchange` and a lower budget, no old execution reused), `all_failures_identical_bytes`, `inactivity_30s_closes_and_releases_handle`, `limits_match_limits_json`, `no_window_pairing_request_rejected_generically`, `reconnect_frame_set_unchanged`, `shutdown_erases_window_and_closes_handle`.
- [ ] **Step 2:** Run `./gradlew testDebugUnitTest --tests "me.nagaev.veles.pairing.*"`; expect FAIL.
- [ ] **Step 3:** Implement per the Global Constraints order; synchronize `chargeAttempt` on one lock; the dispatcher keeps one `PairingExecution` per connection and closes it in `onDisconnected`; `SharingService.shutdownSessions()` cancels the window and closes executions.
- [ ] **Step 4:** Create `EnrollmentScenarioTest.kt` (maps fixture events and effects of `protocol/v1/fixtures/enrollment.json` to the machine, as `ScenarioConformanceTest` does for OTP-09); rerun; expect PASS. Flip the window, admission, deadline and `cA`/`cB` ids in `otp-10-scenarios.json` to `execute`.
- [ ] **Step 5:** Commit `feat(app): add phone pairing admission and machine`.

## Task 6: Enrollment records and keys (both endpoints)

**Files:** Create Kotlin `pairing/{EnrollmentSchedule.kt,EnrollmentRecords.kt}`; TS `web-extension/src/pairing/{enrollment.ts,frames.ts}`; tests `EnrollmentRecordsTest.kt`, `EnrollmentVectorsTest.kt`, `web-extension/test/pairing/{enrollment.test.ts,frames.test.ts}`.

**Interfaces:**
- Kotlin: `EnrollmentSchedule.derive(shared: ByteArray, context: ByteArray): EnrollmentKeys` (zeroable); `EnrollmentRecords.encode/decode`; `fun transcript(phoneId, clientId, context): ByteArray` (`ET`); `recordFingerprint(...)`, `pairFingerprint(...)`.
- TS mirror: `deriveEnrollmentKeys`, `encodeIdentityRecord`, `decodeIdentityRecord`, `transcript`, `recordFingerprint`, `pairFingerprint`; `frames.ts` pairing-frame codec (types 1–5, `preauth_reject` 0x0F).

- [ ] **Step 1:** Write failing tests from the vectors (`veles-enrollment-schedule`, fingerprints, `veles-neg-context-mismatch`) and: `altered_identity_or_context_changes_ET_and_blocks_activation`, `device_type_roles_enforced` (phone `0x00`, client `0x01`/`0x02`), `record_length_bounds`, `bad_record_fingerprint_stores_nothing`, `enroll_identity_rejected_in_reconnect`, `keys_zeroed_after_use`.
- [ ] **Step 2:** Run Kotlin tests and `npm test -- enrollment frames`; expect FAIL.
- [ ] **Step 3:** Implement both sides; `EnrollmentSession` (Kotlin, Task 7) will use these; MAC comparisons use the existing constant-time helpers, and a grep rule fails on direct `==` over MACs.
- [ ] **Step 4:** Add the new TS tests to the `npm test` list; rerun; expect PASS.
- [ ] **Step 5:** Commit `feat: enrollment schedule, records and fingerprints on both endpoints`.

## Task 7: Phone enrollment flow

**Files:** Create `pairing/EnrollmentSession.kt`; modify `PhonePairingMachine.kt`; tests `EnrollmentFlowTest.kt`.

**Interfaces:** Consumes Tasks 4–6 and `TrustRepository.addPending(...)` (suspend). Produces the machine's states through `awaiting_commit`; `EnrollmentSession` over `RecordCodec.open/seal` with the OTP2-CRY-020a allow-list.

- [ ] **Step 1:** Write failing tests: `phone_sends_record_only_after_cB_verified`, `duplicate_installation_id_sends_enrollment_rejected_and_closes_window` (existing record unchanged), `pending_persisted_before_prepare`, `persist_failure_publishes_nothing`, `early_or_mismatched_prepare_erases`, `cancel_after_pending_deletes_record`, `code_one_use_after_cB`, `hint_does_not_select_identity`, `erasure_on_success_failure_cancel`, `record_type_outside_allow_list_erases`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement (duplicate check before `addPending`). **Step 4:** Run; expect PASS; extend `EnrollmentScenarioTest` and flip the phone enrollment ids to `execute`.
- [ ] **Step 5:** Commit `feat(app): phone enrollment flow`.

## Task 8: Chrome enrollment flow and TS trace runner

**Files:** Create `web-extension/src/pairing/{client-machine.ts,link.ts}`, tests `web-extension/test/pairing/{client-machine.test.ts,enrollment-scenarios.test.ts}`; modify `web-extension/src/connector.ts` and `connector/*` to host the flow.

**Interfaces:** `class ClientMachine { constructor(deps: { core: typeof PairingExecution; repo: ChromeTrustRepository; clock: () => number }); onFrame(bytes: Uint8Array): Promise<Uint8Array[]>; cancel(): Promise<void>; state: ClientState; fingerprint(): string | undefined }`; `link.ts` runs the flow over the BLE link outside `SessionManager`.

- [ ] **Step 1:** Write failing tests: `wrong_code_cA_fails_no_cB_sent`, `keys_derived_only_after_cA_verified`, `client_releases_record_only_after_phone_record_authenticated`, `client_persists_pending_before_release`, `rejected_after_pending_deletes_record`, `client_timeouts`, `cancel_deletes_pending_record`, `tab_teardown_closes_handle_and_deletes_pending`, `fingerprint_only_after_both_authenticated`, `duplicate_id_takes_persist_failure_path`.
- [ ] **Step 2:** Run `npm test -- client-machine`; expect FAIL.
- [ ] **Step 3:** Implement §11.2; `link.ts` never routes through `SessionManager`.
- [ ] **Step 4:** Create `enrollment-scenarios.test.ts` (maps fixture events/effects to `ClientMachine`); add all new test files to the `npm test` list; run; expect PASS; flip the client enrollment ids to `execute`.
- [ ] **Step 5:** Commit `feat(web-extension): client enrollment flow`.

## Task 9a: Recovery keys and `ET` in lookups

**Files:** Modify `session/ReconnectKeySchedule.kt`, `session/PeerLookup.kt`, `session/PhoneSessionEngine.kt`, `sharing/SharingService.kt` (`peerLookup()` must load `ET` from the pending/committed record); TS `session/schedule.ts`, `session/engine.ts`, `connector.ts` (`lookupPhone`); tests `RecoveryKeysTest.kt`, `recovery-keys.test.ts`.

**Interfaces:** `ReconnectKeySchedule.derive(ss, phoneNonce, clientNonce, et: ByteArray?)` returns `ReconnectKeys` with `recovery: RecoveryKeys?` (`kRecClient`, `kRecPhone`) derived whenever `et != null`; `PeerRecord.et: ByteArray? = null` and the TS lookup equivalent (defaults keep existing callers `TestPeers.kt`, `ScenarioConformanceTest.kt`, `ReconnectKeyScheduleTest.kt`, `PhoneLoopbackMain.kt` compiling; `derive(..., et: ByteArray? = null)` likewise).

- [ ] **Step 1:** Failing tests: `recovery_keys_match_crypto_vectors` (`k_rec_client`, `k_rec_phone`), `recovery_keys_erased_in_full_class`, `no_et_no_recovery_keys`. **Step 2:** Run; expect FAIL. **Step 3:** Implement and thread `et` through both lookups. **Step 4:** Run; expect PASS (all existing session tests still green).
- [ ] **Step 5:** Commit `feat: derive recovery activation keys`.

## Task 9b: Handler session context

**Files:** Modify `session/SessionEffect.kt` (`ApplicationHandler`), `session/PhoneSessionEngine.kt`, `app/src/debug/java/me/nagaev/veles/debugprobe/SyntheticProbeHandler.kt` (the `forSession` override at ~line 94), `app/src/testDebug/java/me/nagaev/veles/debugprobe/SyntheticProbeHandlerTest.kt` (calls at ~131 and ~158 build a `SessionContext`), TS `session/engine.ts` and handler type; tests `HandlerContextTest.kt`, `handler-context.test.ts`.

**Interfaces:** `data class SessionContext(val sessionIdPrefix: ByteArray, val sessionClass: SessionClass, val peerId: String, val et: ByteArray?, val peerState: Int, val recovery: RecoveryKeys?)`; `ApplicationHandler.forSession(context: SessionContext)`; handlers may send on `Authenticated`. On the TS side the hook is optional (`forSession?(ctx: SessionContext): ApplicationHandler`, the engine falls back to the handler itself), so `src/debug/probe.ts`, `ScenarioHandler` in `test/session/scenarios.test.ts` and `test/connector/session-harness.ts` keep compiling. Release `BuildSessionExtensions.applicationHandler` stays null; Task 9d composes the activation handler.

- [ ] **Step 1:** Failing tests: `handler_receives_context_on_authenticated`, `handler_can_send_unprompted_record`, `activation_record_without_handler_protocol_violation`. **Step 2:** Run; expect FAIL. **Step 3:** Implement; keep release `BuildSessionExtensions` handler null. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: pass session context to application handlers`.

## Task 9c: Store fixes

**Files:** Modify `trust/TrustRepository.kt`, `web-extension/src/trust/repository.ts`; tests in `TrustRepositoryTest.kt`, `trust.test.ts`.

- [ ] **Step 1:** Failing tests: `phone_duplicate_commit_on_active_is_noop_after_mac` (`st-rec-full-pending-peer-commit-duplicate-no-regression`), `wrong_mac_duplicate_commit_still_rejected`, `chrome_authenticated_pending_writes_nothing` (record byte-identical, `pendingContext` checked), `chrome_authenticated_pending_other_context_rejected`. **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `fix: accept pending authentication and duplicate commit for recovery`.

## Task 9d: Activation engines and proof registries

**Files:** Modify `ScenarioConformanceTest.kt` and `scenarios.test.ts` (event/effect mapping and client-id set for the recovery ids flipped in 9d/9e). Create Kotlin `pairing/{ActivationEngine.kt,ProofRegistry.kt}`, TS `pairing/{activation.ts,proofs.ts}`; modify `sharing/SharingService.kt`, `connector.ts` (verifier holder), `main` composition; tests `ActivationEngineTest.kt`, `activation.test.ts`.

**Interfaces:** `ProofRegistry.register(sessionId, proofs)` / `consume(...)`; the phone verifier consults it (only `SharingService`'s repository; debug `BuildSessionExtensions` and `ProbeReceiver` repositories stay reject-all); Chrome combines `manager.verifier` with the registry for the `complete` stage; `SharingService` always builds the activation handler and composes it with `BuildSessionExtensions.applicationHandler(...)` when that is non-null (debug probe), so a release build with a null extension handler still activates; the TS side composes in `main` likewise.

- [ ] **Step 1:** Failing tests: `activation_full_flow`, `wrong_mac_changes_nothing`, `step0_prepare_unprompted`, `step5_ack_only_for_active_family`, `pending_client_recommits`, `recovery_class_never_upgrades`, `explicit_peer_starts_recovery_for_pending`, `debug_repositories_stay_reject_all`, `replayed_recovery_record_rejected`, `release_style_null_extension_handler_still_activates`. **Step 2:** Run both suites; expect FAIL. **Step 3:** Implement OTP2-STA-010b steps 0, 5 and 6. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: trust activation and recovery engines`.

## Task 9e: Expiry, interruption and multi-peer

**Files:** Modify `ScenarioConformanceTest.kt`, `scenarios.test.ts`. Tests `ActivationInterruptionTest.kt`, `activation-interruption.test.ts`; fixes in the stores as needed; update `otp-10-scenarios.json`.

- [ ] **Step 1:** Failing tests: `phone_expiry_86400_fake_clock_deletes_record`, `chrome_context_loss_deletes_pending`, `boot_continuity_loss_deletes_incomplete_only`, `mirror_survives_restart`, `interruption_at_every_activation_message`, `one_sided_pending`, `no_record_after_cB_disconnect`, `incomplete_records_authorize_no_otp`, `two_desktops_sequential`. **Step 2:** Run; expect FAIL where unimplemented. **Step 3:** Implement the gaps. **Step 4:** Run; expect PASS; flip all remaining OTP-10 ids to `execute` in `otp-10-scenarios.json`.
- [ ] **Step 5:** Commit `test: activation expiry, interruption and multi-peer`.

## Task 10: Debug harness and release guards

**Files:** Create `app/src/debug/java/me/nagaev/veles/debugpairing/{PairingDebugScreen.kt,PairingDebugHooks.kt,PairingDebugReceiver.kt}`, `web-extension/src/debug/pairing-panel.ts`; modify `app/src/debug/AndroidManifest.xml` (non-launcher activity and receiver), `build.gradle.kts`/`app/build.gradle.kts` (`releaseHookForbidden`), `web-extension/test/bundle.test.ts`, the Log guard in `check-android.yml`.

**Interfaces:** `PairingDebugHooks.state: StateFlow<PairingDebugState>` (debug only, includes the code); broadcasts `ACTION_PAIRING_START`/`ACTION_PAIRING_CANCEL` (non-echoing); Clear reuses `REVOKE_PEER`. TS `mountPairingPanel(host: HTMLElement, deps): void`, behind `__VELES_CHANNEL__` and a dynamic import.

- [ ] **Step 1:** Failing tests: `broadcast_start_result_has_no_code`, `clear_then_repair_succeeds`, `chrome_clear_uses_revoke`, `panel_absent_from_production_bundle`, `code_never_in_diagnostic_ring_or_view_model`, and a grep test that fails if `PairingCode` reaches a logger (covering `app/src/debug` and `web-extension/src/debug`).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement; add `me/nagaev/veles/debugpairing`, the `ACTION_PAIRING_*` strings and the screen strings explicitly to `releaseHookForbidden` (the existing `debugprobe` and `debug.` entries do not match). Add the new TS test to the `npm test` list.
- [ ] **Step 4:** Run `./gradlew :app:releaseHookGuard` and `npm run test:bundle`; expect PASS; verify a deliberate leak in release makes the guard fail, then revert.
- [ ] **Step 5:** Commit `feat: debug-only pairing controls with release guards`.

## Task 11: Loopback, instrumented test and gate

**Files:** Modify `web-extension/test/session/loopback.test.ts`, `client-harness.ts`, `app/src/test/java/me/nagaev/veles/session/loopback/PhoneLoopbackMain.kt` (host the pairing machine); create `app/src/androidTest/java/me/nagaev/veles/pairing/PairingInstrumentedTest.kt`.

- [ ] **Step 1:** Add loopback scenarios `enrollment_full` and `interrupted_activation_resumes` (Kotlin phone on the host library via `PhoneLoopbackMain`, TS client on WASM; `PhoneLoopbackMain` uses a fixed `SecureRandom` producing code `000042`, which the TS client takes as a string and must carry to a confirmed session); run `./gradlew sessionLoopbackCheck`; expect FAIL before wiring, PASS after.
- [ ] **Step 2:** Add the instrumented test with a Kotlin test client on the device `.so`; run `./gradlew connectedDebugAndroidTest` when a device is available.
- [ ] **Step 3:** Run the full gate from `tasks.md` 7.3; expect all PASS.
- [ ] **Step 4:** Assert nothing is deferred to OTP-10 and remove the OTP-10 deferrals from `otp-09-scenarios.json` (runners already read the merged inventory from Task 3); rerun the runners.
- [ ] **Step 5:** Commit `test: cross-endpoint enrollment loopback`.

## Task 12: Physical runs and close-out

**Files:** Create `docs/otp-10-physical-runs.md`.

- [ ] **Step 1 (needs the user):** Windows: build with `--init-script config/android_keys/fixture-signing.gradle`, compare cert fingerprints, `adb install -r`; clear trust via debug Clear on both sides; pair; reconnect 1 (selected, upgrades Chrome to `active`) and 2 (automatic); interrupted activation with the connector tab open. Record only observed values.
- [ ] **Step 2 (needs the user):** Repeat on macOS; record fingerprints and observed SPAKE2 time.
- [ ] **Step 3:** Open the PR (title and body Conventional Commit) after the CLAUDE.md local checks; then `/opsx:verify`, sync specs and archive.
