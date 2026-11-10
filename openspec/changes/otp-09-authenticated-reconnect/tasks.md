## Conventions for every group

- **Green commits:** every group is one commit, or a small series, that passes the checks
  for what it touches.
  - New Vitest files are registered in `web-extension/package.json` `"test"` in the **same
    commit** that adds them (the script lists files explicitly, and CI runs it).
  - Existing tests or bundle inventories affected by the change are updated in that commit
    too.
- **Commit messages** follow Conventional Commits and are checked with
  `python3 -I scripts/commitlint.py --message "..."`. New tracked files are owned according
  to `python3 -I scripts/components.py check`, which is run once they are staged.
- **Specs and design:** WHAT is in `specs/{reconnect-authentication,protected-record-session,synthetic-session-probe}/spec.md`.
  HOW is in `design.md` D0–D6.

## 1. Correct the OTP-02 reconnect vector (labelled first commit; design D0; spec "Reconnect vectors conform to the protocol")

- [x] 1.1 Add a failing check in `protocol/checks/crypto.mjs` (and the matching contract
  test) that each hello point equals the public key of the ephemeral private key used for
  ECDH. Confirm it fails on the committed `veles-reconnect-authentication`.
- [x] 1.2 Fix `deriveReconnect`/`helloPayload` in `protocol/checks/crypto.mjs` and
  `protocol/tools/crosscheck-vectors/oracle.py` so both hellos encode the ephemeral public
  key.
- [x] 1.3 Regenerate `protocol/v1/fixtures/crypto.json` with the pinned generator
  (`protocol/tools/generate-vectors.mjs`) and audit the diff.
  - **May change:** the hello payloads, `rbase`, `rbase_digest`,
    `phone_sig`/`phone_sig_r`/`phone_sig_s`, `client_sig`, `reconnect_confirm_input`,
    `conf_a`/`conf_b`, the `deviation.value` and inputs of `veles-neg-sig-high-s`, `-der`
    and `-truncated` (derived from `phone_sig`), and the `veles-record-reconnect-confirm`
    body, ciphertext and tag.
  - **Must not change:** `ss`, `reconnect_salt`, `reconnect_prk`, every directional key and
    prefix, `session_id`, and every non-reconnect fixture. Assert this explicitly in the
    audit.
- [x] 1.4 Run `./gradlew protocolCheck` (OpenSSL cross-check, Kotlin, Rust and both
  TypeScript consumers) and fix any consumer that encoded the old hello.
- [x] 1.5 Commit alone as
  `fix(protocol): hello carries ephemeral key in reconnect vectors (OTP2-REC-020)`. The
  body lists the changed expected values and states that `veles-v1.md` is unchanged.

## 2. Android codec and crypto primitives (Kotlin, JCA; design D1; spec reconnect-authentication codec requirements, protected-record-session "Protected record format", "Record rejection")

- [x] 2.1 Create the session package (e.g. `app/src/main/java/me/nagaev/veles/session/`)
  with the §4 pre-auth frame codec: exact `payload_len`, a 4096-byte cap, no trailing bytes.
  Add unit tests.
- [x] 2.2 Implement `reconnect_hello` encode and strict decode (OTP2-REC-020), covering
  capability rules, version, role, and P-256 point validation that rejects off-curve, zero,
  infinity, compressed and wrong-length points. Test with the adapted `veles-neg-point-*`
  values at their malformed lengths.
- [x] 2.3 Implement the signature frame codec (`role u8 || 64-byte P1363`, exactly 65
  bytes), strict DER→P1363 conversion with exact 32-byte `r` and `s`, low-S normalization,
  and strict low-S verification over a single-hashed `rbase`.
  - Fixture signatures are verified, not reproduced.
  - Test `veles-neg-sig-*` rejection.
  - Test that a **double-hashed** signature fails verification.
- [x] 2.4 Implement ephemeral key generation, ECDH (X coordinate; reject an identity or
  zero result), HKDF-SHA256 extract/expand, and the seven OTP2-REC-020b labels. Test
  byte-for-byte against the corrected `veles-reconnect-authentication`.
- [x] 2.5 Implement `reconnect_confirm_input` (64-byte signatures without the role bytes),
  `conf_A`/`conf_B`, and the `reconnect_confirm` body with `peer_state`. Test against the
  corrected `veles-record-reconnect-confirm`.
- [x] 2.6 Implement the §7.1 header/AAD, the prefix‖u64be nonce, AES-256-GCM seal/open, the
  size and `ciphertext_length` checks, the §7.3 type/direction table, and the OTP2-ENC-021
  error codes.
  - Test `veles-record-push-event` byte-for-byte.
  - Test `veles-neg-record-*` rejection.
  - Test per-byte tampering of **both AAD and ciphertext**.
  - Test rejection of an unknown `app_error` code.

## 3. Android phone-role session engine (design D1–D3; spec reconnect-authentication engine requirements, protected-record-session session requirements)

- [x] 3.1 Build the pure state machine (bytes in → effects out), with injected clock,
  randomness, Keystore signer and trust lookup, following the §11.3 phone rows.
- [x] 3.2 Implement trust lookup:
  - a record in state `active`, `pending` or `committed_waiting_ack` gets a reply
  - an unknown or deleted id gets a byte-identical `preauth_reject` and a disconnect, with
    no hello or signature
- [x] 3.3 Implement:
  - the signature order (phone first)
  - the authentication gate: both signatures verified, own confirm sent, peer confirm
    verified
  - class selection over every §11.3 row, with no later upgrade
- [x] 3.4 Implement:
  - exact per-direction sequences
  - the u64 ceiling (2^64−1 accepted once, then terminate)
  - class, type and direction authorization
  - the fail-closed unhandled-record path (`app_error` `protocol_violation`), which also
    covers activation records
- [x] 3.5 Implement:
  - the heartbeat rules: spacing from `limits.json`, the OTP2-REC-034 threshold of 5 in
    60 s as a named constant, and separate counting per direction
  - the 180 s idle timeout across both directions
  - the 30 s handshake deadlines, as a named constant anchored to §11.3
  - terminal `app_error`
  - teardown erasure
- [x] 3.6 Update `lastSeen` only after `session_open`, for the current generation.
- [x] 3.7 Engine unit tests:
  - table-driven class selection, including `full_pending_peer`
  - the authentication-gate scenario where only the peer's confirm has arrived
  - a correctly decrypted confirm with the **wrong HMAC** (ends the session, erases keys)
  - replay, reorder, skip and duplicate
  - early heartbeats ignored vs the fifth heartbeat terminating
  - idle timeout with outbound-only traffic
  - handshake deadlines and late completions
  - an unhandled activation record failing closed
  - input after teardown
  - trust surviving teardown

## 4. Android transport wiring and echo isolation (design D4; spec "Android transport adapter", "No plaintext path")

- [x] 4.1 Extend `SharingGattServer` with a per-connection-generation adapter: connect and
  disconnect callbacks, multiple outbound sends, an ordered send-then-close, and handling of
  send failures. Update the existing `SharingGattServerTest`/`GattTransportBudgetTest`.
- [x] 4.2 Wire `SharingService` to create one engine per admitted connection generation,
  serialize its work, and discard async results from generations that have ended.
- [x] 4.3 Move `SyntheticTransportFixture.reply` behind an explicit, instrumentation-only
  transport mode that cannot run alongside the engine, and update the OTP-06
  instrumentation to enable it.
- [x] 4.4 Tests:
  - link loss during Keystore signing or trust lookup, then reconnect from the same
    address
  - terminal frame ordering
  - `VEL` echo bytes on the normal debug path get no plaintext reply

## 5. Chrome codec and crypto primitives (TypeScript, Web Crypto; mirrors group 2)

- [x] 5.1 Create the session module (e.g. `web-extension/src/session/`) with the §4 frame
  codec and `reconnect_hello` encode and strict decode, including point validation. Test
  with the adapted `veles-neg-point-*` values, and register the tests.
- [x] 5.2 Implement the signature frame codec, `crypto.subtle` ECDSA P-256 signing with
  low-S normalization, and strict low-S P1363 verification.
  - Test fixture signature verification.
  - Test `veles-neg-sig-*` rejection.
  - Test the double-hash failure.
- [x] 5.3 Implement ECDH, HKDF and the seven labels, plus `reconnect_confirm_input`,
  `conf_A`/`conf_B` and the confirm body. Test byte-for-byte against the corrected
  `veles-reconnect-authentication` and `veles-record-reconnect-confirm`.
- [x] 5.4 Implement the §7.1 record seal/open, size checks, the §7.3 table and the
  OTP2-ENC-021 codes.
  - Test `veles-record-push-event`.
  - Test `veles-neg-record-*`.
  - Test per-byte AAD and ciphertext tampering.
  - Test the unknown `app_error` code.

## 6. Chrome client-role session engine (design D1–D3; mirrors group 3)

- [x] 6.1 Build the client state machine following the §11.3 client rows:
  - send the hello first
  - verify the phone's signature against the stored key for the hello's id and the
    selected peer
  - the authentication gate
  - class selection
  - the generic reason-free failure state
- [x] 6.2 Implement the `active_activation_pending → active` upgrade before class
  selection, through `ChromeTrustRepository.confirm(peerId, 'phone_active', proof)` with a
  verifier bound to the session. Send `peer_state = 0x01` for the active family.
- [x] 6.3 Implement sequences, the u64 ceiling, authorization (including fail-closed for
  unhandled records), heartbeat, idle, the handshake deadline, terminal errors and teardown,
  matching the Android engine.
- [x] 6.4 Engine tests:
  - table-driven class selection
  - the mirror-upgrade and no-upgrade scenarios, with persistence across a recreated
    trust repository and engine (connector restart integration is covered in 7.5)
  - the authentication gate
  - the wrong-HMAC confirm
  - heartbeat, idle and deadline timing
  - input after teardown

## 7. Chrome connector wiring (design D4; spec "Chrome serialized session execution", "Chrome recovery succeeds only after authentication", "Successful-authentication metadata")

- [x] 7.1 Add a bounded, serialized per-session queue for inbound records and outbound
  requests:
  - each sequence and nonce is reserved inside the sealing step
  - overflow ends the session with `resource_exhausted`
  - completions with a stale handle or port generation are dropped
- [x] 7.2 Wire `connector/controller.ts`/`connector.ts` to create one engine per phone
  handle, route `record_base64` to it, and close only that phone's link when it terminates.
- [x] 7.3 Change recovery (`connector.ts`, `connector/reconnect.ts`) so an attempt succeeds,
  and renews the allowance, only after the authentication gate opens and the class is
  fixed. An authentication failure consumes the attempt.
- [x] 7.4 Gate `ChromeTrustRepository.authenticated` (`lastSeen` and hint replacement) on
  `session_open` for the current generation.
- [x] 7.5 Tests:
  - delayed crypto with adjacent handshake frames
  - a simultaneous ack and heartbeat getting distinct sequences
  - port replacement while crypto is pending
  - peer isolation
  - repeated BLE success with authentication failure, up to the recovery bound
  - metadata unchanged on failure or stale completion
  - the mirror upgrade surviving a real connector restart
  - the bridge carrying only frames and sealed records

## 8. Debug-only probe, trust injection and channel gating (design D5; spec synthetic-session-probe)

- [ ] 8.1 Android debug: a trust-injection entry point (export identity, install a peer as
  `ACTIVE`), reachable through adb only in debug builds (debug source set or
  `BuildConfig.DEBUG`).
- [ ] 8.2 Android debug: a synthetic application handler (`pull_request` → one §8.1 event:
  marker, `Veles synthetic`, 100 at scale 2, `XTS`, lifetime ≤ 600) and an adb push command
  that does nothing without an open `full` session. Log the marker and event id to logcat,
  in debug only.
- [ ] 8.3 Extension build-time channel gating:
  - a build-time constant selects the development or production channel
  - development-only modules (injection hook, probe, readout) are excluded from the
    production build at compile time
  - a **guard-only production build** writes to its own output directory, using a
    test-only production identity input kept separate from the publishable
    `VELES_RELEASE_CONFIG`
  - the development build stays the one used for physical verification
- [ ] 8.4 Chrome development channel: a trust-injection hook reachable only from trusted
  extension contexts, rejecting web-page and content-script calls.
- [ ] 8.5 Chrome development channel: send `pull_request`, parse the event strictly, send
  `event_ack`, and show a debug readout with the marker and session id prefix. A malformed
  event ends the session.
- [ ] 8.6 Tests:
  - cross-install trust gives class `full`
  - hook rejection for web content
  - pull and push round trips
  - push with no session does nothing
  - malformed event
  - a real notification does not leak into the probe

## 9. Release guards, conformance, loopback (design D5, D6; spec "Release artifacts exclude test hooks", "Vector and scenario conformance", "Cross-endpoint loopback in CI")

- [ ] 9.1 Add the Gradle guard task `releaseHookGuard`:
  - it depends on `:app:assembleRelease`
  - it inspects the minified APK's DEX, manifest and resources (not just ZIP entry names)
    for hook classes, receivers and probe strings
  - it runs in a secret-free job in `check-android.yml` that activates the pinned Rust
    toolchain and installs the `rustNdkVersion` NDK, like the existing JNI job
  - no signing or upload
  - the header comment in `check-android.yml` ("nothing here builds … a release APK") is
    updated to say an unsigned, non-uploaded release build runs only for this guard
- [ ] 9.2 Make `test:bundle` produce its own guard artifact, in the same commit as the new
  checks.
  - The npm script builds the guard-only production build from 8.3 first, then runs the
    bundle tests. Every existing caller therefore stays valid with no workflow edits:
    `check-web-extension.yml:58` and `build-web-extension.yml:36`, whose command strings
    `verify/test/workflow-contracts.test.mjs` pins.
  - The production exclusion checks point at the guard artifact.
  - The existing `dist/` inventory checks keep running against the ordinary build.
- [ ] 9.3 Create the scenario inventory: a checked-in list mapping every
  `records.json`/`reconnect.json` id to one of executed, engine-effect-of-trace-negative,
  or deferred with its owning task (OTP-10/11/13/14/15), following design D6. A test fails
  if any fixture id is missing from the list.
- [ ] 9.4 Kotlin scenario runner that executes the inventory's in-scope ids against the
  phone engine.
- [ ] 9.5 TypeScript scenario runner that executes the inventory's in-scope ids against the
  client engine. Register it in the `"test"` script.
- [ ] 9.6 Loopback endpoint drivers:
  - a JVM phone-engine driver and a Vitest client-engine driver
  - each can produce or consume the exchange for the handshake, pull/pull_events/ack,
    push, heartbeat, `app_error`, and two reconnects
  - recorded transcript vs live pipe is the builder's call
- [ ] 9.7 Loopback orchestration: an aggregate `./gradlew sessionLoopbackCheck` that runs
  both drivers and asserts that the two reconnects have distinct session ids and keys.
  - `protocolCheck` (`build.gradle.kts`, currently an aggregate of explicit dependencies)
    gains a dependency on `sessionLoopbackCheck`, so `check-protocol.yml` runs it unchanged.
  - It reuses the existing `protocolWebInstall` and JVM compilation prerequisites, adding no
    second install and creating no dependency cycle.
- [ ] 9.8 Cross-platform signature tests: Android-signed verified by Chrome and the
  reverse, both 64 bytes with low-S. Run them as part of the loopback drivers.

## 10. Physical verification (homepc, adb; spec "Physical round-trip evidence")

- [ ] 10.1 Build the debug APK and the development extension.
  - Compare the APK's signing-certificate fingerprint with the one installed on the phone.
  - If they differ, the user has authorized a reinstall (2026-11-10) for the OTP-09
    physical run.
  - Record the fingerprints actually read, and whether a reinstall happened.
  - No production signing changes.
- [ ] 10.2 On a real phone with Chrome on Windows, run in order:
  1. inject trust both ways
  2. authenticate
  3. pull and ack
  4. adb push and ack
  5. idle heartbeat
  6. disconnect and reconnect (new session id prefix)
  7. **disconnect, remove the injected trust record on the phone, reconnect**, and verify
     the generic authentication failure with no protected data. This is not a live
     revocation of a connected peer (OTP-11).

  Record the observed result of each step.
- [ ] 10.3 Repeat 10.2 with Chrome on macOS.
- [ ] 10.4 Write the dated `docs/otp-09-*.md` evidence note with safe redaction: commit,
  devices, OS builds, Chrome versions, fingerprints from 10.1, and per-step pass/fail for
  both platforms.

## 11. Gate and PR

- [ ] 11.1 Run `./gradlew spotlessCheck detekt lintDebug`, the raw `android.util.Log` guard
  from `check-android.yml`, `testDebugUnitTest`, `protocolCheck` (including
  `sessionLoopbackCheck`) and `releaseHookGuard`. All must pass.
- [ ] 11.2 Run the web extension `format:check`, `lint`, `typecheck`, `test`, `build` and
  `test:bundle` (which builds the guard artifact itself). All must pass.
- [ ] 11.3 Run:
  - `python3 -I scripts/components.py check` with all new files staged
  - `scripts/commitlint.py` on every commit message and on the PR title and body
  - `node --test verify/test/workflow-contracts.test.mjs`, because the workflows change
  - `openspec validate otp-09-authenticated-reconnect --strict`
- [ ] 11.4 Open one PR with a Conventional Commit title. The D0 commit stays first, and the
  description lists the vector correction, the deferred scenario ids and the evidence note.
