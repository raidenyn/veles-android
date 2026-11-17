## Design Summary

OTP-12 (issue #94, RFC section OTP-12) is the Gate B task: prove the protected, production-shaped
path with synthetic data on physical Windows and macOS Chrome. Most of it is physical evidence
(signed per-user packages, two OSes, phones, a locked-phone 20-minute run). The user chose the
scope: this change builds only the **repository deliverables** that make those runs possible and
checkable. The physical runs, the evidence they produce and the explicit Gate B decision stay a
separate owner-driven step, and the issue stays open.

Three deliverables, all test infrastructure, no release behaviour change (one no-op call ships in release, see below):

1. **Android Gate B history fixture** (debug source set, package `debugprobe`, so the existing
   `releaseHookGuard` prefix already covers it). A handler that serves a bounded history of at
   most five synthetic records, each with its own expiry of at most ten minutes, over the existing
   protected `pull_request` / `pull_events` path, plus a debug command that schedules one
   synthetic `push_event` for a chosen delay (the minute-20 push) inside the running foreground
   sharing service. It is a new component, separate from the one-event `SyntheticProbeHandler`
   (the `synthetic-session-probe` spec forbids using that probe as the Gate B fixture) and is not
   OTP-13's production buffer.
2. **Client-agnostic scenario set and a scripted non-desktop client check.** A JSON journey file
   under `protocol/conformance/` naming what every client must pass (enrollment, reconnect with
   fresh keys where trust does not depend on any platform hint, protected pull, push, ack, expiry,
   revocation) in protocol terms only, with no Chrome, native-host, tab or handle vocabulary in
   its format. A scripted client built on the TypeScript session, trust and pairing layers
   (no connector, port, native-bridge or extension-page code reached, enforced by an import guard
   test) runs it against the real Kotlin phone engine in the existing child-JVM loopback.
   `PairingLink` and `src/trust/storage.ts` import connector types and `chrome.*`, so the client
   uses the pairing core and activation engine directly and an in-memory trust storage; the guard
   is a transitive closure check with a direct-import allow-list fallback (design D8). The
   loopback's `private` helpers are loosened to `internal` for the test-only main. The desktop
   connector and, later, a watch client or the OTP-27 virtual link are other adapters over the
   same file. Running it end to end on the virtual BLE link is OTP-27 and gates nothing here.
3. **Evidence matrix, validator and runbook.** A committed matrix listing every required RFC case
   for Windows and for macOS plus the Android scenarios, all `NOT RUN`, with a Python validator (docs node) that
   checks structure, required evidence fields per verdict, the timing rules and that a `PASS` row
   cannot exist without raw captures, a checksum catalog and a separate adjudication. A runbook
   documents each scenario's exact steps and what to record. The tooling never writes `PASS` and
   a validator pass is not acceptance, following the OTP-24 matrix convention.

Observed in the code (not assumptions):

- `SyntheticProbeHandler` answers every `pull_request` with exactly one fresh event and is wired
  through `DebugProbeRuntime`; `ProbeReceiver` exposes the adb commands (`EXPORT_IDENTITY`,
  `INJECT_PEER`, `PUSH`, `REVOKE_PEER`). The protocol allows `pull_events` with 0..5 events and
  `remaining_lifetime` ≤ 600 (`veles-v1.md`), so five records and ten minutes fit unchanged.
- `CompositeApplicationHandler` calls **every** child whose `handles(type)` is true, so two
  handlers for `pull_request` would both reply, but one response answers one pull. The fixture
  and the probe therefore cannot both be live for `pull_request`; see D2 in `design.md`.
- `SharingService` is a `connectedDevice` foreground service, `START_STICKY`, with a private
  `sessionScope` that exists only while running. Debug code does not reach that scope: the debug
  `BuildSessionExtensions.attach(host)` receives only the `SessionHost`, `SessionHost` keeps its
  scope private, and the only teardown calls it makes are `detachRevocation()` and
  `detachPairing()`; `DebugProbeRuntime.host` is never cleared today.
- `web-extension/test/session/loopback.test.ts` drives the production TypeScript client engine
  against the Kotlin phone engine in a child JVM, started by Gradle `sessionLoopbackCheck` with
  the extension as working directory. Its session mode fixes one `ACTIVE` trust record and has no
  clock control; enrollment is a separate `pairing` mode. The child JVM must gain a clock-advance
  command and a revoke command for expiry and revocation journeys; this is test-only code.
- `protocol/conformance/*.json` map fixture state ids to expected engine effects and are consumed
  by both Kotlin and TypeScript tests. They are not journeys, so the Gate B file is a different
  shape in the same folder, cross-referencing state ids where one applies.
- Matrix conventions exist (`native-bridge/host/acceptance/matrix.json`, `physical-matrix.py`,
  `docs/otp-10-physical-runs.md`); OTP-24's tooling is Python and defaults rows to `BLOCKED` with
  a blocker. OTP-24 is also not physically accepted (its matrix says so; follow-up #125 is open),
  so the runbook records that prerequisite state rather than assuming it.

## Alternatives Considered

### Alternative A: Fixture, journey file, scripted client and matrix tooling inside existing nodes
- **Approach**: As summarized. Android fixture in the debug source set; journey file in
  `protocol/conformance/`; matrix, Python validator and tests under `docs/` (the non-release docs
  node); scripted client under `web-extension/test/` (the loopback's home) with a test-only loopback
  main in `android/app/src/testDebug`; a development-only multi-event pull in
  `web-extension/src/debug/probe.ts`; runbook under `docs/`.
- **Pros**: Reuses loopback, probe and matrix conventions; no new node, workflow or release
  surface; every piece is unit-testable without a device.
- **Cons**: The real proof still needs a later owner-driven run; the scripted client exercises
  the phone engine, not BLE; the work spans four nodes' checks (android, web-extension, protocol, docs).
- **Why not chosen**: n/a, this is the chosen approach.

### Alternative B: A new standalone `gate-b/` repository node with its own CI
- **Approach**: Python or Rust harness folder with its own `AGENTS.md`, rules, components entry
  and workflow.
- **Pros**: Clean ownership; can hold device drivers.
- **Cons**: A node needs `AGENTS.md`, a rule-check entry, `components.json`, CI and `ci-ok`
  wiring; heavy for a validator and some JSON; the harness still calls code that lives elsewhere.
- **Why not chosen**: Cost far exceeds the value at this scope.

### Alternative C: Extend `SyntheticProbeHandler` to serve five records
- **Approach**: Make the existing probe return up to five events and add a delayed push.
- **Pros**: Least new code, and no double reply.
- **Cons**: Contradicts the `synthetic-session-probe` requirement that the probe must not serve
  as OTP-12's fixture; mixes expiry and scheduling into the OTP-09 one-event contract.
- **Why not chosen**: Violates an existing spec and widens a stable, tested surface.

## Agreed Approach

Alternative A. The user chose the scope (harness, fixture and matrix tooling only; physical runs
and the Gate B decision stay separate) and the `opus` review preset. The remaining design choices
are the assistant's calls, recorded in `design.md`.

## Key Decisions

- **One responder per pull.** The fixture is an armed mode of the debug wiring: unarmed, the
  probe answers `pull_request` exactly as today; armed (after a seed), only the fixture answers.
  `synthetic-session-probe` gets a MODIFIED delta saying the probe answers only while the fixture
  is not armed. Disarm is explicit (adb) and on service restart.
- **Staggered, per-record expiry.** A seed command creates exactly five fresh records, each with
  a lifetime in seconds from a command argument list (default five equal values of 600; each
  1..600). Each record's expiry is seed time plus its lifetime on the elapsed-realtime clock. A pull
  returns only records whose remaining lifetime is at least 1 second, with
  `remaining_lifetime` = floor of the remaining seconds, ≥ 1, ≤ 600; a record at or below 0 is
  omitted, never sent as 0. A runbook step reseeds before the post-minute-15 pull so unexpired
  and expired records can be present together, as the freeze/discard criterion needs. A seed
  replaces the history only and never touches a scheduled push.
- **Push joins nothing.** The scheduled push event is separate from the five-record history: it is
  not stored, not counted against the five on the phone (the client's own global limit of five
  still applies, so a push after five pulled records evicts one on the client; the runbook says so), and `event_ack` for it is logged. `event_ack` for a
  history record only marks the record acknowledged in a log line; records still expire by time
  only, so a re-pull after freeze/discard still sees them.
- **Push survival.** The fixture owns its own coroutine scope, created in `attach(host)` and
  cancelled from a new host-detach hook in the existing teardown path (`shutdownSessions()` in the main source set gains one call as its first statement, before the host is shut down; both
  `BuildSessionExtensions` objects gain the function, the release one as a no-op stub, so release
  behaviour does not change). The same hook clears `DebugProbeRuntime.host`, so a push never
  fires against a dead host. While a push is scheduled the fixture holds a partial wake lock
  (debug manifest only), released when the push fires, is skipped or is cancelled and on detach,
  with a timed acquire as a backstop. The deadline is computed on the elapsed-realtime clock, which counts deep
  sleep; coroutine `delay` does not, so the scheduler wakes in short steps and re-checks the
  deadline against elapsed realtime rather than trusting one long `delay`. The clock and the
  timer are injected interfaces; unit tests advance them by hand and never sleep. Whether this
  keeps the push in the 19..21 minute window on a locked phone is verified on a real phone in
  the plan, not assumed.
- **Cancellation.** A scheduled push is cancelled only by service stop (the detach hook), an
  explicit cancel command, disarm, or a new schedule command; revocation is not a trigger (no full session open at fire time
  is recorded as `skipped`); a protected
  session closing and a reseed do not cancel it, because the Gate B scenario connects, pulls,
  reseeds and closes before the push fires. At fire time it sends to each currently open `full`
  session; with none open it records `skipped`. Scheduling requires an armed fixture. The
  existing adb `PUSH` while armed sends nothing and logs that the fixture owns pushes, so a
  stray probe event cannot pollute a run.
- **Observable outcomes.** The fixture logs seeded, scheduled (with delay and process id), fired
  (per destination), skipped, cancelled and expired-omitted outcomes with elapsed-realtime
  timestamps (marker and event id only, never a key), so a matrix row can classify a failure and
  show the same process survived task removal.
- **Journey file and scripted client.** Steps are protocol-level actions with expectations;
  adapters map actions to a client. A schema check forbids desktop-only vocabulary in action
  names and parameters. "Hint" appears only as the property "trust does not depend on any
  platform hint", matching the RFC's hint-based reconnect with extension-owned reselection.
- **Loopback additions are test-only.** The loopback's session and pairing modes are separate
  and the session mode fixes one `ACTIVE` record. The journey file therefore splits into two independent journeys, each in
  one mode and in its own child JVM: an enrollment journey (pairing mode, which already runs the
  real reconnect engine after activation against the record enrollment wrote, so the
  reconnect-with-fresh-keys check also runs there) and a protected-record journey (session mode,
  where a seed-derived in-memory `ACTIVE` record stands in for an enrolled peer, extended with the
  seeded fixture, a clock-advance command and a revoke command). The two do not share a trust
  record. No test sleeps.
- **Validator.** Python standard library under `docs/checks/`, matrix under `docs/gate-b/`, so it
  follows OTP-24's convention, runs in the existing non-release `docs` CI node and later evidence
  commits do not count toward release nodes. Pure functions over supplied timestamps (no clock, no
  sleep). It carries over OTP-24's rule that a verdict names its source/evaluator revisions,
  hashes and executable/package/signing identities, and that a validator pass is necessary, not
  sufficient (the external provenance anchor and the separate adjudication stay required for any
  `PASS` and are checked as fields, not performed by the tool). It keeps `decision: OPEN` and
  `gate_b_accepted: false`. Rows default to `NOT RUN` with a blocker, because no physical run
  happened.
- **Pull routing.** A router handler replaces the probe in the debug composition and picks probe or
  fixture once per pull, so a mode change between the engine's and the composite's `handles` calls
  cannot end a session.
- **Connector observability.** The development connector's one-event decoder cannot show five
  records, so a development-only `pullAll()` decodes and acks 0..5 events; the one-event path is
  unchanged.
- **Rows.** One row per platform × case × phone set (`phones` field `none`, `A`, `AB`), covering at
  least: missing-host and incompatible-host recovery, extension-owned search, SPAKE2 pairing, hint
  reconnect with fresh keys, protected pull, push, background native event delivery with ten
  continuous minutes and an ordinary tab focused, offscreen copy, forced freeze, forced discard
  (each with trace, `chrome.tabs`, badge/status, queue and host-process fields and the
  retained/expired fixture result), connector closure and restart, host closure and restart,
  revocation, Bluetooth interruption, two-phone lifecycle/permission recovery; plus Android rows
  for the locked-phone 20-minute scenario. Each carries the RFC's required evidence fields.
- **Testing rule.** The Android fixture is run on a real phone or emulator, and a non-evidentiary
  cross-component smoke run (phone fixture pulled and pushed through a real Chrome with the
  development-channel connector, local host) is planned and reported as a smoke run, never as
  Gate B evidence.
- **Release guard.** New strings that fall outside the existing forbidden prefixes
  (`me/nagaev/veles/debugprobe`, `me.nagaev.veles.debug.`) are added to `releaseHookForbidden`
  and to `ReleaseGuardStringsTest` together.

## Open Questions

- Which extension artifact observes protected pull/push in a signed-package physical run (a
  development-channel build with the production host, or a dedicated Gate B build)? The runbook
  records the identity either way; the choice belongs to the later run and is not decided here.
- Whether a partial wake lock plus the elapsed-realtime clock keeps the push within the 19..21
  minute window on a locked, task-removed phone. This is proved by the real-device step of this
  change's plan and finally by the Gate B run; the design does not assume it.
