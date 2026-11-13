# reconnect-authentication Specification

## Purpose
TBD — this capability was introduced by the OTP-09 change; describe its purpose here.

## Requirements

### Requirement: Platform crypto and engine ownership
The Android phone-role session engine MUST use Kotlin with JCA platform crypto and the
Android Keystore identity key. The Chrome client-role engine MUST run in the connector tab
and use Web Crypto with the non-extractable IndexedDB identity key. Neither engine may use
Rust or WASM for session crypto, and identity private keys MUST never leave their platform
key store. The phone engine MUST NOT assume the peer is a Chrome client.

#### Scenario: Identity key stays in the platform store
- **WHEN** either engine signs a reconnect transcript
- **THEN** the signature comes from the Keystore or non-extractable `CryptoKey`, and no
  private identity key bytes are passed to the session code or to Rust/WASM

#### Scenario: Client-agnostic phone engine
- **WHEN** the phone engine is driven by a scripted non-Chrome client fixture with a valid
  trust record
- **THEN** it authenticates and opens a session exactly as it does with the Chrome connector

### Requirement: Reconnect hello layout
Each endpoint MUST encode the `reconnect_hello` payload (frame `0x06`, §4 envelope) exactly
as OTP2-REC-020 defines it:
- installation id, 16 bytes
- ephemeral public key, 65 bytes uncompressed SEC1 P-256
- nonce, 16 bytes
- role, u8
- `protocol_version`, u16be = 1
- `caps_len`, u16be, followed by the capabilities

The key MUST come from a key pair generated for this connection, and the nonce MUST be fresh.

#### Scenario: Client sends first with a fresh ephemeral key
- **WHEN** a client starts a reconnect on a new link
- **THEN** its first frame is a `reconnect_hello` whose point is the public key of a newly
  generated ephemeral key pair, not its identity key, with a fresh nonce

#### Scenario: Hello point is the ECDH key
- **WHEN** an engine completes a handshake
- **THEN** the point it sent in its hello is the public key of the ephemeral private key it
  used for ECDH

### Requirement: Reconnect hello validation
A receiver MUST reject an invalid hello with the generic `preauth_reject` before any state
mutation. Invalid means any of:
- wrong envelope or payload length, or trailing bytes
- an invalid point (off-curve, zero coordinates, infinity, compressed, or a wrong length)
- capabilities that are not 1..16 in number, sorted and unique, or that lack mandatory
  `0x01`
- an unknown capability
- a version other than 1
- a role that is not the sender's

#### Scenario: Invalid ephemeral point
- **WHEN** a hello's point bytes are replaced by any `veles-neg-point-*` fixture value at that
  fixture's malformed length
- **THEN** the receiver sends `preauth_reject`, closes the link, and derives no keys

#### Scenario: Malformed initial client hello
- **WHEN** the phone receives an initial client hello with a wrong `payload_len`, trailing
  bytes, unsorted or duplicate capabilities, no capability `0x01`, version ≠ 1, or role ≠
  `0x02`
- **THEN** the phone sends `preauth_reject` and does not send its own hello

#### Scenario: Malformed phone hello
- **WHEN** the client receives a phone hello that fails any validation rule
- **THEN** the client aborts with the generic failure and sends no signature

### Requirement: Trust lookup at the phone
The phone MUST reply to a client hello only when the hello's installation id matches a stored
trust record whose state is `active`, `pending` or `committed_waiting_ack`. For an unknown or
deleted installation id, the phone MUST send only `preauth_reject` and disconnect. The
response for unknown and deleted ids MUST be identical, and MUST include no phone hello, no
signature and no protected data (OTP2-REV-001).

#### Scenario: Unknown client installation
- **WHEN** a client whose installation id has no trust record sends a valid hello
- **THEN** the phone sends `preauth_reject`, disconnects, and sends no hello, signature or
  record

#### Scenario: Deleted record looks like unknown
- **WHEN** a peer whose trust record was deleted reconnects
- **THEN** the phone's response is byte-for-byte the same as the unknown-peer response

### Requirement: Phone identity check at the client
The client MUST verify the phone using only the identity public key it has stored for the
installation id in the phone hello. If that id is unknown, or differs from the peer the
client is connecting to, the client MUST abort with the generic failure. Every unsuccessful
authentication MUST produce the same reason-free client state ("could not authenticate —
re-pair required"), never a revocation-specific state.

#### Scenario: Phone identity mismatch
- **WHEN** the phone hello names an untrusted installation id, or one that differs from the
  selected peer
- **THEN** the client aborts, sends no signature, and shows the generic failure state

#### Scenario: Revoked peer on the client
- **WHEN** the client's reconnect ends in `preauth_reject` because the phone deleted its
  record
- **THEN** the client shows the same generic failure state as for any other authentication
  failure

### Requirement: Transcript and signature frame
Both endpoints MUST build
`rbase = "Veles reconnect transcript v1" || phone_hello_payload || client_hello_payload`.
Each MUST send a signature frame (`0x07`) whose payload is exactly 65 bytes: role u8 followed
by a 64-byte P1363 `r||s` ECDSA-SHA256 signature over `rbase`, with `rbase` hashed exactly
once. The phone MUST send its signature first, and the client MUST verify it before sending
its own.

#### Scenario: Fixture transcript
- **WHEN** an engine is fed the corrected `veles-reconnect-authentication` fixture inputs
- **THEN** it reproduces both hello payloads, `rbase` and `rbase_digest` byte-for-byte, and
  its platform verifier accepts both fixture signatures

#### Scenario: Malformed signature frame
- **WHEN** a signature frame has a wrong or missing role byte, a payload length other than
  65, or trailing bytes
- **THEN** the receiver sends `preauth_reject` and derives no keys

#### Scenario: Signature order
- **WHEN** the client receives its own expected turn before a verified phone signature
- **THEN** the client does not send its signature

### Requirement: Strict signature verification
Verification MUST accept only 64-byte fixed-width P1363 signatures with low-S over a single
SHA-256 of `rbase`. Endpoints MUST reject any other signature with `preauth_reject`,
including DER, truncated, high-S, double-hashed and wrong-key signatures.

#### Scenario: Non-canonical signatures
- **WHEN** a signature frame carries the `veles-neg-sig-high-s`, `veles-neg-sig-der` or
  `veles-neg-sig-truncated` value
- **THEN** the receiver sends `preauth_reject` and derives no session keys

#### Scenario: Double hash
- **WHEN** a signature was produced over `SHA-256(rbase)` with an extra hash applied
- **THEN** verification fails

### Requirement: Platform signature normalization
Before sending, Android MUST strictly convert the Keystore's DER signature to P1363 (exact
32-byte `r` and `s`, rejecting over-long integers) and normalize it to low-S. Chrome MUST
normalize its Web Crypto P1363 signature to low-S before sending.

#### Scenario: Cross-platform signing
- **WHEN** an Android Keystore identity signs `rbase` and Chrome verifies it, and the other
  way round
- **THEN** both verifications succeed, and both emitted signatures are 64 bytes with low-S

### Requirement: Ephemeral ECDH and HKDF reconnect key schedule
After both signatures verify, each endpoint MUST derive `ss` with ECDH as the 32-byte X
coordinate, rejecting an identity or all-zero result. It MUST then derive the salt, the
`reconnect_prk`, and the seven OTP2-REC-020b outputs (two AES keys, two nonce prefixes, two
confirmation keys, and the session id) under their exact labels. When the peer record carries
`ET`, it MUST also derive the OTP2-CRY-019f recovery activation keys and erase them when the
session class resolves to `full`. All of these MUST be fresh on every connection.

#### Scenario: Fixture key schedule
- **WHEN** an engine runs the corrected `veles-reconnect-authentication` fixture with its
  fixed ephemeral keys and nonces
- **THEN** `ss`, `reconnect_salt`, `reconnect_prk`, all six directional outputs and
  `session_id` match the fixture byte-for-byte

#### Scenario: Recovery keys
- **WHEN** the peer record carries `ET`
- **THEN** `k_rec_client` and `k_rec_phone` match the `crypto.json` vectors

#### Scenario: Fresh material per connection
- **WHEN** the same two peers authenticate twice in a row
- **THEN** the two sessions have different ephemeral keys, nonces, traffic keys, prefixes and
  session ids

### Requirement: Encrypted key confirmation
Each endpoint MUST compute `reconnect_confirm_input` from `rbase` and the two 64-byte
signatures (excluding the role bytes), and its `conf_A` or `conf_B` HMAC (OTP2-REC-020c). It
MUST send the confirmation as the `reconnect_confirm` (`0x0001`) protected record at
sequence 0 of its direction, with the correct `peer_state` byte. A confirmation that fails
the AEAD tag, session, sequence or HMAC check MUST end the session and erase keys.

#### Scenario: Confirmation vector
- **WHEN** an engine seals and opens the corrected `veles-record-reconnect-confirm` fixture
- **THEN** the header, ciphertext, tag, `conf_a`/`conf_b` and `reconnect_confirm_input` match
  byte-for-byte

#### Scenario: Wrong confirmation
- **WHEN** a `reconnect_confirm` decrypts correctly but its HMAC does not match
- **THEN** the session ends without authorization, and all keys are erased

### Requirement: Authentication gate
An endpoint MUST treat its peer as authenticated only after all three of the following:
- both signatures have verified
- it has sent its own `reconnect_confirm`
- it has verified the peer's `reconnect_confirm`

Before that point it MUST NOT send, accept or deliver any record other than
`reconnect_confirm`.

#### Scenario: Record before confirmation
- **WHEN** a peer sends a heartbeat or application record before its own valid
  `reconnect_confirm`
- **THEN** the receiver ends the session, erases keys, and delivers nothing

#### Scenario: Peer confirmation alone does not open the gate
- **WHEN** an endpoint has verified the peer's confirmation, but sending its own confirmation
  is still pending
- **THEN** it reports no authentication and sends or delivers no application record until its
  own confirmation has been sent

### Requirement: Reconnect handshake deadlines
Each handshake wait in `veles-v1.md` §11.3 MUST end after 30 s of monotonic time. On expiry,
the endpoint MUST disconnect and erase its ephemeral state. The client MUST also show the
generic failure. Any async result that arrives after the deadline MUST be discarded. The
30 s value MUST be one named constant anchored to §11.3, shared by the engine's waits.

#### Scenario: Phone hello never arrives
- **WHEN** a client has sent its hello and receives no phone hello within 30 s
- **THEN** the client disconnects, erases its ephemeral key, and shows the generic failure

#### Scenario: Client signature never arrives
- **WHEN** the phone has sent its signature and receives no client signature within 30 s
- **THEN** the phone disconnects and erases its ephemeral state

#### Scenario: Late async completion
- **WHEN** a signing or verification result completes after the deadline has expired
- **THEN** it is discarded, and nothing is sent

### Requirement: Session class selection
Each endpoint MUST fix the session class (`full`, `full_pending_peer` or
`activation_recovery`) at session open, exactly according to the §11.3 rows, using its local
record state and the peer's verified `peer_state`. The class MUST NOT change afterwards. A
client MUST send `peer_state = 0x01` for any record in the active family, including
`active_activation_pending`.

#### Scenario: Class table
- **WHEN** a table-driven test covers every §11.3 combination of local record state and peer
  `peer_state` on both roles
- **THEN** each endpoint fixes the class listed in §11.3, including `full_pending_peer` and
  `activation_recovery`

#### Scenario: Both active
- **WHEN** both records are `active` and both confirmations carry `peer_state = 0x01`
- **THEN** both endpoints fix class `full`

#### Scenario: Recovery class never upgrades
- **WHEN** a session was fixed as `activation_recovery` and the records become `active`
  during it
- **THEN** the class stays `activation_recovery` until teardown

### Requirement: Client activation mirror upgrade
When the client's record is `active_activation_pending`, a verified phone confirmation
carrying `peer_state = 0x01` MUST upgrade that record to `active` **before** class
selection. The upgrade MUST go through the trust repository's `phone_active` confirmation,
with a verifier bound to the current session (OTP2-SID-001, OTP2-STA-010c).

#### Scenario: Mirror upgrades on phone proof
- **WHEN** a client record is `active_activation_pending` and the phone's verified
  confirmation carries `peer_state = 0x01`
- **THEN** the record becomes `active` before class selection, the session is `full`, and the
  upgrade survives a connector restart

#### Scenario: No upgrade without proof
- **WHEN** a client record is `active_activation_pending` and the phone reports
  `peer_state = 0x02`
- **THEN** the record does not change, and the class is `activation_recovery`

### Requirement: Successful-authentication metadata
An endpoint MUST update its successful-authentication metadata (`lastSeen`, and on Chrome the
persisted platform hint) only after `session_open`, and only for the authenticated peer of
the current session generation. A transport-level connect, a partial or failed
authentication, or a stale session's completion MUST NOT update either value. For a Chrome
record still in `pending`, a successful authentication MUST verify and check `pendingContext`
but MUST write neither value.

#### Scenario: Success updates metadata
- **WHEN** a session reaches `session_open` for a non-pending record
- **THEN** the peer's `lastSeen` is updated, and on Chrome the platform hint is replaced

#### Scenario: Pending record unchanged
- **WHEN** a recovery session reaches `session_open` for a `pending` Chrome record
- **THEN** the record keeps `lastSeen = null`, no hint, and its pending fields

#### Scenario: Failure leaves metadata untouched
- **WHEN** a connection reaches the transport level but authentication fails, or a stale
  generation completes late
- **THEN** neither `lastSeen` nor the platform hint changes

### Requirement: Reconnect vectors conform to the protocol
The committed `veles-reconnect-authentication` fixture, and every fixture derived from its
transcript, MUST put each peer's ephemeral public key in its hello (OTP2-REC-020). The
generator and the cross-check oracle MUST enforce this invariant. This correction MUST land
as a standalone, labelled first commit, and MUST NOT change `veles-v1.md`.

#### Scenario: Invariant fails on identity key
- **WHEN** a generator or fixture places an identity public key in a hello
- **THEN** `./gradlew protocolCheck` fails

#### Scenario: Corrected suite passes
- **WHEN** the corrected fixtures are regenerated with the pinned tooling
- **THEN** `./gradlew protocolCheck`, including the OpenSSL cross-check and every consumer,
  passes

### Requirement: Application handler session context
Both engines MUST pass an application or activation handler the session context: session id
prefix, session class, peer installation ID, `ET`, the reported `peer_state`, and the recovery
keys when derived. Handlers MUST be able to send records on `Authenticated` without a prior
inbound record.

#### Scenario: Handler receives context
- **WHEN** a session reaches `Authenticated`
- **THEN** the registered handler is given the class, peer ID, `ET`, `peer_state` and recovery
  keys, and may send an unprompted record
