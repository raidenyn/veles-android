## Why

Android and Chrome can hold identities and trust records (OTP-04/05) and authenticate a
reconnect between already-trusted peers (OTP-09), but nothing yet creates that first trust. Real
OTP delivery is blocked on it. The frozen contract (`veles-v1.md` §3, §5, §11) and the pinned
SPAKE2 core (OTP-03) exist; OTP-10 turns them into a one-time, short-code enrollment that cannot
be guessed offline and never grants OTP access after an interrupted activation.

## What Changes

- **Phone pairing window and admission control (Kotlin).**
  - One window at a time, with a random 128-bit `pairing_id` and a uniform six-digit code.
  - The window lasts 300 s on a monotonic clock. The code is held in memory only and is never
    sent, logged or persisted.
  - Attempt and rate limits per §3 and §11.1:
    - five attempts per `pairing_id` and five per rolling five minutes
    - one SPAKE2 operation in flight
    - one fresh `pA` per connection and five per rolling minute
    - separate limits for malformed pre-PAKE input
  - Every valid `pB` is charged before any `pB`-dependent operation and never refunded. Every
    failure before confirmation is the generic `preauth_reject` plus disconnect.
  - Also enforced: the 30 s inactivity timeouts, the window deadline before every
    password-sensitive step, rejection of a second concurrent execution without consuming an
    attempt, and closing core executions on every exit path.
- **SPAKE2 enrollment on both endpoints.**
  - Phone (party A) and Chrome client (party B, TypeScript) drive the OTP-03 core through JNI
    and WASM, with `cA`/`cB` confirmation in both directions.
  - Labeled enrollment-key derivation and encrypted `enroll_identity` records follow, with
    installation ID, `device_type` (`desktop` accepted), identity key and fingerprint.
  - The phone releases its record only after verifying `cB`, and the client only after
    authenticating the phone record. Both identities and the protocol context are bound into
    the enrollment transcript. A native handle or platform hint never substitutes for the
    enrolled identity.
  - A held installation ID is refused after confirmation with the protected
    `enrollment_rejected` error (OTP2-STA-012); re-pairing itself stays with OTP-11.
- **Trust activation and recovery.**
  - Prepare, commit, `activation_complete` and `activation_ack`, with durable `pending` and
    `committed_waiting_ack` records. Expiry follows OTP2-STA-010c/010d (boot-scoped on the
    phone, connector-scoped on Chrome), tested with injected clocks.
  - Activation handlers replace the OTP-09 reject-all stubs (`ActivationVerifier` and the
    reject-all application handler) so activation records are accepted in the classes the protocol allows.
  - Recovery runs in `activation_recovery`, or `full_pending_peer` when one side is already
    `active` (OTP2-STA-010b steps 0, 5, 6). It uses the fresh recovery keys of OTP2-CRY-019f,
    which are new key-schedule work in both OTP-09 engines.
  - Two trust-store fixes recovery needs: the Chrome store must accept authentication of a
    still-`pending` record (verify only, no write), and the phone store must accept a duplicate `commit`
    on an `active` record as a no-op (OTP2-STA-010b, §11.4).
  - There are three interruption outcomes: resume, expire with no partial access, or no record
    at all (interruption after a verified `cB` but before the peer identity was persisted),
    where the only path is a new pairing window.
  - Code, SPAKE2 state and enrollment keys are erased on both sides, and both show the same
    enrollment fingerprint once both records are authenticated.
- **Debug-only pairing controls.** Android Add device (start/cancel, on-screen code) and Chrome
  code entry in the connector, reusing the existing extension-owned phone search and selection.
  Also a control to cancel or clear an incomplete record, because revocation is OTP-11. The code
  is shown on screen and exposed to instrumentation in process, never logged. The controls are
  absent from release APKs and from the production extension bundle, with guard tests.
- **Evidence.**
  - Both endpoints consume the existing OTP-02 enrollment and crypto vectors; new vectors are
    added only where missing (for example the recovery keys).
  - Attempt, expiry, non-refund, no-charge, fresh-`pA` rate, malformed-input limit and
    wrong-code tests, plus interruption and erasure tests.
  - A CI cross-endpoint loopback.
  - A physical phone-to-Chrome pairing and reconnect run on Windows and macOS, starting by
    clearing any injected trust, recorded in `docs/`.

Out of scope: release pairing UI and peer management, re-pairing of a trusted installation
(rejected here, handled by OTP-11), revocation, the watch client (`device_type = watch` stays
in the schema), and real OTP data.

## Capabilities

### New Capabilities
- `pairing-window-admission`: the phone's single pairing window, code and `pairing_id`
  generation, expiry, attempt and rate budgets, charging and no-charge rules, timeouts, core
  execution cleanup, and pre-confirmation generic failure behavior.
- `spake2-enrollment-exchange`: the Android party A and Chrome party B SPAKE2 flows over the
  OTP-03 core, confirmation, enrollment-key derivation, protected `enroll_identity` records and
  their release order, identity and context binding, the duplicate-installation refusal,
  fingerprint derivation and display gating, and sensitive-state erasure.
- `trust-activation-recovery`: durable prepare/commit/ack activation, pending record expiry,
  the recovery key schedule, resume in `activation_recovery` and `full_pending_peer`, the
  no-record case, and the rule that incomplete records authorize no OTP data.
- `pairing-debug-harness`: debug-only Android and Chrome pairing controls, the cancel/clear
  control for incomplete records, the code readout path that never logs the code, and
  release-artifact exclusion of the pairing controls with its own guard tests.

### Modified Capabilities
- `reconnect-authentication`: the key schedule gains the OTP2-CRY-019f recovery keys, the
  successful-authentication metadata requirement is relaxed for a still-`pending` record
  (verify, write nothing), and the handler hooks receive session context.
- `protected-record-session`: the requirement that authorized activation records without a
  handler fail with `protocol_violation` changes, since OTP-10 adds the handlers. Its deferred
  scenario listing and CI loopback scope also change to include the OTP-10 scenarios.

## Impact

- **Android (`app/`):** pairing-window and enrollment state machines in the sharing service
  path, JNI calls to the OTP-03 core, trust-store pending/committed transitions, and a debug
  source set with the Add device control.
- **Web extension (`web-extension/`):** client pairing state machine and WASM calls in the
  connector, activation records in the Chrome store, and a debug-only code-entry panel.
- **Rust core / protocol:** no new core API is expected. The OTP-02 vectors already exist and are
  consumed; any new vectors (such as the recovery keys) are added next to them and covered by
  `protocolCheck`.
- **Core wiring (decided: real core everywhere as evidence):** the extension has no WASM import, build step
  or CI wiring today, and the JNI library loads only on Android ABIs. This change adds a
  host-built native library for JVM tests and the CI loopback, and bundles the WASM package into
  the extension. That means changes to the extension build, ZIP and CI order, component
  versioning (`.github/components.json` already gives `web-extension` ownership of `rust/`, so
  Rust changes bump the extension), and the SBOM and reproducibility checks. The design settles the mechanics.
- **CI and docs:** guard tests in the Android and web-extension checks and a physical-run record
  in `docs/`. Signing and release flows are unchanged.
