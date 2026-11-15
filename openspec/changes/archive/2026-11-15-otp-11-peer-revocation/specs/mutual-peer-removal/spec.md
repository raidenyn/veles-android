## ADDED Requirements

### Requirement: Removal request handling
On a full-authorization session, an endpoint that receives `removal_request` (0x0205, empty body)
MUST delete its trust record for the authenticated peer, reply `removal_response` (0x0206, empty
body) and then terminate that session. After the response, no further record MAY flow in either
direction on that session. The other authenticated sessions and enrollment connections of the same
peer MUST be terminated by the revocation.

#### Scenario: Responder removes and closes
- **WHEN** an endpoint receives `removal_request` on a full session
- **THEN** its record for that peer is deleted, `removal_response` is the last record it sends, and
  the session is closed

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
the record type. A handler MUST NOT tear the session down itself or await its own generation.

#### Scenario: Response precedes close
- **WHEN** a handler requests close after reply
- **THEN** the sealed reply is sent, then the terminal effect closes the transport, and nothing is
  sent after it

#### Scenario: Composed handler forwards the outcome
- **WHEN** the removal handler runs inside the production composed handler
- **THEN** the session terminates after the response exactly as it does without composition

### Requirement: Requester deletes only after a valid response
The requesting endpoint MUST send `removal_request`, mark the session as awaiting the response, and
delete its local record only after validating `removal_response` on that same session. If the
response is lost, the session ends, or the response deadline expires, the
requester MUST keep its record and report that the other endpoint may retain its record until
separately removed.

#### Scenario: Response received
- **WHEN** the requester receives a valid `removal_response` on the awaiting session
- **THEN** it deletes its record and closes the session

#### Scenario: Response lost
- **WHEN** the session ends before a response arrives
- **THEN** the requester's record is still present and local removal remains available

#### Scenario: Deadline expires
- **WHEN** the injected clock advances past `REMOVAL_RESPONSE_TIMEOUT_MS` with no response
- **THEN** the session fails, the record is kept and the user is told removal completed here only
  if they choose local removal

### Requirement: Removal response deadline
The requester's response deadline MUST be `REMOVAL_RESPONSE_TIMEOUT_MS` = 10 000 ms measured on
an injected clock. This is a design call, not a protocol value.

#### Scenario: Deadline value
- **WHEN** the requester sends `removal_request`
- **THEN** it waits at most `REMOVAL_RESPONSE_TIMEOUT_MS` on the injected clock

### Requirement: Requester fencing after the request
After sending `removal_request`, the awaiting session MUST accept only `removal_response`,
heartbeat and `app_error`. Any other application record, and any application send on that session,
MUST fail the session. An unsolicited `removal_response`, or one on a session that did not send a
request, MUST be a protocol violation that ends the session without deleting any record.

#### Scenario: Traffic after the request
- **WHEN** an application record other than the allowed three arrives on an awaiting session
- **THEN** the session fails and the local record is unchanged

#### Scenario: Unsolicited response
- **WHEN** `removal_response` arrives with no pending request on that session
- **THEN** the endpoint ends the session with a protocol violation and deletes nothing

### Requirement: Fence lifetime for handler-driven removal
A removal driven from a handler MUST take the revocation fence and delete the record inside the
handler, return the close-after-reply outcome, and complete the revocation in a detached job that
awaits every other matching generation and then the current generation's end before the final sweep
and fence release. The fence MUST outlive the handler's return and MUST NOT be awaited from inside
the handler.

#### Scenario: Fence held across handler return
- **WHEN** the responder handler returns
- **THEN** the fence is still held, and it is released only after the current generation has ended
  and the sweep has run

### Requirement: History cleanup extension point
Both endpoints MUST call an OTP-history cleanup extension point for the removed peer on removal.
Until a history buffer exists the point MUST have no registered consumer and MUST NOT fail the
removal.

#### Scenario: No consumer registered
- **WHEN** a peer is removed and nothing is registered
- **THEN** the removal completes normally

### Requirement: Removal conformance
Both endpoints MUST execute the existing mutual-removal scenario fixture
(`st-rec-removal-mutual-authenticated`) and the empty-body encoding fixtures against the removal
handlers, with no change to the protocol contract.

#### Scenario: Fixture executed
- **WHEN** the scenario conformance suites run
- **THEN** the removal scenario passes on Android and on Chrome with the expected effects and the
  record deleted and the session closed
