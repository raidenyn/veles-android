## ADDED Requirements

### Requirement: Peer rejections are narrow
Each trust repository MUST classify failures at one choke point by what failed. A peer rejection
MUST be only a failure raised by an operation's own explicit peer-level checks (record missing,
wrong state) or by the generation and revocation fences.

#### Scenario: Missing record
- **WHEN** an operation names a peer whose record does not exist
- **THEN** the failure is a peer rejection

### Requirement: Every other failure is a store failure
Every failure that is not a peer rejection, before or after the operation, MUST be a store
failure carrying the same user-facing "re-pair required" text. This covers storage read or decode,
identity read, owner liveness, snapshot or per-peer validation, key and metadata mismatch, and the
durable write.

#### Scenario: Corrupt serialized state
- **WHEN** the stored trust data cannot be decoded
- **THEN** the repository raises a store failure, not a peer rejection

#### Scenario: Failing durable write
- **WHEN** the durable write fails during an activation or authentication
- **THEN** the failure is classified as a store failure

### Requirement: Store failure is reported once per episode
Each repository MUST invoke an `onStoreFailure` callback once per failure episode from the choke
point, whichever caller first observed it (reconnect lookup, authentication metadata, pairing,
activation, the coordinator). An episode begins at the first store failure and ends when the
repository next completes a transaction successfully, is recreated, or is reset; a failure after
that begins a new episode and reports again.

#### Scenario: One callback per episode
- **WHEN** several callers hit the same store failure
- **THEN** the callback runs once for that episode

#### Scenario: New episode after recovery
- **WHEN** a transaction succeeds after a store failure and a later transaction fails again
- **THEN** the callback runs again for the new episode

### Requirement: Fail closed on a store failure
The composition root MUST route the store-failure callback to a fail-closed action that reports
the single status "re-pair required" and terminates every live session and every pairing
connection, bound or not. The action MUST NOT delete trust, MUST NOT start a reset, and its text
MUST NOT suggest erasing trust automatically.

#### Scenario: Corruption seen by reconnect
- **WHEN** a reconnect lookup hits corrupt trust data while unrelated peers have open sessions
- **THEN** all sessions and pairing connections are terminated and the status is "re-pair required"

#### Scenario: Peer rejection during a revocation race
- **WHEN** a confirm fails because its peer was just revoked
- **THEN** no unrelated session is terminated

#### Scenario: Identity or write failure with live sessions
- **WHEN** a malformed Android state, an invalid Chrome identity or a failing durable write is
  observed with sessions of other peers open
- **THEN** those sessions are terminated

### Requirement: Android identity reset
Android MUST provide an explicit, user-initiated identity reset that works when the stored payload
is unreadable: terminate all sessions and cancel pairings, delete the trust row without decoding
it through a new `TrustStorage.reset()`, then delete the keystore alias through a new
`IdentityKeyStore.delete()`. The next repository use MUST create a fresh identity and an empty
peer set.

#### Scenario: Reset with corrupt storage
- **WHEN** reset runs while the stored payload cannot be decoded
- **THEN** the row and the key are deleted and the next use starts a new identity

#### Scenario: Reset terminates first
- **WHEN** reset runs with sessions and an enrollment connection open
- **THEN** every session and pairing connection is terminated before any deletion

### Requirement: Android reset is safe to repeat and never automatic
The Android reset MUST be idempotent, MUST fail closed after an interruption at either deletion,
and MUST never run automatically or from an error path.

#### Scenario: Interrupted reset
- **WHEN** the process stops after only one of the two deletions
- **THEN** the next repository use fails closed, and running reset again completes it

#### Scenario: Interruption after both deletions
- **WHEN** the process stops after both deletions but before reset returns
- **THEN** the next repository use creates a fresh identity without error

#### Scenario: Error path does not reset
- **WHEN** a store failure is observed
- **THEN** no reset runs until the user starts one

### Requirement: Chrome identity reset
Chrome MUST reuse its existing `reset()` as the explicit recovery action, invoked only by a user
action, and it MUST terminate every session and pairing link first.

#### Scenario: Reset after identity loss
- **WHEN** the user resets after the identity key was lost
- **THEN** all trust is discarded and a fresh pairing is required

### Requirement: Lost or corrupt state requires pairing again
Reinstallation, app-data or profile loss, private-key loss and trust-store corruption MUST leave
the affected relationship untrusted: no OTP data flows until pairing is repeated, and platform
hints and labels MUST NOT restore trust.

#### Scenario: Key lost, metadata present
- **WHEN** the key is missing and the metadata survives, or the reverse
- **THEN** every repository call fails closed and no session authenticates
