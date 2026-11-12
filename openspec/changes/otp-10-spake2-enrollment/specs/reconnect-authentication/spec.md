## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Application handler session context
Both engines MUST pass an application or activation handler the session context: session id
prefix, session class, peer installation ID, `ET`, the reported `peer_state`, and the recovery
keys when derived. Handlers MUST be able to send records on `Authenticated` without a prior
inbound record.

#### Scenario: Handler receives context
- **WHEN** a session reaches `Authenticated`
- **THEN** the registered handler is given the class, peer ID, `ET`, `peer_state` and recovery
  keys, and may send an unprompted record
