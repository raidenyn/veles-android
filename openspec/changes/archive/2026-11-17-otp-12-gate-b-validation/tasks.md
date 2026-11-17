## 1. Fixture core (Android debug, TDD, injected clocks)

- [x] 1.1 Add a lifetime parameter (default 600) to `createSyntheticEvent`; keep every existing probe test green
- [x] 1.2 Add `GateBFixture` in `debugprobe`: seed with five lifetimes (validation, refusal on a bad list), fixed event ids, receipt epoch from the seed's wall clock, in-memory only, injected elapsed-realtime and wall clocks
- [x] 1.3 Add the per-pull encoding: unexpired records in seed order with the same event id and floor remaining seconds, omission below one second, `event_count = 0`, `event_ack` logged and never deleting
- [x] 1.4 Add the outcome log vocabulary (seeded, scheduled, fired, skipped, cancelled, disarmed, expired-omitted, refusals) with process id and elapsed-realtime timestamps and no keys or full installation ids
- [x] 1.5 Unit tests for 1.2-1.4: every `gate-b-history-fixture` scenario for seed, bad lists, expiry (0.5 s boundary, all expired, retained and expired together), seed order, ack never deletes, restart returns unarmed, log content; no sleeping

## 2. Router and composition

- [x] 2.1 Add `GateBPullRouter` (mode read once per `handle`, always claims `pull_request` and `event_ack`, ack routing by owner using the probe's installation id, logs which component answered) and a shared mode holder in `DebugProbeRuntime` with a test seam; store the application context in `DebugProbeRuntime` from `applicationHandler(context)`
- [x] 2.2 Wire the router in the debug `BuildSessionExtensions.applicationHandler` in place of the bare probe (absent when the identity is unavailable); make adb `PUSH` send nothing and log while armed
- [x] 2.3 Unit tests: two sessions through `CompositeApplicationHandler` get exactly one reply each, mode flip between the engine's and the router's lookups, ack routing for history/pushed/unknown ids, router absent without identity, unarmed behaviour identical to today's probe tests, adb `PUSH` while armed sends nothing

## 3. Push scheduler and detach

- [x] 3.1 Add the clock, timer and wake-resource interfaces and `GateBPushScheduler`: stepped re-check against elapsed realtime, delay validation 1..7200, refusal while unarmed, backstop delay+60 s, one fresh event per open `full` session, `skipped` when none, replacement and explicit cancel, wake resource released on every exit; pushed events never join the history
- [x] 3.2 Add `detach()` to both `BuildSessionExtensions` (release no-op), call detach as the first statement of `SharingService.shutdownSessions()` before `host.shutdown()` (no other production edit; the order is guarded by a source-order unit test and observed on a device in 9.1), and make the debug version disarm, cancel, release and clear `DebugProbeRuntime.host`; create the scheduler's own scope in `attach(host)`
- [x] 3.3 Unit tests: not before the deadline, fires at the deadline with a hand-advanced clock, delays 0/-1/7201 refused, unarmed refused, skipped, one `fired` line per destination, session close/reseed/revocation do not cancel, explicit cancel and replacement logged, disarm cancels, detach order (source-order guard test), disarm makes the next pull answered by the probe again, wake resource released on fire/skip/cancel/detach

## 4. adb surface, manifest and release guard

- [x] 4.1 Add receiver actions with the `me.nagaev.veles.debug.` prefix so the existing guard prefix covers them: `GATE_B_SEED` (comma-list extra), `GATE_B_DISARM`, `GATE_B_SCHEDULE_PUSH`, `GATE_B_CANCEL_PUSH`; debug-manifest intent filters, shell-only permission, structured result output, and the debug-manifest `WAKE_LOCK`; refuse a seed while no sharing host runs
- [x] 4.2 Unit tests for the receiver actions (parsing, refusals, result output, seed with no host) in `testDebug`
- [x] 4.3 Add any new strings outside the existing forbidden prefixes (the wake-lock tag) to `releaseHookForbidden` and `ReleaseGuardStringsTest`; do not add `WAKE_LOCK`; run the release guard on a release build and check the merged release manifest

## 5. Debug connector multi-event pull

- [x] 5.1 Add development-only `pullAll()` to `web-extension/src/debug/probe.ts`: the per-session handler picks the decoder from the pending request kind; decode the whole record (count 0..5, count matches payload, no duplicate ids) before acking any event, otherwise reject, send no ack and end the session; one-event `pull()` unchanged
- [x] 5.2 Tests in `debug-probe.test.ts`: five events, empty history, malformed count, duplicate id, bound violation, one-event pull unchanged; extend `bundle.test.ts` with a distinctive `pullAll` marker string that must be absent from the production bundle

## 6. Journey file

- [x] 6.1 Write `protocol/conformance/gate-b-journeys.json` with the `enrollment` and `protected-records` journeys, expectations and `trust_independent_of_platform_hint`
- [x] 6.2 Add the schema and vocabulary test in `protocol/checks/` (token matching incl. `transport_mtu` allowed and `tab_id` rejected, action list, hint rule, unknown action), add it to the named list in `check.mjs`, run `./gradlew protocolCheck`

## 7. Scripted client and loopback

- [x] 7.1 Add the test-only child-JVM main in `android/app/src/testDebug` with `seed <lifetimes>`, `schedule <delay>`, `advance <seconds>`, `revoke` on a manual clock and a seed-derived `ACTIVE` record; loosen the needed `private` helpers in `PhoneLoopbackMain.kt` to `internal`; confirm release unit tests do not compile it
- [x] 7.2 Build the scripted client's own transport glue over the child JVM's stdio and its in-memory trust implementation in `web-extension/test/gate-b/` (no `PairingLink`, no `src/trust/repository.ts`)
- [x] 7.3 Implement the `enrollment` journey runner (pairing mode, pairing core and activation engine, reconnect with fresh keys observed as different session key material) and its test
- [x] 7.4 Implement the `protected-records` journey runner (seed, pull, schedule_push plus advance_clock, ack, expiry, revoke, reconnect rejected) and its test
- [x] 7.5 Add the transitive import-guard test with a negative fixture that imports `src/connector/port.ts` and lives outside the scanned set (under a `fixtures/` folder the guard excludes); fallback allow-list (with `rust-wasm/pkg`) only if the closure cannot be made clean, with the divergence recorded in the guard test's header comment and the apply report
- [x] 7.6 Put the import-guard test and the scripted-client tests in the `web-extension/package.json` `test` list (the loopback-dependent ones skip locally without `VELES_LOOPBACK_REQUIRED`, as the existing loopback test does) and in the root `sessionLoopbackCheck` command line, where the variable makes a missing classpath a failure; run `./gradlew sessionLoopbackCheck`

## 8. Matrix, validator and runbook

- [x] 8.1 Write `docs/gate-b/matrix.json` with the required row table, each row's `journeys` and `no_bond_handle_or_hint_reliance`, `initial_state: true`, `decision: OPEN`, `gate_b_accepted: false`, every row `NOT RUN` with a blocker
- [x] 8.2 Write `docs/checks/test_check_gate_b_matrix.py` first, then `docs/checks/check_gate_b_matrix.py` (stdlib only): structure, row set ("Missing row"), status vocabulary ("Unknown status"), initial-state pin, invariants ("Accepted without every PASS"), never-accepts output
- [x] 8.3 Add tests then code for the evidence-field and case-specific-evidence checks and the pure timing functions (20-minute and background rules, all boundary scenarios); no clock, no sleep
- [x] 8.4 Add the protocol-side test in `protocol/checks/` that reads `docs/gate-b/matrix.json` and asserts every cited journey id exists in the journey file (the docs test reads the journey file too), and add it to the named list in `check.mjs`
- [x] 8.5 Write `docs/otp-12-gate-b-runbook.md` (section per case id, minute 0, reseed step, client eviction note, prerequisite state, smoke-run rule) with a test that checks the headings against the row cases and every required statement; add the check line to `docs/AGENTS.md`; run `python3 -m unittest discover -s docs/checks -p 'test_*.py'`, `python3 docs/checks/check_agent_files.py --root .` and `./gradlew protocolCheck`

## 9. Real-environment verification (non-evidentiary)

- [x] 9.1 Build the debug APK; on an emulator or real phone, drive seed, staggered expiry, reseed, schedule, disarm, adb `PUSH` while armed a process kill (unarmed after restart) and stopping the sharing service while a push is pending (cancelled line, no push, detach logged before host shutdown) through adb
- [x] 9.2 On a real phone with a real desktop Chrome and the development-channel connector, run `pullAll()` against the seeded fixture and receive a short scheduled push; an emulator cannot cover the BLE and Chrome part
- [ ] 9.3 On a real phone, schedule a 20-minute push, remove the task, lock the screen and record when it fires
- [ ] 9.4 Only if 9.3 misses the 19..21 minute window: implement the AlarmManager fallback behind the timer interface (debug-manifest `SCHEDULE_EXACT_ALARM`, guard-list decision after checking the merged release manifest and rerunning the release guard, unit tests for the alarm timer, runbook exact-alarm grant step, added only if the fallback is built), then rerun 9.3 and section 10
- [x] 9.5 Record what ran and on what in the PR note; if a device or Chrome is unavailable stop and ask, and never report the change as tested

## 10. Pre-push checks

- [x] 10.1 Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and run the checks listed in the `AGENTS.md` of every selected node (including the Android lint rule and `test:bundle`); fix failures
- [x] 10.2 Run `openspec validate --all` and the agent-file check
