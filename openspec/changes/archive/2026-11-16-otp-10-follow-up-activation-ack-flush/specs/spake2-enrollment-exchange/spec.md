## ADDED Requirements

### Requirement: Enrollment completion hold

After the client sends `activation_ack` it MUST keep the enrollment transport open until the hold
ends in one of the ways below, and MUST NOT issue a disconnect request on its own finishing path
before then. The hold ends in exactly one of three ways: the phone closes the connection (the client
then issues no disconnect request); the hold bound expires (the client then issues exactly one
disconnect request); or another owner ends the link — a user cancel, connector-context loss, peer
revocation, fail-closed recovery, identity reset or host port death — and that path closes the held
link as it closes any other enrollment connection. The bound is `ACTIVATION_HOLD_MS` (5000 ms, below
the phone's 30-second request-inactivity bound) and is measured from the ack's successful send. The
phone ending the connection is the client's only delivery evidence, because the host's `sent` report
means the batch was submitted, not delivered.

While the link is held the client MUST drop every arriving record without feeding it to the
enrollment machine and MUST NOT send or accept OTP data; the enrollment keys are already erased and
the core handle closed when the ack is sent (OTP2-CRY-019e). A completed activation MUST be terminal
for the lifetime of the link: a teardown path MUST NOT drive the machine into a failed state or
report the enrollment as failed, and the link MUST NOT delete the `active_activation_pending` mirror
— a revocation or reset removes records only through its own trust-store write. A held link MUST
remain selectable by peer revocation, fail-closed recovery, reset and port death. The logical end of
the enrollment connection — the `close_enrollment_connection` effect the conformance trace reports
for the client's `activation_complete` — still happens when activation completes; only the transport
release is deferred.

#### Scenario: Phone ends the connection after the ack

- **WHEN** the phone verifies `activation_ack`, activates its record and closes the connection
- **THEN** the client finishes without issuing a disconnect request, the machine stays `active`, no
  failure is reported and no recovery is scheduled for that handle

#### Scenario: Phone never ends the connection

- **WHEN** the hold bound expires after the ack was sent
- **THEN** the client issues exactly one disconnect request, keeps the `active_activation_pending`
  mirror, leaves the machine `active` and leaves the enrollment to the OTP2-STA-010b recovery
  protocol

#### Scenario: Close observed before the send resolves

- **WHEN** the phone's close is observed while the ack's send is still pending and that send still
  succeeds
- **THEN** the link finishes at once without issuing a disconnect request and without waiting for the
  bound

#### Scenario: Record during the hold

- **WHEN** any record arrives while the link is held
- **THEN** the client drops it without feeding it to the enrollment machine and without changing the
  activated record

#### Scenario: Erasure before the hold

- **WHEN** `activation_ack` has been sealed and sent
- **THEN** the enrollment keys are already erased and the core handle closed, and the hold holds no
  enrollment material

#### Scenario: No OTP data on the enrollment connection

- **WHEN** the client is in the hold
- **THEN** no OTP application record is sent or accepted on that connection

#### Scenario: Teardown during the hold

- **WHEN** a fail-closed recovery, identity reset or host port death runs while the link is held
- **THEN** the link is closed, the machine does not enter a failed state, and the link deletes no
  record — a reset removes trust only through its own reset write

#### Scenario: Revoked peer during the hold

- **WHEN** the peer of that enrollment is revoked while the link is held
- **THEN** the revocation selects and closes the held link without reporting a failure, and the peer
  record is removed by the revocation's own trust-store write

#### Scenario: Cancel or context loss during the hold

- **WHEN** the user cancels or the connector context is lost while the link is held
- **THEN** the link closes immediately, the machine ends in `idle` without a failure, and the mirror
  is kept because it belongs to a completed activation

#### Scenario: Failed send of the ack

- **WHEN** sending `activation_ack` fails for any cause
- **THEN** the link ends through its normal teardown, including its disconnect request so that no
  handle leaks, the machine does not enter a failed state, and the enrollment is left to the
  recovery protocol
