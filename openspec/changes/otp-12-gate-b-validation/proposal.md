## Why

Gate B (OTP-12) must prove the protected transport, background and lifecycle path with synthetic data on physical Windows and macOS Chrome before any real OTP integration. The repository has no five-record expiring fixture, no scheduled push, no client-agnostic journey set and no committed evidence matrix, so those runs cannot start or be judged consistently. This change builds that tooling now so the owner-driven physical runs can follow without ad-hoc harness work.

## What Changes

The scope chosen by the user is tooling only. The physical runs, their evidence and the explicit Gate B decision stay a separate owner-driven step, and issue #94 stays open.

- Add an Android debug-only Gate B history fixture: a seed creates exactly five synthetic records with per-record expiry of at most ten minutes and a pull returns the unexpired ones (up to five), plus a debug command that schedules one synthetic push inside the running foreground service. It is designed to survive task removal and a locked phone; whether it does is verified on a real phone in this change's plan. A reseed or session close does not cancel the push.
- Add a development-channel-only multi-event pull in the web-extension debug probe (decode and ack 0..5 events) so the fixture is observable in a real Chrome.
- Add a client-agnostic journey file (an enrollment journey and a protected-records journey) and a scripted non-desktop client check, import-guarded to the session, trust and pairing layers, that runs against the real phone engine in the child-JVM loopback.
- Add a committed evidence matrix in the `docs` node (every RFC case for Windows and macOS plus the Android scenarios, all `NOT RUN`, committed state `decision: OPEN`, `gate_b_accepted: false`), a Python validator that enforces the required evidence fields and timing rules (a validator pass is necessary, not sufficient; it never writes `PASS`, and a `PASS` needs raw captures, a checksum catalog and a separate adjudication), and a runbook.

**Synthetic probe answering and push**
- From: the debug probe answers every `pull_request` with one event, and the adb `PUSH` command always sends one synthetic event over an open `full` session.
- To: the probe answers `pull_request` only while the Gate B fixture is not armed (armed, only the fixture answers), and the adb `PUSH` sends nothing while armed.
- Reason: one response answers one pull, and the probe must not serve as the Gate B fixture.
- Impact: non-breaking; debug builds only.

**Strict client parsing**
- From: the development connector's pull decodes exactly one event and acks it.
- To: a separate debug-only `pullAll()` also decodes and acks 0..5 events strictly per §8.1, rejecting and ending the session on a bound violation as today; the one-event path is unchanged.
- Reason: the five-record fixture is otherwise unobservable in Chrome.
- Impact: non-breaking; development channel only.

## Capabilities

### New Capabilities
- `gate-b-history-fixture`: the Android debug fixture, its seed, expiry, push scheduling, cancellation and outcome logging.
- `gate-b-journey-harness`: the protocol-level journey file with its two independent journeys, the import-guarded scripted client (session, trust and pairing layers only) and the test-only loopback main.
- `gate-b-evidence-matrix`: the committed matrix, its validator, its required-case list and timing rules, the committed not-accepted state and the runbook.

### Modified Capabilities
- `synthetic-session-probe`: requirements "Synthetic event source", "Phone-initiated synthetic push" and "Strict client parsing and acknowledgement" change as above.

## Impact

- `android/app/src/debug` (fixture, router, scheduler, receiver actions, host-detach hook, manifest wake-lock and intent filters), `android/app/src/release` (no-op `detach()` stub), `android/app/build.gradle.kts` `releaseHookForbidden` and the release guard tests, and `android/app/src/testDebug` (test-only loopback main and unit tests).
- One production-source edit: a no-op `detach()` call in `SharingService.shutdownSessions()` (main source set, ships in release, behaviour unchanged).
- `web-extension/src/debug/probe.ts` (development channel only), `web-extension/test` (scripted client, import guard, bundle guard), `web-extension/package.json` test list and root `build.gradle.kts` `sessionLoopbackCheck`.
- `protocol/conformance` (journey file) and `protocol/checks` (its schema and vocabulary test, added to the named list in `check.mjs`).
- `docs/gate-b/matrix.json`, `docs/checks` (validator and tests), `docs/AGENTS.md`, the runbook under `docs/`. No protocol contract, production extension behaviour or release behaviour changes (commits on `android/` and `web-extension/` still count toward those components' versions). Conditionally, if the real-phone step fails the timing window, the debug manifest gains the exact-alarm permission for the decided fallback.
