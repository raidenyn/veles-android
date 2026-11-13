## MODIFIED Requirements

### Requirement: Authorized records without a handler fail closed
An authorized record type with no registered handler MUST end the session with `app_error`
`protocol_violation`. Release builds have no OTP application handler, so every OTP application
record fails this way. Activation records MUST be handled by the activation handler in the
classes the protocol allows; when no activation handler is registered they MUST end the session
with `protocol_violation`. Classes that do not allow them are covered by the record and class
authorization requirement.

#### Scenario: Release build has no application handler
- **WHEN** a release build in a `full` session receives an authorized `pull_request`
- **THEN** it sends `app_error` `protocol_violation`, closes, and erases keys

#### Scenario: Activation record in an allowed class
- **WHEN** an `activation_recovery` or `full_pending_peer` session receives an authorized
  activation record and the activation handler is registered
- **THEN** the activation handler receives it with the session context and answers per
  OTP2-STA-010b

#### Scenario: Activation record without a handler
- **WHEN** an activation record arrives and no activation handler is registered
- **THEN** the receiver sends `app_error` `protocol_violation`, closes, and changes no trust
  state

### Requirement: Vector and scenario conformance
Both engines MUST pass:
- the reconnect and record `crypto.json` fixtures listed in the archived OTP-09 design (D6)
- the executed `records.json`/`reconnect.json` scenarios pinned by id there
- the OTP-10 enrollment and recovery scenarios, pinned by id in `protocol/conformance/otp-10-scenarios.json`

Fixture trace-validation negatives (expectation-stage traces, the u64-overflow trace) MUST
stay in the OTP-02 runner. Their endpoint effects MUST be asserted as engine behaviour.
Every deferred scenario MUST be named by id, together with the task that owns it. Every former
`deferred:OTP-10` scenario MUST be executed.

#### Scenario: Deferred scenario listing
- **WHEN** the state-scenario suite runs
- **THEN** every scenario id is either executed or listed as deferred to OTP-11, 13/14 or
  15, and none is silently skipped

#### Scenario: Expectation-stage trace as engine behaviour
- **WHEN** the engine replays the `st-neg-pull-authorized-while-record-pending` situation
- **THEN** it ends the session, erases keys and delivers no application record

### Requirement: Cross-endpoint loopback in CI
A CI test with no hardware MUST run the real Kotlin phone engine against the real TypeScript
client engine, using the real host-built native core and the WASM core. It MUST cover:
- a full handshake
- pull, pull_events and ack
- push
- a heartbeat
- `app_error`
- two reconnects
- a full enrollment (SPAKE2, confirmation, enrollment records and activation)
- an interrupted activation that resumes through recovery

#### Scenario: Loopback in CI
- **WHEN** the CI loopback test runs
- **THEN** both engines complete every listed exchange, and the two reconnects show
  different session ids and keys

#### Scenario: Enrollment loopback
- **WHEN** the enrollment scenario runs
- **THEN** both stores end with matching trust records and the same fingerprint
