## ADDED Requirements

### Requirement: Removal response delivery hold

When the Chrome endpoint is the responder in a mutual removal, its native-bridge transport reports
only local submission — a `sent` event, never an application acknowledgement, per OTP2-BRD-022.5
and OTP2-GAT-013 — so the endpoint MUST keep the transport open after `removal_response` until the
requester's connection ends or a bounded hold expires. The hold bound
MUST be `REMOVAL_DELIVERY_HOLD_MS` = 5 000 ms measured on an injected clock, below the requester's
10 000 ms response deadline. The session's logical termination, trust deletion, revocation fence
and reported outcome MUST happen before or independently of the hold. The requester's deadline,
fencing and offline-removal semantics MUST NOT change, and the Android responder MUST NOT change.

While the link is held the endpoint MUST NOT accept or send any record on that session and MUST NOT
retain session keys. If the requester's connection ends first, for any reason, the endpoint MUST
finish locally without issuing a disconnect request and MUST NOT record or report the response as
delivered. If the hold expires, the endpoint MUST issue exactly one disconnect request and MUST NOT
claim that the response was delivered. A successful removal MUST NOT schedule an automatic
reconnect for the removed handle.

#### Scenario: Requester connection ends

- **WHEN** the Chrome responder sends `removal_response` and the requester's connection ends
- **THEN** the responder finishes without issuing a disconnect request, schedules no automatic
  reconnect for that handle, reports the outcome as removed and makes no delivery claim beyond the
  observed close

#### Scenario: Connection ends for another reason

- **WHEN** the requester's connection is lost, the user disconnects, or the host reports a
  disconnect before the bound
- **THEN** the responder finishes locally without a disconnect request and does not record the
  response as delivered

#### Scenario: Hold bound expires

- **WHEN** the requester's connection has not ended before `REMOVAL_DELIVERY_HOLD_MS`
- **THEN** the responder issues exactly one disconnect request, keeps its record deleted, reports no
  failure, and does not claim that the response was delivered

#### Scenario: Concurrent removal requests, peer request first

- **WHEN** the Chrome endpoint has an own `removal_request` queued but not yet sent, and its peer's
  `removal_request` is handled first
- **THEN** the Chrome endpoint is the responder for that session, holds the response link, settles
  its own pending removal as removed, and sends no second removal request

#### Scenario: Concurrent removal requests already on the air

- **WHEN** both endpoints' removal requests are already on the air when the peer's request arrives
- **THEN** the existing requester fencing ends the session with an authorization failure and both
  records are kept

#### Scenario: Requester path is unchanged

- **WHEN** the endpoint is the requester and receives a validated `removal_response`
- **THEN** it deletes its record and releases the transport immediately, with no delivery hold

#### Scenario: Held link carries no session state

- **WHEN** the link is held after the response
- **THEN** no session remains addressable for that handle, the engine is erased, and any arriving
  record is ignored and sent nowhere

#### Scenario: No automatic recovery for the removed handle

- **WHEN** the held handle disappears while the connector would otherwise schedule a reconnect
- **THEN** no automatic reconnect is scheduled for that handle, and a later unrelated recovery
  cannot target the deleted peer because its trust record is gone

---

### Requirement: Removal delivery-hold cleanup

An identity reset or fail-closed recovery MUST finish every hold that exists when it starts
terminating sessions, and a peer revocation MUST finish only the existing holds of that peer; each
finished hold MUST receive exactly one disconnect request. A hold MUST keep the authenticated peer
installation id it was created for, and a revocation MUST select holds only by that id. The
selection MUST be a snapshot taken synchronously when the operation starts terminating sessions,
before it awaits any session or link teardown, so the responder's own revocation finish — started
while its handler step is still running — cannot see or release the hold that step creates later. A
hold created by a removal step that is still in flight when an identity reset or fail-closed
recovery starts needs no second sweep and may survive to the bound; it carries no keys and accepts
no records. A host port close MUST clear every hold timer and recovery mark with no disconnect
request.

#### Scenario: Reset during the hold

- **WHEN** an identity reset starts while a responder link is held
- **THEN** the hold is finished with exactly one disconnect request, the reset's trust deletion
  proceeds independently, no automatic reconnect is scheduled, and the logical termination is not
  delayed

#### Scenario: Fail-closed during the hold

- **WHEN** a fail-closed recovery starts while a responder link is held
- **THEN** the hold is finished with exactly one disconnect request and the fail-closed termination
  of every session and pairing link is not delayed

#### Scenario: Revocation of the held peer

- **WHEN** the peer whose response link is held is revoked
- **THEN** that peer's existing hold is finished with exactly one disconnect request and the
  revocation's own trust write is unaffected

#### Scenario: Revocation of another peer

- **WHEN** a different peer is revoked while a responder link is held
- **THEN** the held link is untouched

#### Scenario: Responder's own revocation finish

- **WHEN** the responder's removal handler starts the detached revocation finish for the peer it is
  removing
- **THEN** that finish does not see the hold created later by the same handler step, and no
  disconnect request is issued before the requester's connection ends or the bound expires

#### Scenario: Removal step in flight during a reset or fail-closed

- **WHEN** an identity reset or fail-closed recovery starts while a responder's removal step is
  still running, so its hold does not exist at the snapshot
- **THEN** that new hold may survive to the bound, the reset or fail-closed trust work has
  completed, and the hold ends at the requester's connection end with no disconnect request or at
  the bound with exactly one disconnect request, carrying no keys and accepting no records

#### Scenario: Port close during the hold

- **WHEN** the native port closes while a responder link is held
- **THEN** every hold timer and recovery mark is cleared with no disconnect request

#### Scenario: Snapshot point

- **WHEN** a cleanup operation starts and a hold is created after its synchronous snapshot
- **THEN** the cleanup does not select that hold, and the new hold is governed by the hold's end
  conditions

---

## MODIFIED Requirements

### Requirement: Removal request handling

On a full-authorization session, an endpoint that receives `removal_request` (0x0205, empty body)
MUST delete its trust record for the authenticated peer, reply `removal_response` (0x0206, empty
body) and then terminate that session. After the response, no further record MAY flow in either
direction on that session. The other authenticated sessions and enrollment connections of the same
peer MUST be terminated by the revocation. The session's logical termination, the trust deletion
and the revocation MUST NOT wait for the transport release: on the Chrome responder, the "Removal
response delivery hold" requirement defers only the physical release.

#### Scenario: Responder removes and closes

- **WHEN** an endpoint receives `removal_request` on a full session
- **THEN** its record for that peer is deleted, `removal_response` is the last record it sends, and
  the session is logically closed; the Chrome transport may remain held under the delivery hold

#### Scenario: Only the removed peer's connections

- **WHEN** a removal completes while storage is healthy
- **THEN** only that peer's sessions and enrollment connections are terminated

#### Scenario: Deletion fails in the responder

- **WHEN** the responder's deletion fails
- **THEN** it is a store failure and the fail-closed action terminates every session

#### Scenario: Removal on a non-full session

- **WHEN** `removal_request` arrives on a session that is not full
- **THEN** the record policy ends the session and no trust changes

### Requirement: Close after reply

A session engine MUST support a handler outcome that seals the handler's buffered replies, tears
the engine down and emits the terminal close effect, so a reply is delivered before the session
ends. Production handler composition MUST forward this outcome from the child handler that handled
the record type. A handler MUST NOT tear the session down itself or await its own generation. When
the handled record is a successful `removal_request`, the Chrome session engine MUST mark the
terminal effect as the responder's delivery-hold case; the Android engine is unchanged. A validated
`removal_response` received by the requester and every other terminal record MUST NOT carry that
mark. The owner MUST release the transport immediately for every unmarked terminal effect.

#### Scenario: Response precedes close

- **WHEN** a handler requests close after reply
- **THEN** the sealed reply is sent, then the terminal effect requests the transport close; for a
  successful removal request the Chrome owner applies the delivery hold, and nothing is sent after
  the reply

#### Scenario: Composed handler forwards the outcome

- **WHEN** the removal handler runs inside the production composed handler
- **THEN** the session terminates after the response exactly as it does without composition

#### Scenario: Requester terminal effect is unmarked

- **WHEN** the terminal effect closes a validated `removal_response` received by the requester
- **THEN** the engine carries no delivery-hold mark and the owner releases the transport
  immediately
