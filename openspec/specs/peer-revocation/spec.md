# peer-revocation Specification

## Purpose
How an endpoint lists its trusted peers, revokes one locally without touching any other while storage is healthy, and guarantees that a revoked peer cannot regain access without a fresh enrollment. A store failure during revocation is a store failure, not a peer rejection, and escalates to the fail-closed action.

## Requirements

### Requirement: Peer listing
Each endpoint MUST list its trusted peers through a read-only summary with the installation id,
label, fingerprint, device type, state, creation time, last-seen time and whether an authenticated
session is currently open for that peer. The summary MUST be computed fresh on each call and MUST
NOT carry key material, platform hints or native handles.

#### Scenario: Multiple peers listed
- **WHEN** an endpoint holds an active peer, a pending peer and a peer with an open session
- **THEN** the list contains all three with their own fingerprint, state, timestamps and a
  connected flag that is true only for the peer with the open session

#### Scenario: No secrets in the summary
- **WHEN** a peer summary is serialized
- **THEN** it contains no public key bytes beyond the fingerprint, no signing key, no session key,
  no platform hint and no native handle

### Requirement: Local revocation order and isolation
Revoking a peer locally MUST, in this order: mark the peer id fenced, delete the peer's durable
trust record (on Chrome, its platform hint with it), terminate that peer's authenticated sessions
and bound enrollment connections, delete the id again as a final sweep, and release the fence.
This holds for records in any state: pending, committed waiting for acknowledgement, and active.

#### Scenario: One of several peers revoked
- **WHEN** peer A is revoked while peers B and C are trusted and B has an open session
- **THEN** A's record is gone, A's sessions are closed, and B's and C's records and B's session
  are unchanged

#### Scenario: Pending record revoked
- **WHEN** a peer whose record is still pending or committed waiting for acknowledgement is revoked
- **THEN** its record is deleted and its activation can no longer complete

### Requirement: Revocation touches only the revoked peer
While storage is healthy, revocation MUST NOT delete another peer's record or terminate another
peer's session or enrollment connection. A store failure observed during revocation, including a
failed durable delete, is not a peer rejection: it escalates to the fail-closed action, which
terminates every session.

#### Scenario: Healthy storage
- **WHEN** a peer is revoked and storage is healthy
- **THEN** no other peer's record, session or enrollment connection changes

### Requirement: Revocation fence
While a revocation of a peer id is in progress, creating a pending record for that id MUST be
refused as a peer-level rejection, so an in-flight enrollment cannot recreate trust after the
delete. The fence MUST be in memory only, MUST be released after the final sweep, and MUST NOT
block a later, deliberate pairing of the same installation id.

#### Scenario: Fence and write share a boundary
- **WHEN** a pending write races the sweep
- **THEN** the fence check and the write run inside one repository transaction or lock, so no
  pending write passes the fence after the sweep has run

#### Scenario: Revoke before the pending record is persisted
- **WHEN** an enrollment has authenticated the peer identity and has not yet persisted it, and
  that peer id is revoked
- **THEN** the persist is refused or its result is removed by the sweep, and no record and no live
  connection remain for that peer

#### Scenario: Revoke between activation steps
- **WHEN** the peer is revoked between persist and commit, between commit and acknowledgement, or
  during signed recovery
- **THEN** the later activation step fails because the record is gone, and the connection is closed

#### Scenario: Revoke during a duplicate commit
- **WHEN** the peer is revoked while a duplicate commit for it is being processed
- **THEN** the commit either completed before the fence or fails, and no record remains after the
  sweep

#### Scenario: Fresh pairing after revocation
- **WHEN** the fence has been released after a revocation
- **THEN** a new pairing of the same installation id through an open pairing window succeeds

### Requirement: Selection is by installation id
Peer selection for deletion and session termination MUST use only the authenticated installation
id bound to the session or enrollment connection. Labels, native handles and platform hints MUST
NOT select a peer, MUST NOT change a peer's identity, and a hint MUST NOT keep a session alive after
revocation. Termination MUST affect only the matching connections, including a pending or
committed enrollment connection once its peer identity is authenticated.

#### Scenario: Same label on two peers
- **WHEN** two peers have the same label and one is revoked
- **THEN** only the one with the revoked installation id is deleted and disconnected

#### Scenario: Handle of another peer untouched
- **WHEN** a peer is revoked while another peer's native handle is open
- **THEN** that other handle stays open

### Requirement: Hint cleanup on Chrome
Revoking a peer on Chrome MUST remove that peer's platform hint, and a later hint write for the
removed id MUST be rejected. A hint MUST remain only an OS search candidate that is accepted after
authenticated pairing or reconnect.

#### Scenario: Hint removed with the peer
- **WHEN** a peer with a stored platform hint is revoked
- **THEN** no hint for that peer remains and the connector falls back to search for it

### Requirement: Offline counterpart cannot regain access
After a peer's record is deleted, a later reconnect by that peer MUST fail with the same generic
authentication failure as any untrusted peer, MUST provide no authenticated proof of revocation,
and MUST NOT require or create a revoked-peer tombstone. The endpoint that still holds a record
MUST need a new pairing to regain access.

#### Scenario: Revoked peer reconnects
- **WHEN** a revoked peer connects after being offline during revocation
- **THEN** authentication fails generically, no OTP data flows, and the response is
  indistinguishable from an unknown peer

#### Scenario: Revoked peer's record elsewhere
- **WHEN** only one endpoint deleted its record
- **THEN** the other endpoint's stale record grants no access, and the user-facing text says
  re-pairing is required

### Requirement: Sessions end even if deletion fails
If deleting the record or the hint throws, the revocation MUST still terminate every matching
session and enrollment connection, because trust is then unproven. Logical termination (engine
teardown erasing session keys and stopping record processing) MUST NOT depend on the transport
disconnect succeeding.

#### Scenario: Delete fails
- **WHEN** the durable delete throws during revocation
- **THEN** the peer's sessions are still terminated, the failure is reported as a store failure,
  and the fail-closed action terminates every other session as well

#### Scenario: Transport close fails
- **WHEN** the transport refuses to close a generation that was logically torn down
- **THEN** the session holds no keys and processes no further record

### Requirement: Explicit re-pairing
After revocation or reset, access MUST be regained only through a fresh enrollment that creates a
new pending record and completes activation. A pairing attempt MUST NOT replace an existing
identity under the same installation id; replacement requires explicit revocation first. A client
whose pending record was lost while the phone still holds one MUST be told to revoke it on the phone
and pair again, never silently replaced.

#### Scenario: Re-pair after phone-side revocation
- **WHEN** the phone revokes a client and the client then pairs again
- **THEN** the pairing completes with a new record and both fingerprints match

#### Scenario: Pair while a record is held
- **WHEN** a client with an installation id already held by the phone starts pairing
- **THEN** it is refused after confirmation with the protected `enrollment_rejected` error and no
  record is replaced
