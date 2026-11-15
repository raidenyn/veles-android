## MODIFIED Requirements

### Requirement: Teardown and key erasure
On disconnect, link loss, a terminal error or a timeout, each endpoint MUST erase:
- the ephemeral private keys
- the traffic keys
- the nonce prefixes
- the confirmation keys
- the session id
- the counters

It MUST refuse any later input for that session. Teardown itself MUST NOT modify durable trust
records. Trust changes only through the explicit coordinator operations, each with its own
ordering: local revocation and mutual removal delete the record and then terminate the matching
sessions, and still terminate them if the deletion fails; identity reset terminates every session
before it deletes anything.

#### Scenario: Input after teardown
- **WHEN** an engine receives bytes after its session ended
- **THEN** it processes nothing and emits nothing

#### Scenario: Trust survives teardown
- **WHEN** a session ends for any reason other than a coordinator operation (disconnect, link
  loss, a terminal error or a timeout)
- **THEN** the peer's trust record and identity key are unchanged

#### Scenario: Teardown caused by revocation
- **WHEN** a session ends because the peer was revoked or mutually removed
- **THEN** the keys were erased, and the record was deleted first or, if the deletion failed, the
  session was still terminated

#### Scenario: Teardown caused by identity reset
- **WHEN** a session ends because the identity was reset
- **THEN** it ended before any deletion and its keys were erased

### Requirement: Authorized records without a handler fail closed
An authorized record type with no registered handler MUST end the session with `app_error`
`protocol_violation`. Release builds have no OTP data application handler, so every OTP data
record fails this way. The removal handler for `removal_request` (0x0205) and `removal_response`
(0x0206) is installed in production builds and handles only those two types. Activation records
MUST be handled by the activation handler in the classes the protocol allows; when no activation
handler is registered they MUST end the session with `protocol_violation`. Classes that do not
allow them are covered by the record and class authorization requirement.

#### Scenario: Release build has no application handler
- **WHEN** a release build in a `full` session receives an authorized `pull_request`
- **THEN** it sends `app_error` `protocol_violation`, closes, and erases keys

#### Scenario: Removal record in a release build
- **WHEN** a release build in a `full` session receives `removal_request`
- **THEN** the removal handler handles it per the mutual-removal requirements

#### Scenario: Activation record in an allowed class
- **WHEN** an `activation_recovery` or `full_pending_peer` session receives an authorized
  activation record and the activation handler is registered
- **THEN** the activation handler receives it with the session context and answers per
  OTP2-STA-010b

#### Scenario: Activation record without a handler
- **WHEN** an activation record arrives and no activation handler is registered
- **THEN** the receiver sends `app_error` `protocol_violation`, closes, and changes no trust
  state

### Requirement: Chrome serialized session execution
The Chrome connector MUST run one client engine per phone handle. Each engine MUST process
inbound records and outbound requests strictly in order through a bounded, serialized queue.
It MUST reserve each outbound sequence and nonce inside the sealing step. Overflow MUST end
the session with `resource_exhausted`. Results tied to a stale handle or port generation MUST
be discarded. One engine ending MUST NOT affect other phones, except that a store-level trust
failure terminates every session as required by the fail-closed requirement.

#### Scenario: Simultaneous outbound records
- **WHEN** an ack and a heartbeat are requested at the same moment
- **THEN** they get consecutive, distinct sequence numbers and nonces

#### Scenario: Adjacent handshake frames with slow crypto
- **WHEN** a frame arrives while verification of the previous frame is still pending
- **THEN** the frames are processed in order, and the handshake completes correctly

#### Scenario: Port replaced while crypto is pending
- **WHEN** the port is replaced while a Web Crypto operation is pending
- **THEN** the late result is discarded, and nothing is sent

#### Scenario: Peer isolation
- **WHEN** one phone's session ends with an error or a peer-level rejection
- **THEN** other phones' sessions continue unaffected

#### Scenario: Store failure terminates all
- **WHEN** a store-level trust failure is observed
- **THEN** every session is terminated, not only the one that observed it
