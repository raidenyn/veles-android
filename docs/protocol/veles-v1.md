# Veles protocol contract v1 (normative wire document)

Status: DRAFT, pending independent review and user acceptance. This document is
the proposed sole authority for exact wire encodings, bounds, constants, and
state effects of the Veles external-device protocol version 1. The
machine-readable index lives in `protocol/v1/manifest.json` and fixed ceilings
in `protocol/v1/limits.json`. Evidence (fixtures, executable checks,
independent reviews) is pending and tracked in the manifest; no acceptance is
claimed here.

Precedence: the approved design
(`docs/superpowers/specs/2026-10-23-otp-02-protocol-contract-design.md`) and
`docs/rfcs/2026-08-21-otp-external-devices.md` (the RFC) govern product and
security requirements; this document governs exact encodings within those
requirements; schemas and fixtures are derived and can never override this
document. Any conflict with the RFC is a defect: it is reported, resolved, and —
if it changes RFC semantics — requires user approval plus an RFC amendment in
the same change.

Requirement IDs: each normative statement below is labeled with a stable
requirement ID (for example `OTP2-ENC-001`). Later plan tasks reference these
IDs, never line numbers. Anchors are stable section IDs
(`<a id="sec-...">`), referenced by the manifest as
`veles-v1.md#sec-...`.

## Table of contents

1. Conventions and canonical rules
2. Threat model and downgrade policy
3. SPAKE2 pairing: ciphersuite, transform, context, transcript, confirmations
4. Pre-auth plaintext framing
5. Enrollment, identity records, activation, recovery
6. Reconnect authentication
7. Protected record format
8. Application records
9. GATT transport, fragmentation, and resource accounting
10. Native Messaging envelope v1
11. State transition tables
12. Verification and fixture requirements
13. Requirements registry (traceability)

---

## 1. Conventions and canonical rules

<a id="sec-conventions"></a>

### 1.1 Requirement index for this section

- **OTP2-CON-001** — RFC governs; this document governs encoding; semantic RFC
  changes require approval and amendment (see precedence above).
- **OTP2-ENC-001** — byte order rule: Veles framing is unsigned network order
  (big-endian). RFC 9382's own `len()` lengths are unsigned 64-bit
  __little-endian__ and are used only where RFC 9382 fixes them (TT length
  prefixes, the enrollment-salt `len(AAD)`). This document quotes both
  distinctly; implementers MUST NOT confuse them.
- **OTP2-ENC-002** — byte-string notation: `||` is byte concatenation. Fixtures
  and this document write bytes as lowercase even-length hex.
- **OTP2-ENC-003** — string rules: all Veles strings are UTF-8 with byte-length
  bounds. A string value MUST be valid UTF-8, MUST NOT contain U+0000 through
  U+001F or U+007F, and MUST NOT be empty only where a minimum length is stated.
  No Unicode normalization (NFC/NFD) is applied anywhere; two strings are equal
  if and only if their bytes are equal. A string longer than its bound rejects
  the record at the length-validation stage without state mutation.
- **OTP2-ENC-004** — canonical rejection rules: unknown record types, unknown
  header versions, unknown enum values, duplicate fields, trailing bytes after
  the declared length, non-canonical length encodings, and malformed point,
  key, or signature encodings are rejected before any state mutation. A
  rejected pre-auth input yields `preauth_reject` plus disconnect (see
  sec-plain-framing); a rejected in-session record tears the session down and
  erases keys (see sec-record-format). "Reject" never silently drops and
  continues for authenticated record types; transport fragments alone may be
  dropped, as their loss only forfeits a record, which then terminates the
  session through the sequence rule.
- **OTP2-CON-002** — prohibition: no plaintext record path, no compatibility
  with the RFC's rejected HMAC spike protocol, no remapping of authenticated
  Veles record fields into JSON, and no unbounded backward compatibility.
- **OTP2-CON-003** — version model: `envelope_version` (Native Messaging
  envelope) is immutable `1`. The BLE protocol version (`protocol_version`) and
  binary schema version (`schema_version`) are separate and are both `1` in this
  contract. The bridge API version is separate again. Initial supported sets
  contain only version 1; a hypothetical version 2 exists solely inside
  negotiation fixtures/tests and MUST be rejected in production.
- **OTP2-CON-004** — transport opacity: the desktop connector and bridge carry
  exactly one complete record per `send`/`message` in `record_base64`; base64
  is a byte-preserving transport encoding only and is never part of the
  authenticated record schema. No authenticated field is serialized, inspected,
  or remapped as JSON anywhere in the stack.
- **OTP2-SEC-005** — safe observability: logs and status surfaces may contain a
  protocol stage, a bounded non-secret error code, and timing. They MUST NOT
  contain OTPs, pairing codes, keys, public-key encodings, plaintext records,
  trust secrets, cryptographic intermediates, raw ciphertext, or
  Native Messaging `send`/`message` bodies. Native-host logs are disabled in
  production except an explicit local diagnostic mode with the same restrictions.

### 1.2 Length-prefix conventions (distinct quotings)

- RFC 9382 `len(·)` — 8-byte unsigned little-endian. Appears in: TT
  (sec-spake-transcript) and the enrollment-salt `len(AAD)`
  (sec-enrollment-schedule). This quoting is RFC-fixed.
- Veles framing — unsigned integers are big-endian (network order). Frame
  payload lengths are 2-byte unsigned big-endian; protected-record header
  lengths are 2-byte or 8-byte unsigned big-endian (sec-record-format).
- RFC 9382 `w` in TT is a fixed-width big-endian 32-byte value for P-256.
- RFC 9382 AAD capacity bound is 2^16 − 128 bits = 8168 bytes; Veles public
  context is additionally capped at 4096 bytes (sec-spake-context).

---

## 2. Threat model and downgrade policy

<a id="sec-threat-model"></a>

- **OTP2-SEC-001** — threat model. Plain GATT and the Native Messaging pipe
  are actively attacked channels. The protocol protects against: passive
  capture; active man-in-the-middle; peer impersonation; message modification,
  replay, reordering, and duplication; cross-session record injection; and an
  unknown nearby BLE client. The password is safe under one online guess per
  protocol execution plus the pairing expiry and attempt limits; a captured
  transcript validates no offline guesses (SPAKE2, no durable verifier). The
  user is assumed to read a short code from the intended unlocked phone and
  enter it in the intended Veles connector tab (or watch dial). A malicious or
  replaced native bridge can observe or alter BLE timing and opaque bytes and
  cause denial of service, but cannot decrypt or forge a protected session.
  Endpoint compromise — unlocked phone, browser/OS, extension context — is
  outside this transport threat boundary. Physical watch extraction is accepted
  at watch-class risk posture (RFC Appendix A); the watch PIN is a display
  gate, not a trust boundary, and never appears in the protocol.
- **OTP2-SEC-002** — downgrade policy. There is exactly one ciphersuite and one
  record schema in protocol version 1. No suite, role, hash, key-schedule, or
  fragmentation-profile negotiation exists: peers publish only fixed constants.
  Protocol/schema version mismatch, capability mismatch, or unsupported value
  at any stage fails closed with the generic failure path; nothing ever runs in
  a weaker or legacy mode. There is no compatibility window beyond the literal
  version 1 profile, and negotiation cannot select plaintext, the spike
  protocol, or a lower security level.
- **OTP2-SEC-003** — stable-ID tracking limitation (acknowledged, not
  redesigned). The reconnect hello carries the sender's installation ID in
  plaintext (sec-reconnect). This is the RFC's own handshake: the stable
  installation ID is exposed to a passive nearby monitor across reconnects,
  which permits linkability of the same installation over time — a
  deliberate, accepted tracking limitation. Any identity-hiding redesign
  requires an approved RFC amendment; this contract preserves the
  requirement and records the limitation.
- **OTP2-SEC-004** — fail-closed semantics. Invalid, unknown, revoked,
  malformed, replayed, reordered, oversized, or incompatible protocol input
  returns no OTP data and mutates no protected state. Attempt consumption
  follows sec-spake-attempts exactly. Removed peers cannot be distinguished
  from strangers (sec-reconnect).
- **OTP2-SEC-006** — boundary permanence: no cloud transport, accounts,
  third-party sync, or telemetry exists anywhere in this protocol; OTP data
  moves only between the user's Veles Android installation and locally
  connected Veles clients.

---

## 3. SPAKE2 pairing

<a id="sec-spake2"></a>

### 3.1 Ciphersuite

<a id="sec-spake-suite"></a>

- **OTP2-CRY-001** — ciphersuite is exactly `SPAKE2-P256-SHA256-HKDF-HMAC`
  (RFC 9382 Section 6, Table 1, row 1): group P-256, hash SHA-256, KDF HKDF
  (RFC 5869), MAC HMAC (RFC 2104). Group elements are 65-byte SEC1
  __uncompressed__ points (`0x04 || X || Y`), 32 bytes each for X and Y.
  This is the RFC-fixed representation.
- **OTP2-CRY-002** — roles are fixed. Android is party A (sends `pA` first,
  uses point M); the client — desktop connector or watch alike — is party B
  (sends `pB`, uses point N). Per-user M/N (RFC 9382 Section 5) is not used.
  The RFC-fixed generator points for P-256 (RFC 9382 Section 6) are:
  - `M = 02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f`
    (compressed, seed `1.2.840.10045.3.1.7 point generation seed (M)`)
  - `N = 03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49`
    (compressed, seed `1.2.840.10045.3.1.7 point generation seed (N)`)
  Implementation stores M and N in this compressed form and decompresses
  internally; points sent on the wire are uncompressed.
- **OTP2-CRY-005a** — point validation: both endpoints run complete,
  constant-time validation of every received group element: it must decode as
  an uncompressed SEC1 point, be on the P-256 curve, be in the
  prime-order subgroup (order `n`), and not be the identity. Failure aborts
  the protocol stage before any password-sensitive operation or state
  mutation. Scalars `x`, `y` are fresh, uniformly sampled (rejection sampling)
  per attempt and never reused.

### 3.2 Pairing identifiers and code transform

<a id="sec-spake-code"></a>

- **OTP2-CRY-009** — pairing identifiers and code. Android generates one
  16-byte uniformly random `pairing_id` and one uniformly random six-ASCII-digit
  `code` from `000000`–`999999` with leading zeroes preserved. One pairing
  window is open at a time; the window lasts 300 seconds (five minutes,
  monotonic clock) and the code is displayed only during it. The code is held
  only during the window and has no durable verifier.
- **OTP2-CRY-009a** — the code is ONE-USE. The pairing code is consumed exactly
  by the phone's successful verification of `pairing_confirm_b` (OTP2-CRY-006):
  at that instant the pairing window closes for that `pairing_id`, the code is
  erased, and no further SPAKE2 execution exists for that `pairing_id` — not
  even after this connection is lost or after the enrollment record reaches
  `pending`/`committed_waiting_ack`/`active`. Disconnecting at any point after
  that instant leaves the pairing machine's window CLOSED (the connection is
  not resumable by re-entering a code). Continuation happens on the EXISTING
  enrollment connection while it lives, or through signed reconnect recovery
  (OTP2-STA-010b) — and recovery is STRICTLY CONDITIONAL on persisted peer
  records EXISTING on both endpoints: a persisted trust record comes into
  being only when an endpoint authenticated the peer's `enroll_identity`
  record, so an interruption AFTER the verified `cB` but BEFORE the peer
  identity record was authenticated/persisted leaves an endpoint with NO
  usable peer record; recovery cannot exist for it, its reconnect fails with
  the generic unknown-installation path (OTP2-REV-001), and the ONLY
  path is an explicitly user-opened NEW pairing window (fresh `pairing_id`,
  fresh code — the old code remains consumed, never reused). A partial
  one-sided `pending` record that DID get persisted (its endpoint
  authenticated the peer's identity, but the peer has no matching record)
  remains INACTIVE — it authorizes nothing beyond the bounded recovery
  exchange, which cannot even authenticate against a record-less peer — and
  can be explicitly cancelled or revoked, or expires (the 86 400-second rule
  with its boot/connector mappings, OTP2-STA-010c/010d), before a fresh
  pairing re-enrolls the same installation (OTP2-STA-012's duplicate guard
  holds only while the record is actually held). Never through a second use
  of the old code in any case.
  Before that instant (no verified `cB`), a disconnect leaves the window open
  and the code held for the remainder of the window; each fresh `pB` still
  consumes its attempt, non-refundably (OTP2-CRY-008).
- **OTP2-CRY-004a** — code-to-`w` transform (RFC-fixed by the RFC's own
  definition):

  ```
  prk = HKDF-Extract-SHA256(salt = pairing_id, IKM = code_ascii)   # code_ascii = 6 ASCII digits
  okm = HKDF-Expand-SHA256(prk, info = "Veles SPAKE2 password input v1", L = 40)
  w   = big-endian integer of okm (40 bytes) mod n      # n = P-256 subgroup order
  ```

  `info` is exactly those 30 ASCII bytes. The 320-bit intermediate follows
  RFC 9382's bias-avoidance guidance. The transform is not an entropy
  amplifier; the security bound remains one online guess per execution.
- **OTP2-CRY-010** — `pairing_id` scopes: attempt counters, the pairing
  window, and the SPAKE2 execution. It is public (part of the AAD context) but
  random per window.

### 3.3 Public context (AAD)

<a id="sec-spake-context"></a>

- **OTP2-CRY-012** — before SPAKE2, both endpoints construct one canonical
  length-prefixed public context and pass it identically as RFC 9382 AAD; it
  is bound into key confirmation through `KDF(Ka, nil, "ConfirmationKeys" || AAD)`.

  ```
  ctx = "Veles SPAKE2 public context v1"        # 30 ASCII bytes
      || u16be protocol_version                 # 1
      || u16be schema_version                   # 1
      || u16be ciphersuite_id                   # 1 (SPAKE2-P256-SHA256-HKDF-HMAC)
      || pairing_id                             # 16 bytes
      || 0x01 || 0x02                           # role markers: A (phone), B (client), fixed
      || phone_nonce                            # 16 bytes, fresh per pairing execution (A)
      || client_nonce                           # 16 bytes, fresh per pairing execution (B)
      || u16be phone_caps_len || phone_caps     # sorted unique u8 capability set (sec-spake-context)
      || u16be client_caps_len || client_caps
  ```

  Total size MUST be ≤ 4096 bytes; `ctx` length (u64 little-endian) feeds the
  enrollment salt (sec-enrollment-schedule). Both endpoints MUST verify byte
  equality of the constructed context before key confirmation; unequal
  context aborts with the generic pre-auth failure.
- **OTP2-CRY-012a** — capability enum (`u8` values, ≤ 16 entries, sorted
  ascending, no duplicates; unknown values reject the exchange):
  - `0x01` `VELES_RECORD_V1` — mandatory in every offer on both sides.
  - `0x02` `DIRECT_GATT` — offered by the phone and by bare watch clients.
  - `0x03` `NATIVE_BRIDGE` — offered by desktop clients (informational in v1).
  Capability negotiation rule: the peer set MUST contain `0x01`; other shared
  values are informative in v1 (no behavior switches). A missing `0x01` or an
  unknown value rejects with the generic pre-auth failure.

### 3.4 Transcript and confirmations

<a id="sec-spake-transcript"></a>

- **OTP2-CRY-003** — transcript, exactly RFC 9382 Section 3.3. `len(·)` here is
  RFC 9382's 8-byte unsigned __little-endian__:

  ```
  TT = len(A) || A || len(B) || B
     || len(pA) || pA || len(pB) || pB
     || len(K)  || K  || len(w) || w
  ```

  where the identity byte strings are exactly the RFC-mandated ASCII:
  - `A` = `Veles Android SPAKE2 party A v1` (31 bytes)
  - `B` = `Veles client SPAKE2 party B v1` (30 bytes)
  `pA`/`pB` are the 65-byte uncompressed SEC1 encodings; `K` is the 65-byte
  uncompressed shared group element; `w` is the fixed-width big-endian
  32-byte encoding (constant length for P-256, per RFC). `K` is computed as
  `A: h·x·(pB − w·N)` / `B: h·y·(pA − w·M)` and is never used as a shared
  secret directly.
- **OTP2-CRY-006** — key confirmation, exactly RFC 9382 Section 4:

  ```
  Ke || Ka = SHA-256(TT)                    # |Ke| = |Ka| = 16 bytes
  KcA || KcB = HKDF-SHA256(IKM = Ka, salt = nil,
                           info = "ConfirmationKeys" || ctx, L = 32)
  cA = HMAC-SHA-256(KcA, TT)                # 32 bytes, sent by A (phone)
  cB = HMAC-SHA-256(KcB, TT)                # 32 bytes, sent by B (client)
  ```

  `nil` salt is the zero-length salt. Confirmation is bidirectional and
  ordered: the phone sends `cA`, the client verifies it, then the client sends
  `cB`, and the phone verifies it. Neither endpoint treats SPAKE2 as complete,
  derives enrollment keys, or releases any enrollment record before its peer's
  confirmation verifies. A wrong confirmation consumes the attempt and fails
  generically.
- **OTP2-CRY-004b** — `w` edge cases: the transform defines `w` for every
  6-digit code including `000000`. The modulo maps every 40-byte output into
  `[0, n)`, so no code value needs special handling and no code value is
  invalid at the scalar level; leading zeroes are preserved in the ASCII IKM.
  The degenerate value `w = 0` has probability ≈ `1/n` (≈ 2^-256) for the
  uniform 320-bit intermediate model — NOT an astronomically smaller
  figure — and, over the fixed 10^6-code domain, it is a deterministic,
  verifiable property of the transform rather than a per-execution event; a
  code mapping to `w = 0` is processed normally, never rejected.
- **OTP2-CRY-003a** — degenerate shared-element abort (normative). After the
  peer's point passes complete validation (OTP2-CRY-005a), each endpoint
  computes the shared group element `K` (OTP2-CRY-003). If `K` is the IDENTITY
  element — which happens exactly when the peer sent `w·` this endpoint's own
  blinding point (`pB = w·N` from the phone's view, `pA = w·M` from the
  client's view) — the endpoint aborts the attempt with the identical generic
  pre-auth failure BEFORE any transcript-derived value (`Ke`/`Ka`, KDF
  outputs, confirmation strings) is produced, exposed, or transmitted. On the
  phone this abort follows the non-refundable attempt consumption of
  OTP2-CRY-008: the already-consumed attempt is NOT refunded, and every
  partially derived intermediate is erased. This is asserted normative
  behavior, not only a generator profile choice: it is proven by the
  committed negative vector `veles-neg-shared-secret-identity` (forcing
  `pB = w·N` / `pA = w·M` for both endpoint views) in the crypto fixture set.

### 3.5 Attempt and rate accounting

<a id="sec-spake-attempts"></a>

- **OTP2-CRY-008** — attempt accounting (RFC-fixed; all counters are
  monotonic-clock based):
  - At most 5 attempts per `pairing_id`.
  - At most 5 attempts in any rolling 300-second (five-minute) interval across
    all pairing IDs (phone-side).
  - Exactly one SPAKE2 operation in flight phone-side; a second concurrent
    pairing execution is rejected generically without consuming an attempt.
  - The phone emits at most 1 fresh `pA` per connection and at most 5 fresh
    `pA` values in any rolling 60-second interval across pairing IDs.
    Excess connection requests are rejected before fresh scalar generation or
    any point multiplication.
  - Consumption rule: after validating framing, `pairing_id`, length, and the
    received `pB` point, the phone atomically and non-refundably consumes one
    attempt before any `pB`-dependent password-sensitive operation (shared
    group element, key schedule, `cA`). Computing `pA` is never counted. A
    connection that elicits a fresh `pA` and disconnects without a valid `pB`
    consumes no attempt. A duplicate or replayed valid `pB`, later
    confirmation failure, disconnect, cancellation, or timeout still consumes
    the attempt.
  - Malformed or invalid-point input is rejected before attempt consumption
    with the identical generic failure (`preauth_reject` frame plus
    disconnect): the current connection is closed and its ephemeral pairing
    execution state is erased, while the pairing window, the per-`pairing_id`
    attempt budget, and rate accounting persist; a next attempt is possible
    only from a fresh connection. Rejected inputs remain subject to separate
    framing, request-rate, concurrency, and resource limits.
  - The pairing window deadline is enforced before every password-sensitive
    progression (each received point and each confirmation verification): a
    progression arriving after the window deadline without the window having
    been reaped is answered with the generic failure and no computation.
  - Excess valid attempts are rejected before any `pB`-dependent
    password-sensitive operation begins.
  - Unknown, expired, incorrect, rate-limited, busy, and malformed attempts
    all produce the identical generic failure (`preauth_reject` frame plus
    disconnect); reason codes never cross the wire.
  - The pairing window is global: at most one window is open; a `pairing_request`
    arriving with no open window (or after window expiry) is rejected
    generically.

---

## 4. Pre-auth plaintext framing

<a id="sec-plain-framing"></a>

Before authentication there is no session key; pairing and reconnect
handshake messages travel as plaintext frames with the following envelope:

```
byte 0      frame_type (u8)
bytes 1-2   payload_len (u16, big-endian)
bytes 3..   payload (payload_len bytes; exactly, no trailing bytes)
```

The frame is carried as one GATT write chain or one bridge `record_base64`
(reassembled by the transport layer per sec-fragmentation). Maximum total
frame size is 4096 bytes (payload ≤ 4093).

- **OTP2-ENC-010** — frame type table (`u8`):

  | Type | Name | Direction | Payload |
  |---|---|---|---|
  | `0x01` | `pairing_request` | client→phone | OTP2-ENC-010a |
  | `0x02` | `pairing_offer` | phone→client | OTP2-ENC-010b |
  | `0x03` | `pairing_client_point` | client→phone | OTP2-ENC-010c |
  | `0x04` | `pairing_confirm_a` | phone→client | 32-byte `cA` |
  | `0x05` | `pairing_confirm_b` | client→phone | 32-byte `cB` |
  | `0x06` | `reconnect_hello` | both | OTP2-REC-020 |
  | `0x07` | `reconnect_signature` | both | 1-byte role + 64-byte signature |
  | `0x0F` | `preauth_reject` | phone→client | empty |
  | other | — | — | rejected (OTP2-ENC-004) |

  Tags `0x08`–`0x0E` and `0x10`–`0xFF` are unused in v1; any use is rejected.
- **OTP2-ENC-010a** — `pairing_request` payload
  (client→phone; sent when the client enters the pairing code and connects):

  ```
  client_nonce        16 bytes   (fresh B pairing nonce)
  protocol_version    u16be (1)
  client_caps_len     u16be, then capability bytes (1..16 entries)
  ```

  Fixed bytes are 16 + 2 + 2 = 20, so `payload_length = 20 + caps_len`.
  Any other length rejects.
- **OTP2-ENC-010b** — `pairing_offer` payload
  (phone→client; only when a window is open, rate limits permit, and one
  SPAKE2 slot is free; the phone generates its nonce and `pA` together):

  ```
  pairing_id          16 bytes
  phone_nonce         16 bytes   (fresh A pairing nonce)
  protocol_version    u16be (1)
  phone_caps_len      u16be, then capability bytes (1..16 entries)
  pA                  65 bytes   (uncompressed SEC1)
  ```

  Fixed bytes are 16 + 16 + 2 + 2 + 65 = 101, so
  `payload_length = 101 + caps_len`. This frame reveals no trust secret; it
  is the only place `pairing_id` and `pA` cross the wire.
- **OTP2-ENC-010c** — `pairing_client_point` payload (client→phone):
  the 65-byte uncompressed SEC1 `pB`. Payload length = exactly 65. The phone
  validates framing, `pairing_id` (from the offer it sent), and the point;
  then consumes one attempt (sec-spake-attempts) and continues. The client
  frame carries no pairing ID: it is only valid as a reply within the current
  in-flight execution keyed by the connection.
- **OTP2-ENC-010d** — `pairing_confirm_a` (phone→client) and
  `pairing_confirm_b` (client→phone) payloads are exactly 32 bytes: `cA` and
  `cB` respectively.
- **OTP2-ENC-010e** — `preauth_reject` is a constant empty-payload frame.
  The phone sends at most one `preauth_reject` before disconnecting, for any
  rejected pre-auth input; it carries no reason (generic failure). The client
  maps every reject or disconnect during a pairing window to the same
  user-visible failure. This frame carries no OTP, trust, or error detail.

Wire-flow summary for pairing (roles fixed, RFC §Application security):

```
client → phone : pairing_request            {client_nonce, version, caps}
phone  → client: pairing_offer              {pairing_id, phone_nonce, version, caps, pA}
client → phone : pairing_client_point       {pB}
phone  → client: pairing_confirm_a          {cA}
client → phone : pairing_confirm_b          {cB}
```

Both peers compute `ctx` after the offer (both nonces, caps, and pairing_id
known), before `pB`/`cA` are validated. The client MUST verify `cA` before
sending `cB`; the phone MUST verify `cB` before deriving enrollment keys and
sending any enrollment record (OTP2-CRY-006).

---

## 5. Enrollment, identity records, activation, recovery

<a id="sec-enrollment"></a>

After both confirmations pass, the SPAKE2 shared secret drives a protected
enrollment channel; enrollment records are AES-256-GCM records (sec-record-format)
with the key schedule below. Stable installation IDs and identity public keys
are never sent before SPAKE2 confirmation succeeds.

### 5.1 Enrollment key schedule

<a id="sec-enrollment-schedule"></a>

- **OTP2-CRY-007a** — enrollment salt (RFC-fixed; label byte lengths in this
  document are exact and MUST be asserted by fixtures/checks):

  ```
  enrollment_salt = SHA-256(
  `Veles SPAKE2 enrollment salt v1`            # 31 ASCII bytes (exact; len=31)
      || len64le(ctx)                              # RFC 9382 len(): u64 LITTLE-endian byte length of AAD
      || ctx )                                     # AAD = canonical public context
  enrollment_prk = HKDF-Extract-SHA256(salt = enrollment_salt, IKM = Ke)
  ```

  `Ke` is the first 16-byte half of `SHA-256(TT)` (RFC 9382's own split).
  `Ka` is reserved exclusively for RFC 9382 key confirmation and is never an
  input to any enrollment or transcript derivation; `Ke` is never used
  directly as a key.
- **OTP2-CRY-007b** — derivations. Each is one `HKDF-Expand-SHA256` call with
  `enrollment_prk` as PRK and the exact ASCII label as `info`:

  | Label (verbatim ASCII, exact length) | Output |
  |---|---|
  | `Veles enrollment A-to-B AES key v1` (34) | 32 bytes (AES-256 key, phone→client) |
  | `Veles enrollment A-to-B nonce v1` (32) | 4 bytes (nonce prefix, phone→client) |
  | `Veles enrollment A confirmation v1` (34) | 32 bytes (activation key, phone, memory-only) |
  | `Veles enrollment B-to-A AES key v1` (34) | 32 bytes (AES-256 key, client→phone) |
  | `Veles enrollment B-to-A nonce v1` (32) | 4 bytes (nonce prefix, client→phone) |
  | `Veles enrollment B confirmation v1` (34) | 32 bytes (activation key, client, memory-only) |

- **OTP2-CRY-007c** — enrollment nonces: the directional 4-byte prefix
  concatenated with the unsigned 64-bit __big-endian__ sequence number,
  starting at zero (12-byte nonce). Enrollment sequence counters start at 0
  per direction, are independent of later sessions, and follow the same
  no-wrap rule as sec-sequences.
- **OTP2-CRY-011** — state erasure: at the end of a complete pairing, both
  endpoints erase the code, the pending SPAKE2 state, `TT` intermediates, and
  all enrollment traffic keys (`Ke`, `Ka`, `KcA`, `KcB`, enrollment AES keys,
  nonce prefixes, and both enrollment activation confirmation keys). No
  enrollment-derived secret is ever persisted: activation confirmation keys
  exist only in process memory for the lifetime of the enrollment connection
  (OTP2-CRY-019e).

### 5.2 Enrollment record session header

<a id="sec-enrollment-header"></a>

- **OTP2-CRY-020a** — enrollment connection header. During the original
  enrollment connection (the connection whose SPAKE2 execution completed),
  protected records use the standard record header (sec-record-format) with
  `session_id_prefix` = first 8 bytes of
  `SHA-256("Veles enrollment session v1" || ctx)` (label is exactly 27 ASCII
  bytes; fixed for the whole enrollment session), direction bytes `0x01` =
  client→phone (`B-to-A`), `0x02` = phone→client (`A-to-B`), and record types
  `0x0100` (`enroll_identity`), `0x0101`–`0x0104` (activation, sec-activation),
  `0x0003` (`app_error`), and `0x0002` (`app_heartbeat`) only. Other record
  types on an enrollment-authorized connection reject and erase. Activation
  records may also travel on a later activation-recovery connection (they then
  carry that session's reconnect session ID in the header and use the
  recovery activation keys, OTP2-CRY-019e); authorization classes are defined
  in sec-authz-classes.
- **OTP2-CRY-020b** — canonical enrollment transcript. `ET` binds the
  complete public SPAKE2 exchange (public context, the public points, and
  both public confirmation values) plus both public identity records via
  length-delimited bytes. It deliberately includes NO secret-derived value —
  not `Hash(TT)` (whose `Ka` half is reserved exclusively for RFC 9382 key
  confirmation, RFC), not `K`, not `w`, and not any `TT` fragment — so that
  persisting `ET` exposes nothing:

  ```
  ET = SHA-256( "Veles enrollment transcript v1"     # 30 ASCII bytes
                || u16be len(ctx)   || ctx
                || u16be len(pA)    || pA        # 65
                || u16be len(pB)    || pB        # 65
                || u16be len(cA)    || cA        # 32
                || u16be len(cB)    || cB        # 32
                || u16be len(phone_identity_record_body)  || phone_identity_record_body
                || u16be len(client_identity_record_body) || client_identity_record_body )
  ```

  length prefixes are unsigned 16-bit __big-endian__ (Veles network order;
  distinct from RFC 9382's u64 little-endian `len()`). The identity record
  bodies are the canonical plaintext bodies of each endpoint's
  `enroll_identity` record (sec-identity-record) in role order (A then B).
  `ET` binds both installation records, the complete SPAKE2 exchange, and the
  activation stages (through the MAC input OTP2-CRY-019a). Both endpoints
  derive the same `ET` or the enrollment fails generically. `ET` is public
  and is the only enrollment-time value that persists in the pending trust
  record.

### 5.3 Identity records and fingerprint

<a id="sec-identity-record"></a>

- **OTP2-REC-010** — `enroll_identity` plaintext body, exactly:

  ```
  installation_id    16 bytes  (stable, random 128-bit installation identity)
  label_len          u16be, then label     # UTF-8, 0..32 bytes (OTP2-ENC-003)
  device_type        u8: 0x00 = phone (required in the phone's own record),
                         0x01 = desktop, 0x02 = watch (required in the
                         client's record; no other value is accepted)
  identity_pubkey    65 bytes  (uncompressed SEC1 P-256 point; validated, OTP2-CRY-005a)
  fingerprint        32 bytes  (own-record fingerprint, OTP2-REC-011a)
  nonce              16 bytes  (random per enrollment)
  ```

  Maximum body size: 16 + 2 + 32 + 1 + 65 + 32 + 16 = 164 bytes
  (label at most 32; `label_len` included in the count). Minimum body size:
  132 bytes (empty label). Any body length outside 132..164 rejects. The
  phone's record MUST carry `device_type = 0x00` (the phone is the A party;
  it is neither desktop nor watch); the client's record MUST carry `0x01`
  (desktop) or `0x02` (watch) — the RFC requires exactly these for client
  records, and no other value is accepted anywhere. A device_type that
  contradicts the sender's role rejects the record and the enrollment; a
  `pairing_confirm_b`-era phone record with any client device_type value is
  likewise rejected. Both endpoints send exactly one
  `enroll_identity` carrying its own values, in RFC-fixed order: the phone
  sends its identity record only after verifying `cB`; the client releases
  its identity record only after authenticating the phone's record (proving
  the phone reached confirmation). The own-record fingerprint
  (OTP2-REC-011a) makes the first-sent record independently verifiable. Both endpoints send exactly one
  `enroll_identity` (each carrying its own values). Sequencing (RFC-fixed):
  the phone sends its record only after verifying `cB`; the client releases
  its record only after authenticating the phone's record, proving the phone
  reached confirmation.
- **OTP2-REC-011a** — own-record fingerprint. The `fingerprint` field of
  each `enroll_identity` record covers ONLY the sender's own identity, so
  the record sent first (the phone's) is fully verifiable when it arrives:

  ```
  fp_self = SHA-256( "Veles enrollment record fingerprint v1"   # 38 ASCII bytes
                     || role_byte          # 0x01 for the phone's record, 0x02 for the client's
                     || installation_id (16)
                     || identity_pubkey (65) )
  ```

  The receiving endpoint recomputes `fp_self` from the record's own fields
  (role taken from the record's direction) and MUST reject the record on
  mismatch before storing or acting on it. The nonce is deliberately not an
  input: fp_self is stable across enrollment retries with the same
  installation identity.
- **OTP2-REC-011** — pair fingerprint derivation and display. After BOTH
  identity records have been authenticated on the channel (no circularity:
  both public keys are then known to both endpoints), each endpoint derives
  the shared enrollment fingerprint:

  ```
  fp_pair = SHA-256( "Veles identity fingerprint v1"   # 29 ASCII bytes
                     || phone_identity_pubkey (65)
                     || client_identity_pubkey (65) )
  ```

  The full 32-byte digest is stored in each endpoint's trust record. The
  user-visible enrollment fingerprint display format is the first 6 bytes of
  `fp_pair` rendered as 12 uppercase hexadecimal characters in three groups
  of four separated by single spaces, for example `3D9F 07B2 C41A`. Both
  devices display the same string for visual comparison at pairing end
  (48-bit visual check; the underlying binding is the transcript
  `ET`, not the display). A device must not display `fp_pair` before both
  identity records are authenticated.
- **OTP2-STA-012** — duplicate installation ID rule: if the phone holds an
  `active`, `pending`, OR `committed_waiting_ack` trust record for the
  installation ID presented in a
  `pairing_request`'s subsequent identity record, enrollment fails closed:
  the phone does not create or replace the existing record, responds with the
  protected error record `0x0003` (`error`, code `0x04`
  `enrollment_rejected`), terminates, erases pairing state, and closes the
  pairing window. Replacement of an identity requires explicit revocation
  first (RFC). Every below-`active` or `active` state of the held record
  participates: the guard is over the installation ID, not a record state.

### 5.4 Activation sequencing

<a id="sec-activation"></a>

- **OTP2-STA-010** — enrollment states are exactly `pending`,
  `committed_waiting_ack` (phone-side), and `active`, plus the client-side
  mirror (`pending` and `active`). A `pending` or `committed_waiting_ack`
  record authorizes ONLY the bounded, idempotent activation-recovery exchange
  of this section; it never authorizes OTP pull, push, or any other
  application record (RFC, OTP-02 Review Focus 3). No OTP record is
  authorized before the RFC activation condition: the phone sends OTP data
  only when its own record is `active`, which it reaches only after
  authenticating the client's `activation_ack`.
- **OTP2-STA-010a** — activation exchange. Before the client may send a
  commit, the phone durably persists its public `pending` record and sends
  `enrollment_prepare`, proving that persistence and the canonical `ET`. The
  order is exact: PERSIST FIRST, prepare sent only after that persistence, and
  the client's commit only after it has verified the prepare:

  ```
  phone  → client: enrollment_prepare     (phone pending durable; ET confirmation)
  client → phone : activation_commit      (only after verifying prepare and its own pending persist)
  phone  → client: activation_complete    (phone: pending → committed_waiting_ack)
  client → phone : activation_ack         (client → active upon validating activation_complete)
  phone          : record active upon authenticating activation_ack
  ```

  The phone sends `enrollment_prepare` as a send effect of persisting its
  pending record — entering `awaiting_commit` only after the prepare went to
  the transport; there is no committed state without a sent prepare. The
  client's state machine enforces the same gate: after persisting its own
  pending record and releasing its identity record it enters
  `awaiting_prepare`, and it may transition to `ready_to_commit` (the
  prepare-verified state) ONLY by verifying an `enrollment_prepare` MAC
  (stage `0x00`) of the CURRENT enrollment connection; an
  `activation_commit` is never sent from `awaiting_prepare`, and an
  `enrollment_prepare` arriving before the client released its own identity
  record (a prepare the phone could not yet have produced) is rejected as an
  ordering violation. If the prepare is lost (the client never verifies one
  on this connection), the client sends NO commit; the pending records
  persist on both endpoints and continuation happens exclusively through the
  recovery protocol (OTP2-STA-010b), whose fresh-session prepare re-attests
  the same durable `ET` before the client's commit there.

  `activation_commit` never mutates anything except the record state:
  the persisted `committed_waiting_ack` record is byte-equal to the candidate
  `pending` record (same public field set, OTP2-CRY-019g) with ONLY the state
  field (and the state-change timestamp) updated — candidate/committed
  equality is an invariant of this section.

  The usable-record persistence boundary is exact (OTP2-STA-010a, R1): each
  endpoint derives `ET` from the two canonical identity bodies — the peer's
  canonical body once its `enroll_identity` was authenticated (OTP2-REC-010/
  011a), plus its OWN body, which it already knows byte-exactly — BEFORE
  atomically persisting the COMPLETE `pending` record, `ET` included with
  the rest of the public field set. Only after durable persistence
  success may the PHONE release `enrollment_prepare` or the CLIENT release
  its own identity record; no partial or ET-less pending record is ever
  persisted or published. A persistence failure or crash before completion
  leaves NO usable record: nothing was published, no prepare/identity is
  sent afterwards, and the endpoint continues on the no-usable-record path
  (fresh explicit pairing window after cleanup/cancellation/expiry,
  OTP2-CRY-009a, OTP2-STA-012).

  Before `activation_commit`, both endpoints persist their inactive `pending`
  trust records durably — persisting ONLY bounded public state: both
  installation IDs, both identity public keys, label, `device_type`, `ET`,
  record state, and timestamps. NO enrollment-derived secret (`Ke`, `Ka`,
  `KcA`/`KcB`, enrollment AES keys, nonce prefixes, activation confirmation
  keys) is ever persisted (RFC: enrollment traffic keys are erased;
  OTP2-CRY-011, OTP2-CRY-019e) — trust activation is explicitly
  interruption-safe, not distributed-atomic. **The original enrollment
  connection terminates when activation completes** (after the client's
  ack sent / the phone's ack verified; both endpoints then erase
  enrollment keys and drop the connection), and no OTP application record
  is EVER authorized on the original enrollment connection — not even
  after both records reach `active` during it. OTP delivery requires a
  fresh, subsequently negotiated full session (sec-activation,
  OTP2-SID-001).
- **OTP2-CRY-019a** — activation MAC inputs. Each activation record body is
  `mac (32 bytes)` where `mac = HMAC-SHA-256(K_act, ET || stage_byte)` with:

  | Record | Direction | Key (enrollment connection) | Stage byte |
  |---|---|---|---|
   | `enrollment_prepare` (`0x0104`) | phone→client | enrollment activation key, phone side (`Veles enrollment A confirmation v1` Expand output) | `0x00` |
   | `activation_commit` (`0x0101`) | client→phone | enrollment activation key, client side (`Veles enrollment B confirmation v1` Expand output) | `0x01` |
  | `activation_complete` (`0x0102`) | phone→client | enrollment activation key, phone side (`Veles enrollment A confirmation v1` Expand output) | `0x02` |
   | `activation_ack` (`0x0103`) | client→phone | enrollment activation key, client side (B confirmation output) | `0x03` |

  On sessions whose class authorizes the completion exchange
  (sec-authz-classes: the enrollment connection, or the recovery classes
  `activation_recovery`/`full_pending_peer`), the same stage bytes apply with
  the class's directional keys per OTP2-CRY-019e/f: `enrollment_prepare`
  (stage `0x00`) is protected by the phone-side key of that class (the
  enrollment A confirmation key, or `K_rec_phone` on a recovery session);
  `activation_commit`/`activation_ack` by the client-side key (the enrollment
  B confirmation key, or `K_rec_client`); `activation_complete` by the
  phone-side key. Each endpoint therefore derives and holds BOTH directional
  enrollment
  activation keys (`Veles enrollment A confirmation v1` and
  `Veles enrollment B confirmation v1` outputs) in memory for the lifetime
  of the enrollment connection — the phone must verify the client's
   `activation_commit`/`activation_ack` MACs and generate its own
   `enrollment_prepare`/`activation_complete` MACs; the client must verify
   `enrollment_prepare` and `activation_complete`
  and generate `activation_commit`/`activation_ack`. These keys are
  memory-only and erased per OTP2-CRY-019e. `ET || stage_byte` is the exact
  33-byte MAC input. A record MAC that does not verify rejects the
  activation sequence (generic failure, erase state, no partial access); it
  never leaves a half-activated trust state.
- **OTP2-CRY-019e** — activation-key lifecycle and erasure obligations:
  - **Enrollment connection:** the six enrollment Expand outputs
    (AES keys, nonce prefixes, both activation confirmation keys) and all
    SPAKE2 state (`TT`, `w`, scalars, `K`) live in process memory only. They
    are erased at the earliest of: (a) the endpoint's last use of them in the
    activation exchange — the phone after verifying the ack that makes the
    record `active`, the client immediately after sending that ack —
    (b) the enrollment connection ending for any reason, (c) the pending
    record expiring (OTP2-STA-010c), (d) explicit cancellation or
    revocation. `ET` (public) is the only enrollment-time value that
    survives into the durable record.
  - **Recovery sessions:** no persisted activation key exists to resume
    with. Instead, an activation-recovery session derives fresh,
    direction-specific activation confirmation keys from that session's own
    `reconnect_prk` (sec-reconnect) with `ET`-bound labels (OTP2-CRY-019f
    below). Recovery MACs use the identical
    `HMAC-SHA-256(K_rec, ET || stage_byte)` input shape; the session class
    (sec-authz-classes) determines which of the enrollment-connection keys
    or recovery keys a received or sent MAC must verify under.
  - **No durable enrollment or activation secrets (OTP2-CRY-019g):** the
    durable record below `active` stores only public values (identities, label,
    `device_type`, `ET`, state, timestamps). A pending record exists durably
    ONLY in its complete form — `ET` is part of that field set and is derived
    and persisted atomically with the rest of the record before any
    `enrollment_prepare`/identity release (OTP2-STA-010a): a persistence
    failure or crash leaves NO usable record, and nothing was published.
    Enforcing this is explicitly
    part of acceptance evidence: recovery authorization MUST work with
    nothing but these public persisted values, and key erasure is verifiable
    through the state/erasure fixtures (OTP2-FIX-004) rather than trusted.
- **OTP2-CRY-019f** — recovery activation keys, one `HKDF-Expand-SHA256`
  call each, per direction, from the recovery session's `reconnect_prk`:

  ```
  K_rec_client = HKDF-Expand-SHA256(reconnect_prk,
      info = "Veles recovery client activation v1" (35 ASCII bytes) || ET, L = 32)
  K_rec_phone  = HKDF-Expand-SHA256(reconnect_prk,
      info = "Veles recovery phone activation v1" (34 ASCII bytes) || ET, L = 32)
  ```

  (the 34/35 byte lengths are the exact ASCII lengths, asserted by checks).
  `K_rec_client` protects `activation_commit`/`activation_ack` MACs;
  `K_rec_phone` protects `activation_complete` MACs AND the
  `enrollment_prepare` MACs of recovery-class sessions (stage byte `0x00`,
  OTP2-STA-010b — the recovery-session analogue of the enrollment prepare,
  an explicit fresh proof bound to the persisted `ET` and this session's
  fresh signed handshake). Both endpoints derive
  both keys whenever a session class needs activation records, using the
  persisted public `ET` from the pending/committed record. They live only
  for the session (erased with it, OTP2-STA-015) and never persist.
- **OTP2-STA-010b** — recovery protocol (fresh signed ECDH sessions;
  recovery activation keys per OTP2-CRY-019e; all transitions replay-safe
  because every MAC is over `ET || stage` and transitions are monotonic).
  On a fresh connection where the phone's record is `pending` or
  `committed_waiting_ack`, the reconnect handshake still authenticates (the
  pending record stores the peer identity public key, sec-reconnect); the
  session class becomes `activation_recovery`; both endpoints derive
  `K_rec_client`/`K_rec_phone` from the fresh session PRK and proceed
  idempotently:
  0. **Recovery prepare (before any commit; exact peer rule).** The phone
     sends `enrollment_prepare` (recovery MAC, stage `0x00` under
     `K_rec_phone`, the current session's sequence — freshly bound to the
     persisted `ET`: the recovery-session analogue of OTP2-STA-010a's
     prepare, an explicit fresh-`ET` proof carried by this session's signed
     handshake keys) EXACTLY whenever the CLIENT's confirmation reported
     `peer_state = 0x02` — i.e. whenever the client's record is below
     active-confirmed, WHATEVER the phone's own record state is
     (`pending` OR `committed_waiting_ack`) and on `full_pending_peer`
     sessions (the client reported `0x02` there by definition). This is the
     deadlock-free rule: a client with a lost `activation_complete` is still
     `pending` and must verify a fresh prepare before re-committing, so the
     committed phone sends the prepare FIRST and only awaits the
     (idempotent-duplicate) re-commit before re-sending
     `activation_complete`. When the client's confirmation reported
     `peer_state = 0x01` the phone sends NO prepare: that client is beyond
     commit and re-sends `activation_ack` directly (step 5) against a
     still-committed phone; a fully-active phone paired with that client has
     a `full` session instead. The client re-sends
     `activation_commit` in this session ONLY after verifying that prepare.
  1. If the client record is `pending` (commit or activation_complete was
     lost): the client re-sends `activation_commit` (recovery MAC, stage
     `0x01`) — freshly computed under THIS session's recovery keys and sent
     as this session's record at the current sequence number. Duplicate
     handling is SEMANTIC idempotency (same `ET`, same stage): each
     recovery session recomputes the MAC under its own fresh keys, so MAC
     values are never identical across sessions; a replay of a previous
     session's protected-record bytes is rejected by AEAD/tag/sequence
     validation and is never accepted as a valid duplicate. There is no
     state regression and no duplicate `activation_complete` side store;
     the phone re-enters or holds `committed_waiting_ack`.
  2. The phone validates the MAC, transitions `pending` →
     `committed_waiting_ack` if not already there, and re-sends
     `activation_complete` (recovery MAC, stage `0x02`) — the same
     statement, freshly computed under this session's keys with this
     session's sequence number. Re-sending a statement the client already
     validated under an earlier recovery session is the defined idempotent
     behavior.
  3. The client validates `activation_complete` (idempotent if the same
     statement was already validated under an earlier recovery session),
     ensures its record is `active`, and sends `activation_ack` (recovery
     MAC, stage `0x03`, fresh under the current session). A duplicate ack
     is accepted idempotently by a phone still in `committed_waiting_ack`.
  4. The phone validates the ack, transitions `active`, erases any
      remaining enrollment remnants of that pairing (OTP2-CRY-019e).
      Every interruption point resumes with these re-sends; nothing is lost
      or partially granted; OTP records stay unauthorized until step 4
      completes. Recovery's precondition, exactly: BOTH endpoints hold a
      usable persisted peer record — the phone's `pending`/
      `committed_waiting_ack` record came into existence when the client's
      `enroll_identity` was authenticated (OTP2-STA-010), and the client's
      mirror when the phone's was. An endpoint whose persisted record for
      this enrollment does NOT exist (an interruption before its peer's
      identity record arrived) cannot authenticate this peer at all
      (OTP2-REV-001): for that half-created enrollment recovery does not
      exist, and the only path is an explicit, user-opened NEW pairing
      window (fresh `pairing_id`, fresh code) after the one-sided record is
      cancelled/revoked or expires (OTP2-CRY-009a, OTP2-STA-010c/010d,
      OTP2-STA-012).
  5. **Active-client ack-loss case (explicit):** phone
     `committed_waiting_ack`, client record `active` (the ack was lost or
     the phone died before processing it). The client's record is `active`
     (it may still carry the incomplete mirror of OTP2-STA-010c until a
     phone-active confirmation arrives); on reconnect the phone's
     confirmation carries `peer_state = 0x02`
     (its record is not yet active), which tells the client the phone is
     awaiting activation completion. The client then re-sends
     `activation_ack` directly (recovery MAC, stage `0x03`, fresh under
     this session's keys) — it does NOT need to re-send commit, because its
     record is beyond `pending` and the phone is already in
     `committed_waiting_ack` (the only pre-`active` state that follows a
     validated commit). The phone validates the ack (idempotent duplicate
     accepted at the statement level), transitions `active`. Conversely, if
     the client reconnects while its record is still `pending`, steps 0-4
     run normally. A client reaching the session start in this class with a
     phone confirmation of `peer_state = 0x01` (phone already active) is the
     upgrade case of OTP2-SID-001: it upgrades immediately and that session
     is `full` on both endpoints — nothing activation-related is retained.
  6. **Class boundary after recovery:** a session that STARTED as
     `activation_recovery` remains recovery-only through its own teardown,
     even if both records become `active` during it. OTP records require a
     fresh, subsequently negotiated full session (OTP2-STA-010a,
     OTP2-SID-001).
- **OTP2-STA-010c** — incomplete-record expiry and continuity. The 24-hour
  bound is enforced as a BOOT-SCOPED MONOTONIC deadline: an incomplete
  activation record lives 86 400 seconds from its creation, measured on
  the endpoint's monotonic clock for the CURRENT boot. Alongside each
  below-`active` record the endpoint persists a random per-boot continuity
  token and the record's monotonic creation reading (meaningful only
  within that boot). At every process load or restart: if the persisted
  continuity token differs from the current boot's token, continuity
  cannot be proven — the endpoint DELETES every incomplete activation
  material (phone-side `pending`/`committed_waiting_ack` records;
  client-side records that are honestly below the client's own activation,
  per OTP2-STA-010d's mapping). Ordinary fully-`active` trust records are
  unaffected. Within a boot (a process restart with the same token), the
  monotonic deadline is evaluated normally: at creation + 86 400 s the
  record is deleted. On the client, a record whose state is `active` but
  whose phone-confirmation has not been received — no `reconnect_confirm`
  carrying `peer_state = 0x01` that passed AEAD and HMAC validation on any
  later session — is INCOMPLETE in trust (it cannot pull OTP data until the
  proof arrives), but it is NOT re-derivable material and therefore NOT
  discarded by client-side continuity or client-side local deadlines: the
  active-mirror record, its public `ET`, and its activation-pending flag
  are DURABLE (OTP2-STA-010d). It upgrades to ordinary fully-active trust
  when such a signed+encrypted phone-active confirmation arrives. The upper
  bound on the mirror's lifetime is the PHONE's authoritative below-`active`
  record lifetime (the same 86 400-second boot-scoped rule, OTP2-STA-010b/019g):
  when the phone's pending/committed record expires or is deleted by
  continuity loss, the phone treats the client as an unknown peer
  (OTP2-REV-001 — generic pre-auth failure; the client shows
  "Could not authenticate — re-pair required"), no stale mirror can ever be
  resurrected into trust, and re-pairing is then possible because the phone
  holds no record for the installation ID anymore. The client imposes NO
  independent 24-hour local trust-delete deadline on its active mirror.
  Expiry or continuity deletion never produces OTP access and never
  persists a tombstone; explicit cancellation or revocation removes material
  earlier.
  No wall-clock value participates in any activation-lifetime decision and
  this contract makes no wall-clock-safety claim. Platform-specific, REAL
  mappings of this rule for endpoints without a boot token or boot-scoped
  monotonic clock (the Chrome client) are defined in OTP2-STA-010d; every
  platform mapping MUST preserve the same conservative failure direction
  (pre-activation material is discarded on provable-loss-of-continuity
  signals; durable active-family trust is never widened nor destroyed, and
  mappings require reviewer approval in the re-review).
- **OTP2-STA-010d** — client platform mapping (Chrome), exact. Chrome
  extension runtimes expose neither a boot token nor a boot-scoped
  monotonic clock, so the two client-side record families map differently:
  (1) **Active-family trust records (`active` and the incomplete
  `active_activation_pending` mirror, with their public `ET`,
  activation-pending flag, and fingerprint metadata) are DURABLE**: they
  persist in durable browser-profile storage across service-worker
  restarts, browser reloads, and extension updates; `chrome.storage.session`
  disappearance, service-worker restart, or any continuity-token loss MUST
  NOT delete or downgrade them (deleting them would introduce restart- or
  update-induced active-trust loss that the RFC does not have). The mirror
  has NO independent client-side 24-hour delete deadline; its usable
  lifetime is bounded exactly as OTP2-STA-010c states above — by the
  PHONE's authoritative pending-record lifetime, surfacing to the user as
  the generic "Could not authenticate — re-pair required" failure and, for
  stale duplicates (only possible if the client kept a mirror whose phone
  record still exists), by the phone's explicit revocation path (OTP2-STA-012
  guard preserved). (2) **Genuinely-pending client records (the record is
  still `pending` — no `activation_commit` has completed on the phone)** map
  to a CONNECTOR-OWNED monotonic interval: the connector context (the
  document/tab driving the pairing) owns a
  `performance.now()`-based interval measured from the record's creation
  reading taken in the SAME connector context; the interval runs for exactly
  `pending_record_expiry_seconds = 86 400`. This is monotonic and
  uninterrupted within that one context. On CONTEXT LOSS (connector context
  closed/replaced — continuity of the interval cannot be proven) the
  fail-closed rule DELETES the pending material (a pre-activation record
  authorizes nothing but recovery, so cleanup is safe and conservative);
  no NEW elapsed epoch is ever trusted from a restarted service worker or
  any other later context (a fresh context cannot synthesize elapsed time
  against a creation reading it did not take). The pending record's
  persisted metadata is conservative (creation reading, record state) and
  its expiry evaluation never uses a wall clock. This mapping changes
  nothing about OTP2-STA-010c's phone-side rules (the phone has a real boot
  clock and enforces its own 86 400-second bound); it is the minimum
  implementable client mapping and remains subject to the re-review's
  approval.
  Consequence for re-pairing (kept deliberately — no tombstone, no silent
  replacement): when the client has discarded a genuinely-pending record
  (context loss) while the phone still holds a trust record for the
  installation ID (`pending`, `committed_waiting_ack`, or `active`), the
  client can neither recover (the ET-bound material is gone) nor re-pair
  (OTP2-STA-012 refuses a second enrollment for a held installation ID).
  The only path is an explicit phone-side revocation of that installation,
  after which a fresh pairing is possible. Recovery and permission UX MUST
  present this as the phone-side action ("revoke this installation on the
  phone, then pair again"), never as silent replacement. The DURABLE
  active mirror is deliberately outside this consequence (it is recoverable
  through signed recovery while the phone's record lives — OTP2-STA-010b).
- **OTP2-REC-013** — authenticated mutual removal record. On an existing
  authorized (full) session, either endpoint may send `removal_request`
  (`0x0205`, empty body). The receiving endpoint validates the session and
  then: deletes its trust record for the peer, sends `removal_response`
  (`0x0206`, empty body), and disconnects. The requester deletes its trust
  record after validating `removal_response`. Disconnect follows the
  requester's deletion; no further records flow after removal in either
  order. If the response is lost, the requester retains its record (offline
  removal semantics: the other endpoint may not have removed its local record
  until separately removed). Local-only removal is the UI path
  `removal_request`-free. Removal is authenticated by the session (it can
  only occur on an authentic, sequence-verified, full-authorization session);
  there is no additional MAC. Both endpoints erase the OTP-history references
  concerning the removed peer immediately.

---

## 6. Reconnect authentication

<a id="sec-reconnect"></a>

### 6.1 Hello, signatures, transcript

<a id="sec-reconnect-hello"></a>

Reconnect messages use the pre-auth plaintext frames (sec-plain-framing).

- **OTP2-REC-020** — `reconnect_hello` frame payload (`0x06`, either
  direction; the client sends first on a client-initiated connection, the
  phone replies with its own hello on the same connection):

  ```
  installation_id     16 bytes  (stable installation identity; plaintext, OTP2-SEC-003)
  ephemeral_pubkey    65 bytes  (fresh uncompressed SEC1 P-256 point, validated)
  nonce               16 bytes  (fresh random 128-bit reconnect nonce)
  role                u8: 0x01 = phone (A), 0x02 = client (B)
  protocol_version    u16be (1)
  caps_len            u16be, then capability bytes (1..16, sorted, unique; OTP2-CRY-012a)
  ```

  Fixed fields total 16 + 65 + 16 + 1 + 2 + 2 = 102 bytes, so
  `payload_length = 102 + caps_len`. This hello is distinct from the local
  Native Messaging `hello` request. Version mismatch, unknown capability,
  missing mandatory capability `0x01`, invalid point, or wrong role value
  rejects with `preauth_reject` (generic).
- **OTP2-REC-020a** — canonical reconnect transcript and signature input:

  ```
  rbase = "Veles reconnect transcript v1"     # 29 ASCII bytes (exact; len=29)
          || phone_hello_payload
          || client_hello_payload
  ```

  Signature frame payload (`0x07`): `role u8 || signature (64 bytes P1363
  fixed-width r||s, low-S-normalized, strict)`. The signature is
  ECDSA-with-SHA256 over `rbase` — `rbase` is hashed EXACTLY ONCE, as the
  ECDSA signing digest: `sig = ECDSA-P256-SHA256-sign(identity_key, rbase)`.
  Implementations offering prehashed APIs MUST supply `SHA-256(rbase)` as
  the prehash and MUST NOT hash again (a double hash is a breaking
  interoperability defect). The phone sends its signature first (role order
  A then B); the client verifies it against the stored peer identity public
  key before sending its own. Verification is the identical single-hash
  construction; it accepts only standard-P1363 fixed-width 64-byte encodings
  with `s` in the lower half-order; any other encoding rejects. A wrong,
  unverifiable, or non-canonical signature rejects with `preauth_reject`.
- **OTP2-REC-020b** — ECDH and key schedule (both peers have both ephemeral
  public keys after the hello exchange):

```
  ss  = X coordinate (32 bytes) of ecdh(own_ephemeral_priv, peer_ephemeral_pub)
        (validated: peer point valid, result not identity/all-zero → else reject)
  salt = SHA-256( "Veles reconnect salt v1"     # 23 ASCII bytes (exact; len=23)
                  || phone_nonce || client_nonce )
  reconnect_prk = HKDF-Extract-SHA256(salt, IKM = ss)
```

Then, one `HKDF-Expand-SHA256(reconnect_prk, info = label, L)` each:

  | Label (verbatim ASCII, exact length) | Output |
  |---|---|
  | `Veles reconnect B-to-A AES key v1` (33) | 32 bytes (client→phone record key) |
  | `Veles reconnect B-to-A nonce v1` (31) | 4 bytes (client→phone nonce prefix) |
  | `Veles reconnect B-to-A confirmation v1` (38) | 32 bytes (client confirmation key) |
  | `Veles reconnect A-to-B AES key v1` (33) | 32 bytes (phone→client record key) |
  | `Veles reconnect A-to-B nonce v1` (31) | 4 bytes (phone→client nonce prefix) |
  | `Veles reconnect A-to-B confirmation v1` (38) | 32 bytes (phone confirmation key) |
  | `Veles reconnect session ID v1` (29) | 32 bytes (session identifier) |

  Direction mapping: `client→phone` ≡ `B-to-A`; `phone→client` ≡ `A-to-B`,
  identical to the enrollment labels. Ephemeral keys, traffic keys, prefixes,
  session ID, and counters are fresh on every connection and are never reused
  (RFC).
- **OTP2-REC-020c** — reconnect confirmation. Both peers compute:

```
  reconnect_confirm_input = SHA-256(
      "Veles reconnect confirmation input v1"    # 37 ASCII bytes (exact; len=37)
      || rbase
      || phone_signature (64)
      || client_signature (64) )
  conf_A = HMAC-SHA-256(phone_confirmation_key,  reconnect_confirm_input)
  conf_B = HMAC-SHA-256(client_confirmation_key, reconnect_confirm_input)
```

  Each sends its 32-byte confirmation as the first protected record of its
  direction (`reconnect_confirm`, `0x0001`) — see below. A peer is
  authenticated only after both signatures pass and its confirmation
  verifies; neither side releases application records earlier (RFC).

### 6.2 Session start and teardown

<a id="sec-reconnect-confirm"></a>

- **OTP2-SID-001** — the first record in each direction after a successful
  reconnect exchange is `reconnect_confirm` (`0x0001`), sent at sequence 0 of
  that direction, encrypted with that direction's AES key and
  `prefix || u64be(0)` nonce. Its plaintext body is:

  ```
  confirmation    32 bytes  (conf_A by the phone, conf_B by the client)
  peer_state      u8: 0x01 = my record is in my endpoint's active family,
                       0x02 = my record is below active (phone: pending or
                       committed_waiting_ack; client: pending)
  ```

  Exact outbound values. A phone sends `0x02` while its record is `pending`
  or `committed_waiting_ack` and `0x01` once active. A client sends `0x02`
  while its record is `pending` and `0x01` as soon as its record is in the
  client active family — INCLUDING the incomplete
  `active_activation_pending` mirror (OTP2-STA-010c: a locally-activated
  client whose signed+encrypted phone-active proof has not yet arrived). A
  client never reports `0x02` for `active_activation_pending`, and 0x02 is
  never sent from a client that has passed its own activation.

  `peer_state = 0x02` marks an activation-recovery session
  (OTP2-STA-010b). The session's authorization class (sec-authz-classes) is
  FIXED at session start from the local record state and the peer's
  `peer_state` byte, and NEVER upgrades mid-session: an
  `activation_recovery` session stays recovery-only through its own
  teardown even if both records become `active` during it — with ONE
  ordered exception on the CLIENT side: the upgrade from
  `active_activation_pending` to fully-`active` is evaluated BEFORE class
  selection, from a valid (AEAD tag, session ID, sequence, HMAC) phone
  confirmation carrying `peer_state = 0x01` — the signed+encrypted
  phone-active proof. That session is then `full` on BOTH endpoints: the
  phone (record `active`, peer reports `0x01`) and the upgraded client both
  fix `full`. When the phone processed the activation ack on the enrollment
  connection, the first valid post-pairing reconnect is therefore a full
  session on both endpoints with NO intervening recovery-class session. If
  instead the phone's record is still `committed_waiting_ack`, that
  reconnect is `activation_recovery` on both (the lost-ack case,
  OTP2-STA-010b step 5) and the NEXT fresh reconnect is full on both. A
  client in `active_activation_pending` that receives `peer_state = 0x02`
  (phone still below active) does not upgrade: that session is
  `activation_recovery`, and the client re-sends the ack of
  OTP2-STA-010b step 5 instead of any OTP record.
  An application record may be sent by a peer only after
  that peer has: sent its own confirmation, received and verified the
  peer's confirmation, and the fixed session class permits the record type
  (sec-authz-classes). A confirmation that fails to
  verify tears the session down without authorization and erases keys.
  Sequence numbering continues at 1 for application records; the
  `reconnect_confirm` occupies 0 (OTP2-CRY-017).
- **OTP2-STA-015** — key erasure on teardown. On normal disconnect, link
  loss, terminal error, revocation, or session teardown, each endpoint erases
  (zeroizes where the platform supports it): ephemeral private keys, traffic
  AES keys, nonce prefixes, confirmation keys, session ID, and sequence
  counters. Nothing is reused after reconnect; a later connection derives
  everything fresh (RFC). Durable trust records (identity keys, peer records,
  fingerprints) persist independently.
- **OTP2-REV-001** — post-deletion generic failure. After a peer's trust
  record is deleted (revocation, mutual removal, expiry, storage loss, or
  key loss), a later reconnect from it receives exactly what any unknown peer
  would receive — the generic pre-auth path: `preauth_reject` then disconnect
  — with no authenticated proof of revocation and no tombstone. The client
  maps every unsuccessful authentication to the same user-visible state:
  "Could not authenticate — re-pair required" (never "revoked"). Revoking a
  currently connected installation immediately disconnects that session.

---

## 7. Protected record format

<a id="sec-record-format"></a>

Every post-handshake message is an AES-256-GCM protected record:

```
bytes 0-22   header (23 bytes, plaintext) — this whole header is the AAD
bytes 23..   ciphertext (0..4057 bytes)
last 16      GCM tag (AEAD authentication tag over ciphertext+AAD)
```

### 7.1 Header and AAD

<a id="sec-record-header"></a>

- **OTP2-CRY-016a** — header layout (all integers unsigned big-endian;
  "Veles network order" — distinct from RFC 9382's little-endian `len()`):

  | Offset | Size | Field | Value |
  |---|---|---|---|
  | 0 | 1 | `protocol_version` | `0x01` |
  | 1 | 1 | `schema_version` | `0x01` |
  | 2 | 1 | `direction` | `0x01` = client→phone, `0x02` = phone→client |
  | 3 | 2 | `record_type` | u16 (sec-record-types) |
  | 5 | 8 | `sequence_number` | u64 (0-based, per direction) |
  | 13 | 2 | `ciphertext_length` | u16 (0..4057) |
  | 15 | 8 | `session_id_prefix` | first 8 bytes of the 32-byte session ID (reconnect) or enrollment prefix (sec-enrollment-header) |

- **OTP2-CRY-016b** — AAD is exactly the 23-byte header. Associated data binds
  protocol version, schema version, session ID, direction, sequence number,
  record type, and ciphertext length (RFC). Nonce (12 bytes) =
  directional 4-byte nonce prefix `||` u64 big-endian sequence number
  (identical construction to enrollment nonces; independent counters and
  prefixes per direction). AEAD is AES-256-GCM as implemented by the platform
  (12-byte nonce, 16-byte tag); a tag that fails verification rejects the
  record without state mutation.
- **OTP2-CRY-016c** — total record size (header + ciphertext + tag) MUST be ≤
  4096 bytes (`record_max_bytes`); the bridge/transport bound is larger and
  independent (sec-bridge-bounds). Records larger than 4096 or with
  `ciphertext_length` inconsistent with the actual length reject.

### 7.2 Sequence enforcement and counters

<a id="sec-sequences"></a>

- **OTP2-CRY-017** — per-direction sequence space. Each direction accepts
  exactly its next expected unsigned sequence number (starting at 0 with
  `reconnect_confirm`); duplicate, skipped, reordered, unauthenticated,
  oversized, unknown-type, or cross-session records terminate the session,
  erase traffic keys (OTP2-STA-015), and produce no output. Counters are u64
  and never wrap: a direction whose counter has issued sequence
  `18446744073709551615` (u64 max) terminates the session before any further
  record is sent or accepted. Counters are never reused after a reconnect
  (fresh keys and fresh counters each connection).

### 7.3 Record type table

<a id="sec-record-types"></a>

- **OTP2-ENC-020** — `record_type` (u16, big-endian):

  | Type | Name | Direction | Body |
  |---|---|---|---|
  | `0x0000` | (reserved) | — | invalid; receiving/sending rejects |
  | `0x0001` | `reconnect_confirm` | both | sec-reconnect-confirm |
  | `0x0002` | `app_heartbeat` | both | empty (0 bytes) |
  | `0x0003` | `app_error` | both | 1-byte error code + `reason_len` u16 + reason UTF-8 (0..64); error codes below |
  | `0x0100` | `enroll_identity` | both | sec-identity-record (enrollment channel only) |
  | `0x0101` | `activation_commit` | client→phone | 32-byte MAC (sec-activation) |
  | `0x0102` | `activation_complete` | phone→client | 32-byte MAC |
   | `0x0103` | `activation_ack` | client→phone | 32-byte MAC |
   | `0x0104` | `enrollment_prepare` | phone→client | 32-byte MAC; HMAC over `ET || 0x00` |
  | `0x0200` | `pull_request` | client→phone | empty |
  | `0x0202` | `pull_events` | phone→client | 1-byte `event_count` (0..5), then that many OTP events (sec-otp-event) |
  | `0x0203` | `push_event` | phone→client | sec-otp-event (one event) |
  | `0x0204` | `event_ack` | client→phone | 16-byte `phone_installation_id` + 16-byte `event_id` (acknowledges one delivered event, pulled or pushed; settles any outstanding push for that event, OTP2-REC-032a) |
  | `0x0205` | `removal_request` | both | empty |
  | `0x0206` | `removal_response` | both | empty |
  | `0x0207` | `phone_policy_state` | phone→client | `policy` u8 (`0` unlocked-only, `1` display-on, `2` always) then `flags` u8: bit0 interactive, bit1 unlocked, bit2 currently eligible, bit3 `response_to_pull` (canonical correlation flag: set ONLY when this record is the blocked policy response to one `pull_request`, OTP2-REC-031), bits4..7 MUST be zero; eligible (bit2) MUST equal the policy evaluation over the observed bits. `0x0207` is phone→client only: no client-sent record sets or overrides phone policy, and the tag value `0x0201` is not assigned in v1 — a record with any type outside this table is rejected (terminates the session) |
  | other | — | — | rejected (terminates session) |

- **OTP2-ENC-021** — application error codes (`app_error` body's first byte):
  `0x01` `protocol_violation`, `0x02` `resource_exhausted`,
  `0x03` `session_expired`, `0x04` `enrollment_rejected`,
  `0x05` `unauthorized_record_class`. Unknown codes terminate the session.
  Sending or receiving `app_error` is terminal: after the error record, the
  sender closes the connection and both endpoints erase session keys. An
  error's reason string is bounded safe text (OTP2-SEC-005) with no secret
  content.

---

## 8. Application records

<a id="sec-app-records"></a>

Application records are permitted only on full-authorization sessions
(except `enroll_identity`/activation records on enrollment or
activation-recovery sessions; sec-authz-classes).

### 8.1 OTP event encoding

<a id="sec-otp-event"></a>

- **OTP2-REC-030** — one OTP event, exact encoding:

  ```
  phone_installation_id    16 bytes  (dedup key part 1)
  event_id                 16 bytes  (dedup key part 2; random per event)
  receipt_epoch_seconds    u64be     (epoch seconds of Android receipt; informative only)
  remaining_lifetime       u32be     (seconds; MUST be ≤ 600; authoritative)
  code_len                 u16be, then code     # UTF-8, 1..16 bytes (OTP2-ENC-003)
  merchant_len             u16be, then merchant # UTF-8, 0..64 bytes
  amount_magnitude         u64be     (minor units, ≥ 0)
  amount_scale             u8        (0..8 decimal places)
  currency                 3 bytes   ASCII "A"-"Z" exactly 3 (ISO 4217 alpha-3)
  ```

  Minimum body size: 44 + 2 + code_len + 2 + merchant_len + 8 + 1 + 3 where
  code ≥ 1. Bounded fields with different lengths reject. The amount value is
  `amount_magnitude × 10^(−amount_scale)` — exact decimal representation, no
  floating point anywhere in the protocol; rendering is the client's locale
  concern. Currency is mandatory and exactly three uppercase A–Z bytes.
  Amount and currency are mandatory because every Veles-matched OTP event
  carries a matched money regex result (RFC event model). The code has no
  protocol-level digit/charset restriction beyond valid bounded non-control
  UTF-8 1..16 bytes: bank SMS codes vary (digits, letters) and the phone's
  regex handler is the authority for what constitutes a code; the bound
  exists so a hostile phone cannot inflate fragments or reassembly buffers
  and a client can render it. Oversized behavior is exact: an event whose
  `code`, `merchant`, or any other field exceeds its bound is rejected WHOLE
  at the length-validation stage by every endpoint (OTP2-ENC-003) — no
  truncation, no clipping, no partial delivery, and no OTP text is ever
  shortened to fit a bound.
- **OTP2-REC-030a** — lifetime handling. Android is authoritative for expiry:
  it computes `remaining_lifetime` at record-creation time; it MUST be 0..600.
  Clients store receipt-local-time plus the received remaining lifetime and
  MUST NOT extend retention from their own clock (clock-skew proof). An event
  with remaining lifetime 0 is expired and MUST NOT be displayed, copied, or
  counted. All OTP retention is memory-only on every endpoint: at most five
  events globally, ten-minute maximum lifetime, cleared on disable, process
  death, reboot, browser profile shutdown, session-storage loss, pairing
  reset, or revocation. History is never durable (RFC; OTP2-PRD-004).
- **OTP2-REC-031** — pull semantics. `pull_request` has an empty body. The
  phone evaluates delivery policy (RFC: `Unlocked only` default, `Display on`,
  `Always`) and expiry immediately before responding, then answers with
  exactly one of:
  - `pull_events` with 0..5 currently eligible events
    (`event_count = 0` is the empty-history case), or
  - `phone_policy_state` (`0x0207`) with the canonical correlation flag
    `response_to_pull` (flags bit3) SET, when delivery policy blocks it right
    now. It is phone→client only: clients cannot set or override phone policy
    (no client-sent record changes it), and no second response ever follows
    one pull.

  Settlement of an outstanding pull (the client keeps AT MOST ONE unanswered
  pull at a time; `pull_outstanding_max` in limits.json) is exact:
  - an eligible `pull_events` response settles the outstanding pull;
  - a blocked `phone_policy_state` WITH flags bit3 set settles the
    outstanding pull (it is a complete answer — the blocked observation);
  - an UNSOLICITED `phone_policy_state` (bit3 clear) NEVER settles anything:
    it is a policy observation only. The phone sends these at full-session
    start and immediately before an eligible delivery after its policy
    observation changes;
  - with NO outstanding pull, a bit3-set `phone_policy_state` is a stale
    report: it is a fully authenticated record and remains accepted (its
    policy/eligibility bytes are still the phone's live observation), the
    client's pull bookkeeping never underflows, and no state effect beyond
    observing the policy occurs.
  Events blocked now remain in the phone's memory
  buffer until normal expiry and can be pulled later when eligible.
  Correlation and sequencing: exactly one response record answers one
  `pull_request`; the client keeps AT MOST ONE unanswered pull at a time
  (client-side obligation; `pull_outstanding_max` in limits.json) — the
  phone MAY process successive pull records (e.g. a retry after a slow
  exchange) and MUST answer them in record order; pull responses are
  ordinary protected records, so everything is processed strictly in
  sequence order (no reordering, OTP2-CRY-017). Repeated pulls are
  idempotent on the client (history dedup by
  (`phone_installation_id`, `event_id`); display history is not duplicated —
  clipboard behavior on repeated pulls is a product-policy concern (RFC
  §Copy behavior, OTP-18), not a protocol surface, and this document states
  no copy rule). After successfully decrypting and merging a `pull_events`
  response, the client sends one `event_ack` per event (same encoding and
  dedup idempotency as push acks, OTP2-REC-033); a pull ack for an event
  that was also outstanding as a push settles that push exactly as a push
  ack would (OTP2-REC-032a) — the phone records pull acks for
  observability only beyond that, and no delivery credit exists that could
  substitute for application acknowledgement (OTP2-GAT-013).
- **OTP2-REC-032** — push semantics. `push_event` carries exactly one event.
  The phone maintains per-peer delivery queues as event references (never
  independent OTP copies), rechecks policy/expiry immediately before
  encryption, and drops evicted/expired/newly-blocked events before
  encryption. Queue overflow (per-peer 16 messages/16 KiB, global
  128 messages/128 KiB) is resolved by not enqueueing the newest event; the
  event stays in the memory buffer until expiry, and the peer is not
  guaranteed a subsequent push (RFC: slow or blocked peers must not block
  others). Delivery of a pushed event is recorded when any valid
  `event_ack` for that event arrives — pulled or pushed delivery both
  settle (OTP2-REC-032a): **a transport write completion (`sent`,
  sec-bridge-messages; or successful GATT write) never substitutes for
  application acknowledgement.**
- **OTP2-REC-032a** — ack semantics and counter integrity. The `event_ack`
  body identifies only the event (`phone_installation_id`, `event_id`); it
  does NOT distinguish pull from push, and none is needed. Settlement rule:
  **any valid `event_ack`** — a record that passed full authentication (AEAD
  tag, sequence, class) and references an event that was actually delivered
  to this peer (by pull response OR push) — **settles (marks delivered) any
  outstanding push for that event**; duplicate acks for the same event are
  idempotent (no double credit, no error). A transport write completion
  (`sent`, sec-bridge-messages; or successful GATT write) never substitutes
  for application acknowledgement (OTP2-REC-032; OTP2-GAT-013).
  - **Unknown-event acks:** an `event_ack` referencing an event this peer
    never received is explicitly a SEMANTIC NO-OP: it is a fully valid
    record — its sequence number is consumed after tag validation and it
    mutates no state; it is neither an error nor a violation.
  - **Counter integrity:** a direction's expected-sequence counter NEVER
    advances without full AEAD tag validation: a record whose tag fails
    verification advances nothing and tears the session down; only a fully
    authenticated record consumes its sequence slot (OTP2-CRY-017).
  - **Races:** all records — pulls, responses, pushes, acks — occupy one
    direction-strict sequence space per session (OTP2-CRY-017). A
    `push_event` may arrive while a pull response (containing the same
    event) is in flight: both are valid sequence-ordered records; the
    client dedups by (`phone_installation_id`, `event_id`) and acks each
    delivered occurrence idempotently. "Response before later push" and
    "push before response" are both valid orderings needing no special
    handling beyond dedup + ack rules.

- **OTP2-REC-033** — push retransmission and idempotency. Pushes are
  app-level: at most one un-acked outstanding push at a time per session.
  After 10 s without an `event_ack` (monotonic), the phone may re-send the
  same event as a NEW record with a fresh sequence number (max 3 retransmits;
  then drop from the queue — the event may still be pulled while it lives).
  Receiving an event already known (same dedup key) is idempotent: display is
  not duplicated, and the receiver MUST re-ack. The ack references
  (`phone_installation_id`, `event_id`); an AUTHENTICATED, well-formed
  `event_ack` whose references match nothing this peer delivered is the
  unknown-event semantic no-op of OTP2-REC-032a: the record passes full
  authentication, CONSUMES its sequence slot, settles no delivery credit,
  mutates no state, and is not an error and not a violation — "mismatched"
  vs "unknown" is not a rejection boundary anywhere; only records that fail
  AEAD/tag validation, the session/sequence rule, or the class check are
  fatal (OTP2-CRY-017, OTP2-ENC-004). No delivered-credit and no
  state-mutation may result from any unknown/mismatched ack.
- **OTP2-REC-034** — heartbeat. Either side may send `app_heartbeat` (empty
  body) at most once per 30 s per direction; its only purposes are liveness
  and sequence flow. A session with no valid record in either direction for
  180 s is torn down by both endpoints (idle timeout; keys erased). Heartbeat
  abuse (rate beyond the limit) is otherwise ignored, with ONE terminating
  threshold: more than 4 heartbeats sent within any rolling 60-second window
  in one direction (i.e., the 5th heartbeat inside that window) terminates
  the session. The numeric rule is exactly the fixed pair
  (threshold 4, window 60 s) pinned by the state descriptor; it is not an
  arithmetic restatement of the 30 s spacing.
- **OTP2-REC-035** — authorization classes and exact type allowlists
  (sec-authz-classes). Every protected record type is classified:

  - **Framework records** (valid in EVERY session class, both directions,
    sequence-normal): `app_heartbeat` (`0x0002`), `app_error` (`0x0003`).
    These never authorize application data.
  - **Enrollment/activation records:** `enroll_identity` (`0x0100`,
    both directions), `enrollment_prepare` (`0x0104`, phone→client only),
    `activation_commit`/`activation_ack` (`0x0101`/`0x0103`,
    client→phone only), `activation_complete` (`0x0102`, phone→client only).
  - **Application records:** `pull_request` (client→phone), `pull_events`
    (phone→client), `push_event` (phone→client), `phone_policy_state`
    (phone→client only),
    `event_ack` (client→phone), `removal_request`/`removal_response` (both).
  - **Connection record:** `reconnect_confirm` (`0x0001`) — sequence 0 of
    each direction, exactly once per session; the session-start handshake
    record, valid in every class.

  Framework records and the connection record above are the ONLY types
  valid in every class. Per class, the allowed NON-framework types are:
  - `enrollment` (the original activation connection):
    `enroll_identity`, `enrollment_prepare`, `activation_commit`,
    `activation_complete`,
    `activation_ack` only; the connection terminates when activation
    completes and NO OTP record is ever authorized on it — even after both
    records reach `active` mid-connection (OTP2-STA-010a).
  - `activation_recovery` / `full_pending_peer`: `enrollment_prepare`,
    `activation_commit`,
    `activation_complete`, `activation_ack` only; the class is FIXED at
    session start and stays recovery-only through that session's teardown
    even if both records become `active` during it (OTP2-STA-010b.6). On
    `activation_recovery` the phone sends `enrollment_prepare` while its
    record is `pending` (before the client's commit, OTP2-STA-010b step 0);
    on `full_pending_peer` it likewise sends the prepare before any commit
    of the awaiting peer.
  - `full`: `pull_request`, `phone_policy_state`, `pull_events`,
    `push_event`,
    `event_ack`, `removal_request`, `removal_response` ONLY — enrollment
    identity and activation types are NOT authorized on a full session
    (receiving one is a protocol violation, terminating the session).
    A `full` session arises only from a fresh signed reconnect whose peer
    confirmation carried `peer_state = 0x01` (OTP2-SID-001); a fresh signed
    reconnect where the client upgraded from `active_activation_pending`
    through a valid phone-active confirmation is exactly such a session on
    both endpoints (OTP2-SID-001).

  A record type outside its class's list, or a valid type sent in the
  direction the table forbids, is a protocol violation (terminates session,
  erases keys; OTP2-ENC-004). Authentication (having a verified session)
  does not imply authorization (record class) — specifically, a recovered
  pending record never yields OTP data (OTP2-STA-010).

---

## 9. GATT transport, fragmentation, and resource accounting

<a id="sec-gatt"></a>

These apply to the phone (peripheral/GATT server) and every bare GATT client
(the watch). Desktop peers traverse the bridge, which performs the identical
framing on behalf of the connector; the connector never sees fragments.

### 9.1 GATT profile

<a id="sec-gatt-profile"></a>

- **OTP2-GAT-001** — frozen UUIDs:

  | Object | UUID |
  |---|---|
  | Veles OTP service | `8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51` |
  | `veles_write` characteristic (client→phone writes) | `8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51` |
  | `veles_notify` characteristic (phone→client notifications) | `8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51` |

  Rationale: single base UUID with generated low suffixes keeps Android
  characteristic lookup trivial (match 16-bit suffix of the base) and avoids
  collision with any Bluetooth SIG or common vendor assignment.
- **OTP2-GAT-002** — characteristic properties, frozen:
  - `veles_write`: Write Without Response only. Rationale: bounded small
    writes at fixed MTU; no write-response latency on the pairing-critical
    path; the application record framing above provides integrity
    (authenticated records) or idempotent re-derivation (plaintext handshake
    frames), so an ATT-level acknowledgment adds nothing.
  - `veles_notify`: Notify only (no Indicate). Rationale: fragments flow
    fastest without per-fragment ATT acknowledgment; sequence numbers (not
    ATT acks) provide exactly-once application ordering, and the fragmentation
    timeout governs stall cleanup.
  - Security permissions: none granted (no encryption required, no
    authenticated signing); these characteristics are unverified plain GATT
    by design (RFC). The characteristics reject reads; `veles_write` accepts
    writes only.
- **OTP2-GAT-003** — MTU. A bare GATT client MUST operate with a negotiated
  ATT MTU ≥ 185 (RFC). A connection whose negotiated MTU < 185 is refused:
  the phone disconnects it during GATT setup before any protocol record.
  Rationale: 185 yields 182-byte ATT payload, covering the fragmentation
  budget below with ≤ 24 fragments for the maximum record.
- **OTP2-GAT-004** — concurrent clients: at most 8 simultaneous GATT clients
  (RFC). The ninth connection is refused during setup. Each peer has an
  independent trust record, session, sequence spaces, queues, and failure
  boundary; a failed or slow peer cannot block another peer or local Android
  behavior (RFC).

### 9.2 Fragmentation framing

<a id="sec-fragmentation"></a>

- **OTP2-GAT-010** — fragment frame (transport-only; fragment IDs are not
  cryptographic sequence numbers; identical on both `veles_write` and
  `veles_notify`; a UNIFORM 4-byte header on EVERY fragment):

  ```
  byte 0     bit 7: final-fragment flag (1 for the last fragment, else 0)
             bits 0-6: fragment_index (0..127, starting at 0, no gaps)
  byte 1     transaction_id (u8: 1..255; the value 0 is INVALID — reject)
  byte 2-3   fragment 0: total_record_length (u16be, 1..4096 = reassembled
             record bytes: header+ciphertext+tag)
             fragments 1+: RESERVED — both bytes MUST be 0x00, else reject
  byte 4..   payload (every fragment carries exactly 4 header bytes plus
             payload; payload_len per fragment is at most MTU − 3 − 4)
  ```

  Size rules (exact): every non-final fragment carries
  `payload_len = negotiated_MTU − 3 − 4` bytes EXCEPT when fewer bytes
  remain, in which case the NEXT fragment is the final one; the final
  fragment carries exactly the remaining bytes of the record
  (`total_record_length − Σ prior payloads`), which may be as few as 0;
  the receiver enforces `Σ all payloads == total_record_length` EXACTLY at
  completion, and a mismatch discards the transaction. Writers MUST send
  fragments of the same transaction in order with no interleaving of other
  transactions (GATT preserve write order per characteristic), and a
  sender keeps at most one incomplete transaction per characteristic.
  Conservative tested floor (interop): the exact non-final size follows the
  negotiated MTU, and the contract additionally fixes the values every
  valid implementation MUST support at the MTU floor 185 (OTP2-GAT-003):
  `fragment_payload_bytes_at_min_mtu = 178` bytes (`185 − 3 − 4`) for
  non-final fragments and at most
  `⌈record_max_bytes / 178⌉ = 24` fragments per record (4096 = 23·178 + 2).
  The committed fragmentation fixtures are all exercised at MTU 185, the
  conservative floor; the committed vector
  `frag-mtu185-max-total-4096` pins the 24-fragment / 178-byte chunking of
  the largest record at exactly that floor. A sender MAY choose larger
  non-final fragments at a higher negotiated MTU (same formula) — that
  choice is the builder's; receivers never assume any size beyond the
  delivered totals (M1 disposition: policy kept as builder choice, floor
  made normative + tested).
- **OTP2-GAT-011** — reassembly rules. A receiver allocates a reassembly
  buffer on fragment 0 after validating: `transaction_id != 0`,
  `total_record_length` in 1..4096, and the peer's reassembly caps. Later
  fragments must carry `fragment_index` as an unbroken run starting at 0
  (the header's 7-bit index), a valid nonzero `transaction_id`, and
  reserved bytes exactly 0x0000 on non-first fragments; a gap, duplicate,
  index exhaustion beyond 127 fragments, zero `transaction_id`, wrong
  reserved bytes, an earlier `final` flag mid-transaction, or a final-
  length mismatch discards the whole partial transaction — transport-only
  loss, no session effect by itself. Max fragments per transaction is 128
  (indices 0..127); a record that cannot fit (e.g. 4096 bytes at an MTU
  whose capacity yields more than 128 fragments) is unschedulable and the
  sender refuses it at fragmentation time (the record layer's 4096-byte
  bound and MTU ≥ 185 keep every valid record within 24 fragments).
  Conflict rule: fragments of a second transaction with a `transaction_id`
  equal to a live incomplete transaction on the same connection are
  ignored; a second conflicting transaction within a rolling 60 s window
  disconnects the peer (bounded hostile behavior). When the final fragment
  completes a buffer and the exact-length rule passes, the reassembled
  1..4096-byte record is delivered to the record layer and the fragment
  state is erased.
- **OTP2-GAT-012** — timers and cleanup:
  - Inter-fragment timeout: 5.0 s between consecutive fragments of one
    transaction (monotonic). On expiry the partial transaction is discarded
    and its buffers are freed (transport-only). The timeout makes NO claim
    that the next fragment or record arrives: the missing record is
    detected ONLY when a later record of that direction is actually
    received out of sequence (record-layer rule → teardown), by peer
    disconnect, or by the session idle timeout — if nothing further ever
    arrives, the session ends at the idle timeout with keys erased, not
    before.
  - Reassembly accounting per peer: 2 incomplete transactions TOTAL and
    8 KiB total bytes (RFC) — this is a per-peer ceiling across the peer's
    inbound traffic, NOT an allowance of 2 per direction (each endpoint has
    exactly one inbound characteristic per peer anyway, so split accounting
    is impossible by construction). Global: 16 incomplete and 64 KiB (RFC).
    Exceeding a cap refuses/drops the newest transaction before allocation.
  - Delivery queues per peer: 16 messages and 16 KiB; global: 128 messages
    and 128 KiB (RFC); overflow policy per OTP2-REC-032.
  - Disconnect cleanup: within 1 s of link loss (or explicit disconnect) the
    endpoint drops all fragment and reassembly state, delivery queues, and
    erases session keys for that peer (OTP2-STA-015). Nothing carries across
    connections.
- **OTP2-GAT-013** — transport-write semantics: a completed GATT write (or a
  bridge `sent` event, sec-bridge-messages) confirms byte transfer only. It is
  never an application acknowledgement (OTP2-REC-032), never an
  authentication result, and never a delivery guarantee to the application
  layer.

### 9.3 Watch-specific notes

<a id="sec-watch"></a>

- **OTP2-WAT-001** — the watch is a bare GATT central using the same
  protocol and ceremony: no new pairing window, limits, or protocol changes;
  Android treats watches as ordinary peers (RFC). Its `device_type` is
  `watch` (`0x02`, mandatory in the identity record).
- **OTP2-WAT-002** — OTP-02's fixture set is host-runnable Rust consumable by
  later watch-crate tests without a browser or JNI dependency; watch
  firmware beyond the protocol surface (design sketch, PIN UX, battery) is
  out of scope here (RFC deferred items). The local watch PIN never appears
  in any protocol message (RFC) and is therefore absent from this document's
  record tables.

---

## 10. Native Messaging envelope v1

<a id="sec-bridge"></a>

This section freezes the local control envelope between the Chrome connector
and the Rust/Tauri native bridge. The bridge owns OS BLE discovery and
plain-GATT byte transport only; it never parses, serializes, inspects, or
remaps authenticated Veles record fields as JSON, holds no trust records or
keys, and stores nothing durable (RFC). The machine-readable strict schema
derived from this section is `protocol/v1/envelope.schema.json` (JSON Schema
2020-12); like every derived artifact it can never override this section, and
a disagreement is a defect that fails the executable checks.

### 10.1 Framing and JSON rules

<a id="sec-bridge-framing"></a>

- **OTP2-BRD-001** — transport framing: each side writes
  `u32le byte_length || JSON bytes` (Chrome's Native Messaging framing: a
  4-byte native-endian unsigned length — all supported platforms are
  little-endian, so this is specified as little-endian — followed by UTF-8
  JSON). `byte_length` MUST be 1..65536 in both directions (stricter than
  Chrome's documented host→extension 1 MiB limit and its 4 GB
  extension→host limit; 64 KiB keeps every event comfortably below Chrome's
  1 MiB ceiling and bounds a hostile host's or extension's output).
- **OTP2-BRD-002** — JSON strictness: UTF-8 only; the raw JSON text is
  inspected for duplicate object keys BEFORE ordinary parsing (duplicates
  reject); trailing bytes after the JSON document reject; unknown fields
  reject; all numbers are integers (non-integer numbers reject); integer
  values outside their field's declared range reject; strings are valid UTF-8
  with declared bounds; a JSON `null` value is never accepted for any field —
  optional fields are omitted entirely when absent. Field order is
  irrelevant; absence of a required field rejects.
- **OTP2-BRD-002a** — string length units. Every string bound in this
  envelope section is a UTF-8 BYTE length, not a code-point or UTF-16-code-unit
  count: `label` 1..64 UTF-8 bytes, `platform_hint` 1..256 UTF-8 bytes,
  `bridge_version` 1..64 UTF-8 bytes, error `stage` 1..32 bytes (its
  restricted `[a-z0-9_]` alphabet makes characters and bytes equal there),
  `request_id` 1..64 ASCII bytes. A string whose UTF-8 encoding exceeds its
  byte bound rejects. (`JSON Schema`'s `maxLength` counts UTF-16 code units,
  so the derived envelope.schema.json is an outer shape gate only; the
  test-only envelope checks enforce the byte-exact bounds.)
- **OTP2-BRD-003** — identifiers and formats:
  - `request_id`: string matching `^[A-Za-z0-9._-]{1,64}$`, connector
    generated, unique among currently outstanding requests. A request ID MAY
    be reused once its terminal completion has been delivered to the
    connector. It MUST NOT be reused while the request is outstanding and
    MUST NOT be reused while its completion event is still awaiting delivery
    inside the bounded output queue (OTP2-BRD-030a): such a second live use
    is ambiguous — a second correlated error for it could not be
    distinguished from a duplicate terminal — so the host emits nothing,
    releases its retained state, closes the port, and exits with exit code 2
    (OTP2-BRD-010 fail-closed path); the connector synthesizes the unresolved
    requests once (OTP2-BRD-020).
  - handles (`scan_handle`, `device_handle`, `connection_handle`): strings
    matching `^[0-9a-f]{16}$`, process-local, random, never reused within one
    host process, and never representing Veles identity.
  - `service_uuid`: canonical lowercase hyphenated UUID string.
  - `platform_hint`: string of 1..256 UTF-8 bytes, no control characters,
    opaque to the bridge beyond a display/OS search hint.
  - `record_base64`: standard base64 (RFC 4648 alphabet, with padding, no
    whitespace); decodes to 1..5120 bytes. Decoding MUST be canonical
    (non-canonical trailing bits reject). Base64 is byte-preserving envelope
    transport only (OTP2-CON-004) and is not the authenticated record schema.
  - `bridge_version`: string 1..64 UTF-8 bytes; `platform`: one of `windows`,
    `macos`.
  - `label` (device label as observed by the OS): string of 1..64 UTF-8 bytes,
    valid UTF-8, no control characters; opaque to the bridge beyond display.
  - `advertisement`: canonical base64 under the identical decoding rules as
    `record_base64`, decoding to 1..256 bytes (OTP2-BRD-030); opaque metadata.

### 10.2 Negotiation

<a id="sec-bridge-negotiation"></a>

- **OTP2-BRD-010** — negotiation state machine. On stdin EOF or port closure
  the host stops scans, disconnects every link, clears process memory, and
  exits. Before `ready`, `hello` is the only valid request; `ready` or a
  correlated `error` are the only valid host responses; every other
  pre-`ready` input is rejected:
  - A recognizable pre-`ready` non-`hello` message with a usable `request_id`
    and `type` gets a correlated `error` (code `invalid_request`,
    recoverability `fatal`, stage `pre_ready`; `envelope_version: 1`) which
    the host flushes to stdout before exiting with exit code 2. No BLE work
    occurs.
  - Input without a usable `request_id`/`type` (unparseable JSON, wrong
    envelope version, duplicated keys, oversize length, invalid UTF-8) gets
    NO unsolicited JSON response and triggers NO BLE work; the host flushes
    nothing and exits with exit code 2 (fail closed, silent).
- **OTP2-BRD-010a** — `hello` request:
  `{envelope_version: 1, type: "hello", request_id, api_versions? |
  api_version_range?}` — exactly one of `api_versions` (array of 1..16
  unique integers, ascending, each 1..1048576) or `api_version_range`
  (`{min, max}`, both integers 1..1048576, min ≤ max). In production both
  sides' supported sets contain only 1; a hypothetical 2 is used only in
  negotiation fixtures (OTP2-CON-003). Repeated `hello` after ready, any
  malformed or missing piece, or an unsupported envelope version is rejected
  by the rules above.
- **OTP2-BRD-010b** — `ready` response (success completion of `hello` ONLY):
  `{envelope_version: 1, api_version, type: "ready", request_id, bridge_version,
  platform, capabilities}` where `api_version` is the highest mutually
  supported version (highest-overlap rule, RFC); `capabilities` is an array of
  0..16 strings; v1 defines exactly one defined value `veles_record_v1`
  (the bridge speaks the frozen record/fragment framing of this document).
  Unknown capability strings reject `ready` processing with a correlated
  `error` (code `invalid_request`, stage `negotiation`) — fail closed.
  Host-side and connector-side are distinguished exactly: the HOST rejects a
  ready-time capability/field problem with the correlated `error` above; the
  CONNECTOR, receiving an invalid `ready` (unknown capability string,
  duplicate caps, wrong fields, or a malformed version/correlation), has no
  correlated-error message defined in this envelope to send back to the host —
  there is no connector→host error REQUEST in v1 — so the connector fails the
  negotiation LOCALLY: it treats the session as unauthenticated, stops
  driving the bridge, and closes the Native Messaging port; it synthesizes
  its own terminal failure for any outstanding requests locally
  (OTP2-BRD-020). No JSON error is sent by the connector over this pipe in
  either direction's failure path; both directions' JSON flows remain
  request/event per the tables below.
- **OTP2-BRD-010c** — no overlap: the host replies with a correlated
  `error` `{envelope_version: 1, type: "error", request_id, code:
  "incompatible_api", recoverability: "fatal", stage: "negotiation"}`, flushes
  it to stdout, and exits. No BLE operation of any kind precedes negotiation.
- **OTP2-BRD-011** — post-`ready` messages: every request and event carries
  both `envelope_version: 1` and the selected `api_version`; a mismatch with
  the negotiated version rejects (correlated error where a request ID
  exists). Unknown message types, unknown request IDs, unknown/duplicate or
  post-release handles, duplicate terminal responses, malformed JSON, invalid
  base64, and oversized fields fail closed within the per-request and process
  limits below. Post-ready input that cannot be correlated at all —
  unparseable JSON, wrong envelope version, duplicated keys, oversize
  framing, or a missing/malformed request ID — produces no JSON response and
  exits with exit code 2 after the full orderly release of OTP2-BRD-010: the
  host stops every scan, disconnects every link, cancels pending writes,
  releases all handles and retained state, and exits — emitting no further
  JSON. The connector observes the exit/port closure and synthesizes the
  failures of its still-outstanding requests locally (OTP2-BRD-020).

### 10.3 Requests and events

<a id="sec-bridge-messages"></a>

- **OTP2-BRD-020** — request table (post-`ready` unless noted):

  | Request | Fields (besides envelope/version/request_id) | Terminal success event |
  |---|---|---|
  | `hello` | `api_versions` xor `api_version_range` | `ready` (pre-ready only) |
  | `start_scan` | `service_uuid` | `scan_state` (`status: "started"`, `scan_handle`) |
  | `stop_scan` | `scan_handle` | `scan_state` (`status: "stopped"`) |
  | `connect` | exactly one of `device_handle` (from a `device_found` of this process) or `platform_hint` | `connected` (`connection_handle`, optional refreshed `platform_hint`) |
  | `disconnect` | `connection_handle` | `disconnected` (correlated) |
  | `send` | `connection_handle`, `record_base64` (exactly one, 1..5120 decoded bytes) | `sent` |
  | `shutdown` | (none) | `shutdown_complete` |

  Every request terminates in exactly one correlated operation-specific
  success event or one correlated `error` (exactly-once terminal completion,
  request-ID correlated; duplicates reject). The host emits the terminal
  event while stdout is available; if the native port fails — or the host
  exits for any other reason while requests are still unresolved
  (OTP2-BRD-030a/011) — the connector synthesizes one correlated `error` per
  outstanding request locally: code `internal`, recoverability `fatal`,
  stage `port_closed`, never over stdout, issued exactly once per request.
  These terminal-error rules apply:
  - `connect` targeting a device already connected via another `connect`
    fails with a correlated `error` (`busy`, `retryable`).
  - An in-flight `send`/`connect` terminated by `disconnect`, link loss, or
    port failure ends with a correlated `error` (`send_failed` /
    `connect_failed`, `fatal`) before the releasing `disconnected` event; the
    terminated request never later reports `sent`/`connected`, and a send
    whose GATT writes had not completed is never misreported as `sent`
    (OTP2-BRD-022.5). Writes cancelled this way are dropped locally.
  - A request whose terminal event is still pending after its 30 s bound
    (OTP2-BRD-030; `start_scan`/`stop_scan` exempt — lifecycle events govern
    scans) is terminated with a correlated `error` (`timeout`) and never
    completes afterwards.
  - `shutdown` terminates every other outstanding request with a correlated
    `error` (`internal`, `fatal`), emits the remaining unsolicited lifecycle
    events (scan stops, disconnects for open links), and then reports
    `shutdown_complete` correlating the `shutdown` request as the FINAL JSON
    message before stdout closes (OTP2-BRD-022.6).
- **OTP2-BRD-021** — event table:

  | Event | Fields | Correlation |
  |---|---|---|
  | `ready` | `api_version`, `bridge_version`, `platform`, `capabilities` | correlates `hello` |
  | `device_found` | `scan_handle`, `device_handle`, `label` (1..64 UTF-8 bytes), optional `platform_hint` (≤256 UTF-8 bytes), optional `advertisement` (base64, ≤256 decoded bytes) | unsolicited (no `request_id`) |
  | `scan_state` | `scan_handle`, `status` (`started`/`stopped`) | correlated (`started`/`stopped`) or unsolicited (scan interruption/expiry, no `request_id`) |
  | `connected` | `connection_handle`, optional `platform_hint` | correlates `connect` |
  | `disconnected` | `connection_handle` | correlates `disconnect`, or unsolicited link loss (no `request_id`) |
  | `sent` | `connection_handle` | correlates `send` |
  | `message` | `connection_handle`, `record_base64` (exactly one complete reassembled record, opaque) | unsolicited |
  | `shutdown_complete` | (none) | correlates `shutdown` |
  | `error` | `code`, `recoverability` (`retryable`/`fatal`), `stage` (≤32 bytes `[a-z0-9_]`), optional `request_id`, optional `scan_handle` xor `connection_handle` (the failing operation's handle, at most one) | correlated or unsolicited |

  `error` codes (bounded enum): `incompatible_api`, `invalid_request`,
  `unknown_handle`, `unknown_request`, `invalid_base64`, `oversize_record`,
  `cap_exceeded`, `busy`, `scan_failed`, `connect_failed`, `send_failed`,
  `timeout`, `internal`. `error` carries no
  sensitive payload (RFC). Correlation rule: an event carries `request_id` if
  and only if it is a correlated completion of one request (`ready`,
  `connected`, correlated `disconnected`, `sent`, `shutdown_complete`,
  correlated `scan_state`, correlated `error`); unsolicited events
  (`device_found`, unsolicited `scan_state`/`disconnected`, `message`,
  unsolicited `error`) MUST NOT carry `request_id`.
- **OTP2-BRD-022** — stdout ordering (authoritative; RFC-fixed):
  1. A correlated `scan_state: started` precedes every `device_found` for
     that scan; `scan_state: stopped` follows its last `device_found`.
  2. `connected` precedes every `message` or unsolicited `disconnected` for
     its handle.
  3. A requested `disconnect` first terminates affected in-flight requests
     with correlated `error`, then emits exactly one correlated
     `disconnected`; no separate lifecycle event exists.
  4. Asynchronous link loss: first correlated `error` for each affected
     in-flight request, then exactly one unsolicited `disconnected`; no later
     `sent` or `message` may use that handle.
  5. A `sent` confirms only completed GATT writes — never an application
     acknowledgement and never a guarantee of Android-side processing
     (OTP2-REC-032; OTP2-GAT-013).
  6. `shutdown`: every other pending request gets a correlated `error`, then
     remaining unsolicited lifecycle events (no request IDs), then
     `shutdown_complete` is the FINAL JSON message before stdout closes.
  7. An uncorrelatable failure (request already terminated, port about to
     close) is emitted as an unsolicited `error` only when stdout is usable.

### 10.4 Bounds, backpressure, freeze/overflow policy

<a id="sec-bridge-bounds"></a>

- **OTP2-BRD-030** — process and request bounds (RFC-fixed numeric ceilings
  preserved where the RFC fixes them; the remaining ceilings are builder
  choices, itemized in `protocol/v1/limits.json`):
  - Concurrent outstanding requests ≤ 64; a 65th is rejected with correlated
    `error` (`cap_exceeded`, `retryable`).
  - Open scans ≤ 2; open connections ≤ 32.
  - Scan behavior: a scan runs until `stop_scan` or until a 300 s cap
    (then an unsolicited `scan_state: stopped` without request ID).
    Discovery events are bounded: ≤ 64 `device_found` per rolling second
    and ≤ 1024 per scan; the bridge coalesces/drops beyond the rate rather
    than queuing unbounded. Independently, the process retains at most 1024
    currently discovered device handles in a bounded cache (FIFO eviction of
    the oldest entry that is not currently connected; a `connect` naming an
    evicted handle fails with correlated `unknown_handle`; a later
    rediscovery yields a fresh handle).
  - Per-request timeout: 30 s (then correlated `error` `timeout`).
    `connect` and `send` complete or fail within it; scans are exempt
    (lifecycle events instead).
- **OTP2-BRD-030a** — output queue and renderer-freeze policy (fail closed):
  the host maintains a bounded pending output queue: ≤ 128 queued JSON
  messages and ≤ 256 KiB queued bytes (whichever bound is hit first blocks
  further queueing). While stdout is blocked (frozen renderer), completions
  and events accumulate only within these ceilings; every queued completion
  continues to hold its request ID (no reuse of an id whose completion is
  queued, OTP2-BRD-003). On queue overflow — no room for the next outbound
  message — the host FAILS CLOSED and does not drop-and-continue: the
  overflowing message is not accepted, stdin is no longer read, every scan
  stops, every link is disconnected, all writes in flight are cancelled,
  every handle and retained state is dropped, and the process exits. Nothing
  further is queued or emitted: stdout keeps exactly the message order
  already delivered, and no message is reordered or dropped mid-stream. The
  lost completions are covered by the native-port-closure rule
  (OTP2-BRD-020/OTP2-BRD-032): the connector synthesizes one correlated
  `error` per still-unresolved request locally, exactly once; there is no
  `overflow` error code and no resurrection of an undelivered `sent`. A
  terminal completion delivered to a frozen renderer does not alter that
  truth: `sent` is emitted only for sends whose writes actually completed
  (no misreporting). The overflow exit is its own exit code (`4`,
  OTP2-BRD-032).
- **OTP2-BRD-031** — limits echo the Veles record bound: `record_base64`
  decodes to 1..5120 bytes (an independent transport bound; the Veles record
  layer separately enforces its own 4096-byte maximum and rejects larger
  records at the record layer without involving the bridge). 5120 bytes
  base64-encode to at most 6828 characters (+ field/framing overhead) — well
  inside the 65536-byte message cap.
- **OTP2-BRD-032** — exit codes, complete and explicit:
  `0` after `shutdown` or clean stdout EOF
  handshake; `2` for rejected input fail-closed with no usable correlation —
  pre-`ready` (both the silent path and the correlated `invalid_request`
  path after flushing it, OTP2-BRD-010) and post-`ready` uncorrelatable input
  (OTP2-BRD-011); `3` for `incompatible_api` after flushing the correlated
  error; `4` for the output-queue overflow fail-closed orderly exit
  (OTP2-BRD-030a: all scans stopped, all links disconnected, writes
  cancelled, retained state released, nothing further emitted); any crash or
  other exit code is a host failure the connector treats as native-port
  closure.

---

## 11. State transition tables

<a id="sec-states"></a>

All timeouts and rate windows use monotonic clocks. Tables name: input,
current state, validation, effect, next state, timeout action, and failure
action. Failure actions are always: reject without state mutation (pre-auth
inputs), or teardown + erase keys (authenticated sessions), never silent
continuation.

### 11.1 Phone pairing states

<a id="sec-states-pairing"></a>

| Current | Input | Validation | Effect | Next | Timeout/failure |
|---|---|---|---|---|---|
| `idle` | enable pairing | — | generate `pairing_id`+code, show code | `window_open` | 300 s → `idle`, erase code |
| `window_open` | enable pairing (again) | — | reject (one window) | `window_open` | — |
| `window_open` | `pairing_request` | framing, caps, rate slot free | allocate pA slot (before scalars if rate-exceeded → generic reject) | `spake_in_flight` | inactivity 30 s → `window_open` |
| `spake_in_flight` | 2nd `pairing_request` | — | generic reject (one in flight) | `spake_in_flight` | — |
| `spake_in_flight` | `pairing_client_point` | framing, pairing id, point validity → else reject BEFORE consumption (generic reject + disconnect; ephemeral execution state erased; next attempt on a fresh connection only); then consume attempt (non-refundable) | compute `cA` | `awaiting_cB` | inactivity 30 s → `window_open` (attempt consumed); window deadline passed → generic reject, no computation |
| `awaiting_cB` | `pairing_confirm_b` | MAC verifies → else consume already-made attempt, generic fail | derive enrollment keys (memory-only, both directions; OTP2-CRY-019e); send own `enroll_identity` (first enrollment record, RFC order; `device_type` 0x00) | `enrollment_protected` (awaiting client identity) | inactivity 30 s → `window_open` |
| `enrollment_protected` | `enroll_identity` (client record) | authenticate (AEAD); verify own-record fingerprint (OTP2-REC-011a); device_type ∈ {desktop, watch}; duplicate-installation check (OTP2-STA-012); bounds | derive `ET` (public) from the canonical identity bodies, atomically persist the COMPLETE `pending` incl. `ET` (public fields only; a persist failure/crash publishes nothing and leaves no usable record), then send `enrollment_prepare` — only after durable success (OTP2-STA-010a) | `awaiting_commit` | inactivity 30 s → `idle` (window closed; conditional continuation per the note below — recovery where a persisted peer record exists, repair via a fresh explicit window before the peer identity was persisted) |
| `awaiting_commit` | `activation_commit` | MAC (stage 0x01, enrollment activation keys; idempotent duplicate accepted) | transition; persist `committed_waiting_ack` (public fields only) — byte-equal to the pending candidate except the state field | `committed_waiting_ack` | inactivity 30 s → `idle` (signed recovery only — a persisted peer record exists here by construction, OTP2-STA-010b) |
| `awaiting_commit` | `enrollment_prepare` (rx) | — (0x0104 is phone→client only) | direction violation: generic reject | `idle` | — |
| `committed_waiting_ack` | `activation_ack` | MAC (stage 0x03, enrollment or recovery activation keys per session class) | transition to `active`; erase code/SPAKE2/enrollment keys | `active` | inactivity 30 s → `idle` (signed recovery only — a persisted peer record exists here by construction, OTP2-STA-010b) |

Note: the pairing code is consumed and the window closed at the phone's
successful `cB` verification (OTP2-CRY-009a; the `awaiting_cB` →
`enrollment_protected` transition carries those effects). A connection that
disconnects BEFORE that verification leaves the phone in `window_open`
(attempt consumed where the rule says so), resumable within the window —
the five-attempt per-`pairing_id` limit and the rolling limits still bound
total attempts (the sixth attempt is refused; generic failure, attempts
non-refundable, OTP2-CRY-008). A disconnect, inactivity timeout, or teardown
AFTER that instant leaves the pairing machine's window closed (`idle`): the
code stays consumed. Continuation is then CONDITIONAL on persisted peer
records existing on BOTH endpoints: where the peer's identity record was
authenticated and the durable `pending`/`committed_waiting_ack` record was
persisted before the interruption, the exchange resumes exclusively through
signed reconnect recovery (OTP2-STA-010b) — never through a second code
entry, and never through a second enrollment while the phone still holds the
record (OTP2-STA-012). An interruption BEFORE the peer's identity record was
authenticated/persisted (e.g. directly after the verified `cB`, while the
phone awaits the client identity, or symmetrically on the client before its
own record exists) leaves at least one endpoint with NO usable persisted
peer record: for it, recovery does not exist — its reconnect fails with the
generic unknown-installation path (OTP2-REV-001) — and the only path is an
explicitly user-opened NEW pairing window (fresh `pairing_id`, fresh code;
the old code stays consumed/one-use). A half-persisted one-sided `pending`
record stays INACTIVE (no OTP, bounded recovery only, and unmatchable
against a record-less peer) until it is explicitly cancelled or revoked, or
expires under OTP2-STA-010c/010d, after which a same-installation
re-enrollment succeeds (OTP2-STA-012).

### 11.2 Client pairing states

<a id="sec-states-pairing-client"></a>

| Current | Input | Validation | Effect | Next | Failure |
|---|---|---|---|---|---|
| `idle` | user enters code, connect | scanned phone selected | send `pairing_request` | `awaiting_offer` | timeout 30 s → visible failure |
| `awaiting_offer` | `pairing_offer` | version/caps/point validity; build `ctx` | compute `pB` | `awaiting_ca` | reject/disconnect → visible generic failure |
| `awaiting_ca` | `pairing_confirm_a` | MAC verifies; build `ctx`; verify caps | send `pairing_confirm_b` | `awaiting_phone_identity` | generic fail |
| `awaiting_phone_identity` | `enroll_identity` (phone record) | authenticate record (AEAD); verify own-record fingerprint (OTP2-REC-011a); bounds/device_type | derive `ET` (public; both canonical bodies now known), atomically persist the durable COMPLETE `pending` incl. `ET` (public fields only; a persist failure/crash publishes nothing — the own identity is never released — and leaves no usable record), then release own identity record (OTP2-STA-010a) | `awaiting_prepare` | generic fail |
| `awaiting_prepare` | `enrollment_prepare` (record `0x0104`, stage 0x00) | MAC verifies | prepare verified for THIS enrollment connection | `ready_to_commit` (prepare-verified) | prepare never arrives → timeout 30 s → the client sends no commit; recovery later (OTP2-STA-010b step 0) |
| `awaiting_prepare` | `activation_commit` | — | NOT SENDABLE without a verified prepare (no transition; state machine never emits it) | — | — |
| `awaiting_phone_identity` | `enrollment_prepare` (early) | prepare must not precede the client's identity release | reject as ordering violation, erase pairing state | `idle` | — |
| `ready_to_commit` | — | pending record persisted, prepare verified, `ET` derived | send `activation_commit` | `awaiting_complete` | timeout 30 s → recovery later (OTP2-STA-010b) |
| `awaiting_complete` | `activation_complete` | MAC (stage 0x02) | become `active`; send `activation_ack`; (enrollment keys erased per OTP2-CRY-019e) | `active` | generic fail |
| `active` | — | — | display `fp_pair` fingerprint (OTP2-REC-011) | — | — |

### 11.3 Session states (role-explicit)

<a id="sec-states-session"></a>

Client (central, initiates) rows are marked C; phone (peripheral) rows P.
Each role runs its own rows concurrently as messages arrive; the table
describes that role's transitions only. All invalid inputs at any row are
rejected per OTP2-ENC-004. The phone's hello-validation row enforces the
record-existence rule: a sender installation ID matching no trust record
(unknown or deleted peer) yields the generic pre-auth path
(OTP2-REV-001) — `preauth_reject` plus disconnect, no phone hello, no
signature, and no OTP data; the client maps it to "Could not authenticate —
re-pair required".

| Role | Current | Input | Validation | Effect | Next | Timeout/failure |
|---|---|---|---|---|---|---|
| C | (none) | connect, send hello | — | — | `awaiting_phone_hello` | 30 s → visible generic failure |
| P | (none) | client hello | framing/version/caps/point; installation ID MUST match an actual trust record (active, pending, or committed_waiting_ack), else generic `preauth_reject` (OTP2-REV-001) | look up record | reply phone hello | mismatch → `preauth_reject` |
| C | `awaiting_phone_hello` | phone hello | framing/version/caps/point overlap | compute rbase | `awaiting_phone_signature` | mismatch → `preauth_reject` |
| P | — (after replying hello) | both hellos seen | rbase defined | send phone signature | `awaiting_client_signature` | 30 s → disconnect |
| C | `awaiting_phone_signature` | phone signature | verify against stored peer identity key | send client signature | `keys_derived` | invalid → `preauth_reject` |
| C | `keys_derived` | client signature sent | both signatures verified | derive ECDH/HKDF; send `reconnect_confirm` (seq 0) | `awaiting_phone_confirm` | — |
| P | `awaiting_client_signature` | client signature | verify | derive ECDH/HKDF; send `reconnect_confirm` (seq 0) | `awaiting_peer_confirm` | invalid → `preauth_reject` |
| C | `awaiting_phone_confirm` | phone `reconnect_confirm`, `peer_state 0x01`, client record `active` (ordinary) | HMAC + `peer_state` | set class `full` | `session_open` | invalid → teardown+erase |
| C | `awaiting_phone_confirm` | phone `reconnect_confirm`, `peer_state 0x01`, client record `active_activation_pending` | HMAC valid — the signed+encrypted phone-active proof | upgrade the incomplete mirror to fully-`active` FIRST (OTP2-STA-010c), then set class `full` | `session_open` | invalid → teardown+erase |
| C | `awaiting_phone_confirm` | phone `reconnect_confirm`, `peer_state 0x02`, client record `active_activation_pending` | HMAC valid; no phone-active proof | set class `activation_recovery` (re-ack per OTP2-STA-010b step 5) | `session_open` | invalid → teardown+erase |
| C | `awaiting_phone_confirm` | phone `reconnect_confirm` (`0x01` or `0x02`), client record `pending` | HMAC valid; client below active | set class `activation_recovery` (OTP2-STA-010b; prepare-then-commit, step 0/1) | `session_open` | invalid → teardown+erase |
| C | `awaiting_phone_confirm` | phone `reconnect_confirm`, `peer_state 0x02`, client record `active` (ordinary) | HMAC + `peer_state` | set class `full_pending_peer` (OTP2-STA-010b step 5) | `session_open` | invalid → teardown+erase |
| P | `awaiting_peer_confirm` | client `reconnect_confirm`, `peer_state 0x01`, phone record `active` | HMAC + `peer_state` | set class `full` | `session_open` | invalid → teardown+erase |
| P | `awaiting_peer_confirm` | client `reconnect_confirm`, `peer_state 0x02`, phone record `active` | HMAC + `peer_state` | set class `full_pending_peer` | `session_open` | invalid → teardown+erase |
| P | `awaiting_peer_confirm` | client `reconnect_confirm` (`0x01` or `0x02`), phone record `pending`/`committed_waiting_ack` | HMAC valid; phone below active | set class `activation_recovery` | `session_open` | invalid → teardown+erase |
| both | `session_open` | valid record of authorized class | sequence/type/AEAD/class | process | `session_open` | invalid → teardown+erase |
| both | `session_open` | idle 180 s / disconnect / link loss | — | erase keys, drop state | (gone) | — |

The class-selection rows above encode OTP2-SID-001 exactly: the client's
upgrade (row `peer_state 0x01` + `active_activation_pending` → `full`) is
evaluated BEFORE selection, and no session ever upgrades after
`session_open`.

### 11.4 Authorization classes

<a id="sec-authz-classes"></a>

| Session class | Condition | Authorized records |
|---|---|---|
| `enrollment` | the connection whose SPAKE2 execution is complete and both identity records are exchanged (both records below `active`) | `enroll_identity` (both), `enrollment_prepare` (P→C), `activation_commit` (C→P), `activation_complete` (P→C), `activation_ack` (C→P) only; NO OTP record ever on this session, even after both records reach `active` mid-connection (OTP2-STA-010a) |
| `activation_recovery` | a later connection where the local record is `pending`/`committed_waiting_ack` (phone) or the client record is below `active`-confirmed | `enrollment_prepare` (P→C), `activation_commit` (C→P), `activation_complete` (P→C), `activation_ack` (C→P) only; recovery-only through teardown even if states become active mid-session (OTP2-STA-010b.6); the client's commit requires this session's verified prepare (step 0) |
| `full` | local record `active` and peer confirmed active (`peer_state 0x01`) on a fresh session | `pull_request` (C→P), `pull_events`/`push_event`/`phone_policy_state` (P→C), `event_ack` (C→P), `removal_request`/`removal_response` (both) ONLY |
| `full_pending_peer` | local record `active`; peer reported `0x02` | `enrollment_prepare` (P→C), `activation_commit` (C→P), `activation_complete` (P→C), `activation_ack` (C→P) only (completion of the peer's pending activation; no OTP data) |

Framework records (`0x0002` `app_heartbeat`, `0x0003` `app_error`) and each
direction's session-start `reconnect_confirm` (`0x0001`, sequence 0, exactly
once) are class-independent: they are authorized on every authenticated
session in every class; the class rows above list the ADDITIONAL
non-framework types. Rationale for
`full_pending_peer`: when the phone is `active` but the peer still reports
`0x02` (its record is below `active`), the phone authorizes only the
bounded activation-completion exchange on that session — never OTP data.
This asymmetry is deliberate: OTP flows only toward peers whose enrollment
completed on the phone side and whose own record is `active`. The
phone-side authorization check — its own record is `active` — is the RFC
activation condition (OTP2-STA-010); the peer-reported byte only informs
the session class. A client that reported `0x02` may finish activation on
this session, but the session stays `full_pending_peer`; the peer's NEXT
fresh reconnect (phone proves `peer_state = 0x01`) is the `full` session.
Reachability note (honest): through the defined flows this combination
cannot arise — a phone record is `active` only after the phone
authenticated the client's `activation_ack`, which only an active client
sends, and a client that discards its incomplete material cannot re-pair
the same installation (OTP2-STA-010d, OTP2-STA-012). The class is retained
as a defensive least-privilege bound: if it ever arises, the only authorized
exchange is the completion exchange with a fresh-prepare gate, and a
`activation_commit` arriving while the phone's record is already `active`
is accepted as an idempotent statement duplicate with NO state regression
(the record stays `active`; committed state is never re-entered).

---

## 12. Verification and fixture requirements

<a id="sec-verification"></a>

These requirements bind the derived artifacts (fixtures, schemas, checks) and
later plan tasks; the normative wire content above is complete regardless of
them. Evidence is pending; nothing here claims acceptance.

- **OTP2-FIX-001** — every fixture carries: stable fixture ID, contract
  version (1), fixture-format version (1), category, the requirement IDs it
  exercises, input, expected bytes or expected rejection with stage, and
  expected state/effects where relevant (approved design).
- **OTP2-FIX-002** — bytes are lowercase even-length hex; u64 values in
  fixture field objects are canonical unsigned decimal strings
  (`"18446744073709551615"` preserved; `"18446744073709551616"` rejected);
  JSON numeric precision loss for large counters is impossible everywhere a
  counter is represented.
- **OTP2-FIX-003** — fixtures use synthetic identities and OTPs only.
- **OTP2-FIX-004** — negative fixtures name the validation stage and assert
  absence of state mutation; a trace that passes must not be treated as
  cryptographic verification of that case unless the cited vector was cryptographically
  derived and cross-checked (provenance per OTP2-FIX-005).
- **OTP2-FIX-005** — cryptographic expected values come from RFC 9382
  published vectors (Appendix B is the conformance anchor for the suite/pair
  mechanics) or from independently cross-checked Veles-specific derivations,
  with recorded provenance and reproducible generation commands; missing
  cross-checks leave the vector explicitly unverified and block acceptance.
- **OTP2-FIX-006** — executable checks are test-only interpreters; they are
  never imported by product code and never become a competing reference
  codec.
- **OTP2-ACC-*** — the OTP-02 acceptance clauses are enumerated in
  `protocol/v1/manifest.json` with category prefix `OTP2-ACC`, each anchored
  to the sections that satisfy it; the machine-checkable ones carry their
  fixture/check evidence references in `evidence_ids` (status
  `specified_evidence_collected`), the ones this contract package cannot
  exhibit without product implementation stay documentary-only
  (`specified_awaiting_controller_review`); no clause claims `reviewed` or
  `accepted` — independent reviews and user acceptance are pending.

### 12.1 RFC 9382 anchor vectors

<a id="sec-rfc-vectors"></a>

Exactly TWO of the four published P-256-SHA256-HKDF-HMAC vectors in RFC 9382
Appendix B are the conformance anchors, and the fixture set says so exactly:
the FIRST (identities `A='server', B='client'` — the only fully non-empty
identity pair, the shape Veles's fixed non-empty A/B identities produce) and
the FOURTH (`A='', B=''` — exercising zero-length identity framing). The
remaining two published vectors (mixed empty/non-empty identities
`A='server', B=''` and `A='', B='client'`) are NOT used as anchors here,
because Veles's fixed identity strings are always non-empty on both sides;
no fixture claims them. The mechanics all four published vectors exercise
(SEC1 uncompressed point handling, TT construction with RFC `len()` u64
little-endian prefixes, `w` fixed-width big-endian encoding, `Hash(TT)`
splitting into `Ke`/`Ka`, the `"ConfirmationKeys" || AAD` KDF derivation of
`KcA`/`KcB`, and HMAC confirmation values) are identical across the set, and
Veles's own identity strings,
password transform, and AAD differ from the vectors; Veles-specific values
are derived separately with provenance (OTP2-FIX-005).

---

## 13. Requirements registry

<a id="sec-traceability"></a>

The complete requirement inventory (every OTP-02 scope/acceptance/evidence
clause and every RFC product/security/watch constraint this contract freezes,
plus the builder constants above) is enumerated in
`protocol/v1/manifest.json` as entries `{id, document_anchor, evidence_ids,
status}`. `status` is exactly one of the manifest's `status_values`
(`specified_awaiting_controller_review` or `specified_evidence_collected`
while independent review and user acceptance remain pending): an entry whose
committed fixtures tag it or whose named checks exercise it carries the
closed evidence references in `evidence_ids` (fixture/check references, and
its normative anchor) and status `specified_evidence_collected`; an entry
with NO committed fixture tag and NO named check — deliberately
`documentary_only` (traceability text, registry entries, product-level
constraints, threat-model/observability boundaries that this contract
package cannot exhibit without product code) — stays
`specified_awaiting_controller_review` with only its anchor in
`evidence_ids`. No entry claims `reviewed` or `accepted` in this
revision: independent reviews and user acceptance are explicitly pending
and tracked in the review records (`docs/protocol/reviews/`). Fixed
ceilings live in `protocol/v1/limits.json` with `origin`
marking RFC-fixed values versus builder choices.

### 13.1 OTP-02 clause inventory (readable index)

Scope clauses (OTP2-SCP-*) — one per clause of RFC OTP-02 Scope:

| ID | Clause (abbreviated) |
|---|---|
| OTP2-SCP-001 | RFC 9382 SPAKE2 profile |
| OTP2-SCP-002 | fixed A/B roles |
| OTP2-SCP-003 | password-to-scalar transform |
| OTP2-SCP-004 | AAD and confirmation context |
| OTP2-SCP-005 | installation identities |
| OTP2-SCP-006 | enrollment states |
| OTP2-SCP-007 | reconnect transcript |
| OTP2-SCP-008 | P1363 signature encoding |
| OTP2-SCP-009 | HKDF schedule |
| OTP2-SCP-010 | AES-GCM records |
| OTP2-SCP-011 | nonce construction |
| OTP2-SCP-012 | sequence rules |
| OTP2-SCP-013 | binary schemas |
| OTP2-SCP-014 | fragmentation bounds |
| OTP2-SCP-015 | protected pull/push/policy/acknowledgement records |
| OTP2-SCP-016 | pairing attempt/concurrency limits |
| OTP2-SCP-017 | capabilities |
| OTP2-SCP-018 | errors |
| OTP2-SCP-019 | version negotiation (protocol hello) |
| OTP2-SCP-020 | fixture format |
| OTP2-SCP-021 | immutable envelope version 1 and hello/ready negotiation |
| OTP2-SCP-022 | completion events (incl. `sent`), unsolicited events, ordering, whole-record boundaries, bounds, handles, errors, single `record_base64` |
| OTP2-SCP-023 | prohibition of JSON remapping of authenticated fields |
| OTP2-SCP-024 | threat model |
| OTP2-SCP-025 | downgrade policy |

Acceptance clauses (OTP2-ACC-*) — one per RFC OTP-02 acceptance-criterion
sentence: ACC-001 no placeholders/ambiguous encodings; ACC-002 explicit
bounds/state transitions; ACC-003 hello-only pre-negotiation and
ready/correlated-error responses; ACC-004 `ready` selects highest overlap;
ACC-005 no overlap flushes `incompatible_api` and exits; ACC-006 post-ready
messages carry envelope 1 + selected API; ACC-007 exact base64 round trip
without authenticated fields as JSON; ACC-008 deterministic vectors cover
success/rejection incl. compatible/incompatible versions; ACC-009
independent protocol/security review accepted; ACC-010 implementation cannot
negotiate plaintext, spike protocol, or unbounded compatibility.

Evidence clauses (OTP2-EVD-*) — one per RFC OTP-02 verification-evidence
item: EVD-001 versioned protocol document; EVD-002 machine-readable
envelope/binary-record fixtures; EVD-003 exact base64 byte-round-trip
vectors; EVD-004 pre/post-negotiation parser and state-machine checks;
EVD-005 compatible/incompatible extension-host version matrix; EVD-006
review record; EVD-007 schema/vector parser checks; EVD-008 cross-language
vector test skeletons.

Product/watch/security constraints from the RFC are the OTP2-PRD-*, OTP2-WAT-*,
OTP2-SEC-*, and OTP2-REV-* entries above (also the crypto, encoding, state,
GATT, and bridge requirement families OTP2-CRY/ENC/STA/REC/GAT/BRD), each
anchored to the section that freezes it.
