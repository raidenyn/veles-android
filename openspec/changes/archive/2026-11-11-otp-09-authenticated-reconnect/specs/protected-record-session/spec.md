## ADDED Requirements

### Requirement: Protected record format
Every record after the handshake MUST be AES-256-GCM, laid out as the 23-byte header of
§7.1, then the ciphertext, then a 16-byte tag. The header holds:
- `protocol_version` = 1
- `schema_version` = 1
- direction
- u16 type
- u64 sequence
- u16 `ciphertext_length` (0..4057)
- the first 8 bytes of the session id

The whole header MUST be the AAD. The nonce MUST be the direction's 4-byte prefix followed by
the u64 big-endian sequence.

#### Scenario: Record vectors
- **WHEN** an engine seals and opens `veles-record-push-event` with the fixture keys
- **THEN** the header, ciphertext and tag match the fixture byte-for-byte

#### Scenario: Nonce construction
- **WHEN** a record is sealed at sequence n in a direction
- **THEN** its nonce is that direction's prefix followed by `u64be(n)`

### Requirement: Record rejection
A receiver MUST reject a record, without changing state, ending the session and erasing
keys, if any of the following holds:
- total size above 4096 bytes
- a `ciphertext_length` that does not match the actual length
- a failed tag
- any header field (version, schema, direction, session prefix) that differs from what it
  expects

#### Scenario: Tampered header or ciphertext
- **WHEN** any single byte of a valid record's header or ciphertext is changed
- **THEN** the receiver rejects the record, ends the session, erases keys and delivers
  nothing

#### Scenario: Negative record vectors
- **WHEN** a receiver gets the `veles-neg-record-tag`, `-session`, `-direction` or
  `-sequence` record
- **THEN** it rejects the record, ends the session and delivers nothing

#### Scenario: Size bounds
- **WHEN** a record is larger than 4096 bytes, or its `ciphertext_length` disagrees with the
  bytes received
- **THEN** it is rejected before decryption, and the session ends

### Requirement: Exact per-direction sequence enforcement
Each direction MUST accept only its next expected sequence number, starting at 0 with
`reconnect_confirm` and continuing at 1. A duplicate, skipped, reordered or cross-session
record MUST end the session, erase keys and produce no output. Counters MUST NOT wrap and
MUST NOT be reused after reconnect.

#### Scenario: Replay
- **WHEN** a valid record is delivered a second time
- **THEN** the session ends, and the duplicate is not delivered

#### Scenario: Reorder or skip
- **WHEN** record n+1 arrives before record n, or record n is missing
- **THEN** the session ends, and the out-of-order record is not delivered

#### Scenario: Prior-session replay
- **WHEN** a record captured from an earlier session of the same peers is injected into a
  new session
- **THEN** it fails authentication, and the new session ends

### Requirement: Sequence ceiling
A direction whose counter has issued sequence 2^64−1 MUST end the session before it sends or
accepts any further record.

#### Scenario: Ceiling reached
- **WHEN** a test hook positions a direction so that its next sequence is 2^64−1, a record is
  sealed and accepted at that sequence, and then another record is due
- **THEN** the record at 2^64−1 is accepted exactly once, and the session ends before any
  later record is sealed or accepted, with no wrap to 0 (matches
  `st-rec-max-u64-sequence-then-terminated`)

### Requirement: Record type and class authorization
An endpoint MUST accept only §7.3 record types, only in their allowed direction, and only
when the fixed session class authorizes them (§11.4). `reconnect_confirm` (once, at sequence
0), `app_heartbeat` and `app_error` are allowed in every class. Any record that breaks the
type, direction or class rules above MUST end the session, including unknown, reserved or
unassigned types, wrong-direction types, and types the class does not authorize.

#### Scenario: Unknown type
- **WHEN** a record carries type `0x0000`, `0x0201`, or any value not in §7.3
- **THEN** the session ends, and nothing is delivered

#### Scenario: Wrong direction
- **WHEN** a client sends `pull_events` or `push_event`, or a phone sends `pull_request`
- **THEN** the receiver ends the session

#### Scenario: Pull in an activation-recovery session
- **WHEN** the session class is `activation_recovery` and the client sends `pull_request`
- **THEN** the phone ends the session, erases keys, and delivers no application data

### Requirement: Authorized records without a handler fail closed
An authorized record type with no registered handler MUST end the session with `app_error`
`protocol_violation`. In OTP-09 this applies to every application record in release builds,
and to activation records in every class, until OTP-10 adds their handlers.

#### Scenario: Release build has no application handler
- **WHEN** a release build in a `full` session receives an authorized `pull_request`
- **THEN** it sends `app_error` `protocol_violation`, closes, and erases keys

#### Scenario: Activation record without a handler
- **WHEN** an `activation_recovery` or `full_pending_peer` session receives an authorized
  activation record
- **THEN** the receiver sends `app_error` `protocol_violation`, closes, and changes no trust
  state

### Requirement: Heartbeat rules
An endpoint MUST send `app_heartbeat` at most once per 30 s per direction
(`heartbeat_min_interval_seconds`). A heartbeat received early MUST otherwise be ignored. The
session MUST end on the fifth heartbeat within any rolling 60 s window in one direction
(OTP2-REC-034: threshold 4, window 60 s). Accounting MUST be separate for each direction.

#### Scenario: Early heartbeat is ignored
- **WHEN** a peer sends two heartbeats 10 s apart
- **THEN** both are accepted as valid records, and the session stays open

#### Scenario: Fifth heartbeat in the window
- **WHEN** a peer sends five heartbeats within 60 s in one direction
- **THEN** the fifth one ends the session

#### Scenario: Directions counted separately
- **WHEN** each direction carries four heartbeats within the same 60 s
- **THEN** the session stays open

### Requirement: Protected terminal errors
`app_error` MUST be a protected record carrying an OTP2-ENC-021 code and a bounded, safe
reason of 0..64 bytes of UTF-8. Sending or receiving it MUST be terminal: the sender closes
the connection, and both sides erase keys. An unknown error code MUST end the session.

#### Scenario: Error is terminal
- **WHEN** either side sends `app_error`
- **THEN** the connection closes, and neither side accepts another record in that session

#### Scenario: Unknown error code
- **WHEN** an `app_error` carries a code outside OTP2-ENC-021
- **THEN** the receiver ends the session

### Requirement: Idle timeout
A session MUST end, and both sides erase keys, after 180 s of monotonic time with no valid
record sent or received in either direction (`reconnect_idle_timeout_seconds`).

#### Scenario: Outbound traffic keeps the session alive
- **WHEN** only one side sends valid records, at least once every 170 s, for 10 minutes
- **THEN** the session stays open

#### Scenario: Silence in both directions
- **WHEN** no valid record is sent or received in either direction for 180 s
- **THEN** both endpoints end the session and erase keys

### Requirement: Teardown and key erasure
On disconnect, link loss, a terminal error or a timeout, each endpoint MUST erase:
- the ephemeral private keys
- the traffic keys
- the nonce prefixes
- the confirmation keys
- the session id
- the counters

It MUST refuse any later input for that session. Durable trust records MUST be unaffected.

#### Scenario: Input after teardown
- **WHEN** an engine receives bytes after its session ended
- **THEN** it processes nothing and emits nothing

#### Scenario: Trust survives teardown
- **WHEN** a session ends for any reason
- **THEN** the peer's trust record and identity key are unchanged

### Requirement: Fresh session on every reconnect
Each new link MUST run a complete new handshake. No traffic key, nonce prefix, session id or
counter may be carried over from an earlier connection.

#### Scenario: Reconnect after disconnect
- **WHEN** a session is torn down and the peers reconnect
- **THEN** the new session has a different session id and traffic keys, and both counters
  start again at 0

### Requirement: Chrome recovery succeeds only after authentication
A Chrome bounded-recovery attempt MUST count as successful, and renew the peer's allowance,
only after the authentication gate opens and the session class is fixed. A transport-level
connect MUST NOT count as success, and an authentication failure MUST consume the attempt.

#### Scenario: Recovery requires authentication
- **WHEN** BLE reconnect succeeds repeatedly but signature or confirmation verification fails
  each time
- **THEN** each failure consumes one attempt, and recovery stops at the bound

### Requirement: Android transport adapter
The Android sharing service MUST route each admitted connection to a session engine for that
connection generation, through an adapter that provides:
- connect and disconnect notifications
- multiple outbound sends
- an ordered send-then-close for terminal frames
- handling of send failures

Per-connection engine work MUST be serialized, and async results from a generation that has
ended MUST be discarded.

#### Scenario: Link loss during signing or trust lookup
- **WHEN** the link drops while Keystore signing or trust lookup is pending, and the same
  address reconnects
- **THEN** the stale result is discarded, and the new connection starts a new handshake

#### Scenario: Terminal frame ordering
- **WHEN** an engine emits `preauth_reject` or `app_error`
- **THEN** the frame is queued before the link closes, and nothing is sent after it

### Requirement: Chrome serialized session execution
The Chrome connector MUST run one client engine per phone handle. Each engine MUST process
inbound records and outbound requests strictly in order through a bounded, serialized queue.
It MUST reserve each outbound sequence and nonce inside the sealing step. Overflow MUST end
the session with `resource_exhausted`. Results tied to a stale handle or port generation MUST
be discarded. One engine ending MUST NOT affect other phones.

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
- **WHEN** one phone's session ends with an error
- **THEN** other phones' sessions continue unaffected

### Requirement: No plaintext path
The OTP-06 plaintext echo MUST run only in an explicitly enabled, instrumentation-only
transport mode that cannot run alongside the session engine. Neither the default debug path
nor the release path may produce any plaintext reply. Only complete opaque records MUST cross
the native bridge, and the bridge MUST never receive keys or plaintext.

#### Scenario: Echo bytes on the normal path
- **WHEN** the OTP-06 echo's `VEL` bytes are sent to a debug build that is not in
  instrumentation mode
- **THEN** no plaintext reply is sent, and the connection is rejected

#### Scenario: Bridge sees only ciphertext
- **WHEN** a session exchanges application records
- **THEN** every `record_base64` sent through Native Messaging is a pre-auth frame or a
  sealed record, and never contains key material or decrypted bodies

### Requirement: Vector and scenario conformance
Both engines MUST pass:
- the reconnect and record `crypto.json` fixtures listed in design D6
- the executed `records.json`/`reconnect.json` scenarios pinned by id in design D6

Fixture trace-validation negatives (expectation-stage traces, the u64-overflow trace) MUST
stay in the OTP-02 runner. Their endpoint effects MUST be asserted as engine behaviour.
Every deferred scenario MUST be named by id, together with the task that owns it.

#### Scenario: Deferred scenario listing
- **WHEN** the state-scenario suite runs
- **THEN** every scenario id is either executed or listed as deferred to OTP-10, 11, 13/14 or
  15, and none is silently skipped

#### Scenario: Expectation-stage trace as engine behaviour
- **WHEN** the engine replays the `st-neg-pull-authorized-while-record-pending` situation
- **THEN** it ends the session, erases keys and delivers no application record

### Requirement: Cross-endpoint loopback in CI
A CI test with no hardware MUST run the real Kotlin phone engine against the real TypeScript
client engine. It MUST cover:
- a full handshake
- pull, pull_events and ack
- push
- a heartbeat
- `app_error`
- two reconnects

#### Scenario: Loopback in CI
- **WHEN** the CI loopback test runs
- **THEN** both engines complete every listed exchange, and the two reconnects show
  different session ids and keys
