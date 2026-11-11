## Context

OTP-09 (issue #91, RFC `docs/rfcs/2026-08-21-otp-external-devices.md` §"Authenticated
reconnect", §"Encoding and protected records", task `### OTP-09`) adds the protected session
layer between a phone and a client that already trust each other. The normative wire contract
is `docs/protocol/veles-v1.md`:

| Topic | Section / requirement ids |
|---|---|
| Plaintext pre-auth frames (`frame_type u8, payload_len u16be, payload`, max 4096 bytes) | §4 |
| `reconnect_hello` `0x06`, signature frame `0x07`, `rbase` transcript | §6.1, OTP2-REC-020/020a |
| ECDH + HKDF reconnect schedule (7 labels) | OTP2-REC-020b |
| `reconnect_confirm` input, `conf_A`/`conf_B`, `peer_state` | OTP2-REC-020c, OTP2-SID-001 |
| Teardown key erasure; generic failure for revoked or unknown peers | OTP2-STA-015, OTP2-REV-001 |
| 23-byte header = AAD, nonce = prefix ‖ u64be seq, record ≤ 4096 | §7.1, OTP2-CRY-016a/b/c |
| Exact per-direction sequence, never wraps | §7.2, OTP2-CRY-017 |
| Record type table, `app_error` codes | §7.3, OTP2-ENC-020/021 |
| Synthetic OTP event body | §8.1, OTP2-REC-030/031 |
| Role-explicit session states, authorization classes | §11.3, §11.4 |

**What already exists:**

- **OTP-02 contract package** (`protocol/`):
  - `fixtures/crypto.json`, containing `veles-reconnect-authentication`,
    `veles-record-reconnect-confirm`, `veles-record-push-event`, `veles-neg-sig-high-s`,
    `-der`, `-truncated`, and `veles-neg-record-tag`, `-session`, `-direction`,
    `-sequence`.
  - `fixtures/records.json`: 25 state scenarios.
  - `fixtures/reconnect.json`: 18 state scenarios.
  - `limits.json`, `binary-layouts.json`, `states.json`, and the Node generator plus OpenSSL
    cross-check.
  - Kotlin (`ProtocolFixturesTest`) and TypeScript (`test/protocol-fixtures.test.ts`)
    currently validate the fixtures only, not product code.
- **OTP-04 Android trust** (`app/.../trust/`):
  - `TrustRepository` with `TrustedPeer` and `TrustState`.
  - `AndroidIdentityKeyStore.sign` returns a **JCA DER** `SHA256withECDSA` signature. Its
    comment assigns P1363/low-S normalization to the session codec, which is this task.
- **OTP-05 Chrome trust** (`web-extension/src/trust/`):
  - `ChromeTrustRepository` with an injected `AuthenticationVerifier`.
  - A non-extractable, sign-only P-256 `CryptoKey`. Web Crypto `sign` already returns P1363
    `r‖s`, but **does not guarantee low-S**, so normalization is needed here too.
- **OTP-06 Android transport:**
  - `SharingGattServer` / `SharingService` deliver complete reassembled records.
  - Today `SharingService` answers through the debug-only `SyntheticTransportFixture.reply`
    echo (behind `BuildConfig.DEBUG`).
- **OTP-07 Chrome connector:**
  - `web-extension/src/connector/` covers the port, protocol (`record_base64`), controller,
    state, and recovery (`reconnect.ts`).
  - The extension already distinguishes `development` and `production` channels
    (`manifest.ts`).

Logging must follow RFC "Safe observability": stage, bounded error code and timing only.
Android uses `VelesLog`; Chrome uses `connector/diagnostics.ts`.

## Goals / Non-Goals

**Goals:**

1. A Kotlin phone-role session engine and a TypeScript client-role session engine that
   implement the full §6/§7 handshake and record layer with platform crypto.
2. Fail-closed behaviour for wrong identity, signature, tag, direction, session, sequence,
   type, length or version. An unknown or revoked peer gets the generic `preauth_reject` and
   no protected data.
3. Neither side authenticates before both signatures **and** both confirmations verify. No
   application record is sent or accepted earlier.
4. Every post-handshake record is AES-256-GCM. Traffic keys, prefixes, session id and
   counters are fresh on every connection and erased on teardown.
5. The engines are wired into the real transports:
   - Android: the GATT records of `SharingService`.
   - Chrome: `record_base64` in the connector.
   - A plaintext echo is never served on a production path.
6. Debug-only trust injection plus a synthetic pull/push/ack probe with a visible readout.
   This gives a physical round trip on real phone + Chrome (Windows and macOS through homepc)
   and a reconnect with fresh keys.
7. Evidence:
   - Both engines pass the committed crypto vectors and every in-scope state scenario.
   - Unit tests for AEAD, transcript, replay/reorder, reconnect and malformed input.
   - A synthetic cross-endpoint loopback test in CI.
   - A recorded physical run.

**Non-Goals:**

- SPAKE2 enrollment, identity-record exchange and the activation messages (OTP-10).
- Revoking a currently connected peer and the `removal_request`/`removal_response` exchange
  (OTP-11).
- Real `OtpMessage` data, the OTP buffer, delivery policy, push retransmit semantics
  (OTP-13/14/15).
- Product UI (OTP-16/17), the Gate B five-record fixture and the physical matrix (OTP-12),
  and the watch client and virtual BLE harness (OTP-26/27).
- Any change to the frozen protocol text, or to the fixtures' expected values beyond the
  correction in D0. A new vector may be **added** through
  `protocol/tools/generate-vectors.mjs` only if a gap appears.

## Decisions

### D0. Correct the OTP-02 reconnect vector first (user decision)
- **The defect:** the committed `veles-reconnect-authentication` vector contradicts
  OTP2-REC-020.
  - The generator (`protocol/checks/crypto.mjs` `deriveReconnect`/`helloPayload`) and the
    Python oracle put the **identity** public key into both hello payloads instead of the
    fresh **ephemeral** public key.
  - ECDH still uses the ephemeral keys.
  - So `phone_hello_payload`, `client_hello_payload`, `rbase`, `rbase_digest`, both
    signatures, `reconnect_confirm_input`, `conf_a`/`conf_b`, and every vector derived from
    them (for example the sealed `veles-record-reconnect-confirm` record) are
    non-conforming.
  - `ss`, the salt, the PRK and the directional keys are unaffected.
- **The decision:** fix it as a **clearly labelled first commit of the OTP-09 PR**, ahead of
  any product code, for example
  `fix(protocol): hello carries ephemeral key in reconnect vectors (OTP2-REC-020)`.
  - Correct the generator and the oracle.
  - Regenerate the affected fixtures with the pinned tooling.
  - Add a generator/check invariant that each hello point equals the public key of the
    ephemeral private key used for ECDH.
  - Re-run `./gradlew protocolCheck`, including the OpenSSL cross-check and every consumer.
  - The protocol text is already correct and is not changed (`protocol/README.md`:
    fixtures never override `veles-v1.md`).
  - The commit message and PR description name the defect, the requirement id, and the
    list of changed expected values, so a reviewer can audit the commit on its own.
- No product compatibility behaviour for the old vector is introduced.

### D1. Two platform-crypto engines, held together by the committed vectors
- **Android:** Kotlin + JCA, with `KeyPairGenerator("EC")` for ephemeral keys,
  `KeyAgreement("ECDH")`, HKDF over `Mac("HmacSHA256")`, and `Cipher("AES/GCM/NoPadding")`.
- **Chrome:** TypeScript + `crypto.subtle`.
- **Why:** the RFC requires platform crypto for normal sessions, and identity keys never
  enter Rust/WASM. **Rejected:** a shared Rust core (brainstorm Alternative A).
- **What each engine contains:** hello/frame codec, `rbase` builder, signature codec (DER ↔
  P1363 on Android, low-S normalize and strict verify on both), key schedule,
  confirmation, record seal/open, sequence tracking, and the session state machine.
- **Pure core vs I/O:**
  - The engines are pure state machines: bytes in → effects out (send frame or record,
    authenticated(class), deliver(plaintext record), terminate(code), erase).
  - Time, randomness, identity signing and trust lookup are injected, so the fixtures can
    drive them deterministically (fixed ephemeral keys and nonces taken from the vectors).
- **Single-hash rule (OTP2-REC-020a):** ECDSA is computed over `rbase` exactly once.
  - Android: `SHA256withECDSA`.
  - Chrome: `{name:'ECDSA', hash:'SHA-256'}`.
  - A test proves the signature fails against `SHA-256(rbase)` signed again.
- **Signatures are verified against the vectors, not reproduced** (review finding 8):
  - The fixture signatures are deterministic RFC 6979 (`protocol/v1/fixtures/PROVENANCE.md`).
    JCA and Web Crypto use random nonces, so production signing is never expected to match
    fixture signature bytes.
  - Tests verify committed signatures with the real platform verifier.
  - Deterministic confirmation and key assertions use injected fixture signatures.
  - Real platform signing, DER↔P1363 conversion and low-S normalization are tested
    separately, including Android-signed / Chrome-verified and the reverse.
- **Hello point invariant** (review finding 1): each hello's 65-byte point is the public key
  of the *ephemeral* private key used for ECDH (OTP2-REC-020/020b), never the identity
  key. Both engines assert this in tests.

### D2. Session class selection is complete; only `full` grants anything in OTP-09
- The engines implement every class-selection row of §11.3 (`full`, `full_pending_peer`,
  `activation_recovery`), so the class is always fixed correctly at session start and never
  upgrades.
- The framework records `reconnect_confirm`, `app_heartbeat` and `app_error` work in every
  class.
- In a `full` session the engines authorize `pull_request`, `pull_events`, `push_event`,
  `event_ack`, `phone_policy_state` and `removal_*` by type and direction. Their bodies go to
  an injected application handler.
- In OTP-09 the only application handler is the debug synthetic probe (D5). Release builds
  have **no** application handler. Authorized-but-unhandled records end the session with
  `app_error` (`protocol_violation`), which fails closed.
- Activation records arriving in recovery or `full_pending_peer` classes are authorized by
  class but have no handler until OTP-10, so they also end the session fail-closed.
- **Why:** this keeps OTP-10 and OTP-11 additive (new handlers) rather than requiring a
  rewrite of the state machine.
- **The client-side `active_activation_pending → active` upgrade is in scope and
  unconditional** (OTP2-SID-001, OTP2-STA-010c; review finding 3).
  - It is a reconnect transition, evaluated **before** class selection.
  - The trigger is a verified phone `reconnect_confirm` (AEAD tag, session ID, sequence and
    HMAC all valid) carrying `peer_state = 0x01`.
  - It uses the existing `ChromeTrustRepository.confirm(peerId, 'phone_active', proof)`
    (`web-extension/src/trust/repository.ts:77`), with an `AuthenticationVerifier` bound to
    the current session.
  - Outbound `peer_state` stays `0x01` for the `active_activation_pending` mirror.
  - A phone reporting `0x02` causes no upgrade.

### D3. Trust lookup and the unknown/revoked path
- The phone answers a client hello only if the hello's installation id matches a stored
  trust record (`active`, `pending` or `committed_waiting_ack`). Otherwise it sends the
  generic `preauth_reject` and disconnects: no phone hello, no signature, no data
  (OTP2-REV-001).
- The client verifies the phone's signature against the **stored** public key of the
  installation id in the phone hello. An unknown id, or an id different from the expected
  peer, gets the generic failure.
- Each side records a successful authentication (`lastSeen`, hint replacement) only after
  `session_open`.

### D4. Wiring into the transports
- **Android needs a transport lifecycle/effect adapter** (review finding 4).
  - **The problem:** today `SharingGattServer` exposes only a synchronous
    `(address, record) → ByteArray?` handler (`SharingGattServer.kt:28-32`) that queues at
    most one reply. `disconnect` is private and `remove` notifies no owner
    (`SharingGattServer.kt:500-514`).
  - **The adapter provides:**
    - connect and disconnect callbacks scoped to a connection generation (an address alone
      is not an identity across reconnects)
    - outbound send(record) and close effects, so the engine can send several handshake
      frames plus unsolicited push and heartbeat records
    - explicit handling of enqueue or send failure
    - an ordered terminal policy: send the `preauth_reject` or `app_error`, then close
  - **Per-peer engine work is serialized.** Async work (Keystore signing, trust lookup) is
    tied to its generation and dropped if the link went away. Tests cover link loss during
    signing or trust lookup, and reconnecting from the same address.
- **The plaintext echo is isolated** (review finding 2).
  - Today every debug inbound record reaches `SyntheticTransportFixture.reply`
    (`SharingService.kt:74-76`) with **no** opt-in.
  - OTP-09 moves the echo behind an explicit, instrumentation-only transport mode that
    cannot run alongside the session engine. The default debug and release paths never
    produce plaintext replies.
  - A test sends the echo's `VEL` bytes to the normal debug path and proves there is no
    plaintext reply and the session terminates.
  - The OTP-06 instrumentation enables that mode explicitly.
- **Chrome:**
  - The connector controller creates one client engine per phone handle when the link is
    up, sends the hello, and passes inbound `record_base64` bytes to it.
  - Engine termination closes that phone's link only; other phones are unaffected.
  - The existing bounded recovery (`reconnect.ts`) triggers a **new** engine and session on
    each attempt.
- **Chrome serializes each session's async work** (review r1 finding 1). Today the controller
  dispatches inbound records to synchronous listeners without awaiting them
  (`connector/controller.ts:506-515`), while Web Crypto is asynchronous, and the send FIFO
  orders only ciphertext that already exists. The design therefore requires:
  - **One serialized queue per session.** Each inbound record and each outbound request
    (ack, heartbeat, terminal error) is processed to completion in arrival order before
    the next one starts.
  - **Atomic sequence allocation.** An outbound sequence number, and so its AES-GCM nonce, is
    reserved inside the serialized step that seals the record. Two concurrent sends never
    share or skip a sequence.
  - **A bounded queue.** Overflow ends the session (`resource_exhausted`).
  - **Generation tokens.** Each async completion is checked against the connection handle
    and port generation. A completion that arrives after teardown, link loss or a port
    replacement is dropped and never sends or mutates state.
  - **Tests with artificially delayed crypto:**
    - adjacent handshake frames arriving while the previous verify or derive is pending
    - simultaneous outbound ack and heartbeat
    - disconnect and reconnect while crypto is pending (proves the stale result is
      discarded)
  - The scheduler mechanism (promise chain, explicit queue) is the builder's call.
- **Chrome separates "transport connected" from "authenticated"** (review finding 5).
  - **The problem:** today a recovery attempt counts as successful, and the peer's
    allowance is renewed, as soon as `controller.connect` succeeds (`connector.ts:424-441`,
    `reconnect.ts:304-313`).
  - **The fix:** an attempt succeeds, and renews the allowance, only after both
    confirmations verify and the session class is fixed. An authentication failure
    consumes the attempt.
  - **Tests:** BLE connects repeatedly but signature or confirmation verification fails,
    and a stale async completion after the port is replaced.
- **Shared limits:**
  - The 180 s idle timeout (`reconnect_idle_timeout_seconds`, no valid record in **either**
    direction) and the 30 s heartbeat spacing (`heartbeat_min_interval_seconds`) come from
    `protocol/v1/limits.json`.
  - Two values have no `limits.json` entry, so each becomes one named constant anchored to its
    protocol clause rather than being written as a literal more than once:
    - the 30 s reconnect handshake waits (§11.3)
    - the heartbeat terminating threshold, the 5th heartbeat in a rolling 60 s window per
      direction (OTP2-REC-034)
  - Early heartbeats below that threshold are ignored, not fatal (specs review r1).

### D5. Debug-only test hooks (absent from release)
- **Trust injection:**
  - Android: a debug-source-set or `BuildConfig.DEBUG` adb-reachable entry point exports
    `{installationId, publicKey}` and installs a peer record as `ACTIVE`, bypassing the
    activation verifier.
  - Chrome: a development-channel-only connector hook does the same. It must not be
    reachable from content scripts or web pages.
- **Synthetic probe:**
  - Phone: a debug application handler answers `pull_request` with `pull_events` carrying
    one §8.1 event (code = random marker, merchant `Veles synthetic`, amount 100 scale 2,
    currency `XTS`, remaining lifetime ≤ 600). An adb command sends one `push_event`
    through the open session.
  - Chrome: decrypts, strictly parses and acks the event, then shows it in a debug-only
    connector readout.
  - Phone debug logcat prints the matching marker. This is the only allowed exception to
    the safe-observability rules, and only in debug builds.
- **Release guards:**
  - A unit or bundle test asserts that the release APK (R8 output) and the production
    extension bundle contain none of the hook entry points or strings.
- **Separation:** the probe is not OTP-12's Gate B fixture.
- **Builder's call:** the exact mechanism, for example a debug-only `BroadcastReceiver`
  versus an instrumentation entry point, or a dev-only `chrome.runtime` message versus a
  devtools global.

### D6. Evidence in one PR
- **Vector conformance:**
  - The Kotlin (`testDebugUnitTest`) and TypeScript (`vitest`) product engines consume
    exactly these `crypto.json` fixtures (review r1 finding 2):
    - `veles-reconnect-authentication`: reproduce hello and transcript bytes, ECDH, the HKDF
      schedule and the confirmations byte-for-byte, and verify the fixture signatures (D1)
    - `veles-record-reconnect-confirm` and `veles-record-push-event`: seal and open
      byte-for-byte
    - `veles-neg-sig-high-s`, `-der`, `-truncated` and `veles-neg-record-tag`, `-session`,
      `-direction`, `-sequence`: reject
  - The point-validation negatives (`veles-neg-point-off-curve`, `-zero-coordinates`,
    `-infinity`, `-compressed`) are adapted into reconnect-specific engine tests: an invalid
    hello ephemeral point gets `preauth_reject`. Each fixture's `deviation.value` bytes
    replace the hello's point bytes with their malformed lengths kept: infinity stays the
    single byte `00`, and the compressed form stays 33 bytes. Nothing is padded or
    decompressed.
  - The RFC 9382 anchors and the SPAKE2 pairing and enrollment fixtures and negatives are
    not OTP-09 engine inputs. The whole OTP-02 suite keeps running unchanged through
    `./gradlew protocolCheck`.
  - They also replay every in-scope `records.json`/`reconnect.json` scenario (scope list
    below).
- **Fixture validation and engine behaviour are asserted separately** (review finding 7):
  - Negative trace-validation fixtures (e.g. `st-neg-pull-authorized-while-record-pending`,
    whose stage is `expectation`, and the u64-overflow trace, which cannot be serialized
    to the wire) stay with the OTP-02 fixture runner.
  - The product-engine test asserts the endpoint behaviour those traces imply: termination,
    key erasure, and no application delivery.
- **Out-of-scope scenarios** are listed explicitly in the test, with the task each is
  deferred to. They are not silently skipped:
  - activation recovery and `full_pending_peer` completion → OTP-10
  - revoking a connected peer and removal → OTP-11
  - pull-response and policy settlement → OTP-13/14
  - push retransmit → OTP-15
- **Cross-endpoint loopback (CI, no hardware):**
  - A generated transcript test: the Kotlin phone engine and the TypeScript client engine
    each run against recorded frames from the other.
  - It covers a full handshake, pull/pull_events/ack, push, heartbeat, `app_error`, and two
    reconnects showing different session ids and keys.
  - **Builder's call** whether this is a recorded-transcript exchange or a live
    cross-process pipe, provided it runs in CI and exercises both real engines.
- **Unit coverage:** AEAD (tag, AAD field tampering per byte), transcript, low-S and DER
  conversion, replay/reorder/skip/duplicate, u64 sequence max, idle timeout, key erasure
  after teardown, malformed hello and frame input (lengths, trailing bytes, invalid point,
  capability rules).
- **Physical run:** Claude runs it through the homepc tools and adb on a real phone with
  Chrome on Windows and on macOS. Steps: inject trust → connect → pull/ack → adb push →
  heartbeat idle → disconnect → reconnect (new session id) → revoke the injected record →
  generic failure. The results go to a dated `docs/otp-09-*.md` evidence note with safe
  redaction.
- **Gate:** before the PR, `./gradlew spotlessCheck detekt lintDebug`, the raw `Log`
  guard, `testDebugUnitTest`, `protocolCheck`, and the web-extension format, lint,
  typecheck, test, build and `test:bundle` checks all pass.

**In-scope state scenarios:**
- Every `records.json` scenario **except**:
  - `push-retransmit-drops-after-max` (OTP-15)
  - `blocked-pull-vs-empty-history`, `policy-response-flag-settles-pull` and
    `neg-policy-notice-does-not-settle-pull` (OTP-13/14). Policy-response semantics are
    deferred consistently (review finding 6); the `phone_policy_state` codec, direction and
    class checks stay in scope.
  - `full-pending-peer-commit-duplicate-no-regression` (OTP-10)
- From `reconnect.json`:
  - `revocation-is-generic-preauth-failure`
  - `authentication-without-authorization`
  - `replay-of-prior-session-rejected`
  - `neg-secret-in-persisted-state`
  - `neg-pull-authorized-while-record-pending`, as engine behaviour per finding 7
  - `client-activation-pending-upgrades-on-phone-proof` and
    `client-mirror-durable-across-restart` (review finding 3)
- Selecting the recovery class, and rejecting activation records that have no handler, are
  tested on their own, independently of the deferred handlers.
- The builder re-checks this list against each scenario's `requirement` tags and moves any
  scenario that needs only OTP-09 machinery into scope.

## Risks / Trade-offs

- **[Risk] Kotlin and TypeScript drift on subtle encoding** (DER↔P1363, low-S, point
  validation, HKDF labels).
  → Both must match the committed byte vectors. The CI loopback runs the real engines
  against each other.
- **[Risk] Android Keystore ECDSA returns DER and may produce high-S.**
  → The codec converts strictly (exact 32-byte r and s, reject over-long integers) and
  normalizes to low-S before sending. Verification rejects high-S.
- **[Risk] Debug hooks leak into release.**
  → Build-type and channel gating plus explicit release-artifact guard tests (D5).
- **[Risk] Key material lingers in the JS or JVM heap.**
  → Erase or zero what the platform allows (byte arrays, drop `CryptoKey` references) and
  test that the engine refuses all input after teardown. This is a documented platform
  limit, not a guarantee of memory scrubbing.
- **[Risk] One PR is large to review.**
  → Ordered commits: D0 vector correction (standalone, labelled) → product codec and crypto →
  engines + vector conformance → transport wiring → debug hooks → evidence. Each commit is
  green on its own.
- **[Trade-off] Activation and recovery classes are selected but inert until OTP-10.**
  → They fail closed, so the trade is safe. It means a `pending` record cannot recover in
  OTP-09, which is acceptable because enrollment does not exist yet.
- **[Risk] Physical hardware is flaky (BLE, adapters).**
  → The physical run is evidence, not a CI gate. Failures are classified in the evidence
  note.

## Open Questions

- None blocking. Builder's calls are marked in D2, D5 and D6.
