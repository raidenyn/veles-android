## Design Summary

OTP-10 turns a nearby phone and a Chrome connector into mutually trusted peers, once, with a
six-digit code. The wire protocol is already frozen in `docs/protocol/veles-v1.md` (§3 pairing, §5
enrollment and activation, §11.1/§11.2 state tables); this change implements it on both endpoints.

- **Phone (Kotlin):**
  - Pairing-window state machine. At most one window is open at a time. It generates a 128-bit
    `pairing_id` and a uniform six-digit code, and expires after 300 s on a monotonic clock.
  - The code is held only in memory. It is never sent, logged or persisted.
  - Enforces the admission and rate limits: five attempts per `pairing_id`, five per rolling five
    minutes across IDs, one SPAKE2 operation in flight, at most one fresh `pA` per connection and
    five per rolling minute, and separate limits for malformed pre-PAKE input.
  - Every valid `pB` is charged before any `pB`-dependent operation and never refunded. Every
    failure before confirmation is generic (`preauth_reject` plus disconnect). After
    confirmation, a held installation ID is refused with the protected error record `0x0003`
    with code `0x04` `enrollment_rejected` and the window closes (OTP2-STA-012). Admission also enforces the
    30 s inactivity timeouts, the window deadline before each password-sensitive step, and
    rejects a second concurrent execution without consuming an attempt.
  - Core executions are closed on every exit path. The core's own limits (eight executions,
    300 s handle expiry) are a backstop below the window limits.
- **Chrome (TypeScript):**
  - Code entry in the connector tab, using the existing extension-owned phone search and
    selection (OTP-07).
  - The client pairing state machine from §11.2.
- **Exchange:** the OTP-03 Rust core (JNI and WASM) does SPAKE2 and the RFC 9382 `cA`/`cB`
  confirmation. Each endpoint then derives the labeled enrollment keys and exchanges the
  encrypted `enroll_identity` records. The phone sends its record only after verifying `cB`; the
  client sends its own only after authenticating the phone record. Both identities and the full
  protocol context are bound into the enrollment transcript `ET`. Activation runs prepare,
  commit, activation_complete and ack. `pending` and `committed_waiting_ack` records go through
  the OTP-04/05 stores and authorize only the activation exchange, never OTP data.
- **Interruption outcomes** (there are three, not two):
  - Resume: where both endpoints persisted a peer record, the exchange resumes through a signed
    reconnect in class `activation_recovery`, or `full_pending_peer` when one side is already
    `active` (OTP2-STA-010b steps 0, 5 and 6, OTP2-CRY-019a). Recovery uses fresh keys derived
    from `reconnect_prk` (OTP2-CRY-019f). Those keys are new key-schedule work in both OTP-09
    engines, since neither has them today.
  - Expire: a half-persisted `pending` record stays inactive until cancelled or expired.
  - No record: an interruption after a verified `cB` but before the peer identity was persisted
    leaves nothing to recover. The only path is a new pairing window.
- **After pairing:** both sides erase the code, SPAKE2 state and enrollment keys, and display the
  same enrollment fingerprint. Later connects use OTP-09 reconnect with no code.
- **Controls:** the Android "Add device" start/cancel with code display and the Chrome code-entry
  panel are debug-only harness controls, absent from release APKs and from the production
  extension bundle (guard tests, as in OTP-09). Product UI comes later.
- **Evidence:** shared enrollment vectors; attempt, expiry and non-refund tests; connect/get-`pA`/
  disconnect no-charge tests; fresh-`pA` and malformed-input limit tests; wrong-code and
  interruption tests at every activation message; state-erasure tests; a CI cross-endpoint
  loopback test; and a physical phone-to-Chrome pairing and reconnect run on both Windows and
  macOS, recorded in `docs/`.

## Alternatives Considered

### Alternative A: Kotlin and TypeScript state machines over the shared core, one change
- **Approach**: Each endpoint owns its pairing and activation state machine and its storage, and
  calls the OTP-03 Rust core only for SPAKE2. This mirrors how OTP-09 was built. One change
  covers both endpoints and the physical run.
- **Pros**:
  - Matches the RFC rule that Kotlin and TypeScript never implement the PAKE.
  - Private identity keys never pass through Rust or WASM.
  - The OTP-04/05 stores (which already carry the pending states and `ET`) and the OTP-09
    session classes and record plumbing are reused. Only the recovery key schedule and the
    activation handlers are new.
  - Shared vectors keep the two implementations aligned.
  - The physical proof and the interruption tests need both ends anyway.
- **Cons**:
  - The state machines are implemented twice and kept in sync only by vectors and scenario tests.
  - The change is large.
- **Why not chosen**: This is the chosen approach.

### Alternative B: Whole enrollment state machine in the Rust core
- **Approach**: Put the pairing and activation state machine in Rust and expose it through JNI
  and WASM, with the platforms supplying only transport and storage.
- **Pros**: One implementation to test. Less drift between endpoints.
- **Cons**:
  - A much larger core API and external-review surface.
  - Identity-key signing and storage must stay in Android Keystore and non-extractable Web
    Crypto, so activation would still need callbacks across the boundary.
  - It reopens the reviewed OTP-03 scope.
- **Why not chosen**: It widens the reviewed core and fights the key-handling rule, for a saving
  that shared vectors already deliver.

### Alternative C: Android first, Chrome as a separate follow-up change
- **Approach**: Split the work per endpoint into two smaller changes.
- **Pros**: Smaller reviews. Earlier feedback on the phone half.
- **Cons**:
  - The phone half cannot be proven without a client, so interruption and recovery tests would
    use a throwaway client.
  - The physical proof is delayed, and the acceptance criteria are only checkable once both
    exist.
- **Why not chosen**: The acceptance criteria are cross-endpoint, and a split adds throwaway
  harness work.

## Agreed Approach

Alternative A. The implementation order inside the change should still put the phone state
machine and its limits first, then the Chrome client, then activation and recovery, then the
physical runs. That keeps the review boundaries without splitting the change.

## Key Decisions

Decided by the user:
- Pairing controls (Android Add device and code display, Chrome code entry) are debug-only
  harness controls. Polish comes later.
- The enrollment fingerprint display on both endpoints is in scope.
- Re-pairing an already trusted installation is not handled here. The duplicate guard rejects it
  and OTP-11 owns the recovery.
- Multiple peers are supported. Each pairing creates an independent trust record, and one phone
  may pair several desktops. Only one pairing window is open at a time.
- The watch is deferred. `device_type = watch` stays in the schema and is not exercised.
- Evidence includes a physical phone-to-Chrome run on both Windows and macOS, plus a CI loopback
  test.
- Approach A (above).
- The real Rust core is used everywhere as evidence, not faked (the only exception is a narrow
  fake that keeps Robolectric tests from loading the native library): a host-built native library for JVM tests
  and the CI loopback, and the WASM package bundled into the extension. The CI, component-map
  and SBOM work this implies is in scope.

Confirmed against the protocol (`veles-v1.md` §3.2, §11.1):
- After five attempts on one `pairing_id`, the window stays open until its 300 s expiry and the
  sixth attempt gets the generic failure. A verified `cB` or an explicit cancel closes the
  window early. The code is one-use (OTP2-CRY-009a), so any later enrollment failure needs a
  new window.
- A fresh code needs a new window after cancel or expiry. It gets a new `pairing_id` and a new
  budget, and the rolling five-per-five-minutes limit still applies across IDs.

My calls (not yet reviewed by the user; several come from a subagent review that I re-checked
against the repo and protocol):
- The pairing window and code live in memory only and do not survive process death.
- Code readout: a debug-screen text plus an in-process observable for instrumentation. No logcat
  and no broadcast that echoes the code. The on-screen code is not a second exception to RFC
  safe observability beyond the OTP-09 logcat marker. The physical run reads the screen.
- The debug-only surfaces follow OTP-09's pattern of build-type gating with guard tests. The
  harness spec owns its own release-exclusion guard.
- Debug harness includes a "cancel or clear incomplete record" control. Without revocation
  (OTP-11), a stranded `pending` record, or an OTP-09 injected `active` record, makes the
  duplicate guard refuse re-pairing (OTP2-STA-012, line 988 note). The physical-run procedure
  starts by clearing injected trust. This does not pull OTP-11 forward.
- No on-device expiry shortcut. Both stores take injectable monotonic clocks, so the 86 400 s
  rule is tested with a fake clock. The spec cites OTP2-STA-010c/010d (boot-scoped on the phone,
  connector-scoped on Chrome, no client deadline for `active_activation_pending`) instead of
  restating one number.
- SPAKE2 timing is not re-measured here: the one-second bound is a Gate D criterion. The
  physical run may record the observed time.
- The existing OTP-02 vectors (`protocol/v1/fixtures/enrollment.json`, 39 scenarios, plus the
  crypto vectors) are consumed by both endpoints. New vectors are added only where missing,
  e.g. recovery keys (019f).
- Fingerprint display is owned by the exchange capability and must not show before both
  records are authenticated (OTP2-REC-011). The debug UI only renders it.
- Platform-hint capture adds nothing beyond the existing post-`session_open` capture in
  `reconnect-authentication`; OTP-10 only guarantees that a hint or native handle never
  substitutes for the enrolled identity.
- The debug Add device control does not need to enforce an unlocked phone. The RFC's "unlocked
  phone" is a product flow; the product UI will enforce it.

## Open Questions

- **Mechanics of the core wiring** (decided: real core, host-built native library plus bundled
  WASM). The design must settle how the host library is built and loaded in JVM tests and the
  loopback, how the WASM package enters the extension build, ZIP and CI order, how
  `.github/components.json` ownership and versioning change, and what the SBOM and
  reproducibility checks need.
