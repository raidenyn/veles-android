# OTP-09 Authenticated Reconnect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Each plan task maps to one group of
> [`tasks.md`](tasks.md). When a plan task is finished, tick the matching `tasks.md` boxes.

**Goal:** previously trusted Android and Chrome installations establish fresh, mutually
authenticated, AES-256-GCM-protected sessions over the existing GATT and Native Messaging
transport, proven by conformance to the cross-language vectors, a CI loopback, and a physical
synthetic round trip.

**Architecture:** there are two independent platform-crypto engines, held to the same
committed vectors.
- **Phone role:** Kotlin + JCA, in package `me.nagaev.veles.session`. It uses no `android.*`
  imports, so it also runs on the plain JVM.
- **Client role:** TypeScript + Web Crypto, in `web-extension/src/session/`.

Each engine is a pure state machine: bytes in, effects out. Clock, randomness, signer and trust
lookup are injected. Thin hosts connect the engines to the transports:
- Android: a per-connection-generation adapter in `SharingGattServer`.
- Chrome: a serialized queue per phone handle in the connector.

The debug-only probe and trust injection live in build-specific source (the Android `debug`
source set, and the extension's development channel), so release artifacts never compile them.

**Tech Stack:**
- Kotlin, JCA (`EC`, `ECDH`, `SHA256withECDSA`, `HmacSHA256`, `AES/GCM/NoPadding`), Android
  Keystore, JUnit 4, MockK, coroutines
- TypeScript, `crypto.subtle`, Vitest, Vite
- Gradle aggregate tasks
- Node scripts in `protocol/`

**Spec:**
- [`proposal.md`](proposal.md) and [`design.md`](design.md) (D0–D6)
- [`specs/reconnect-authentication/spec.md`](specs/reconnect-authentication/spec.md)
- [`specs/protected-record-session/spec.md`](specs/protected-record-session/spec.md)
- [`specs/synthetic-session-probe/spec.md`](specs/synthetic-session-probe/spec.md)
- Normative wire contract: `docs/protocol/veles-v1.md`, which wins on any conflict.

## Global Constraints

- **Pre-auth frame:** `type u8 || payload_len u16be || payload`. Total ≤ 4096. Types:
  `0x06` `reconnect_hello`, `0x07` `reconnect_signature`, `0x0F` `preauth_reject` (empty
  payload). Every other type is rejected.
- **Hello payload:** `installation_id[16] || ephemeral_pub[65] || nonce[16] || role u8 ||
  protocol_version u16be (=1) || caps_len u16be || caps[1..16]`. Caps are sorted, unique, and
  include `0x01`. Role `0x01` = phone, `0x02` = client.
- **Signature payload:** `role u8 || sig[64]`, exactly 65 bytes. P1363 `r||s`, low-S,
  ECDSA-P256-SHA256 over `rbase` hashed once. The phone sends its signature first.
- **Labels** (exact ASCII):
  - `Veles reconnect transcript v1` (29)
  - `Veles reconnect salt v1` (23)
  - `Veles reconnect B-to-A AES key v1` (33)
  - `Veles reconnect B-to-A nonce v1` (31)
  - `Veles reconnect B-to-A confirmation v1` (38)
  - `Veles reconnect A-to-B AES key v1` (33)
  - `Veles reconnect A-to-B nonce v1` (31)
  - `Veles reconnect A-to-B confirmation v1` (38)
  - `Veles reconnect session ID v1` (29)
  - `Veles reconnect confirmation input v1` (37)
- **Key schedule:**
  - `rbase = transcript_label || phone_hello_payload || client_hello_payload`
  - `salt = SHA-256(salt_label || phone_nonce || client_nonce)`
  - `prk = HKDF-Extract(salt, ss)`, where `ss` = the 32-byte ECDH X coordinate
  - AES keys and confirmation keys are 32 bytes, prefixes 4 bytes, the session id 32 bytes
- **Confirmation:**
  - `confirm_input = SHA-256(confirmation_label || rbase || phone_sig64 || client_sig64)`
  - `conf = HMAC-SHA-256(own_confirmation_key, confirm_input)`
  - It travels in `reconnect_confirm` `0x0001` at seq 0. The body is `conf[32] || peer_state
    u8` (`0x01` active family, `0x02` below active).
- **Record:** a 23-byte header, used whole as the AAD:
  - `protocol_version 0x01`
  - `schema_version 0x01`
  - direction: `0x01` client→phone, `0x02` phone→client
  - `type u16`
  - `seq u64`
  - `ct_len u16` (0..4057)
  - `session_id[0..8]`

  The nonce is `prefix[4] || u64be(seq)`. The 16-byte tag follows. Total ≤ 4096.
- **Direction mapping:** client→phone = B-to-A; phone→client = A-to-B.
- **Record types:** `0x0001` confirm, `0x0002` heartbeat, `0x0003` `app_error`, `0x0100` to
  `0x0104` enrollment/activation, `0x0200`, `0x0202` to `0x0207` application. Directions and
  class allowlists are exactly as in §7.3 and §11.4. `0x0000`, `0x0201` and any unlisted type
  end the session.
- **`app_error` codes:** `0x01` `protocol_violation`, `0x02` `resource_exhausted`, `0x03`
  `session_expired`, `0x04` `enrollment_rejected`, `0x05` `unauthorized_record_class`. The
  reason is 0..64 bytes of UTF-8. An `app_error` is terminal.
- **Timing:**
  - handshake waits: 30 s (§11.3)
  - idle: 180 s with no valid record in either direction (`reconnect_idle_timeout_seconds`)
  - heartbeat send spacing: ≥ 30 s (`heartbeat_min_interval_seconds`)
  - heartbeat terminate: on the 5th within a rolling 60 s window per direction (OTP2-REC-034)

  All of these use monotonic time: Android `SystemClock.elapsedRealtime`, Chrome
  `performance.now`.
- **Heartbeat driver (plan decision; the RFC forbids Chrome liveness on short throttled JS
  timers):**
  - **Android host:** ticks each open session every 1 s, using a coroutine `delay` on the
    service scope. The engine emits `app_heartbeat` after 60 s with no outbound record.
  - **Chrome engine:** sends its own heartbeat only while processing an inbound event, and
    only when its outbound silence is ≥ 60 s. Phone heartbeats arrive as native events and
    wake the tab.
  - **Chrome deadlines:** they are evaluated on every inbound event, and also on a coarse 15 s
    `setTimeout` that may be throttled. Throttling can only close a session late, never
    early.
  - **Expiry first:** the deadlines are checked **before** a late inbound event is processed
    and before any heartbeat is generated. A session that has already been idle for 180 s is
    torn down rather than revived.
  - Both directions stay ≤ 1 heartbeat per 60 s, under the limits.
- **Authentication boundary:** `Authenticated` happens only after **send completion** of the
  local `reconnect_confirm`, not after it is enqueued.
  - Android completion = the GATT `onNotificationSent` for the last fragment of that record.
  - Chrome completion = `controller.send` resolving `ok`.
  - A failed send, or one completing after teardown, ends the session.
- **Sequences:** they start at 0 (confirm), then 1, 2 and so on. 2^64−1 is accepted once, then
  the session terminates. They never wrap.
- **Logging:** stage, a bounded error code and timing only. The only exception is the
  synthetic marker, in debug builds only. Android logs through `VelesLog` (no raw
  `android.util.Log`); Chrome logs through `connector/diagnostics.ts`.
- **Commits:** Conventional Commits, checked with `python3 -I scripts/commitlint.py
  --message`. New Vitest files are added to `web-extension/package.json` `"test"` in the same
  commit that creates them. There is one exception: `test/session/loopback.test.ts` runs only
  through `sessionLoopbackCheck`.

## Review Focus

1. **A client hello repeated on the same connection** (BLE retransmit, or a buggy client). The
   phone must end that session with `preauth_reject`, never restart the handshake with a
   second ephemeral key. The test goes in Task 4: `repeatedHelloOnSameGenerationRejects`.
2. **Two simultaneous links from the same client installation** (a stale link not yet
   detected, plus a new one). Each generation authenticates independently, with distinct
   session ids, and neither session reads the other's state. The test goes in Task 5:
   `twoGenerationsSameInstallationAreIndependent`.
3. **Phone sleep while a session is open.** Idle and handshake deadlines use
   `elapsedRealtime`, which counts sleep, so a session idle across a 5-minute doze ends when
   the phone wakes, not later. The test goes in Task 4: `idleTimeoutCountsInjectedSleepTime`.
4. **The trust record is deleted between hello and signature.** The phone re-checks that the
   record still exists before sending its `reconnect_confirm`. If it is gone, it sends
   `preauth_reject` and nothing protected. The test goes in Task 4:
   `recordDeletedMidHandshakeYieldsNoProtectedData`.
5. **A maximum-size record (4096 bytes) arriving through GATT reassembly.** It is accepted, and
   4097 is rejected before decryption. The test goes in Task 5:
   `maxSizeRecordThroughAdapterAccepted`.

---

### Task 1: Correct the OTP-02 reconnect vector (tasks.md group 1, design D0)

**Files:**
- Modify: `protocol/checks/crypto.mjs` (`helloPayload`, `deriveReconnect` ~L507–L800)
- Modify: `protocol/tools/crosscheck-vectors/oracle.py` (~L595–L612)
- Modify: `protocol/checks/crypto.test.mjs`
- Regenerate: `protocol/v1/fixtures/crypto.json` via `protocol/tools/generate-vectors.mjs`

**Interfaces:**
- Produces: corrected `veles-reconnect-authentication` outputs, in which both
  `phone_hello_payload` and `client_hello_payload` embed `phone_ephemeral_pub` and
  `client_ephemeral_pub`. Every later task consumes these.

- [ ] **Step 1: Add the failing invariant test** `reconnect hello carries the ephemeral key`
  to `crypto.test.mjs`. Assert, for the committed fixture:
  - `phone_hello_payload.slice(16, 81) === phone_ephemeral_pub`
  - `client_hello_payload.slice(16, 81) === client_ephemeral_pub`
  - each of those equals `pubFromPriv(*_ephemeral_priv)`
- [ ] **Step 2: Run** `cd protocol && npm test`. Expect FAIL: the hello embeds `0424eebc…`
  (the identity key).
- [ ] **Step 3: Fix it.** `helloPayload` takes `ephemeral_pub`. `deriveReconnect` computes
  `phoneEphPub` and `clientEphPub` before building the hellos and passes them in. Make the
  same change in `oracle.py`. Add the same invariant as a hard `throw` inside
  `deriveReconnect`.
- [ ] **Step 4: Regenerate** `node protocol/tools/generate-vectors.mjs`, then audit with
  `git diff --stat protocol/v1/fixtures` and a jq or Python diff of field names.
  - **May change:** hello payloads, `rbase`, `rbase_digest`, `phone_sig`, `phone_sig_r`,
    `phone_sig_s`, `client_sig`, `reconnect_confirm_input`, `conf_a`, `conf_b`;
    `veles-neg-sig-{high-s,der,truncated}` inputs and `deviation.value`; the
    `veles-record-reconnect-confirm` body, ciphertext and tag.
  - **Must be byte-identical:** `ss`, `reconnect_salt`, `reconnect_prk`, `k_*`, `session_id*`,
    and every other fixture id.
- [ ] **Step 5: Run** `./gradlew protocolCheck`. Expect PASS, including the OpenSSL
  cross-check and the Kotlin, Rust, web and bridge consumers.
- [ ] **Step 6: Commit** alone:
  `fix(protocol): hello carries ephemeral key in reconnect vectors (OTP2-REC-020)`. The body
  lists the changed fields and states "veles-v1.md unchanged".

---

### Task 2: Android codec and crypto primitives (tasks.md 2.1–2.6)

**Files:**
- Create in `app/src/main/java/me/nagaev/veles/session/`:
  - `Wire.kt`: `Role`, `Direction`, `FrameType`, `RecordType`, `AppErrorCode`,
    `SessionClass`
  - `PreAuthCodec.kt`
  - `ReconnectHello.kt`
  - `P256.kt`
  - `EcdsaP1363.kt`
  - `SignatureFrame.kt`
  - `Hkdf.kt`
  - `ReconnectKeySchedule.kt`
  - `RecordCodec.kt`
  - `SessionReject.kt`
- Test in `app/src/test/java/me/nagaev/veles/session/`:
  - `SessionFixtures.kt`: loads `/fixtures/crypto.json` the way `ProtocolFixturesTest` does
  - `PreAuthCodecTest.kt`
  - `ReconnectHelloTest.kt`
  - `EcdsaP1363Test.kt`
  - `ReconnectKeyScheduleTest.kt`
  - `RecordCodecTest.kt`

**Interfaces (Produces):**
```kotlin
enum class Role(val wire: Int) { PHONE(1), CLIENT(2) }
enum class Direction(val wire: Int) { CLIENT_TO_PHONE(1), PHONE_TO_CLIENT(2) }
enum class SessionClass { FULL, FULL_PENDING_PEER, ACTIVATION_RECOVERY }
class SessionReject(val code: AppErrorCode? = null) : Exception()   // generic; no message text
object PreAuthCodec { fun encode(type: Int, payload: ByteArray): ByteArray; fun decode(frame: ByteArray): Pair<Int, ByteArray> }
data class ReconnectHello(val installationId: ByteArray, val ephemeralPublicKey: ByteArray, val nonce: ByteArray, val role: Role, val caps: ByteArray) {
    fun encode(): ByteArray
    companion object { val LOCAL_CAPS: ByteArray /* = byteArrayOf(0x01) */; fun decode(payload: ByteArray, expectedRole: Role): ReconnectHello }
}
object P256 { fun requireValidPoint(uncompressed: ByteArray): java.security.interfaces.ECPublicKey; fun generate(): java.security.KeyPair; fun privateFromScalar(s: ByteArray): java.security.interfaces.ECPrivateKey; fun ecdhX(priv: java.security.PrivateKey, peer: ByteArray): ByteArray }
object EcdsaP1363 { fun fromDer(der: ByteArray): ByteArray; fun normalizeLowS(sig: ByteArray): ByteArray; fun requireValid(publicKey: ByteArray, message: ByteArray, sig: ByteArray) /* throws SessionReject */ }
data class SignatureFrame(val role: Role, val signature: ByteArray) { fun encode(): ByteArray; companion object { fun decode(payload: ByteArray, expectedRole: Role): SignatureFrame } }
data class ReconnectKeys(val c2pKey: ByteArray, val c2pPrefix: ByteArray, val clientConfirmKey: ByteArray, val p2cKey: ByteArray, val p2cPrefix: ByteArray, val phoneConfirmKey: ByteArray, val sessionId: ByteArray) { fun erase() }
object ReconnectKeySchedule { fun rbase(phoneHello: ByteArray, clientHello: ByteArray): ByteArray; fun derive(ss: ByteArray, phoneNonce: ByteArray, clientNonce: ByteArray): ReconnectKeys; fun confirmInput(rbase: ByteArray, phoneSig: ByteArray, clientSig: ByteArray): ByteArray; fun confirmation(key: ByteArray, input: ByteArray): ByteArray }
data class OpenedRecord(val type: Int, val sequence: ULong, val body: ByteArray)
object RecordCodec { const val HEADER = 23; const val TAG = 16; const val MAX_RECORD = 4096; const val MAX_CIPHERTEXT = 4057
    fun seal(key: ByteArray, prefix: ByteArray, sessionIdPrefix: ByteArray, direction: Direction, type: Int, sequence: ULong, body: ByteArray): ByteArray
    fun open(key: ByteArray, prefix: ByteArray, sessionIdPrefix: ByteArray, direction: Direction, expectedSequence: ULong, record: ByteArray): OpenedRecord }
```
Every decode, verify or open failure throws `SessionReject`. Nothing is partially applied.

- [ ] **Step 1: Write failing tests.** Each test uses the spec's exact values.
  - `PreAuthCodecTest`: `rejectsWrongLength`, `rejectsTrailingBytes`, `rejectsOver4096`,
    `rejectsUnknownType(0x08, 0x10)`
  - `ReconnectHelloTest`:
    - `fixtureHelloRoundTrips`: decode then encode of the fixture's `phone_hello_payload` and
      `client_hello_payload` gives the same bytes
    - `rejectsNegPointFixtures`: each `veles-neg-point-*` `deviation.value` is spliced at bytes
      16..81, keeping its raw length (the infinity value is 1 byte, the compressed one 33)
    - `rejectsCapsUnsortedDuplicateMissing01`, `rejectsUnknownCapability` (`[0x01, 0x02]`
      when only `0x01` is known), `rejectsVersion2`, `rejectsWrongRole`
  - `EcdsaP1363Test`:
    - `verifiesFixtureSignatures`, using the public keys
      `phone_identity_pub` and `client_identity_pub`
    - `rejectsNegSigFixtures`
    - `rejectsDoubleHash`: signing `SHA-256(rbase)` with `SHA256withECDSA` must not verify
      over `rbase`
    - `fromDerRejectsOverlongInteger`
    - `normalizeLowSFlipsHighS`
  - `ReconnectKeyScheduleTest`:
    - `fixtureScheduleMatches`: `ss`, `reconnect_salt`, `reconnect_prk`, `k_ba_*`, `k_ab_*`,
      `session_id`, `reconnect_confirm_input`, `conf_a`, `conf_b`
    - `rejectsZeroSharedSecret`
  - `RecordCodecTest`:
    - `sealsFixtureRecords`, for `veles-record-reconnect-confirm` and
      `veles-record-push-event`
    - `rejectsNegRecordFixtures`
    - `tamperEveryHeaderAndCiphertextByteRejected`
    - `rejectsLengthMismatch`, `rejects4097`, `rejectsUnknownAppErrorCode`
- [ ] **Step 2: Run**
  `./gradlew :app:testDebugUnitTest --tests "me.nagaev.veles.session.*"`. Expect FAIL
  (unresolved references).
- [ ] **Step 3: Implement the interfaces above.**
  - JCA verify needs DER, so `requireValid` checks the 64-byte length and `s ≤ n/2`, converts
    to DER, then calls `SHA256withECDSA`. Every failure throws `SessionReject`.
  - `requireValidPoint` checks for exactly 65 bytes with prefix `0x04` before anything else,
    rejects (0,0), then builds a key with `ECPublicKeySpec` and lets `KeyFactory` validate it
    on the curve.
  - Hand-check `y² = x³ − 3x + b mod p`, because some providers skip that check.
- [ ] **Step 4: Run** the same command. Expect PASS.
- [ ] **Step 5: Commit** `feat(app): reconnect codec and crypto primitives (OTP-09)`.

---

### Task 3: Kotlin limits and phone-role engine core (tasks.md 3.1–3.3, 3.6)

**Files:**
- Create in `app/src/main/java/me/nagaev/veles/session/`:
  - `SessionLimits.kt`
  - `SessionEffect.kt`
  - `PhoneSessionEngine.kt`
  - `PeerLookup.kt`
- Test in `app/src/test/java/me/nagaev/veles/session/`:
  - `SessionLimitsTest.kt`
  - `PhoneSessionEngineHandshakeTest.kt`
  - `ClassSelectionTest.kt`
  - `TestPeers.kt`: software P-256 identities, plus an engine factory with fixed ephemeral
    keys and nonces

**Interfaces:**
- Consumes: Task 2.
- Produces:
```kotlin
object SessionLimits { const val HANDSHAKE_TIMEOUT_MS = 30_000L; const val IDLE_TIMEOUT_MS = 180_000L; const val HEARTBEAT_MIN_INTERVAL_MS = 30_000L
    const val HEARTBEAT_TERMINATE_THRESHOLD = 4; const val HEARTBEAT_WINDOW_MS = 60_000L; val SEQUENCE_MAX: ULong = ULong.MAX_VALUE }
enum class LocalRecordState { ACTIVE, PENDING, COMMITTED_WAITING_ACK }
data class PeerRecord(val installationId: ByteArray, val identityPublicKey: ByteArray, val state: LocalRecordState)
fun interface PeerLookup { suspend fun find(installationId: ByteArray): PeerRecord? }
fun interface IdentitySigner { suspend fun signDer(message: ByteArray): ByteArray }
sealed interface SessionEffect {
    data class Send(val bytes: ByteArray, val completionToken: Long? = null) : SessionEffect   // non-null = engine awaits onSendComplete(token, ok)
    data class Authenticated(val sessionClass: SessionClass, val peerId: ByteArray, val sessionIdPrefix: ByteArray) : SessionEffect
    data class Deliver(val type: Int, val body: ByteArray) : SessionEffect
    data class Terminated(val reason: String /* bounded stage code */) : SessionEffect   // always followed by Close
    data object Close : SessionEffect }
interface ApplicationHandler { fun handles(type: Int): Boolean; suspend fun handle(type: Int, body: ByteArray, reply: suspend (type: Int, body: ByteArray) -> Unit) }
class PhoneSessionEngine(localInstallationId: ByteArray, signer: IdentitySigner, lookup: PeerLookup, clock: () -> Long,
                         ephemeral: () -> java.security.KeyPair = P256::generate, nonce: () -> ByteArray, handler: ApplicationHandler? = null) {
    suspend fun onInbound(bytes: ByteArray): List<SessionEffect>
    suspend fun onSendComplete(token: Long, ok: Boolean): List<SessionEffect>
    suspend fun sendApplication(type: Int, body: ByteArray): List<SessionEffect>   // throws SessionReject unless open and class authorizes it
    suspend fun onTick(): List<SessionEffect>   // deadlines, idle, and the 60 s outbound-silence heartbeat
    fun teardown()
    val isOpen: Boolean
    val sessionClass: SessionClass?   // non-null only after Authenticated
    val peerId: ByteArray? }
```
- The engine performs no repository writes.
- `lastSeen` (tasks.md 3.6) is written by the host (Task 5), on the `Authenticated` effect,
  under that generation's mutex, and only if the generation is still current.
- The `Authenticated` effect is emitted from `onSendComplete(confirmToken, true)` or from
  `onInbound(peerConfirm)`, whichever comes **second**. Both must have happened.

- [ ] **Step 1: Write failing tests.**
  - `SessionLimitsTest.matchesLimitsJson`: the resource `/limits.json` (the test resources
    map the `protocol/v1` root) gives `reconnect_idle_timeout_seconds` = 180 and
    `heartbeat_min_interval_seconds` = 30, and these equal the constants divided by 1000.
  - `PhoneSessionEngineHandshakeTest`:
    - `fullHandshakeOpensFullClass`: a software client harness drives hello → phone hello +
      phone signature → client signature → both confirms. Expect
      `Authenticated(FULL, peer, prefix)`.
    - `unknownClientGetsByteIdenticalReject`: unknown and deleted ids both produce exactly
      `[Send(0f0000), Close]`.
    - `phoneSignsFirst`
    - `peerConfirmBeforeOwnConfirmSentDoesNotAuthenticate`: the peer confirm arrives while
      the confirm token is still pending, so there is no `Authenticated` effect.
      `onSendComplete(token, true)` then authenticates; `onSendComplete(token, false)` instead
      terminates.
    - `wrongHmacConfirmTerminatesAndErases`
    - `recordBeforeConfirmTerminates`
    - `tickEmitsHeartbeatAfter60sOutboundSilence`
  - `ClassSelectionTest.phoneRows`: parameterized over the §11.3 P rows (record `ACTIVE` ×
    peer `0x01`/`0x02`, `PENDING`/`COMMITTED_WAITING_ACK` × either), expecting `FULL`,
    `FULL_PENDING_PEER` and `ACTIVATION_RECOVERY` respectively.
- [ ] **Step 2: Run**
  `./gradlew :app:testDebugUnitTest --tests "me.nagaev.veles.session.*"`. Expect FAIL.
- [ ] **Step 3: Implement `PhoneSessionEngine`** with explicit states `AwaitingClientHello`,
  `AwaitingClientSignature`, `AwaitingPeerConfirm`, `Open`, `Closed`.
  - The phone sends its hello and its signature together, after the lookup succeeds.
  - It re-checks `lookup.find` immediately before sealing its confirm (Review Focus 4).
  - Its own confirm is emitted with a `completionToken`. `Authenticated` is emitted only once
    the peer's confirm has verified **and** that token has completed with `ok`.
- [ ] **Step 4: Run** the same command. Expect PASS.
- [ ] **Step 5: Commit** `feat(app): phone reconnect session engine (OTP-09)`.

---

### Task 4: Kotlin protected-session rules (tasks.md 3.4, 3.5, 3.7)

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/session/PhoneSessionEngine.kt`
- Create: `app/src/main/java/me/nagaev/veles/session/RecordPolicy.kt`, containing
  `fun authorized(type: Int, direction: Direction, cls: SessionClass): Boolean` with the
  §7.3/§11.4 tables
- Test in `app/src/test/java/me/nagaev/veles/session/`:
  - `PhoneSessionEngineRecordsTest.kt`
  - `PhoneSessionEngineTimingTest.kt`

**Interfaces:** the same engine API as Task 3. `RecordPolicy.authorized` is consumed by
Task 7 for parity.

- [ ] **Step 1: Write failing tests.**
  - `PhoneSessionEngineRecordsTest`:
    - `replayDuplicateTerminates`, `reorderTerminates`, `skipTerminates`
    - `priorSessionRecordRejected`
    - `sequenceMaxAcceptedOnceThenTerminates`: a test hook sets the next inbound sequence to
      2^64−1
    - `unknownType0000And0201Terminate`
    - `wrongDirectionTerminates`
    - `pullInActivationRecoveryTerminatesNoDelivery`
    - `unhandledAuthorizedRecordSendsProtocolViolation`: there is no handler; expect a sealed
      `app_error` 0x01, then `Close`
    - `unhandledActivationRecordFailsClosed`
    - `appErrorReceivedIsTerminal`, `unknownAppErrorCodeTerminates`
    - `inputAfterTeardownIgnored`
    - `repeatedHelloOnSameGenerationRejects`
    - `recordDeletedMidHandshakeYieldsNoProtectedData`
  - `PhoneSessionEngineTimingTest`, all with an injected clock:
    - `earlyHeartbeatIgnored`: two heartbeats 10 s apart, still open
    - `fifthHeartbeatIn60sTerminates`
    - `directionsCountedSeparately`
    - `outboundOnlyActivityPreventsIdle`
    - `idleTimeoutCountsInjectedSleepTime`
    - `clientSignatureTimeout30s`
    - `lateSignerCompletionDiscarded`
    - `trustSurvivesTeardown`
- [ ] **Step 2: Run** the session tests. Expect FAIL.
- [ ] **Step 3: Implement** in the engine:
  - sequence tracking
  - `RecordPolicy`
  - a heartbeat ring buffer of receive times per direction
  - the idle deadline, refreshed on every valid send **or** receive
  - the handshake deadline per wait state
  - `teardown()`, which erases with `ReconnectKeys.erase()` and `fill(0)` on ephemeral
    scalars, then sets the state to `Closed`
- [ ] **Step 4: Run** the session tests. Expect PASS.
- [ ] **Step 5: Commit** `feat(app): protected record session rules (OTP-09)`.

---

### Task 5: Android transport adapter and echo isolation (tasks.md group 4)

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/sharing/SharingGattServer.kt` (constructor
  L28–32, record callback L170–200, `remove`/`disconnect` L500–514)
- Modify: `app/src/main/java/me/nagaev/veles/sharing/SharingService.kt:72-77`
- Modify: `app/src/main/java/me/nagaev/veles/sharing/SyntheticTransportFixture.kt`
- Create: `app/src/main/java/me/nagaev/veles/sharing/SessionHost.kt`
- Create **both** `app/src/release/java/me/nagaev/veles/session/BuildSessionExtensions.kt`
  and `app/src/debug/java/me/nagaev/veles/session/BuildSessionExtensions.kt`. Both return
  `null` for now. There is no `main` copy; each build type compiles exactly one. Task 9 fills
  in the debug copy.
- Modify: `app/src/main/java/me/nagaev/veles/trust/TrustRepository.kt`, adding
  `suspend fun markAuthenticated(peerId: String)` to `InstallationTrustRepository` and
  `TrustRepository`.
  - It sets `lastSeenMillis = wallMillis()` in one transaction, for **any** existing,
    validated record that can authenticate (`ACTIVE`, `PENDING`, `COMMITTED_WAITING_ACK`).
  - It never changes `state`, `stateChangedAtMillis`, or the pending creation and expiry
    fields, so a pending record's lifetime is not extended.
  - It throws `TrustException` only for an unknown or deleted id.
  - Add tests to the existing trust tests: each of the three states, unknown and deleted
    rejected, and the value persisted across repository recreation.
- Modify tests: `app/src/test/java/me/nagaev/veles/sharing/SharingGattServerTest.kt`,
  `GattTransportBudgetTest.kt`
- Create test: `app/src/test/java/me/nagaev/veles/sharing/SessionHostTest.kt`
- Modify: the OTP-06 instrumentation that relies on the echo, under `app/src/androidTest/`.
  Find it with `grep -rl SyntheticTransportFixture app/src/androidTest`.

**Interfaces:**
- Consumes: `PhoneSessionEngine` and `SessionEffect` (Tasks 3–4).
- Produces:
```kotlin
data class ConnectionGeneration(val address: String, val serial: Long)
interface ConnectionListener { fun onConnected(gen: ConnectionGeneration); fun onRecord(gen: ConnectionGeneration, bytes: ByteArray /* owned by callee */); fun onDisconnected(gen: ConnectionGeneration)
    fun onSendComplete(gen: ConnectionGeneration, token: Long, ok: Boolean) }
interface SessionTransport { fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean /* enqueue admission only */; fun close(gen: ConnectionGeneration) }   // SharingGattServer implements; close() = send-queue flush then cancelConnection
class SessionHost(scope: CoroutineScope, transport: SessionTransport, engineFactory: (ConnectionGeneration) -> PhoneSessionEngine,
                  trust: InstallationTrustRepository, clock: () -> Long) : ConnectionListener {
    fun openFullSessions(): List<ConnectionGeneration>                     // class FULL and open
    suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean
    fun hasOpenSession(peerId: String): Boolean }
object BuildSessionExtensions { fun applicationHandler(context: Context): ApplicationHandler?; fun attach(host: SessionHost) }   // both stubs: null / no-op until Task 9
internal object TransportMode { @Volatile var instrumentationEcho: Boolean = false }   // debug-only read; mutually exclusive with SessionHost
```
- `SessionHost` keeps one `Mutex` and one engine per generation. Every engine call (inbound,
  send completion, tick, application send) for that generation runs under that mutex.
- A `Send` effect for a generation that has ended is dropped. A `false` return from `send`
  causes `teardown()` and `close`.
- **Buffer ownership:** `SharingGattServer` passes a **copy** to `onRecord` before it zeroes
  its own buffer. The host zeroes that copy after processing, or when the generation ends.
  Pending inbound work is bounded by the existing `IngressBudget`.
- **Send completion:** `SharingGattServer` reports `onSendComplete(gen, token, ok)` when the
  last fragment of a tokened record is notified (`onNotificationSent`), or `ok = false` when
  it is dropped.
- **Ticks:** each generation runs a 1 s tick job on the host scope, which is cancelled on
  `onDisconnected` or `Close`.
- **On `Authenticated`:** the host calls `trust.markAuthenticated(peerId)`, still under the
  mutex, after re-checking that the generation is current and the engine is open.

- [ ] **Step 1: Write failing tests.**
  - `SessionHostTest`:
    - `linkLossDuringSigningDiscardsResult`: the signer suspends, `onDisconnected` fires,
      and the signer then resumes, so no `send` is recorded
    - `sameAddressNewGenerationStartsFresh`
    - `twoGenerationsSameInstallationAreIndependent`
    - `terminalFrameQueuedBeforeClose`
    - `sendFailureTearsDown`
    - `maxSizeRecordThroughAdapterAccepted`
    - `confirmCompletionPendingThenPeerConfirm`: completion succeeds →
      `markAuthenticated` is called once; completion fails → it is never called
    - `recordBufferCopiedBeforeCallerZeroes`: engine execution is delayed until after the
      callback returns, and the bytes are intact
    - `tickFiresIdleTeardownWithoutInbound`, `tickSendsHeartbeatWithoutInbound`,
      `wakeAfterExpiryTearsDownOnFirstTick`
    - `staleGenerationDoesNotMarkAuthenticated`
    - `openFullSessionsExcludesRecoveryClass`
  - `SharingGattServerTest.echoBytesOnNormalPathGetNoPlaintextReply`: `VEL\x01` bytes with
    `instrumentationEcho = false` produce no `transport.enqueue` with those bytes, and the
    connection is rejected
- [ ] **Step 2: Run** the `me.nagaev.veles.sharing.*` tests. Expect FAIL.
- [ ] **Step 3: Implement.**
  - Replace the `onRecord: (String, ByteArray) -> ByteArray?` callback with
    `ConnectionListener`.
  - Assign a monotonically increasing `serial` on each `STATE_CONNECTED`.
  - Make `disconnect` reachable through `SessionTransport.close`.
  - In `SharingService`, when `BuildConfig.DEBUG && TransportMode.instrumentationEcho`, wire
    the old echo listener. Otherwise wire `SessionHost`. Its engine factory uses
    `AndroidIdentityKeyStore` and a `PeerLookup` over the `peers()` of the repository from
    `createAndroidTrustRepository(context, verifier)`. No production implementation of
    `ActivationVerifier` exists yet, and OTP-09 performs no activation step, so the verifier
    is an explicit reject-all `ActivationVerifier { _, _, _ -> false }`, with a comment
    naming OTP-10 as its replacement.
    The factory also takes `BuildSessionExtensions.applicationHandler(this)`.
  - The OTP-06 instrumentation sets `instrumentationEcho = true` before it starts the
    service.
- [ ] **Step 4: Run** `./gradlew :app:testDebugUnitTest`. Expect PASS for the whole module.
- [ ] **Step 5: Commit** `feat(app): wire reconnect sessions into GATT sharing (OTP-09)`.

---

### Task 6: Chrome codec and crypto primitives (tasks.md group 5)

**Files:**
- Create in `web-extension/src/session/`:
  - `wire.ts`: the same enums as `Wire.kt`
  - `frames.ts`
  - `hello.ts`
  - `p256.ts`
  - `ecdsa.ts`
  - `schedule.ts`
  - `record.ts`
  - `reject.ts`
- Test in `web-extension/test/session/`:
  - `fixtures.ts`: loads from `resolve(import.meta.dirname, '../../../protocol/v1')`
  - `frames.test.ts`
  - `hello.test.ts`
  - `ecdsa.test.ts`
  - `schedule.test.ts`
  - `record.test.ts`
- Modify: `web-extension/package.json` `"test"`, adding the five files.

**Interfaces (Produces):** the same names and shapes as Task 2, in camelCase TypeScript.
Everything is `async` where Web Crypto is involved.
```ts
export class SessionReject extends Error { constructor(readonly code?: AppErrorCode) }
export function encodeFrame(type: number, payload: Uint8Array): Uint8Array; export function decodeFrame(frame: Uint8Array): { type: number; payload: Uint8Array };
export interface ReconnectHello { installationId: Uint8Array; ephemeralPublicKey: Uint8Array; nonce: Uint8Array; role: Role; caps: Uint8Array }
export function encodeHello(h: ReconnectHello): Uint8Array; export function decodeHello(p: Uint8Array, expectedRole: Role): ReconnectHello;
export function requireValidPoint(p: Uint8Array): Promise<CryptoKey>;          // ECDH-usable public key
export function normalizeLowS(sig: Uint8Array): Uint8Array; export function verifyP1363(pub: Uint8Array, msg: Uint8Array, sig: Uint8Array): Promise<boolean>;
export function signRbase(key: CryptoKey, rbase: Uint8Array): Promise<Uint8Array>;   // crypto.subtle.sign then normalizeLowS
export interface ReconnectKeys { c2pKey: CryptoKey; c2pPrefix: Uint8Array; clientConfirmKey: Uint8Array; p2cKey: CryptoKey; p2cPrefix: Uint8Array; phoneConfirmKey: Uint8Array; sessionId: Uint8Array }
export function deriveKeyMaterial(ss: Uint8Array, phoneNonce: Uint8Array, clientNonce: Uint8Array): Promise<Record<'c2pKey'|'c2pPrefix'|'clientConfirmKey'|'p2cKey'|'p2cPrefix'|'phoneConfirmKey'|'sessionId', Uint8Array>>; // raw bytes; tests assert these against the fixture
export function importKeys(m: Awaited<ReturnType<typeof deriveKeyMaterial>>): Promise<ReconnectKeys>;   // AES keys non-extractable; caller zeroes the raw AES bytes after import
export function sealRecord(k: CryptoKey, prefix: Uint8Array, sid8: Uint8Array, dir: Direction, type: number, seq: bigint, body: Uint8Array): Promise<Uint8Array>;
export function openRecord(k: CryptoKey, prefix: Uint8Array, sid8: Uint8Array, dir: Direction, expectedSeq: bigint, rec: Uint8Array): Promise<{ type: number; seq: bigint; body: Uint8Array }>;
```
- Web Crypto rejects malformed points on `importKey`, but an explicit length and prefix check
  still runs first, so the `veles-neg-point-*` values fail with `SessionReject`.
- `importKeys` imports the AES keys as non-extractable. The engine never keeps the raw AES
  bytes.

- [ ] **Step 1: Write failing tests.** These mirror the Task 2 test names in camelCase,
  for example `it('rejects double-hashed signature')`, with the same fixtures and assertions.
- [ ] **Step 2: Run** `cd web-extension && npx vitest run test/session/`. Expect FAIL.
- [ ] **Step 3: Implement the interfaces above.**
- [ ] **Step 4: Run** the same command, then `npm test`. Expect PASS for both.
- [ ] **Step 5: Commit** `feat(web-extension): reconnect codec and crypto primitives (OTP-09)`.

---

### Task 7: Chrome client-role engine (tasks.md group 6)

**Files:**
- Create in `web-extension/src/session/`: `limits.ts`, `policy.ts` (a port of `RecordPolicy`),
  `engine.ts`
- Test in `web-extension/test/session/`: `limits.test.ts`, `engine-handshake.test.ts`,
  `engine-records.test.ts`, `class-selection.test.ts`. Register all of them.

**Interfaces:**
- Consumes: Task 6, and `ChromeTrustRepository` from `web-extension/src/trust/repository.ts`
  (`peers()`, `confirm(peerId, 'phone_active', proof)`).
- Produces:
```ts
export interface ApplicationHandler { handles(type: number): boolean; handle(type: number, body: Uint8Array, reply: (type: number, body: Uint8Array) => Promise<void>): Promise<void> }
export type SessionEffect = { kind: 'send'; bytes: Uint8Array; completionToken?: number } | { kind: 'authenticated'; sessionClass: SessionClass; peerId: string; sessionIdPrefix: Uint8Array }
  | { kind: 'deliver'; type: number; body: Uint8Array } | { kind: 'terminated'; reason: string } | { kind: 'close' };
export interface ClientEngineDeps { localInstallationId: Uint8Array; sign(rbase: Uint8Array): Promise<Uint8Array>;
  expectedPeerId: string;    // always set: the intended trust-record id (see SessionManager.open); a phone hello naming any other id fails before signing
  isCurrent(): boolean;      // generation fence; checked immediately before upgradeMirror
  lookupPhone(id: string): Promise<{ publicKey: Uint8Array; state: 'pending' | 'active_activation_pending' | 'active' } | undefined>;
  upgradeMirror(peerId: string, proof: Uint8Array): Promise<void>;   // wraps repository.confirm(…, 'phone_active', proof) with a session-bound verifier
  now(): number; ephemeral?(): Promise<CryptoKeyPair>; nonce?(): Uint8Array; handler?: ApplicationHandler }
export class ClientSessionEngine { start(): Promise<SessionEffect[]>; onInbound(b: Uint8Array): Promise<SessionEffect[]>;
  onSendComplete(token: number, ok: boolean): Promise<SessionEffect[]>;   // send effects carry an optional completionToken, as in Kotlin
  sendApplication(type: number, body: Uint8Array): Promise<SessionEffect[]>; onTick(): Promise<SessionEffect[]>; teardown(): void; readonly isOpen: boolean }
```
- The `proof` passed to `upgradeMirror` is the verified phone confirm, bound to its session.
  The verifier closes over the engine's current session id and returns `false` for any other
  session.
- `upgradeMirror` is called only if `isCurrent()` is true. A teardown that happens **after**
  the call does not undo the upgrade: the trigger is the cryptographically verified
  phone-active proof, and that remains valid whether or not this session survives.

- [ ] **Step 1: Write failing tests.**
  - `limits.test.ts`: the constants equal `limits.json`.
  - `engine-handshake.test.ts`:
    - `sends hello first with a fresh ephemeral key`
    - `aborts on phone id mismatch without signing`
    - `generic failure state is identical for reject and bad signature`
    - `peer confirm alone does not open the gate` (completion pending, then ok, and then the
      failure variant)
    - `selected trusted A, valid trusted B answers: rejected before signing and before any
      metadata write`
    - `heartbeat sent only while processing inbound after 60 s outbound silence`
    - `upgrade not attempted when the generation is stale`
    - `wrong HMAC confirm terminates`
    - `mirror upgrades before class selection and survives a recreated repository`
    - `no upgrade on peer_state 0x02`
  - `class-selection.test.ts`: every §11.3 C row.
  - `engine-records.test.ts`: the same list as Task 4 (records and timing, with fake timers),
    plus `phone hello timeout after 30 s shows generic failure`.
- [ ] **Step 2: Run** `npx vitest run test/session/`. Expect FAIL.
- [ ] **Step 3: Implement** `ClientSessionEngine` to mirror `PhoneSessionEngine`. All mutable
  state lives behind one private `#state` object, so that Task 8's queue is the only
  concurrency boundary.
- [ ] **Step 4: Run** `npm test`. Expect PASS.
- [ ] **Step 5: Commit** `feat(web-extension): client reconnect session engine (OTP-09)`.

---

### Task 8: Chrome connector wiring (tasks.md group 7)

**Files:**
- Create: `web-extension/src/session/queue.ts`, `web-extension/src/connector/sessions.ts`
- Modify:
  - `web-extension/src/connector.ts` (recovery success at L424–441)
  - `web-extension/src/connector/reconnect.ts` (allowance renewal at L304–313)
  - `web-extension/src/connector/controller.ts` (only if `onRecord` needs a generation
    token)
- Test in `web-extension/test/connector/`: `sessions.test.ts`, plus updates to
  `hints-reconnect.test.ts`, `integration.test.ts` and `connector-app.test.ts`. Register
  `sessions.test.ts`.
  - In `connector-app.test.ts`, the successful-connect fixtures (currently L101–108 and
    L311–320, which expect completion without authentication) must now seed one intended
    trusted peer and complete a scripted handshake.
  - Keep, or add, dedicated failure tests for zero and for several candidate phones.

**Interfaces:**
- Consumes: `ConnectorController.connect`, `send(handle, bytes)`, `onRecord(listener)` and
  `disconnect(handle)`; and Task 7's engine.
- Produces:
```ts
export class SerialQueue { constructor(limit: number); run<T>(job: () => Promise<T>): Promise<T>; /* throws QueueOverflow past limit */ }
export class SessionManager { constructor(controller: ConnectorController, repository: ChromeTrustRepository, deps: (handle: string, peerId: string) => ClientEngineDeps, portGeneration: () => number);
  open(handle: string, peerId: string): Promise<{ result: 'authenticated'; peerId: string } | { result: 'failed' }>;   // resolves only after the gate opens + class fixed, or on failure
  readonly verifier: AuthenticationVerifier;   // stage-specific, see below
  static intendedPeer(trusted: TrustedPeer[], explicit?: string): string | undefined;   // explicit if given; else the single active-family peer (by installationId); else undefined
  sendApplication(handle: string, type: number, body: Uint8Array): Promise<void>; close(handle: string): void }
```
- Every queued job captures `(handle, portGeneration())` and drops its result if either has
  changed.
- The queue limit is a named constant, `SESSION_QUEUE_LIMIT = 16`, matching the per-peer
  queued-message cap in the RFC.
- **Every** successful `controller.connect` goes through `sessions.open` with an
  **intended peer id**. Discovery metadata never supplies it.
  - **Recovery:** the target's `peerId`.
  - **Manual Connect** (`runConnect`, `connector.ts:558–565`):
    - The `connect` action gains an optional `peerId`, so
      `{ type: 'connect'; deviceHandle; peerId? }`.
    - Otherwise `SessionManager.intendedPeer` picks the single active-family trusted phone.
    - If there are zero or several candidates and no explicit id, the connect fails with the
      generic failure **before** `controller.connect`.
    - Choosing among several phones in the product UI is OTP-16/17. The development
      `velesDebug.connect(deviceHandle, peerId)` passes it explicitly.
  - `markReady()` moves after `'authenticated'`, and the authenticated `peerId` is recorded in
    `connectionPeerTargets`.
- **Stage-specific verifier** (`sessions.verifier(peer, stage, proof)`):
  - **`phone_active`:** returns true only while that peer's current-generation engine is
    finalizing its handshake: the phone confirm has passed the AEAD tag, sequence and HMAC
    checks, `peer_state = 0x01`, class not yet selected, and `proof` equals that confirm.
  - **`authenticated`:** returns true only after the authentication gate opened for that
    peer's current generation, with the same `proof`.
  - Anything else, including another generation or stage `complete`, returns false.
  - Both are tested through the single production repository and manager, with no
    permissive test verifier.
- Recovery treats `'failed'` as a consumed attempt. `discard(peer)` and hint replacement
  happen only after `'authenticated'`.
- **One production repository.** The connector creates a single `ChromeTrustRepository`
  whose verifier is `sessions.verifier` (wired in `connector.ts` and `connector/hints.ts`,
  replacing the default reject-all at `hints.ts:142–144`). After `'authenticated'`, the
  manager calls `repository.authenticated(peerId, hint, proof)`, where `proof` is the
  session's verified phone confirm. The manager re-checks `isCurrent()` just before that
  call.

- [ ] **Step 1: Write failing tests** in `sessions.test.ts`, with fake timers and a deferred
  fake `crypto.subtle`:
  - `adjacent handshake frames processed in order while verify is pending`
  - `simultaneous ack and heartbeat get consecutive sequences`
  - `port replacement while crypto pending discards result`
  - `queue overflow ends session with resource_exhausted`
  - `one phone failing does not affect another`
  - `metadata unchanged on failure and on stale completion`
  - `bridge send payloads are only frames or sealed records`: every `controller.send` byte
    string starts with `0x06`, `0x07` or `0x0f`, or parses as a record header with the
    current session prefix

  In `hints-reconnect.test.ts`:
  - `BLE success with failed auth consumes attempts until the bound`

  In `integration.test.ts`:
  - `mirror upgrade survives connector restart`
  - `fresh injected peer without a hint authenticates on manual connect`: there is no
    `markReady` before authentication
  - `manual connect with two trusted phones and no explicit id fails before connect`
  - `mirror upgrade through the production verifier wiring (phone_active stage) then
    authenticated stage`
  - `persisted lastSeen and hint updated only after authentication; unchanged after failure
    and stale completion`
  - `confirm send completion pending blocks authentication`
- [ ] **Step 2: Run** `npx vitest run test/connector/`. Expect FAIL.
- [ ] **Step 3: Implement** `SerialQueue` and `SessionManager`, then change `connector.ts`
  recovery as specified above.
- [ ] **Step 4: Run** `npm test && npm run typecheck && npm run lint && npm run build && npm
  run test:bundle`. If the build's chunk set changed, update the exact `dist/` list in
  `test/bundle.test.ts` in this commit and review the new chunks. Expect PASS.
- [ ] **Step 5: Commit** `feat(web-extension): authenticated sessions in the connector
  (OTP-09)`.

---

### Task 9: Debug probe, trust injection and extension channel gating (tasks.md group 8)

**Files:**
- **Android, debug only:**
  - Modify: `app/src/debug/java/me/nagaev/veles/session/BuildSessionExtensions.kt`, created
    as a `null` stub in Task 5. It now returns the probe handler and exposes
    `fun attach(host: SessionHost)`, which `SharingService` calls after it creates the host.
    The release stub's `attach` is a no-op.
  - Create: `app/src/debug/java/me/nagaev/veles/debugprobe/{DebugTrustInjector.kt,SyntheticProbeHandler.kt,ProbeReceiver.kt}`
  - Create or modify: `app/src/debug/AndroidManifest.xml`
- **Android tests:** `app/src/testDebug/java/me/nagaev/veles/debugprobe/SyntheticProbeHandlerTest.kt`
- **Extension:**
  - Modify: `web-extension/vite.config.ts` (`define: { __VELES_CHANNEL__ }`; a guard mode that
    writes to `../build/web-extension-guard/`)
  - Modify: `web-extension/src/manifest.ts` (a guard production identity, used only when
    `VELES_GUARD_BUILD=1`)
  - Create: `web-extension/src/debug/{probe.ts,inject.ts,readout.ts}`, loaded from
    `connector.ts` only inside `if (__VELES_CHANNEL__ === 'development') { await
    import('./debug/probe') }`
  - Create test: `web-extension/test/debug-probe.test.ts`, and register it

**Interfaces:**
- Consumes: `ApplicationHandler` and `SessionHost` (Tasks 3 and 5); `SessionManager` (Task 8).
- Produces:
  - **Android probe event:**
    - `phone_installation_id`: the local id
    - `event_id`: 16 random bytes
    - `receipt_epoch_seconds`: now
    - `remaining_lifetime`: 600
    - `code`: a marker of the form `"V" + 5` uppercase alphanumerics
    - `merchant`: `"Veles synthetic"`
    - `amount_magnitude` 100, `amount_scale` 2
    - `currency`: `"XTS"`
  - **Android adb surface:** `ProbeReceiver` is exported with
    `android:permission="android.permission.DUMP"`, so only the shell can reach it.
    - Actions: `me.nagaev.veles.debug.EXPORT_IDENTITY`, `me.nagaev.veles.debug.INJECT_PEER`
      (extras `id`, `pub`), `me.nagaev.veles.debug.PUSH`, and
      `me.nagaev.veles.debug.REVOKE_PEER` (extra `id`; calls the existing
      `TrustRepository.revoke` only when `host.hasOpenSession(id)` is false, and refuses
      otherwise, so it never becomes the OTP-11 live revocation)
    - `PUSH` sends one event to each generation in `host.openFullSessions()`, via
      `host.sendApplication(gen, 0x0203, event)`. If that list is empty, nothing is sent.
    - Broadcasts always target the component explicitly:
      `-n me.nagaev.veles.debug/me.nagaev.veles.debugprobe.ProbeReceiver`. The debug
      package is `me.nagaev.veles.debug` (`applicationIdSuffix = ".debug"`).
    - It logs through `VelesLog` with tag `VelesProbe`: `marker=<code> event=<hex16>
      sid=<hex8>`.
  - **Android trust injection:** `DebugTrustInjector.injectActive(id: String, pub: String)`
    writes through `TrustStorage.transaction`, producing a `TrustedPeer` in state `ACTIVE`
    that passes `validatePeer`. It computes the fingerprint the same way as
    `TrustRepository`; if needed, expose that function as `internal fun peerFingerprint` in
    `TrustRepository.kt`.
  - **Chrome:** in development builds, `globalThis.velesDebug` exists on the connector page
    only:
    - `exportIdentity(): Promise<{installationId, publicKey}>`
    - `connect(deviceHandle: string, peerId: string): Promise<void>`, which dispatches
      `{ type: 'connect', deviceHandle, peerId }`
    - `injectPeer(id, pub): Promise<void>`
    - `pull(handle?: string): Promise<ProbeEvent>`. With no argument it uses the single open
      full session, and throws if there are zero or several.

    There is no `chrome.runtime.onMessage` handler, so content scripts and web pages cannot
    reach it.
  - **Readout:** a `<section data-testid="veles-debug-readout">` showing the marker, the event
    id and the session id prefix.

- [ ] **Step 1: Write failing tests.**
  - Android: `SyntheticProbeHandlerTest`:
    - `pullReturnsOneSection81Event`: decoding it gives the exact fields listed above
    - `pushWithoutOpenSessionSendsNothing`, `pushBeforeAnyPullIsSent`,
      `pushSkipsActivationRecoverySessions`, `pushReachesEveryOpenFullSession`
    - `revokeRefusedWhileSessionOpen`
    - `realNotificationNotUsed`: no dependency on `NotificationListener`, asserted by the
      handler's constructor parameters
  - Chrome: `debug-probe.test.ts`:
    - `cross-installed peers authenticate as full`
    - `runtime message from content script has no handler`
    - `pull round trip shows matching marker in readout`
    - `malformed event ends session and leaves readout empty`
- [ ] **Step 2: Run** `./gradlew :app:testDebugUnitTest --tests
  "me.nagaev.veles.debugprobe.*"` and `npx vitest run test/debug-probe.test.ts`. Expect
  FAIL.
- [ ] **Step 3: Implement the interfaces above.** The extension guard build is
  `VELES_GUARD_BUILD=1 vite build --outDir ../build/web-extension-guard`, which uses the
  production channel with a fixed test-only id from `manifest.ts`. It never reads
  `VELES_RELEASE_CONFIG`.
- [ ] **Step 4: Run** both test commands, then `./gradlew :app:assembleDebug`, then `npm run
  build && npm run test:bundle`. The development dynamic-import chunk changes the `dist/`
  set, so update the exact list in `test/bundle.test.ts` **in this commit** and review the
  new chunk. Put the `await import('./debug/probe')` inside the existing async bootstrap of
  `connector.ts`. Expect PASS.
- [ ] **Step 5: Commit** `feat: debug-only synthetic session probe and trust injection
  (OTP-09)`.

---

### Task 10: Release guards, conformance runners, loopback (tasks.md group 9)

**Files:**
- **Release guard:** modify `app/build.gradle.kts` to register `releaseHookGuard`.
- **Bundle guard:**
  - Modify: `web-extension/package.json`, so `"test:bundle"` becomes
    `"VELES_GUARD_BUILD=1 vite build --outDir ../build/web-extension-guard && vitest run
    test/bundle.test.ts"`. Use `cross-env`, or a Node wrapper script in
    `web-extension/scripts/`, if inline env does not work on Windows runners.
  - Modify: `web-extension/test/bundle.test.ts`.
- **Android CI job:** modify `.github/workflows/check-android.yml`, adding the
  `release-hook-guard` job and the header comment. `verify/test/workflow-contracts.test.mjs`
  needs updating if it asserts the job list.
- **Scenario inventory:** create `protocol/conformance/otp-09-scenarios.json`. It is owned by
  the `protocol` component; confirm with `components.py check`.
- **Scenario runners:**
  - Kotlin: `app/src/test/java/me/nagaev/veles/session/ScenarioConformanceTest.kt`
  - TypeScript: `web-extension/test/session/scenarios.test.ts` (registered)
- **Loopback:**
  - `app/src/test/java/me/nagaev/veles/session/loopback/PhoneLoopbackMain.kt`
  - `web-extension/test/session/loopback.test.ts`: **not** registered in `"test"`
  - `app/build.gradle.kts` (`sessionLoopbackClasspath`)
  - root `build.gradle.kts` (`sessionLoopbackCheck`, added to `protocolCheck.dependsOn`)

**Interfaces:**
- **Inventory format:** `{ "<fixture id>": "execute" | { "engine-effect": { "terminate":
  bool, "erase": bool, "deliver": bool } } | "deferred:OTP-10" | "deferred:OTP-11" |
  "deferred:OTP-13/14" | "deferred:OTP-15" }`. Classifications follow design D6.
- **Harness**, shared in shape by both runners:
  - `ScenarioPeer` is a scripted counterpart built from the same codec. It produces real
    frames and records for the opposite role.
  - `input.role` selects which engine is under test.
  - `persisted_state` seeds an in-memory trust store (`durable_phone_record:main=active` →
    `LocalRecordState.ACTIVE`, and so on).
  - `ScenarioAppHandler` is a test-only, **passive** `ApplicationHandler`. It records each
    delivered record, including each `event_ack` as known or unknown against its buffer, but
    it never replies on its own. It is not the debug probe.
  - **Emissions** happen only at explicit steps. A `send_protected_record` step makes the
    engine under test call `sendApplication(record_type, body)` exactly once. For `0x0202`,
    the body is built at that step from its `event_count` (0..5 synthetic events, which also
    become the buffer's known event ids). So each fixture send is exactly one emission, with
    the fixture's sequence.
  - Multi-peer steps (`peer`) create one engine per named peer, so isolation can be asserted.
- **Step mapping.** Each step's `event` drives the engine, and its `simulate` flags mutate
  the real bytes. Every key used by an in-scope fixture is mapped:
  - **Lookup and hello:**
    - `record_exists:false` → `lookup` returns null
    - `hello_valid:false` → caps `[0x02]`
  - **Signature and confirm:**
    - `sig_ok:false` → flip the last byte of `r`
    - `hmac_ok:false` → seal a confirm with `conf` XOR `0x01`
    - `peer_state` → the confirm body byte
  - **Record bytes:**
    - `tag_ok:false` → flip the last tag byte
    - `length_ok:false` → `ct_len + 1`; for the oversize scenario, a 4097-byte record
    - `session_id_ok:false` → seal with another session prefix
    - `sequence` / `record_type` → used verbatim
  - **Application semantics:**
    - `event_count` → the handler's buffer size
    - `event_known:false` → ack an event id that is not in the buffer
    - `pull_response` → the `phone_policy_state` flag (deferred ids only)
  - **Sequence seeking:**
    - `rx_next` / `counter_seek` → a test-only `seekSequences(rx: ULong, tx: ULong)` on the
      engine, which is `@VisibleForTesting` in Kotlin and an underscore-prefixed export in TS
  - **Lifecycle events:**
    - `send_hello`, `send_reconnect_confirm`, `send_protected_record` → the engine under test
      emits its own frames
    - `close_connection` → `teardown()`
    - `process_load` → recreate the engine and repository over the same persisted store
      (`continuity_token_matches:false` → reseed with a different token)
    - `revoke` → remove the record from the store between connections
  - **Clock:** `t_ms` → the injected clock.
  - **Wrong-context frames:** `pairing_confirm_a` → a real `0x04` pre-auth frame with a
    32-byte payload, delivered to the reconnect engine. The engine rejects it as a frame type
    invalid in reconnect context (`preauth_reject` on the phone, generic abort on the client).
    It is used by `st-rec-session-role-mismatch-terminates`.
  - **Unmapped keys:** keys appearing only in deferred ids (`act_mac_ok`, `pending_expiry`,
    `push_retransmit`) throw if met in an executed id.
- **Assertions:**
  - `expected.accepted`
  - each step's `expect` when present
  - `final_state`, compared against the engine's session state and the store's record states
  - every `effects.require` entry
  - **no** `effects.forbid` entry
- **Effect names:**
  - They map to engine observations, for example `terminate_session` → `Terminated` +
    `Close`; `erase_session_keys` → the engine's `_keysErased` test probe is true;
    `counter_not_advanced` → the rx counter is unchanged; `reject_*` → the bounded reason
    code; `set_class_*` → `Authenticated.sessionClass`.
  - Any name without a mapping fails the test.
- **Trace-validation negatives** (`expected.stage` with no `effects`): their inventory entry
  is `engine-effect` and carries the explicit endpoint assertion, for example
  `{ "terminate": true, "erase": true, "deliver": false }`. The runner checks that instead.
- **Loopback protocol:**
  - `PhoneLoopbackMain` reads messages from stdin as `kind u8 || u32be length || bytes`:
    - kind 0: a transport frame or record for the phone engine
    - kind 1: a UTF-8 command, either `push` or `reset` (a reset starts a new generation)
  - It writes the phone's `Send` effects to stdout as `kind 0 || length || bytes`, and the
    events `authenticated` and `closed`, with the session-id prefix, as kind 2. Stderr is for
    diagnostics only.
  - It uses software identities from a seed passed as `argv[1]`. The client's matching
    identity and the phone's trust record are derived from the same seed.
  - `loopback.test.ts` spawns `java -cp $VELES_LOOPBACK_CLASSPATH
    me.nagaev.veles.session.loopback.PhoneLoopbackMainKt <seed>`, and is skipped when the
    variable is unset.
  - It also includes the cross-platform signature tests: Kotlin-signed and TS-verified, and
    the reverse, both 64 bytes with low-S.

- [ ] **Step 1: Write the guard tests first.**
  - `releaseHookGuard` unzips `app/build/outputs/apk/release/*.apk` and scans every
    `classes*.dex`, `AndroidManifest.xml` and `resources.arsc`, in both UTF-8 and UTF-16LE.
    Forbidden: `me/nagaev/veles/debugprobe`, `VelesProbe`, `Veles synthetic`,
    `me.nagaev.veles.debug.`, `INJECT_PEER`, `REVOKE_PEER`. Any hit fails the task.
  - `bundle.test.ts` adds `guard bundle excludes debug probe`: scan
    `../build/web-extension-guard/**` for `velesDebug`, `Veles synthetic` and
    `veles-debug-readout`. The existing `dist/` inventory assertions are kept.
  - To prove the guards work, run each once against the debug and development outputs.
    Expect FAIL.
- [ ] **Step 2: Run** `./gradlew :app:releaseHookGuard` and `npm run test:bundle`. Expect
  PASS against release and guard output. Add the `release-hook-guard` job to
  `check-android.yml`, copying the Rust and NDK setup from the existing JNI job: no secrets,
  no upload.
- [ ] **Step 3: Write the scenario inventory and both runners.** Add
  `inventoryCoversEveryFixtureId` to both runners. Run them; expect FAIL until the mapping is
  complete, then PASS. Each executed id passes, and every deferred id is printed with its
  owning task.
- [ ] **Step 4: Write the loopback.**
  - `:app:sessionLoopbackClasspath` reuses the `protocolJvmTest` pattern
    (`app/build.gradle.kts:129–139`): it depends on the unit-test compilation tasks and writes
    `unitTest.classpath` to `app/build/session-loopback/classpath.txt`.
  - `sessionLoopbackCheck` depends on it and on `protocolWebInstall`, then runs `npm exec
    vitest run test/session/loopback.test.ts`. It sets `VELES_LOOPBACK_CLASSPATH` and
    `VELES_LOOPBACK_REQUIRED=1`; with the latter set, the test **fails** rather than skips
    when the classpath is missing.
  - The child uses the `java` of the Gradle toolchain, which is Java 21 per `.java-version`.
  - `releaseHookGuard` declares `dependsOn(":app:assembleRelease")` and fails if no APK
    exists. Tests:
  - `handshake, pull/ack, push, heartbeat, app_error`
  - `two reconnects have distinct session ids and keys`
  - `kotlin-signed verifies in TS and vice versa`

  Run `./gradlew sessionLoopbackCheck protocolCheck`. Expect PASS.
- [ ] **Step 5: Run** `node --test verify/test/workflow-contracts.test.mjs` and
  `python3 -I scripts/components.py check`, with the new files staged. Expect PASS.
- [ ] **Step 6: Commit** `test: OTP-09 conformance runners, loopback and release guards`.

---

### Task 11: Physical verification, evidence, gate and PR (tasks.md groups 10–11)

**Files:**
- Create: `docs/otp-09-physical-2026-11-XX.md`, using the actual run date.

**Interfaces:**
- Consumes: the debug APK from `./gradlew :app:assembleDebug` and the development extension
  from `npm run build` (`web-extension/dist`).
- Use the `homepc` skill for every device and Chrome action: the Windows PC for adb and
  Chrome, and the Mac for its Chrome.

- [ ] **Step 1: Install the debug APK.**
  - Take the package name from the built APK (`aapt2 dump packagename`, expected
    `me.nagaev.veles.debug`).
  - Read the new APK's fingerprint with `apksigner verify --print-certs`. Read the installed
    one by running `adb shell pm path me.nagaev.veles.debug`, `adb pull`, then `apksigner
    verify --print-certs`.
  - If they differ, reinstall. The user authorized this on 2026-11-10.
  - Record both fingerprints, as read, in the evidence note.
- [ ] **Step 2: Windows Chrome.** Load the development extension unpacked, open the connector,
  and run in order:
  1. `adb shell am broadcast -n me.nagaev.veles.debug/me.nagaev.veles.debugprobe.ProbeReceiver
     -a me.nagaev.veles.debug.EXPORT_IDENTITY`. The same `-n` component is used for every
     action below. The command prints an ordered-broadcast result line carrying
     `data="identity=<id> pub=<key>"` with the full values; logcat shows only bounded
     prefixes. Then `velesDebug.exportIdentity()`, then `INJECT_PEER` and
     `velesDebug.injectPeer()` to cross-inject.
  2. Connect, and expect authentication with class full.
  3. `velesDebug.pull()`, and expect the readout marker to equal the logcat
     `VelesProbe marker=`.
  4. `adb … PUSH`, and expect it acked and shown.
  5. Wait 60 s or more and see a heartbeat.
  6. Disconnect and reconnect, and expect a new `sid` prefix.
  7. Disconnect, run `adb … REVOKE_PEER --es id <client id>`, reconnect, and expect the
     generic failure with an empty readout.
- [ ] **Step 3: macOS Chrome.** Repeat Step 2 using the Mac's dedicated Chrome.
- [ ] **Step 4: Write the evidence note.** For each platform record the commit SHA, phone model
  and Android build, OS builds, Chrome versions, fingerprints, and each step's observed result
  and PASS/FAIL. Only markers and prefixes appear; no keys.
- [ ] **Step 5: Gate.** Run:
  - `./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck
    :app:releaseHookGuard`
  - the raw-Log guard command from `check-android.yml`
  - in `web-extension`: `npm run format:check && npm run lint && npm run typecheck && npm test
    && npm run build && npm run test:bundle`
  - `python3 -I scripts/components.py check`
  - `node --test verify/test/workflow-contracts.test.mjs`
  - `openspec validate otp-09-authenticated-reconnect --strict`

  Everything must exit 0.
- [ ] **Step 6: Commit** `docs: OTP-09 physical round-trip evidence`. Validate each commit
  message with `scripts/commitlint.py`.
- [ ] **Step 7: Open the PR** with title `feat: authenticated reconnect and protected records
  (OTP-09)`. Validate the title and body with `scripts/commitlint.py`. The body lists:
  - the D0 commit
  - the deferred scenario ids, taken from the inventory
  - the evidence note
  - the closing line: `Closes #91`
