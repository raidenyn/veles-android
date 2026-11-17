# OTP-12 Gate B tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the debug-only Android Gate B fixture and scheduled push, a client-agnostic journey set with a scripted client check, a development-only multi-event pull, and a committed not-accepted evidence matrix with validator and runbook, so the owner-driven physical Gate B runs can follow.

**Architecture:** A `GateBPullRouter` replaces the probe in the debug handler composition and dispatches once per pull to the probe or a new in-memory `GateBFixture`; a `GateBPushScheduler` behind clock/timer/wake-resource interfaces fires one push inside the foreground service. Two journeys in `protocol/conformance/` are run by a scripted TypeScript client against a test-only child-JVM main wired with the real fixture. The matrix, a Python validator and the runbook live in the non-release `docs` node.

**Tech Stack:** Kotlin (Android debug and `testDebug` source sets, JUnit), TypeScript (vitest, `web-extension/`), Node `node:test` (`protocol/checks`), Python 3 stdlib `unittest` (`docs/checks`), Gradle.

**Spec:** `proposal.md`, `design.md` (D1-D11) and `specs/*/spec.md` in this change directory; RFC section `### OTP-12` in `docs/rfcs/2026-08-21-otp-external-devices.md`.

## Global Constraints

- Scope is tooling only: no physical Windows/macOS run, no Gate B evidence, no Gate B decision; issue stays open.
- Everything new is debug/test-only. The only production-source edits are the no-op `detach()` call as the first statement of `SharingService.shutdownSessions()` and the release `BuildSessionExtensions.detach()` no-op stub.
- No test may sleep or read a wall clock; clocks, timers and wake resources are injected and advanced by hand (`.agents/rules/repo-no-time-based-tests.md`).
- Fixture: exactly five records on seed; lifetimes 1..600 (default five 600); `remaining_lifetime` = floor(remaining seconds), omitted below 1 s, never 0; `event_count` 0..5; merchant `Veles synthetic`, currency `XTS`, amount 100 scale 2; ack logs and never deletes; memory only.
- Push: delay 1..7200 s whole seconds, requires an armed fixture, deadline on elapsed realtime, backstop delay + 60 s, one fresh event per open `full` session, `skipped` when none; cancelled only by detach, disarm, explicit cancel or new schedule (never by session close, reseed or revocation).
- New adb actions use the `me.nagaev.veles.debug.` prefix; new strings outside the existing guard prefixes go in `releaseHookForbidden` and `ReleaseGuardStringsTest`; `WAKE_LOCK` is not a guard string.
- Journey vocabulary: lowercased tokens (split on non-alphanumerics and camelCase) must not equal tab, port, native, host, handle, extension, offscreen, badge; `hint` only as `trust_independent_of_platform_hint`.
- Matrix: required row table from `specs/gate-b-evidence-matrix/spec.md`; statuses `NOT RUN`/`FAIL`/`PASS`; committed `decision: OPEN`, `gate_b_accepted: false`, `initial_state: true`; the tool never writes `PASS`.
- Commits are Conventional Commits `type(scope): description`. Before pushing run the node checks selected by `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`.
- Real-device rule: Android and web-extension logic are run on a real phone/emulator and a real Chrome; report what ran; if unavailable, stop and ask.

## Review Focus

- A pull while armed whose mode flips between the engine's and the router's lookups: exactly one reply, session stays open (Task 2).
- A seed with a bad list (four values, 0, 601) or no running host: refused, nothing changes (Tasks 1, 4).
- A push scheduled, then the connector pulls and closes, a reseed runs, every peer is revoked: the push still fires or logs `skipped` (Task 3).
- A record with 0.5 s remaining and an all-expired history: omitted, `event_count = 0`, never `remaining_lifetime` 0 (Task 1).
- `pullAll()` with count 6, a count/payload mismatch or a duplicate id: whole record rejected, no ack sent (Task 5).
- A journey parameter named `transport_mtu` (allowed) vs `tab_id` (rejected) (Task 6).
- A matrix row `PASS` by hand without captures, adjudication or provenance anchor; `gate_b_accepted` true with a non-`PASS` row (Task 8).
- Push arrival at exactly minute 19 and minute 21 pass, 18:54 and 21:01 fail, unlock at 14 minutes fails (Task 8).

---

### Task 1: Gate B fixture core

**Files:**
- Modify: `android/app/src/debug/java/me/nagaev/veles/debugprobe/SyntheticProbeHandler.kt` (`createSyntheticEvent`)
- Create: `android/app/src/debug/java/me/nagaev/veles/debugprobe/GateBFixture.kt`
- Test: `android/app/src/testDebug/java/me/nagaev/veles/debugprobe/GateBFixtureTest.kt`; existing `SyntheticProbeHandlerTest.kt` stays green

**Interfaces:**
- Produces: `createSyntheticEvent(localInstallationId: ByteArray, receiptEpochSeconds: Long, random: (Int) -> ByteArray, lifetimeSeconds: Long = SYNTHETIC_REMAINING_LIFETIME): SyntheticEvent`
- Produces: `internal fun interface ElapsedClock { fun elapsedMillis(): Long }`; `GateBFixture(localInstallationId: ByteArray, elapsed: ElapsedClock, wallMillis: () -> Long, random: (Int) -> ByteArray, log: (String) -> Unit)` with `fun seed(lifetimes: List<Int>): Boolean`, `@Volatile var armed: Boolean` (private set), `fun disarm()`, `fun pullBody(): ByteArray` (the `pull_events` body: count byte then events), `fun acknowledge(eventId: ByteArray): Boolean` (true when it owns the id, logs only), `fun nextPush(): SyntheticEvent` (fresh event; its id is kept in a bounded set of the last 16 pushed ids only so `acknowledge` can own it, never in the history). All methods are `@Synchronized` (the receiver's IO coroutine and session handlers both touch it); `armed` is the single source of armed state

- [ ] **Step 1: Write failing tests** in `GateBFixtureTest`: `seed_rejects_bad_lists` (sizes 4 and 6, values 0 and 601 return false, `armed` unchanged, one refusal log); `pull_returns_five_with_distinct_ids_and_600_lifetimes`; `retained_and_expired_together` (lifetimes `[60,120,300,600,600]`, advance the manual clock 150 s: pull body count 3, same event ids as before, `remaining_lifetime` 150/450/450 floor values decoded from the body); `half_second_remaining_is_omitted`; `all_expired_pull_is_count_zero`; `ack_never_deletes_and_unknown_ack_is_false`; `restart_is_unarmed` (a new instance is unarmed, no records); `log_has_no_key_or_full_installation_id` (log lines contain marker and event id only); `probe_default_lifetime_still_600` in the existing probe test.
- [ ] **Step 2: Run** `cd android && ./gradlew :app:testDebugUnitTest --tests '*GateBFixtureTest' --tests '*SyntheticProbeHandlerTest'` — expect FAIL (unresolved `GateBFixture`).
- [ ] **Step 3: Add the `lifetimeSeconds` parameter** to `createSyntheticEvent` (writes it with `writeU32`; default keeps probe behaviour).
- [ ] **Step 4: Implement `GateBFixture`** per the interface above: records keep fixed event id and receipt epoch (wall seconds at seed); expiry = seed elapsed + lifetime*1000; `pullBody` re-encodes each unexpired record by calling the encoder with `lifetimeSeconds = floor(remainingMillis/1000)`, skipping records under 1000 ms; log lines under tag text via the `log` lambda: `seeded`, `refused`, `disarmed`, `expired-omitted count=N` with the elapsed timestamp.
- [ ] **Step 5: Run the Step 2 command** — expect PASS.
- [ ] **Step 6: Commit** `git add android/app/src && git commit -m "feat(android): add the debug Gate B history fixture"`

### Task 2: Pull router and composition

**Files:**
- Create: `android/app/src/debug/java/me/nagaev/veles/debugprobe/GateBPullRouter.kt`
- Modify: `DebugProbeRuntime` in `ProbeReceiver.kt` (`fixture`, application context), `android/app/src/debug/java/me/nagaev/veles/session/BuildSessionExtensions.kt` (compose router), `ProbeCommands` (new constructor parameter `gateBArmed: () -> Boolean = { false }`; `push()` sends nothing and logs while it returns true; `debugProbeCommands(...)` passes `{ DebugProbeRuntime.fixture?.armed == true }`, and Task 3 keeps this parameter beside the new `gateB`)
- Test: `android/app/src/testDebug/java/me/nagaev/veles/debugprobe/GateBPullRouterTest.kt`

**Interfaces:**
- Consumes: Task 1 `GateBFixture`; `SyntheticProbeHandler`; `CompositeApplicationHandler`
- Produces: `DebugProbeRuntime.fixture: GateBFixture?` (set in `applicationHandler(context)` and read by `attach(host)`); `internal fun buildDebugHandler(identity: InstallationIdentity?, ...): ApplicationHandler?` returning null when the identity is null (the pure branch `applicationHandler` calls);   `GateBPullRouter(probe: ApplicationHandler, fixture: GateBFixture, mode: () -> Boolean = { fixture.armed }, log: (String) -> Unit) : ApplicationHandler` with `forSession` returning a router over `probe.forSession(context)` sharing the fixture; `handles(type)` true for `PULL_REQUEST` and `EVENT_ACK`; `handle` reads `mode()` once; a non-empty `pull_request` body throws `SessionReject`; for `EVENT_ACK` it validates the 32-byte shape and installation id with the probe's check (extracted as `internal fun validateAck(body, installationId)` in `SyntheticProbeHandler.kt`), then logs when the fixture owns the id, else delegates to the probe

- [ ] **Step 1: Write failing tests**: `two_sessions_each_get_exactly_one_reply_while_armed` (through `composeApplicationHandlers(...)`, the public entry to the private composite), `mode_flip_between_lookups_answers_once` (mode lambda flips after the first `handles` call), `unarmed_behaves_like_probe` (reuses the probe test expectations), `ack_routing_for_history_pushed_and_unknown_ids` (history and pushed ids logged, unknown id no-op, wrong installation id still throws `SessionReject` as the probe does), `disarm_returns_pulls_to_probe`, `router_logs_which_component_answered`, `router_absent_without_identity` (the pure `buildDebugHandler(null, ...)` returns null; no Hilt or Robolectric needed), and in `ProbeCommandsTest` `push_while_armed_sends_nothing_and_logs`.
- [ ] **Step 2: Run** `cd android && ./gradlew :app:testDebugUnitTest --tests '*GateBPullRouterTest' --tests '*ProbeCommandsTest'` — expect FAIL.
- [ ] **Step 3: Implement `GateBPullRouter`**; in `BuildSessionExtensions.applicationHandler` build the fixture next to the probe, store the fixture and `context.applicationContext` in `DebugProbeRuntime`, and return the router (null when the identity is unavailable, as now); in `ProbeCommands.push()` log `fixture owns pushes` and return when armed.
- [ ] **Step 4: Run Step 2 command** — expect PASS, then `./gradlew :app:testDebugUnitTest --tests '*debugprobe*'` — all PASS.
- [ ] **Step 5: Commit** `feat(android): route synthetic pulls between the probe and the Gate B fixture`

### Task 3: Push scheduler and detach

**Files:**
- Create: `android/app/src/debug/java/me/nagaev/veles/debugprobe/GateBPushScheduler.kt`, `GateBController.kt`, `PowerManagerWakeResource.kt`, `CoroutineStepTimer.kt` (same package)
- Modify: `android/app/src/debug/.../session/BuildSessionExtensions.kt` (`attach`, `detach`), `android/app/src/release/.../session/BuildSessionExtensions.kt` (no-op `detach`), `android/app/src/main/java/me/nagaev/veles/sharing/SharingService.kt` (`shutdownSessions()` first statement)
- Test: `android/app/src/testDebug/java/me/nagaev/veles/debugprobe/GateBPushSchedulerTest.kt`, `.../sharing/ShutdownOrderTest.kt`

**Interfaces:**
- Consumes: Task 1 `GateBFixture` (`armed`, `nextPush()`), `ElapsedClock`; Task 2 `DebugProbeRuntime.fixture`; `ProbeSessions` (`openFullSessions`, `sendApplication`)
- Produces: `GateBController(fixture: GateBFixture, scheduler: GateBPushScheduler)` stored in `DebugProbeRuntime.gateB` with `seed(lifetimes)`, `disarm()` (disarms the fixture and cancels with reason `disarm`), `schedule(delaySeconds)`, `cancel()` (reason `explicit`), `detach()`; `ProbeCommands` gains `gateB: GateBController? = null`; `fun interface StepTimer { suspend fun pause(millis: Long) }`; `interface WakeResource { fun acquire(timeoutMillis: Long); fun release() }`; `GateBPushScheduler(fixture, sessions: () -> ProbeSessions, clock: ElapsedClock, timer: StepTimer, wake: WakeResource, scope: CoroutineScope, processId: Int, log: (String) -> Unit)` with `fun schedule(delaySeconds: Long): Boolean`, `fun cancel(reason: String)`, `fun detach()`; `BuildSessionExtensions.detach()`

- [ ] **Step 1: Write failing tests** (manual clock and timer, `kotlinx-coroutines-test` as the project already uses): `no_push_one_second_before_deadline`, `fires_at_deadline_to_each_open_full_session` (two sessions, two `fired` lines, history count unchanged), `skipped_when_no_session`, `delay_0_negative_and_7201_refused`, `unarmed_refused`, `second_schedule_replaces_first_and_is_logged`, `explicit_cancel_logged`, `session_close_reseed_and_revocation_do_not_cancel`, `disarm_cancels`, `wake_resource_released_on_fire_skip_cancel_and_detach`, `backstop_timeout_is_delay_plus_60_seconds`, `scheduled_and_fired_lines_carry_the_same_process_id_and_elapsed_timestamps`, `scheduler_log_lines_have_no_key_or_full_installation_id`, `detach_joins_an_in_flight_send_and_is_idempotent`, `wake_resource_release_after_backstop_expiry_is_safe` (a mockk `PowerManager.WakeLock` via the existing mockk-agent; asserts `setReferenceCounted(false)`); `ShutdownOrderTest.detach_precedes_host_shutdown` reads `SharingService.kt` source text and asserts the index of the `BuildSessionExtensions.detach()` call is the first statement of `shutdownSessions` and precedes `shutdown()`.
- [ ] **Step 2: Run** `cd android && ./gradlew :app:testDebugUnitTest --tests '*GateBPushSchedulerTest' --tests '*ShutdownOrderTest'` — expect FAIL.
- [ ] **Step 3: Implement the scheduler**: log `scheduled` with delay, deadline and `processId` and every line with the elapsed timestamp; deadline = `clock.elapsedMillis() + delay*1000`; loop `timer.pause(step)` (default 1000 ms step) and re-check; wake acquire with `delay*1000 + 60_000`; release in `finally`.
- [ ] **Step 3b: Implement `PowerManagerWakeResource` (partial wake lock, `setReferenceCounted(false)`, timed acquire), `CoroutineStepTimer` (`delay`) and `GateBController`; add `gateB` to `ProbeCommands`.**
- [ ] **Step 4: Implement hooks**: debug `attach(host)` creates the scheduler scope and the `GateBController` (stored in `DebugProbeRuntime.gateB`) and stores `DebugProbeRuntime.host`; debug `detach()` is idempotent (`fail()` and `onDestroy()` can both reach shutdown) and, via `GateBController.detach()`, disarms, cancels, then does its own `runBlocking { job.join() }` on the scheduler's job (the scheduler scope uses `Dispatchers.Default`, never Main, because `onDestroy` runs on the main thread), releases the wake resource and clears `host`; release `detach()` is empty; add the call as the first line of `shutdownSessions()`.
- [ ] **Step 5: Run Step 2 command and** `./gradlew :app:testDebugUnitTest` — expect PASS.
- [ ] **Step 6: Commit** `feat(android): schedule one synthetic Gate B push inside the sharing service`

### Task 4: adb surface, manifest and release guard

**Files:**
- Modify: `android/app/src/debug/java/me/nagaev/veles/debugprobe/ProbeReceiver.kt` (actions), `android/app/src/debug/AndroidManifest.xml` (intent filters, `WAKE_LOCK`), `android/app/build.gradle.kts` (`releaseHookForbidden`), `android/app/src/testDebug/.../debugpairing/ReleaseGuardStringsTest.kt`
- Test: `android/app/src/testDebug/java/me/nagaev/veles/debugprobe/GateBCommandsTest.kt`

**Interfaces:**
- Consumes: Tasks 1-3
- Produces: lifetime and delay parsing in `ProbeCommands` (testable; `ProbeReceiver.dispatch` only forwards extras); actions `me.nagaev.veles.debug.GATE_B_SEED` (extra `lifetimes`, comma list), `GATE_B_DISARM`, `GATE_B_SCHEDULE_PUSH` (extra `delay`), `GATE_B_CANCEL_PUSH`; each returns a structured result string through the existing result channel

- [ ] **Step 1: Write failing tests**: `seed_parses_comma_list_and_reports_result`, `seed_without_host_is_refused`, `schedule_requires_delay_extra`, `disarm_and_cancel_report_results`, `new_strings_covered_by_release_guard` (every new action and the wake-lock tag is matched by `releaseHookForbidden`).
- [ ] **Step 2: Run** `cd android && ./gradlew :app:testDebugUnitTest --tests '*GateBCommandsTest' --tests '*ReleaseGuardStringsTest'` — expect FAIL.
- [ ] **Step 3: Add the actions** to `ProbeCommands`/`ProbeReceiver.dispatch`, the manifest entries, and add only the wake-lock tag (if outside the prefixes) to `releaseHookForbidden` and the string test.
- [ ] **Step 4: Run Step 2 command, then, from `android/`,** `./gradlew :app:assembleRelease :app:releaseHookGuard` and inspect the merged release manifest for `SCHEDULE_EXACT_ALARM`/fixture strings — expect guard PASS.
- [ ] **Step 5: Run the Android lint gate** `cd android && ./gradlew spotlessCheck detekt lintDebug` — expect PASS.
- [ ] **Step 6: Commit** `feat(android): add the Gate B adb commands and guard their strings`

### Task 5: Debug connector multi-event pull

**Files:**
- Modify: `web-extension/src/debug/probe.ts` (`pullAll`, decoder by pending request kind, `VelesDebug` interface), `web-extension/test/bundle.test.ts`
- Test: `web-extension/test/debug-probe.test.ts`

**Interfaces:**
- Produces: `VelesDebug.pullAll(handle?: string): Promise<ProbeEvent[]>`

- [ ] **Step 1: Write failing tests**: `pullAll_five_events_acks_each`, `pullAll_empty_history_returns_empty_and_no_ack`, `pullAll_count_six_rejected_without_ack`, `pullAll_count_payload_mismatch_rejected`, `pullAll_duplicate_event_id_rejected`, `pullAll_bound_violation_ends_session`, `pull_still_requires_exactly_one_event`; in `bundle.test.ts` `production_bundle_has_no_pullAll_marker` (the literal marker string `veles-debug-pullAll` embedded in the development-only code path (a string literal survives minification)).
- [ ] **Step 2: Run** `cd web-extension && npx vitest run test/debug-probe.test.ts` — expect FAIL.
- [ ] **Step 3: Implement `pullAll`**: record the pending request kind; the per-session handler decodes the whole `pull_events` body (`cursor.u8()` count 0..5, strict §8.1 per event via the existing decoder, reject duplicate ids and length mismatch) before sending any `event_ack`.
- [ ] **Step 4: Run Step 2 command, then `./gradlew rustWasm`, `cd web-extension && npm run build` and `npm run test:bundle`** — expect PASS.
- [ ] **Step 5: Commit** `feat(web-extension): decode up to five events in the development probe`

### Task 6: Journey file and schema test

**Files:**
- Create: `protocol/conformance/gate-b-journeys.json`, `protocol/checks/gate-b-journeys.test.mjs`
- Modify: `protocol/checks/check.mjs` (named test list)

**Interfaces:**
- Produces: JSON `{ "trust_independent_of_platform_hint": true, "journeys": { "enrollment": [steps], "protected-records": [steps] } }` where a step is `{ "action": <enum>, "params": {...}, "expect": {...} }`; actions `enroll, reconnect, seed, pull, schedule_push, push, ack, advance_clock, revoke, reconnect_expect_rejected`; expectations `session_class`, `event_count`, `remaining_lifetime_range`, `key_fresh`, `rejected`

- [ ] **Step 1: Write failing tests** in `gate-b-journeys.test.mjs`: `file_has_both_journeys`, `unknown_action_fails`, `forbidden_token_in_any_key_or_value_fails` (`tab_id` fails), `transport_mtu_passes`, `hint_only_as_the_named_property`.
- [ ] **Step 2: Run** `node --test protocol/checks/gate-b-journeys.test.mjs` — expect FAIL.
- [ ] **Step 3: Write the journeys**: `enrollment` = enroll, reconnect (`key_fresh: true`); `protected-records` = seed (`[60,120,300,600,600]`), pull (5 events), advance_clock 150, pull (3 events, same ids), schedule_push 1200, advance_clock 1200, push (1 event), ack, revoke, reconnect_expect_rejected. Implement the tokenizing check in the test (lowercase, split on non-alphanumerics and camelCase).
- [ ] **Step 4: Add the test to `check.mjs`'s list; run** `./gradlew protocolCheck` — expect PASS.
- [ ] **Step 5: Commit** `test(protocol): add the client-agnostic Gate B journeys`

### Task 7: Scripted client, loopback main and CI wiring

**Files:**
- Create: `android/app/src/testDebug/java/me/nagaev/veles/session/GateBLoopbackMain.kt`, `web-extension/test/gate-b/{transport.ts,trust.ts,enrollment-journey.test.ts,records-journey.test.ts,import-guard.test.ts,fixtures/forbidden-import.ts}`
- Modify: `android/app/src/test/.../PhoneLoopbackMain.kt` (`private` helpers to `internal`), `web-extension/package.json` (`test` list), root `build.gradle.kts` (`sessionLoopbackCheck` command line)

**Interfaces:**
- Consumes: Task 1-3 fixture and scheduler; Task 6 journey file; production `ClientSessionEngine`, `PairingExecution`, `ActivationEngine`
- Produces: child JVM `me.nagaev.veles.session.GateBLoopbackMainKt` (fully qualified in the TS spawn; the existing main is `...session.loopback.PhoneLoopbackMainKt`) speaking the existing binary loopback frames (`kind u8 || u32be length || payload`, commands as kind-1 UTF-8) plus commands `seed <csv>`, `schedule <delay>`, `advance <seconds>`, `revoke`; the fixture and scheduler use their own manual clock, separate from the phone session engine's clock so `advance` cannot trip session timeouts; TS `runJourney(steps, driver)`; in-memory `MemoryTrust` implementing `ClientTrustRepository` (`src/pairing/client-machine.ts`), `ActivationTrustRepository` (`src/pairing/activation.ts`) and the `ClientEngineDeps` lookups the session engine needs; the enrollment client drives `ClientMachine` and the pairing frames directly instead of `PairingLink`; `enrollment` spawns the existing `PhoneLoopbackMainKt ... pairing` mode and `protected-records` spawns `GateBLoopbackMainKt`

- [ ] **Step 1: Write failing tests**: `import-guard.test.ts` — `closure_of_client_files_avoids_forbidden_modules` and `negative_fixture_is_detected` (the guard run directly over `fixtures/forbidden-import.ts`, which imports `../../../src/connector/port`, fails; the real guard scans only `test/gate-b/*.ts`, not `fixtures/`); `enrollment-journey.test.ts` — runs the `enrollment` journey (skips unless the loopback classpath variable is set; fails under `VELES_LOOPBACK_REQUIRED=1` when missing); `records-journey.test.ts` — runs `protected-records`, asserting expiry after `advance` without any real delay.
- [ ] **Step 2: Run** `cd web-extension && npx vitest run test/gate-b/import-guard.test.ts test/gate-b/enrollment-journey.test.ts test/gate-b/records-journey.test.ts` with `VELES_LOOPBACK_REQUIRED=1` (the import guard runs alone through vitest; the journey tests are exercised through `./gradlew sessionLoopbackCheck` once Step 6 adds them to its command line) — expect FAIL (the import guard has no client files to scan; the journey tests, run through `./gradlew sessionLoopbackCheck` so the classpath exists, have no runner yet).
- [ ] **Step 3: Implement** the transport glue over the child JVM's stdio and `MemoryTrust` (never importing `src/pairing/link.ts`, `src/trust/repository.ts`, `src/trust/storage.ts`), the journey runners and the import guard (transitive closure; fallback to an allow-list with `rust-wasm/pkg` only if the closure cannot be made clean, recording the divergence in the guard test's header comment).
- [ ] **Step 4: Implement `GateBLoopbackMain`** (the loopback engine's application handler is the real `GateBPullRouter` over the fixture; with a `LoopbackProbeSessions : ProbeSessions` adapter over the loopback engine's `sendApplication`; `revoke` removes the stand-in peer record so reconnect is rejected) by reusing the shared loopback helpers: session mode with a seed-derived `ACTIVE` record, `GateBFixture` on a manual `ElapsedClock`, scheduler on a manual timer, commands as above; confirm `./gradlew :app:compileReleaseUnitTestKotlin` does not compile it.
- [ ] **Step 5: Wire CI**: add the three test files to `package.json` `test` and the `sessionLoopbackCheck` vitest command; run `./gradlew sessionLoopbackCheck` — expect PASS with both journeys executed (not skipped).
- [ ] **Step 6: Commit** `test(web-extension): run the Gate B journeys with a scripted client against the phone engine`

### Task 8: Matrix, validator and runbook

**Files:**
- Create: `docs/gate-b/matrix.json`, `docs/checks/check_gate_b_matrix.py`, `docs/checks/gate_b_timing.py`, `docs/checks/test_check_gate_b_matrix.py`, `docs/otp-12-gate-b-runbook.md`, `protocol/checks/gate-b-matrix-journeys.test.mjs`
- Modify: `docs/AGENTS.md` (one line), `protocol/checks/check.mjs` (named list)

**Interfaces:**
- Produces: all Python files start with `from __future__ import annotations` (Python 3.8+); `check_gate_b_matrix.validate(matrix: dict, journey_ids: set) -> list` (findings); `docs/checks/gate_b_timing.py` with pure functions over integer seconds: `twenty_minute_ok(minute0, arrival, task_removal, unlock, pull, delay_seconds, foreground_present) -> bool` and `background_ok(start, end, event, copy, other_tab_focused, exemption_active) -> bool`. Row fields: `id, platform, phones, case, journeys, status, blocker, no_bond_handle_or_hint_reliance`; non-`NOT RUN` rows add `evidence` with `source_revision, evaluator_revision, hashes, identities, models, os_builds, chrome_versions, adapters, focused_tab, exemption_absent, actions, bindings, return_codes, expected, actual, classification, cleanup, raw_captures, checksum_catalog, repetitions, reproducibility, no_plaintext_fallback, host_process_queue, redacted_logs, provenance_anchor, adjudication`; case extras `forced_trace, tabs_observations, badge_clear, port_generation_invalidated, old_port_closed_no_host, repull_retained_expired, fresh_keys, fresh_handles, no_host_process, clipboard_transition`

- [ ] **Step 1: Write failing tests** in `test_check_gate_b_matrix.py`: `missing_row_names_the_id`, `unknown_status_rejected`, `initial_state_pin_requires_all_not_run`, `accepted_without_every_pass_rejected`, `nonrun_status_without_captures_rejected` (PASS and FAIL), `pass_requires_no_bond_field_true`, `accepted_requires_decision_reference` (top-level key `decision_reference`), `restart_row_requires_fresh_handles_and_keys`, `reconnect_row_requires_fresh_keys`, `offscreen_row_requires_clipboard_transition`, `freeze_row_requires_retained_expired_result`, `twenty_minute_requires_foreground_present`, `background_requires_other_tab_focused`, `pass_without_adjudication_or_anchor_rejected`, `not_run_with_evidence_rejected`, `freeze_row_without_badge_clear_rejected`, `closure_row_without_no_host_process_rejected`, `arrival_18_54_fails_19_00_passes_21_00_passes_21_01_fails`, `unlock_at_14_minutes_fails`, `pull_at_minute_15_fails`, `delay_not_1200_fails`, `background_needs_minute_seven_event_and_no_exemption`, `output_states_tool_does_not_accept_gate_b`, `runbook_has_section_per_case_and_required_statements`, `committed_matrix_validates` (also loads `protocol/conformance/gate-b-journeys.json` and checks every cited journey id, because a matrix-only edit does not run the protocol node's tests).
- [ ] **Step 2: Run** `python3 -m unittest discover -s docs/checks -p 'test_check_gate_b_matrix.py' -v` — expect FAIL.
- [ ] **Step 3: Write `matrix.json`** from the spec's required row table (row id `<platform>.<phones>.<case>`, `journeys`, `status` `NOT RUN`, `blocker`, `no_bond_handle_or_hint_reliance: null`) and implement the validator (stdlib only, pure functions, never writes a status).
- [ ] **Step 4: Write the runbook** with a heading per row case id, minute-0 definition, reseed step, client eviction note, prerequisite state (OTP-24 physical acceptance not complete; extension artifact recorded as an identity) and the smoke-run rule; add the `docs/AGENTS.md` line.
- [ ] **Step 5: Write `gate-b-matrix-journeys.test.mjs`** (every journey id cited by the matrix exists in the journey file), add it to `check.mjs`.
- [ ] **Step 6: Run** the Step 2 command, `python3 -m unittest discover -s docs/checks -p 'test_*.py'`, `python3 docs/checks/check_agent_files.py --root .` and `./gradlew protocolCheck` — expect PASS.
- [ ] **Step 7: Commit** `docs(gate-b): add the not-accepted evidence matrix, validator and runbook`

### Task 9: Real-environment verification (non-evidentiary)

**Files:** none (results go in the PR note and `apply.md`)

- [ ] **Step 1:** Build and install the debug APK (compare the signing certificate first per `android/AGENTS.md`); with adb, run seed with staggered lifetimes, reseed, schedule a 30 s push, disarm, adb `PUSH` while armed, kill the process (unarmed after restart), and stop the sharing service with a push pending (`cancelled` line, no push, detach before host shutdown). Record the log lines.
- [ ] **Step 2:** On a real phone with a real desktop Chrome and the development-channel extension, run `velesDebug.pullAll()` against the seeded fixture and receive a short scheduled push; report the observed events.
- [ ] **Step 3:** On a real phone, schedule a 1200 s push, remove the task, lock the screen for the whole run, record when the push arrives against the 19..21 minute window.
- [ ] **Step 4 (only if Step 3 misses the window):** implement the AlarmManager timer behind `StepTimer`/the timer interface with the debug-manifest `SCHEDULE_EXACT_ALARM`, check the merged release manifest and rerun `releaseHookGuard`, add alarm-timer unit tests and the runbook grant step, then repeat Step 3 and Task 10.
- [ ] **Step 5:** State in the PR note what ran and on what; if a phone or Chrome was unavailable, say so and ask — never report the change as tested.

### Task 10: Pre-push checks

- [ ] **Step 1:** Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and run the checks listed in each selected node's `AGENTS.md` (Android: `cd android && ./gradlew spotlessCheck detekt lintDebug`, the unit/guard tasks and the raw `android.util.Log` grep guard from `.agents/rules/android-lint-before-push.md`; web-extension: `format:check`, `lint`, `typecheck`, `npm test`, `npm run test:bundle`; protocol: `./gradlew protocolCheck`; docs: the unittest and agent-file commands). Fix failures.
- [ ] **Step 2:** Run `openspec validate --all` — expect valid.
- [ ] **Step 3: Commit** any fixes `fix(gate-b): address pre-push check findings`.
