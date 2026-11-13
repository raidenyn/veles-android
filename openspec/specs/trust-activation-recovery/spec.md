# trust-activation-recovery Specification

## Purpose
TBD — introduced by the OTP-10 change; describe its purpose here.

## Requirements

### Requirement: Durable activation exchange
After enrollment, the endpoints MUST run prepare, commit, `activation_complete` and
`activation_ack` through the OTP-04/05 stores with durable `pending`, `committed_waiting_ack`
(phone) and `active_activation_pending` (Chrome) records. The activation verifier MUST accept a
proof only from a session-bound proof registry filled by the pairing machine or a recovery
session. `pending` and `committed_waiting_ack` records MUST authorize only the activation
exchange.

#### Scenario: Successful activation
- **WHEN** all four messages are exchanged
- **THEN** the phone record is `active`, the Chrome record is `active_activation_pending`, and
  the Chrome record upgrades to `active` on its next reconnect

#### Scenario: Wrong proof
- **WHEN** a commit or ack MAC does not verify
- **THEN** the record state does not change and the session ends

### Requirement: Incomplete records authorize no OTP data
A `pending` or `committed_waiting_ack` record MUST NOT authorize any OTP data record in any
session.

#### Scenario: Pull while pending
- **WHEN** an authorized-looking `pull_request` arrives for a peer in `pending`
- **THEN** the session ends and no OTP data is delivered

### Requirement: Pending record expiry
Phone pending records MUST expire per OTP2-STA-010c (boot-scoped, 86 400 s). Chrome pending
records MUST be connector-scoped per OTP2-STA-010d and deleted on context loss. Expiry MUST
leave no partial access. Both stores MUST take injectable monotonic clocks.

#### Scenario: Phone expiry
- **WHEN** a fake clock passes 86 400 s on a `pending` phone record
- **THEN** the record is deleted and cannot be recovered

#### Scenario: Boot continuity loss
- **WHEN** boot continuity is lost
- **THEN** only incomplete records are deleted (`st-rec-boot-continuity-loss-deletes-incomplete-only`)

#### Scenario: Chrome active mirror survives restart
- **WHEN** the extension restarts with an active mirror record
- **THEN** it persists (`st-rec-client-mirror-durable-across-restart`)

#### Scenario: Chrome context loss
- **WHEN** the connector context is lost with a `pending` Chrome record
- **THEN** the record is deleted on next load

### Requirement: Recovery keys
Each OTP-09 engine MUST derive the OTP2-CRY-019f recovery activation keys inside
`ReconnectKeySchedule.derive` whenever the peer record carries `ET`, and MUST erase them when
the class resolves to `full`. Both peer lookups MUST carry `ET`.

#### Scenario: Recovery vectors
- **WHEN** an engine derives keys from the `crypto.json` recovery vectors
- **THEN** `k_rec_client` and `k_rec_phone` match byte-for-byte

#### Scenario: Full class
- **WHEN** the session class is `full`
- **THEN** the recovery keys are erased

### Requirement: Resume in recovery classes
An interrupted activation MUST resume in `activation_recovery`, or in `full_pending_peer` when
one side is already `active` (OTP2-STA-010b steps 0, 5 and 6). The phone MUST send an
unprompted step-0 prepare when the client reports `peer_state = 0x02`, and an active-family
client that sees `peer_state = 0x02` MUST send the step-5 ack on `Authenticated`. A `pending`
client MUST instead wait for the step-0 prepare and re-commit (OTP2-STA-010b steps 0, 1, 5). A recovery class MUST NOT upgrade to `full`. Chrome MUST
start a recovery session for a pending client with an explicit installation ID.

#### Scenario: Interrupted before commit
- **WHEN** the link drops after prepare and both records persist
- **THEN** a signed reconnect in `activation_recovery` completes activation without a code

#### Scenario: Ack lost
- **WHEN** the phone is `committed_waiting_ack` and the client is already active-family
- **THEN** recovery re-delivers the ack and the phone becomes `active`

#### Scenario: Duplicate commit on active
- **WHEN** a `full_pending_peer` session delivers a duplicate `commit` for an `ACTIVE` phone record
- **THEN** after the MAC verifies it is a no-op and no state regresses

#### Scenario: Replayed recovery record
- **WHEN** a recovery record is replayed in another session
- **THEN** it is rejected

### Requirement: Three interruption outcomes
An interruption MUST end in exactly one of: resume (both peer records persisted), expiry with
no partial access, or no record (interruption after a verified `cB` but before the peer
identity was persisted), where the only path is a new pairing window.

#### Scenario: No record
- **WHEN** the link drops after `cB` verification and before the peer identity is stored on
  either side
- **THEN** no record exists there and reconnect fails generically
  (`st-en-cb-disconnect-before-identity-repair-only`)

#### Scenario: One-sided pending
- **WHEN** the client persisted `pending` but the phone never received its record
- **THEN** the client record stays inactive and blocks re-pairing until cancel, clear or expiry
  (`st-en-client-one-sided-pending-cancelled-then-fresh-pairing`)

#### Scenario: Interruption at every message
- **WHEN** the link is dropped at each activation message in turn
- **THEN** each case ends in resume, expiry or a new window, never OTP access

### Requirement: Chrome store accepts a pending authentication
For a `pending` Chrome record, `authenticated()` MUST run the verifier, check `pendingContext`,
and write nothing (no `lastSeen`, no hint, no change to pending fields), so a recovery session
does not terminate.

#### Scenario: Recovery session for pending client
- **WHEN** a recovery session authenticates a `pending` record
- **THEN** the session continues and the record is byte-identical afterwards

### Requirement: Phone store accepts a duplicate commit
The phone store MUST accept a duplicate `commit` on an `ACTIVE` record as a no-op once the MAC
verifies, per §11.4.

#### Scenario: st-rec-full-pending-peer-commit-duplicate-no-regression
- **WHEN** the scenario (in `records.json`) runs against the Kotlin store
- **THEN** it passes with the record staying `ACTIVE`

### Requirement: Multiple peers
Each pairing MUST create an independent trust record. A phone MAY pair several desktops one
after another, with one window and one SPAKE2 operation globally.

#### Scenario: Two desktops
- **WHEN** a second desktop pairs after the first
- **THEN** both records exist and either reconnects without a code

### Requirement: Conformance scenarios executed
The `deferred:OTP-10` entries in `protocol/conformance/otp-09-scenarios.json` and the recovery
scenarios in `reconnect.json` MUST become executed scenarios on both endpoints.

#### Scenario: No deferred OTP-10 entries
- **WHEN** the state-scenario suite runs
- **THEN** no scenario is listed as deferred to OTP-10
