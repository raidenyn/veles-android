## Why

Android and Chrome can already find each other over plain GATT (OTP-06/07) and hold durable
installation identities and trust records (OTP-04/05). But nothing yet turns a link between
previously trusted peers into an authenticated, encrypted session. Plain GATT is treated as
an actively attacked channel, so every later task that moves data (pull, push, revocation,
Gate B) is blocked on this layer. The frozen contract (`veles-v1.md` §6/§7) already defines
it. OTP-09 implements it on both endpoints, with no plaintext compatibility path, and proves
it with cross-language vectors and a real phone-to-Chrome synthetic round trip.

## What Changes

- **Correct the OTP-02 reconnect vector (first commit).**
  - The generator and oracle put the identity key into `reconnect_hello` instead of the
    ephemeral key (OTP2-REC-020).
  - Fix both, regenerate the affected fixtures, and add an invariant. The protocol text is
    unchanged.
- **Android phone-role session engine** (Kotlin, JCA, Android Keystore):
  - reconnect hello and the `rbase` transcript
  - P1363/low-S ECDSA (DER conversion)
  - ephemeral ECDH and the HKDF reconnect schedule
  - encrypted `reconnect_confirm`
  - session class selection
  - AES-256-GCM records with exact per-direction sequences
  - terminal `app_error`, teardown with key erasure, and the 180 s idle timeout
- **Chrome client-role session engine** (TypeScript, Web Crypto, in the connector tab).
  - Same scope as the Android engine.
  - Also the `active_activation_pending → active` upgrade on a verified phone-active
    proof.
- **Transport wiring:**
  - Android: a per-connection-generation adapter in `SharingGattServer`/`SharingService`
    with send/close effects.
  - Chrome: one engine per phone handle in the connector.
  - Bounded recovery succeeds only after authentication.
- **The plaintext echo is isolated.**
  - From: debug builds answer every inbound GATT record through `SyntheticTransportFixture`.
  - To: the echo runs only in an explicit, instrumentation-only transport mode that cannot
    run alongside the session engine. Default debug and release paths never send
    plaintext replies.
  - Reason: OTP-09 requires that no plaintext path exists.
  - Impact: the OTP-06 instrumentation must enable that mode explicitly.
- **Debug-only test hooks**, absent from release APKs and from the production extension
  bundle, with guard tests:
  - trust injection on both sides
  - a synthetic §8.1 pull/push/ack probe
  - a connector readout and a matching phone logcat line
- **Evidence:**
  - both engines conform to the committed crypto, record and reconnect vectors and to the
    in-scope state scenarios
  - a CI cross-endpoint loopback test
  - a physical phone ↔ Chrome run on Windows and macOS, recorded in `docs/`

Out of scope: SPAKE2 enrollment and activation handlers (OTP-10); live revocation and the
removal exchange (OTP-11); real OTP data, the buffer, policy and pull/push semantics
(OTP-13/14/15); product UI; Gate B (OTP-12).

## Capabilities

### New Capabilities

- `reconnect-authentication`: the mutually authenticated reconnect handshake between trusted
  peers. It covers:
  - hello encoding and validation
  - the canonical transcript and signatures
  - ECDH/HKDF key schedule and encrypted confirmation
  - trust lookup with the generic failure for unknown or revoked peers
  - session class selection, and the conformance of the reconnect test vectors
- `protected-record-session`: the post-handshake protected session. It covers:
  - the AES-256-GCM record format and AAD
  - exact sequence enforcement and u64 no-wrap
  - record type and class authorization
  - terminal errors, idle timeout, teardown and key erasure
  - fresh keys per reconnect
  - wiring into the Android GATT and Chrome connector transports, with no plaintext path
- `synthetic-session-probe`: debug-only support for demonstrating the session on real
  hardware:
  - trust injection
  - the synthetic pull/push/ack event probe
  - the connector readout and matching logcat line
  - release-artifact exclusion guards

### Modified Capabilities

- None. The existing specs (`ci-pipeline`, `commit-conventions`, `component-versioning`,
  `release-publishing`) are unchanged.

## Impact

- **Protocol package:** `protocol/checks/crypto.mjs`, the Python oracle under
  `protocol/tools/crosscheck-vectors`, and `protocol/v1/fixtures/crypto.json` (regenerated).
- **Android:**
  - new session package (codec, crypto, engine) under `app/src/main/java/me/nagaev/veles/`
  - `sharing/SharingGattServer.kt`, `sharing/SharingService.kt`,
    `sharing/SyntheticTransportFixture.kt`
  - the trust lookup in `trust/`
  - debug-only hook code
- **Chrome extension:**
  - new session module under `web-extension/src/`
  - `connector/controller.ts`, `connector.ts`, `connector/reconnect.ts`
  - `trust/repository.ts` usage
  - development-channel-only hook and readout
  - `test:bundle` guards
- **Tests:** Kotlin unit tests and vitest suites that consume `protocol/v1/fixtures/`; a
  cross-endpoint loopback test; release-artifact guard tests.
- **Docs:** a dated `docs/otp-09-*.md` physical evidence note.
- **No new runtime dependencies.** Platform crypto only. The native bridge, installers,
  Rust core and release workflows are unchanged.
