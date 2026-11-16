## Context

OTP-12 (issue #94) is the Gate B task. The user scoped this change to repository tooling: the
physical Windows/macOS runs, their evidence and the Gate B decision are a separate owner step.
`brainstorm.md` records the code observations this design relies on: the one-event
`SyntheticProbeHandler`, `CompositeApplicationHandler` calling every matching child, the
`SharingService` foreground service with a private scope, `SessionHost` with only
`detachRevocation()`/`detachPairing()` teardown, the child-JVM loopback with separate `session` and
`pairing` modes, and the existing matrix conventions. Further facts checked while designing:

- `PhoneLoopbackMain` lives in the shared `android/app/src/test` source set (compiled for debug and
  release unit tests) while the fixture will live in `src/debug`; a `src/testDebug` source set
  exists and is on the loopback classpath (`testDebugUnitTest`).
- `web-extension/src/debug/probe.ts` decodes `pull_events` strictly as exactly one event and acks
  one; no extension code decodes 0..5 events.
- `PairingLink` (`src/pairing/link.ts`) imports types from `src/connector/*` and uses
  `deviceHandle`/`connectionHandle`; `src/trust/storage.ts` uses `chrome.storage`.
- `protocol/checks/check.mjs`, `web-extension/package.json` `test` and root `sessionLoopbackCheck`
  list test files by name; `docs/checks` is the Python stdlib check area of the non-release `docs`
  CI node, discovered with `python3 -m unittest discover -s docs/checks`.

The protocol allows `pull_events` with 0..5 events and `remaining_lifetime` 0..600 (never sent as 0
here), so no protocol change is needed. Everything new is test infrastructure. The only production
source edit is one no-op call in `SharingService.shutdownSessions()` (D5).

## Goals / Non-Goals

**Goals:**
- A debug-only Android fixture serving at most five synthetic records with per-record expiry and a
  scheduled push that fires about 20 minutes later on a locked, task-removed phone.
- A client-agnostic journey file and a scripted client check, so a watch client or the OTP-27
  virtual link can reuse the same checks.
- A development-channel connector debug path that can decode and ack up to five events, so the
  fixture is observable in a real Chrome.
- A committed evidence matrix, validator and runbook that make the later physical runs checkable
  and keep Gate B visibly not accepted.

**Non-Goals:**
- Running the Windows/macOS/phone scenarios, producing Gate B evidence or recording the decision.
- OTP-13's production buffer, real notification data, multi-phone/multi-computer release validation.
- Running the journeys end to end on the OTP-27 virtual BLE link.
- Any protocol, production-extension or release-build behaviour change.

## Decisions

**D1. A separate fixture component in the debug `debugprobe` package.** `GateBFixture` holds the
history, `GateBPushScheduler` the schedule, `GateBPullRouter` the handler. Alternative: widen
`SyntheticProbeHandler`; rejected because the `synthetic-session-probe` spec forbids it as the Gate B
fixture and its tests assume one event. The package prefix is already in `releaseHookForbidden`; any
new string outside the prefixes (`me/nagaev/veles/debugprobe`, `me.nagaev.veles.debug.`), including
the wake-lock tag, is added to `releaseHookForbidden` in `android/app/build.gradle.kts` and to
`ReleaseGuardStringsTest` together. The debug manifest gains `WAKE_LOCK` and intent-filter entries
for each new adb action.

**D2. One responder per pull via a router, not an armed flag read twice.** `engine.handles()` and
`CompositeApplicationHandler` each evaluate `handles(type)`, so a flag read in `handles()` could flip
between the two and end the session with a `SessionReject`. `GateBPullRouter` replaces the probe in
the debug composition: it always `handles` `pull_request` and `event_ack`, and each `handle` call
reads the shared mode once (`probe` or `fixture`) and dispatches to the probe handler or the
fixture. Mode is `probe` until a seed arms it; a disarm command and the detach hook return it to
`probe`. `event_ack` goes to the component that owns the id (an ack for a fixture-pushed event is logged by the fixture); an unknown id is the OTP2-REC-032a
no-op, and the installation id check is the probe's, so the fixture uses the same installation id.
The router needs the same identity as the probe, so it is absent when the identity is unavailable,
as the probe is today. While armed, adb `PUSH` sends nothing and logs that the fixture owns pushes;
adb `PUSH` is unchanged when not armed.

**D3. Seed and expiry.** `GATE_B_SEED` creates exactly five records with fresh random markers
(merchant `Veles synthetic`, currency `XTS`, amount 100 scale 2), a fixed event id and receipt epoch
(seed wall time) per record, and a lifetime from a comma-list argument (default five 600s values;
each 1..600; a list that is not exactly five in range is refused and changes nothing). Expiry is seed
elapsed-realtime plus lifetime. On each pull the fixture re-encodes every unexpired record with the
same event id and `remaining_lifetime` = floor(remaining seconds), at least 1; expired records are
omitted; an empty set is `event_count = 0`. `createSyntheticEvent` gains a lifetime parameter whose
default keeps the probe at 600. A seed replaces only the history; it never touches a scheduled push.
`event_ack` is logged only and never deletes a record, so a re-pull after freeze/discard still sees
unexpired records. Memory only; a process restart returns to `probe`. A seed with no running service
is refused with a log line.

**D4. Push scheduler.** `GATE_B_SCHEDULE_PUSH` takes a delay in seconds and requires an armed
fixture. The deadline is `elapsedRealtime + delay`. Coroutine `delay` does not count deep sleep, so
the scheduler sleeps in short injected steps (default 1s) and re-checks the deadline against elapsed
realtime. A partial wake lock (the application context is stored in `DebugProbeRuntime` by
`applicationHandler(context)`) is held while a push is scheduled, released when the push fires, is
skipped or cancelled and on detach, with a timed acquire of delay plus 60s as a backstop. The
scheduler's scope is created by the fixture itself in `attach(host)`. At fire time it sends one fresh
synthetic event as `push_event` to each currently open `full` session through the same send path as
the existing `PUSH`; with none open it records `skipped`. Clock, timer and wake lock are interfaces;
tests advance them by hand and never sleep. **Fallback decided now:** if the real-phone step shows the
wake lock does not hold the 19..21 minute window, the timer implementation behind the interface is
swapped for `AlarmManager.setExactAndAllowWhileIdle` with a runtime-registered receiver and
`SCHEDULE_EXACT_ALARM` (declared in the debug manifest and added to the guard lists if it is a new string) granted over adb; the interface keeps that change local and the plan carries it
as a conditional task.

**D5. Host-detach hook and cancellation.** Both `BuildSessionExtensions` objects (`src/debug`,
`src/release`; there is no main copy) gain a `detach()` function (release: no-op). `SharingService`
`shutdownSessions()` in the main source set calls it as its first statement, **before** `host.shutdown()` (which
currently runs ahead of `detachRevocation()`), so a push cannot fire against a host that is shutting down. The debug version
returns to `probe`, cancels the schedule, releases the wake lock and clears `DebugProbeRuntime.host`.
A schedule is cancelled only by detach, disarm, an explicit cancel command or a new schedule command;
a protected session closing, a reseed and a revocation never cancel it (with no full session open at
fire time it is logged `skipped`). Revocation is deliberately not a cancel trigger: it would need a
new production hook, and `skipped` already records the outcome.

**D6. Outcome log.** One bounded line per outcome under the existing `VelesProbe` tag: `seeded`,
`scheduled` (delay, deadline, process id), `fired` (per destination session prefix), `skipped`,
`cancelled` (reason), `disarmed`, `expired-omitted` (count), each with an elapsed-realtime timestamp
and only the marker and event id, never a key or full installation id. The matrix cites these lines
and the process id to show the same process survived task removal.

**D7. Journey file in `protocol/conformance/gate-b-journeys.json`.** Existing conformance files map
fixture state ids to expected engine effects and are read by name; a new file with a journey shape
breaks no consumer; its schema and vocabulary test lives in `protocol/checks/` and is added to the named list in `check.mjs`. A journey is an ordered list of protocol-level actions (`enroll`, `reconnect`, `seed`, `pull`,
`schedule_push`, `push`, `ack`, `advance_clock`, `revoke`, `reconnect_expect_rejected`) with expectations
(session class, event count, remaining lifetime ranges, key freshness, rejection). A schema check
forbids desktop vocabulary (tab, port, native host, handle, extension, offscreen, badge) in action
names and parameters. "Hint" appears only as the property that trust does not depend on any
platform hint. Two independent journeys, because the loopback modes and child JVMs are separate:
`enrollment` (pairing mode, which already runs the real reconnect engine against the enrolled record,
so reconnect with fresh keys is checked there) and `protected-records` (a seed-derived in-memory
`ACTIVE` record stands in for an enrolled peer).

**D8. Scripted client and loopback.** The scripted client lives in `web-extension/test/gate-b/`,
drives the production TypeScript client engine and reads the journey file. It uses `PairingExecution`
and `ActivationEngine` from `src/pairing` but not `PairingLink` or `PairingTransport`, and its own in-memory implementation of the trust-repository interface; it never imports `src/trust/repository.ts` (which statically imports `src/trust/storage.ts` and its `chrome.storage` use). **Import guard:** a
test computes the transitive import closure of the scripted client's files and fails if it reaches
`src/connector/**`, `src/background/**`, `src/offscreen*`, `src/content*`, `src/debug/**`,
`src/pairing/link.ts`, `src/trust/storage.ts` or any native-bridge code, or if those files mention
`chrome.`, `deviceHandle` or `connectionHandle`. If the closure cannot be made clean without a
production edit, the guard falls back to a direct-import allow-list (`src/session`, `src/trust`,
`src/pairing` and named test helpers) plus the identifier scan (the allow-list also names `rust-wasm/pkg`, which `src/pairing/core.ts` imports), and the plan records the divergence;
it does not widen scope silently. The protected-records journey runs against a new test-only child
JVM main in `android/app/src/testDebug` (so the debug fixture is on its classpath and release unit
tests do not compile it), reusing helpers from the shared `PhoneLoopbackMain` (whose `private` helpers are loosened to `internal`, a test-source edit). It wires the real
`GateBFixture` on a manual clock, with `advance` and `revoke` commands. The scripted-client tests are
added to `web-extension/package.json` `test` and to the root `sessionLoopbackCheck` task, so they do
not skip in CI. Alternative: a new Rust or Kotlin client; rejected as larger than the need.

**D9. Debug connector multi-event path.** `web-extension/src/debug/probe.ts` gains a development-only
`pullAll()` that decodes `event_count` 0..5 strictly per §8.1 and acks each event; the existing
one-event `pull()` is unchanged. The probe's per-session handler picks the decoder from the pending request kind (`pull` vs `pullAll`), so `pull()` keeps today's exactly-one rule and `pullAll()` accepts 0..5. It sits behind the existing development-channel guard, and
`bundle.test.ts` and the release guard cover it. This makes the fixture observable in a real Chrome
(smoke run and the later Gate B run) without touching production extension code.

**D10. Matrix, validator and runbook (docs node).** `docs/gate-b/matrix.json`,
`docs/checks/check_gate_b_matrix.py` with `docs/checks/test_check_gate_b_matrix.py`, and
`docs/otp-12-gate-b-runbook.md`. Python stdlib follows the OTP-24 convention and keeps the files in
the non-release `docs` node, so later evidence commits do not count toward release nodes
(`protocol/` is in the Android/web-extension release paths); `docs/AGENTS.md` gains a line for it.
Rows are platform (`windows`, `macos`, `android`) × case × `phones` (`none`/`A`/`AB`), status
`NOT RUN` with a blocker, and carry a `journeys` list linking the row to journey ids (the validator test reads `protocol/conformance/gate-b-journeys.json` and a protocol-conformance test asserts every cited journey id exists, so an edit to either file is caught; each row also has a `no_bond_handle_or_hint_reliance` field) so the desktop
run is checked against the same steps. The committed state is `decision: OPEN`,
`gate_b_accepted: false`. **Required cases** (the validator holds this list and a test fails when
one is missing): host-missing recovery; host-incompatible recovery; extension-owned search; SPAKE2
pairing; hint-based reconnect with fresh keys; protected pull; push; background native event
delivery (ten continuous minutes, another ordinary tab focused, no audio/WebRTC/throttling
exemption, protected event and offscreen copy after minute seven); offscreen copy; forced freeze;
forced discard; connector closure; connector restart (fresh handles and keys); host closure; host
restart; revocation; Bluetooth interruption; two-phone lifecycle/permission/radio recovery; and on
`android`: the locked-phone 20-minute scenario and the five-record/ten-minute fixture retained and
expired. Freeze and discard rows additionally require the forced-trace, `chrome.tabs` observations,
the immediate per-tab badge/status clear, old-port-generation invalidation before queued events,
old-port closure with no host process, and the fresh-host re-pull showing unexpired records present
and expired absent. Every row requires no plaintext fallback, repetitions and a reproducibility
classification. For any non-`NOT RUN` status the validator requires: source/evaluator revisions and
hashes, executable/package/signing identities, phone/desktop models, OS builds, Chrome versions,
adapters, focused-tab identity, controlled actions with controller timestamps, phone/source
bindings, return codes, expected and actual results, classification, cleanup, raw-capture and
checksum-catalog references and a separate adjudication reference. `PASS` additionally requires the
adjudication. **Timing rule:** minute 0 is the controller-clock time of the phone's `scheduled`
log line for a push scheduled with a 1200 s delay (the log line carries the phone's elapsed
realtime; the controller records its own clock when it reads the line, and the row records the
offset method). Arrival is the controller's receipt time of the `push_event`. PASS needs arrival
within minute 19 to minute 21 inclusive; the phone locked for at least 15 minutes after task removal
with the foreground indication present throughout; and a connector pull after minute 15. The rule
functions are pure over supplied timestamps. The tool never writes `PASS`, a validator pass is
necessary, not sufficient, and the external provenance anchor and adjudication are required fields
it checks but does not perform.

**D11. Verification of this change.** JVM, Node and Python unit tests carry the logic. The Android
fixture is also run on a real phone or emulator through adb (seed, pull, push, task removal, a
locked-screen wait) and a non-evidentiary smoke run pulls and pushes the fixture through a real
Chrome with the development-channel connector and `pullAll()`. Results are reported as such, never
as Gate B evidence. If a device or Chrome is unavailable, the plan stops and asks.

## Risks / Trade-offs

- [Wake lock plus short sleeps may miss the 19..21 window on some phones] → real-phone run records
  the drift; the decided AlarmManager fallback (D4) and the matrix classification handle a miss.
- [Router dispatch hides which handler answered] → the log records the mode per pull and a unit test
  drives two sessions through composition asserting exactly one reply per pull.
- [The scripted client reuses the extension's engine, so it is not a truly independent client] →
  the transitive import guard and a vocabulary-free journey file are what "client-agnostic" means
  here; an independent watch client is later work.
- [Matrix completeness drifts from the RFC] → the validator holds the required-case list with a
  test that fails when a case is missing.
- [A client keeps at most five events overall, so a push after five pulled records evicts one] → the
  runbook states it so a re-pull observation is not misread.
- [Debug code leaking into release] → `releaseHookGuard` and `ReleaseGuardStringsTest` cover every
  new string; the release extension stub is a no-op and `bundle.test.ts` covers `pullAll`.

## Migration Plan

Additive and debug-only; nothing to migrate. Rollback is reverting the change. The only production
source edit is the no-op `detach()` call in `SharingService.shutdownSessions()` and the release stub.

## Open Questions

- Which extension artifact observes protected pull/push in a signed-package physical run
  (development-channel build with the production host, or a dedicated Gate B build)? The runbook
  records the identity; the later run decides.
- Whether the wake-lock scheduler holds the 19..21 minute window on a locked phone; the real-phone
  step answers it for this change and the Gate B run finally, with the AlarmManager fallback ready.
