## Context

OTP-10 implements the frozen contract in `docs/protocol/veles-v1.md` (§3 pairing, §5 enrollment
and activation, §11.1/§11.2 state tables) on both endpoints. See `brainstorm.md` for the
decisions already taken (approach A, debug-only controls, real core everywhere, three
interruption outcomes) and `proposal.md` for scope.

Current state, verified in the repo:

- **Core:** `rust/veles-crypto` exposes the OTP-03 command API (`docs/protocol/otp-03-api.md`)
  through `VelesCrypto.pairingCommand` (JNI) and `pairing_command` (WASM). The JNI entry points are
  compiled only for `target_os = "android"` (`rust/veles-crypto/src/lib.rs:19`) and `jni` is an
  Android-only target dependency. `VelesCrypto` calls `System.loadLibrary("veles_crypto")`, so
  nothing outside an Android runtime can use it today. The `exchange` command validates the peer
  point *and* computes the shared element in one call.
- **WASM:** `rustWasm` builds `web-extension/rust-wasm/pkg` (generated, git-ignored) and already
  runs in `check-rust` (through `rustTest`) and `build-rust`. The extension source has no WASM
  import, `vite.config.ts` has no WASM handling, and the extension workflows do not run
  `rustWasm`. The manifest CSP already allows `wasm-unsafe-eval`.
- **Session engines (OTP-09):** Kotlin phone engine (`app/.../session/`) and TypeScript client
  engine (`web-extension/src/session/`) carry the session classes, activation record types and
  class policy. They have no recovery key schedule (OTP2-CRY-019f), no enrollment session class
  (`RecordPolicy` rejects `enroll_identity`), and no activation handlers. `SharingService` builds
  the trust repository with a reject-all `ActivationVerifier`, and the release
  `BuildSessionExtensions` has no handler.
- **Trust stores (OTP-04/05):** both carry `pending`, `committed_waiting_ack` /
  `active_activation_pending`, the enrollment transcript `ET`, boot- or connector-scoped pending
  expiry, and injectable clocks. Two behaviors do not yet match the protocol (see D4).
- **CI:** `sessionLoopbackCheck` (part of `protocolCheck`) runs the TS client against the Kotlin
  phone engine in a child JVM started with only `-cp`. `components.json` already lists `rust/` and
  `protocol/` under both `android` and `web-extension`.

Constraints: Kotlin and TypeScript never implement the PAKE; private identity keys never pass
through Rust or WASM; the pairing code and enrollment keys are never logged; release artifacts
contain no pairing controls.

## Goals / Non-Goals

**Goals:**
- Phone and Chrome pair once with a six-digit code and end with trust on both sides (the phone
  `active`, Chrome `active_activation_pending` until its next reconnect upgrades it, which the
  physical run records), the same fingerprint shown, and all code, SPAKE2 and enrollment-key
  state erased. Enrollment captures no platform hint and the automatic hint path reads only
  `active` peers, so the first reconnect is a user-selected connect that upgrades the record and
  captures the hint; the physical run records the second reconnect as automatic.
- Every §3 attempt, rate, timeout and generic-failure rule holds, and every interruption ends in
  resume, expiry, or a new window, never partial OTP access.
- The real Rust core is exercised in JVM tests, extension tests, the CI loopback, instrumented
  tests and the physical runs.
- Both OTP-09 engines gain the enrollment layer, the activation handlers and the recovery key
  schedule.

**Non-Goals:**
- Release pairing UI, peer management, revocation, and re-pairing of a trusted installation
  (OTP-11).
- The watch client, real OTP data, and re-measuring the one-second SPAKE2 bound (a Gate D
  criterion; the physical run may record the observed time).
- Any change to the frozen wire protocol or to the default Android/WASM core builds.
  (Adding the malformed-input limits to `protocol/v1/limits.json` is a `protocol`-component
  change, which bumps every release component; the spec states this.)

## Decisions

### D1. Core wiring: a `host-jni` cargo feature for the JVM, bundled WASM for the extension

**JVM.** Add a non-default cargo feature `host-jni` that compiles the existing JNI exports on
non-Android targets.
- Cargo shape: keep the Android `jni` target dependency as is, add
  `[target.'cfg(not(target_os = "android"))'.dependencies] jni = { version = "=0.22.4", optional = true }`
  and `host-jni = ["dep:jni"]`. The Android build enables no new feature. `Cargo.lock` already
  contains `jni 0.22.4`, so no lockfile change is expected. The first task builds both ways and
  compares the Android `.so` hash to confirm.
- Edit the `#[cfg]` at `lib.rs:19` in place so line numbers do not shift (panic locations are
  embedded in the binary).
- A new Gradle task `rustHostJni` builds the cdylib for the host with the locked toolchain, the
  registry-path remap, `CARGO_PROFILE_RELEASE_PANIC=unwind` and a separate `CARGO_TARGET_DIR`
  (the target directory follows `rustJniConformance`; the remap flags are set explicitly because
  that task sets none). Its output goes under `build/rust/…` so `clean` and `assert-clean.sh`
  already cover it.
- Lint: add a host clippy run with `--features host-jni`, otherwise `rustLintHost` never checks
  the JNI code.
- Loading: `testDebugUnitTest`, `:app:protocolJvmTest` and the loopback child JVM set
  `java.library.path` to that directory. The loopback child is spawned from
  `web-extension/test/session/loopback.test.ts` with `-cp` only, so `sessionLoopbackCheck` passes
  a new environment variable that the spawn turns into `-Djava.library.path=…`.
- Robolectric: a JNI library binds to one class loader per JVM, and Robolectric loads app classes
  in its own sandbox loader, so a real-core JUnit test and a Robolectric test that both
  initialize `VelesCrypto` in one test JVM fail by test order (`UnsatisfiedLinkError: already
  loaded in another classloader`). Hard rule: Robolectric tests (13 classes, including
  `SharingServiceTest`) never initialize `VelesCrypto`. Android components built without
  arguments (`SharingService`, `SessionHost`) resolve the production core lazily, only when a
  pairing window opens or a `pairing_request` arrives. Classes that tests build directly (the
  pairing machine, `PairingExecution`) take a narrow `PairingCore` interface by constructor, and a
  Robolectric test that needs one may pass a fake. This is a class-loader-forced exception that
  only keeps non-pairing tests from loading `VelesCrypto`; it is never evidence of core behavior.
  A guard test fails if `VelesCrypto` initializes under the Robolectric runner. Real-core tests are plain JUnit
  (in their own `Test` task if ordering still bites); real-core evidence never goes through a
  fake.
- Core capacity: the core allows eight executions per loaded library and a leaked handle lives
  300 s, shared by every test in one Gradle test JVM. Tests must always close handles, and the
  pairing test base class asserts none are left open.

Alternatives considered:
- *Run the WASM package inside the JVM.* Needs a JS engine and tests a different binary from the
  one the phone ships.
- *Fakes at the core boundary as evidence of core behavior.* Rejected by the user; it also loses
  real-core vector conformance.

Guard rails, because the core is externally reviewed: the feature is off by default, the crate
diff is `cfg`/manifest only, and the existing `verify-apk-jni.sh` and `rustPackage` file-set
assertions already fail if a host library reaches the APK or package. The host library is a
test-only artifact and is never packaged or uploaded.

**Extension.** Bundle the existing web-target WASM package.
- Vite imports `rust-wasm/pkg/veles_crypto.js` and the `.wasm` as a URL asset. The connector tab
  (where the session engine already runs) initializes it once; the MV3 service worker never loads
  it.
- `rustWasm` must run before every command that resolves the import: `npm run build`, `package`,
  `typecheck`, the Vitest suites and the Playwright browser tests. Source-level tests initialize
  the module from the package bytes, as `rust/veles-crypto/tests/pairing-wasm.mjs` does, and fail
  with a clear message if the package is missing.
- CI jobs that need the Rust toolchain, the `build/rust-tools` cache and `rustWasm` (and, for the
  JVM side, `rustHostJni`):
  - `check-web-extension.yml`: lint (`tsc --noEmit`), unit-tests and browser-tests.
  - `build-web-extension.yml`.
  - `check-protocol.yml`: it has only the toolchain step today, but `protocolCheck` runs the
    loopback and `protocolWebTest`.
  - `check-android.yml` unit-tests: `testDebugUnitTest` needs the host library.
  - `check-rust.yml` `jni-integration`: its `npm ci && npm run build` step needs `rustWasm`, and the
    `assert-clean` step that follows must cover the new output.
  - `verify/test/workflow-contracts.test.mjs` pins the `build-web-extension` command chain, the
    one-owner command inventory and the clean-contract order. `rustWasm` is added as separate
    steps and the suite is updated in the same task.
- Packaging: `package.mjs` walks `dist/`, so the `.wasm` enters the deterministic ZIP with no
  script change. The extension `SHA256SUMS` covers only the ZIP and its sidecar, so no `.wasm` hash
  appears there; reproducibility rests on the existing remapped, locked `rustWasm` build.
  `bundle.test.ts` lists exact hashed file names, so the WASM asset is matched by pattern to avoid
  breaking on every Rust change. `npm test` lists test files one by one, so the new pairing tests
  are added to that list.
- Supply chain: `check-remote-code.mjs` scans source paths only, and `rust.cdx.json` is generated
  with all targets and features, so it already covers the crate. `web-extension.cdx.json` is
  npm-only and will not list the bundled crate; the SBOM docs note this. Docs to update:
  `docs/reproducible-builds.md` (extension no longer free of Gradle wrapping) and the extension
  commands in `CLAUDE.md`.
- Component ownership already covers `rust/` under `web-extension`, so Rust changes bump the
  extension version; `components.py check` confirms no unowned paths.

### D2. Android structure (package `me.nagaev.veles.pairing`, new)

- `PairingWindow`: the single window, injectable monotonic clock, `pairing_id`, and a
  `PairingCode` value that redacts in `toString` and is held only here. The code is drawn from
  `SecureRandom` by rejection sampling so all of `000000`–`999999` are uniform.
- `PairingAdmission`: attempt budget per `pairing_id`, rolling 5-minute and `pA` 1-minute
  windows, one-in-flight slot, malformed-input and request-rate limits. Charging is one atomic
  call. Because the core's `exchange` validates the point and computes in one call, the
  required order is fixed here: check framing and `pairing_id`, validate `pB` with
  `P256.requireValidPoint`, charge the attempt atomically, and only then call `exchange`. A
  malformed point is rejected before charging; a valid one is always charged first. The
  malformed-input and request-rate limit values are not in `limits.json`, so the spec chooses
  and states them.
- `PhonePairingMachine`: the §11.1 table as an explicit state type, with the 30 s inactivity
  and window-deadline checks before every password-sensitive step.
- `PairingExecution`: owns one core handle with a `use`-style close on every exit path. The
  window narrows the core's capacity of eight.
- `EnrollmentSession`: a new session class and record layer inside the pairing machine. It
  reuses `RecordCodec.open/seal` with the enrollment keys and nonces (OTP2-CRY-007c), the
  enrollment session-id prefix (OTP2-CRY-020a) and the allowed record types, including
  `app_heartbeat` and `app_error`. `RecordPolicy` keeps rejecting `enroll_identity` in the
  reconnect engine. The enrollment connection ends when activation completes and never carries
  OTP data.
- `EnrollmentSchedule`, `EnrollmentRecords`: the §5.1 HKDF schedule, `enroll_identity` codec,
  `ET` derivation, and two distinct fingerprints: the per-record fingerprint checked before
  storing (OTP2-REC-011a) and the shared pair fingerprint shown as `XXXX XXXX XXXX`
  (OTP2-REC-011), displayed only after both records are authenticated. Keys live in a
  zeroable holder, wiped after activation or on failure.
- Duplicate IDs: `addPending` throws a generic `TrustException` for a held installation ID, so
  the machine checks first and answers with the protected `enrollment_rejected` error
  (OTP2-STA-012).
- `ActivationEngine` and the verifier: prepare, commit, `activation_complete` and
  `activation_ack` over the OTP-04 repository. The repository's `ActivationVerifier` is fixed
  when the repository is built, so it consults a *session-bound proof registry* that the
  pairing machine and the recovery path fill with the current MAC inputs and keys; it replaces
  the reject-all stub.
- Routing: `SessionHost` builds a concrete `PhoneSessionEngine` per connection at `onConnected`,
  so it becomes a per-connection dispatcher that picks the pairing machine or the reconnect
  engine from the first frame. `FrameType` is shared with `PhoneSessionEngine.isPreAuthFrame` and
  `PreAuthCodec.encode`, so the pairing frame types (0x01–0x05) live in their own enum with an
  explicit per-machine allow-list, which keeps the reconnect engine's accepted and emitted set
  unchanged. With no open window a `pairing_request` gets the generic rejection, which is the
  release behavior because nothing but the debug controls can open a window.

### D3. Chrome structure (`web-extension/src/pairing/`, new)

- `core.ts`: thin typed wrapper over `pairing_command` that guarantees a close on every exit path,
  including connector tab teardown.
- `client-machine.ts`: the §11.2 table. `enrollment.ts`: schedule, records, `ET`, both
  fingerprints. `activation.ts`: activation over the OTP-05 repository. The enrollment
  connection is not a `SessionManager` session, and `SessionManager`'s verifier returns false for
  the `complete` stage, so the repository verifier here also consults a session-bound proof
  registry filled by the pairing flow and by recovery sessions.
- The connector reuses its extension-owned search and selection. Code entry is the debug panel
  (D5); the product form comes later.
- A native handle or platform hint only helps *find* a phone. Pairing reads the phone's identity
  from the authenticated enrollment record, never from a handle or hint.

### D4. Recovery, activation handlers and the store changes they need

Both engines get the OTP2-CRY-019f recovery activation keys and the OTP2-STA-010b steps 0, 5 and
6: the recovery prepare gate, the active-client ack-loss path, and "recovery class never
upgrades".

- **Key schedule:** `reconnect_prk` is not exposed by `ReconnectKeys` (the PRK accessor is
  `internal`), so the recovery keys are derived inside `derive`. The session class is only fixed
  after `reconnect_confirm`, so both keys are derived whenever the peer record carries `ET`, and
  erased once the class resolves to `full`. Both peer lookups (`PeerRecord` in `PeerLookup.kt`
  and TS `lookupPhone`) are extended to carry `ET`. The 019f vectors already exist in `crypto.json`
  (`k_rec_client`, `k_rec_phone`).
- **Handlers:** the existing handler hooks are too thin. Kotlin's
  `ApplicationHandler.forSession(sessionIdPrefix)` gets no class, peer id, `ET`, `peer_state` or
  recovery keys, and the TS `ApplicationHandler` has no session context. They are extended so an
  activation handler receives that context. Two messages are sent unprompted at session open:
  the phone's step-0 prepare when the client reports `peer_state = 0x02`, and the client's step-5
  ack; both are triggered on `Authenticated` through the existing `sendApplication`. The
  activation handler lives in `main` and is composed with the debug probe handler, rather than
  being added to the release or debug `BuildSessionExtensions`.
- **Chrome store:** `ChromeTrustRepository.authenticated()` rejects `pending` records and
  `SessionManager` terminates the session when it throws, so a recovery session for a still
  pending client always dies. The pending invariant (`validatePeer`) also requires
  `lastSeenMillis === null` and no hint, so a metadata write would fail it too. For a `pending`
  record `authenticated()` therefore runs the verifier, checks `pendingContext` as `confirm()`
  does, and writes nothing: no `lastSeen`, no
  hint, no change to `pendingSince` or `pendingContext`. This is a delta to the
  `reconnect-authentication` metadata requirement (it currently says every successful
  authentication updates `lastSeen`).
- **Starting recovery from Chrome:** `SessionManager.intendedPeer` and the hint path pick only
  active-family peers, so a still-pending client is never chosen. The pairing flow keeps the
  selected phone and starts the recovery session with an explicit installation ID, which
  `intendedPeer` already honors.
- **Verifier wiring:** on Chrome the repository's verifier holder is set to `manager.verifier`,
  which rejects every stage except `phone_active` and `authenticated`; it becomes a combination
  of that verifier and the proof registry for the `complete` stage, not a second repository. On
  the phone only the `SharingService` repository gets the registry; the debug
  `BuildSessionExtensions` and `ProbeReceiver` repositories stay reject-all.
- **Phone store:** `TrustRepository.confirm` throws when the record is `ACTIVE` and the step is
  `COMMIT`. §11.4 requires accepting that commit on `full_pending_peer` as an idempotent
  duplicate (scenario `st-rec-full-pending-peer-commit-duplicate-no-regression`), so after the
  MAC verifies it becomes a no-op.

These changes are specified as deltas to `protected-record-session` and
`reconnect-authentication` (key schedule outputs, successful-authentication metadata, handler
and session context); the records they accept are exactly the types and directions the class
policy allows. Activation MAC comparisons are constant-time on both sides, and the code never
enters Chrome's `DiagnosticRing` or any view-model snapshot.

### D5. Debug harness

- **Cancel semantics (both sides):** cancel follows `cancel_pairing` in the state tables: it
  tears down the enrollment connection, erases code, SPAKE2 and key material, and, when the
  current enrollment already created a `pending` or `committed_waiting_ack` record, deletes that
  record (fixtures `st-en-cancel-cancels-pending`,
  `st-en-client-one-sided-pending-cancelled-then-fresh-pairing`). A duplicate installation ID on
  the client side takes the "persist failure publishes nothing" path.
- **Android:** a `debug` source-set screen shows the code and has Start, Cancel and "Clear
  incomplete record" buttons. Clear covers records that no enrollment owns any more (stranded or
  OTP-09 injected). `PairingDebugHooks` exposes the window state, including the code,
  as an in-process `StateFlow` for instrumented tests only. Start and cancel are also available
  as non-echoing broadcasts for scripted physical runs; "clear" reuses the existing debug
  `REVOKE_PEER` broadcast, which already removes any record. The screen is not
  launcher-visible and no broadcast, log line or notification contains the code.
- **Chrome:** a debug panel in the connector, behind the build-time `__VELES_CHANNEL__` constant
  and a dynamic import, so a production build tree-shakes it. It takes the code and a phone
  selection and shows state and the fingerprint, and has Cancel and Clear controls backed by
  `repository.revoke`. Chrome `addPending` refuses any installation ID it already holds, so the
  physical-run preamble clears injected trust on Chrome as well as on the phone. The existing
  `REVOKE_PEER` broadcast refuses while an authenticated session is open, so the Android clear
  runs with no session open.
- **Release exclusion:** the pairing engines link into release builds (D2), so the guard
  covers only the *debug* surface: the debug-pairing package, actions and screen strings are
  added to the `releaseHookForbidden` list of the Gradle `releaseHookGuard` task that scans the
  R8 output, as OTP-09 did. On the extension side `bundle.test.ts` asserts the production bundle
  has no debug panel text. A grep-style test fails if `PairingCode` is passed to a logger.

### D6. Evidence and test layers

1. Vectors: both endpoints consume `protocol/v1/fixtures/enrollment.json` (39 scenarios) and the
   crypto vectors. Today only `protocol/checks/state.test.mjs` consumes the enrollment fixtures,
   so the ten `deferred:OTP-10` entries in `protocol/conformance/otp-09-scenarios.json` and the
   recovery scenarios in `reconnect.json` are switched to executed with an OTP-10 scenario list.
2. Unit/state: fake clocks cover the window, 86 400 s expiry, rolling limits, no-charge for a
   `pA`-only connection, non-refund, malformed-input limits, wrong code, replayed `pB` and
   replayed recovery records, and interruption at every activation message.
3. CI loopback: extend `sessionLoopbackCheck` with an enrollment scenario using the Kotlin
   phone on the host library and the TS client on WASM in Node.
4. Instrumented: the emulator job has no route to a Node client, so the instrumented test
   drives the pairing machine on the device's real `.so` with a Kotlin test client. The
   cross-endpoint proof is the loopback plus the physical runs.
5. Physical: a phone-to-Chrome run on Windows and macOS, started by clearing injected trust,
   covering pairing, reconnect without a code, and an interrupted activation, recorded in
   `docs/`. The Chrome interrupted-activation run keeps the connector tab open, because losing
   the tab context deletes the client's `pending` record (OTP2-STA-010d) and strands the
   phone's.

### D7. Lifecycle and multi-peer behavior

The window and code live in service-scoped memory only. Cleanup hooks in two places:
per-connection `PairingExecution` teardown in `SessionHost.onDisconnected`, and the window and
code in `SharingService.shutdownSessions()`, which both `onDestroy` and `fail()` call. Process
death or expiry erases the code and closes every core handle. Persisted phone `pending` records
survive and follow the boot-scoped §5.4 expiry; Chrome `pending` records are connector-scoped
and are deleted on context loss. There is one window and one SPAKE2 operation in flight
globally, but each completed pairing is an independent record, so a phone can pair several
desktops one after another. A held installation ID is refused after confirmation with the
protected `enrollment_rejected` error and the window closes (OTP2-STA-012).

## Risks / Trade-offs

- [The `host-jni` feature touches a reviewed crate] → `cfg`/manifest-only diff, default off,
  Android `.so` and WASM compared before and after, and the diff flagged for the core's
  external reviewer.
- [Two state machines drift] → Shared fixtures and scenario IDs; the loopback runs both against
  each other in CI.
- [The change is large] → Task order puts the phone limits first, then the client, activation,
  recovery and the physical runs, each green in CI before the next.
- [Extension build and several CI jobs now depend on the Rust toolchain] → Reuse the cached tool
  install, make the missing-package failure explicit, and list every affected workflow in the
  tasks.
- [Recovery depends on store changes that OTP-04/05 did not need] → Spell them out in D4 with
  scenarios (`st-rec-*`) so they are tested, not assumed.
- [A stranded pending record blocks re-pairing until revocation exists] → The debug clear
  control and the physical-run preamble; the product answer is OTP-11.
- [Memory erasure is best effort in managed runtimes] → Zero owned buffers, avoid copies, claim
  no more than OTP-03 does.
- [The debug code readout leaks the code] → Debug-only, excluded from release by the R8-output
  guard, and the code never appears in logs or broadcasts.

## Migration Plan

No stored-data schema migration: the trust stores already carry the pending states and `ET`.
Two behavior fixes apply to the stores (D4: Chrome `authenticated()` on `pending`, phone
duplicate commit on `ACTIVE`). Release behavior is unchanged in practice: the engines link into
release builds, but nothing can open a pairing window, so every `pairing_request` is rejected
generically. Rollback is reverting the change; the only persisted artifacts are `pending`
records, which expire or are cleared by the debug control. The extension package gains the WASM
file, so its first release after this change contains it.

## Open Questions

- Whether Cargo accepts the different optional-ness of `jni` across the two target tables and
  leaves the Android `.so` hash unchanged: confirmed by the first task with one build each way.
  (Lockfile: no change expected, `jni 0.22.4` is already locked.)
- The concrete values for the malformed-input and request-rate limits, which `limits.json` does
  not define: chosen in the spec and added to the limits file.
- Whether the extension SBOM docs need a sentence about the bundled crate, or a separate
  extension-side WASM entry: settled when running `verify/verify-supply-chain.sh` after the
  extension build change.
