# spake2-enrollment-exchange Specification

## Purpose
The one-time enrollment exchange between the phone and the Chrome connector: SPAKE2 through the OTP-03 core, confirmation in both directions, persist-first enrollment records bound to both identities, and the duplicate-installation refusal.

## Requirements

### Requirement: SPAKE2 over the shared core only
The phone (party A, sends first) and the Chrome client (party B) MUST run SPAKE2 and the
RFC 9382 `cA`/`cB` confirmation only through the OTP-03 core (JNI on Android, WASM in the
extension). Kotlin and TypeScript MUST NOT implement the PAKE. Identity private keys MUST NOT
pass through the core. JVM tests, extension tests and the CI loopback MUST use the real core.

#### Scenario: Shared crypto vectors
- **WHEN** either endpoint runs the enrollment crypto vectors in `crypto.json`
- **THEN** the derived values match byte-for-byte

#### Scenario: State traces executed
- **WHEN** the suites run
- **THEN** the phone machine executes the phone-side and the client machine the client-side
  scenarios of `protocol/v1/fixtures/enrollment.json` by id, with none skipped. The fixture
  self-check traces (unknown event, non-monotonic time, early timeout, unknown machine, wrong
  step expectation) stay in the OTP-02 runner

#### Scenario: Wrong code at the client
- **WHEN** the user enters a wrong code
- **THEN** the client's `cA` verification fails, it sends no `cB`, shows the generic failure,
  and the phone's attempt stays charged

### Requirement: Confirmation both directions and key derivation
The client MUST verify `cA` before sending `cB` and before deriving any enrollment key
(OTP2-CRY-006). The phone MUST verify `cB` before deriving enrollment keys or releasing its
record. Each endpoint MUST then derive the enrollment keys, nonces and session-id prefix with
the labels of §5.1.

#### Scenario: Phone verifies cB first
- **WHEN** the phone has not verified `cB`
- **THEN** it sends no `enroll_identity` record

#### Scenario: Client verifies the phone record first
- **WHEN** the client has not authenticated the phone's `enroll_identity` record
- **THEN** it sends no `enroll_identity` record of its own

### Requirement: Persist-first and prepare gate
Each side MUST derive `ET` and atomically persist a complete `pending` record (with `ET`)
before the phone sends `enrollment_prepare` and before the client releases its own
`enroll_identity` (OTP2-STA-010a). A failed persist MUST publish nothing. The client MUST reach
`ready_to_commit` only through a verified prepare from this connection, and MUST NOT send a
commit after a lost prepare. An early or mismatched prepare MUST be an ordering violation that
erases state. A `committed_waiting_ack` record MUST equal the `pending` record except for the
state field.

#### Scenario: Prepare after persist
- **WHEN** the phone has not durably persisted its `pending` record
- **THEN** it sends no `enrollment_prepare` (`st-en-prepare-sent-after-persist-pairing`)

#### Scenario: Commit without verified prepare
- **WHEN** the client has no verified prepare from this connection
- **THEN** it sends no commit (`st-neg-commit-without-verified-prepare`,
  `st-en-client-prepare-gate-full-flow`)

#### Scenario: Early or mismatched prepare
- **WHEN** a prepare arrives early or with a mismatched `ET`
- **THEN** the receiver erases state and fails (`st-neg-early-prepare-rejected`,
  `st-neg-prepare-mismatch-erases`)

#### Scenario: Persist failure
- **WHEN** persisting the `pending` record fails
- **THEN** nothing is published and pairing state is erased (`st-neg-*-persist-failure-*`)

### Requirement: Protected enroll_identity records
Each endpoint MUST send one encrypted `enroll_identity` record carrying installation ID,
`device_type` (`desktop` accepted from Chrome), identity key and fingerprint, sealed with the
enrollment keys through `RecordCodec`. `RecordPolicy` MUST keep rejecting `enroll_identity` in
reconnect sessions. The enrollment connection MUST reject any record type outside the
OTP2-CRY-020a list and erase state. The phone's record MUST carry `device_type` `0x00`; client
records MUST carry `0x01` (`desktop`) or `0x02` (`watch`) per the protocol, and the body MUST
be within the protocol's length bounds. The enrollment connection MUST end when activation completes and MUST NOT
carry OTP data.

#### Scenario: Record check
- **WHEN** a received record has a bad key, an invalid `device_type` or a per-record
  fingerprint (OTP2-REC-011a) that does not match its key
- **THEN** the receiver stores nothing and fails closed

#### Scenario: Enroll record in reconnect
- **WHEN** a reconnect session receives `enroll_identity`
- **THEN** it ends with `protocol_violation`

### Requirement: Identity and context binding
The enrollment transcript `ET` MUST bind both identities and the protocol context. A native
handle or platform hint MUST NOT substitute for the identity in the authenticated enrollment
record.

#### Scenario: Altered identity
- **WHEN** either side's identity or context differs between the two endpoints' `ET` inputs
- **THEN** `ET` differs and activation fails with no trust activated

#### Scenario: Hint does not select identity
- **WHEN** the selected device handle maps to a different installation ID than the enrolled one
- **THEN** the enrolled identity is stored, and the hint is not used as identity

### Requirement: Duplicate installation ID refusal
After confirmation, if the phone holds an `active`, `pending` or `committed_waiting_ack` record
for the presented installation ID, it MUST NOT create or replace a record, MUST send the
protected error `0x0003` with code `0x04` `enrollment_rejected`, MUST erase pairing state and
MUST close the window (OTP2-STA-012). It MUST check before `addPending`. A duplicate on the
client side MUST take the persist-failure path and publish nothing.

#### Scenario: Held installation ID
- **WHEN** a verified client presents an installation ID the phone already holds
- **THEN** the phone sends `enrollment_rejected`, the existing record is unchanged, and the
  window is closed

#### Scenario: Client receives enrollment_rejected
- **WHEN** the client receives `enrollment_rejected` after persisting its own `pending` record
- **THEN** it deletes that record, erases pairing state and shows the failure

### Requirement: Fingerprint display gating
Both endpoints MUST derive the shared pair fingerprint (OTP2-REC-011) and MUST show it only
after both records are authenticated. The per-record fingerprint MUST be checked separately
before storing.

#### Scenario: Not shown early
- **WHEN** only one record is authenticated
- **THEN** no fingerprint is available to the UI layer

#### Scenario: Same fingerprint
- **WHEN** both records are authenticated
- **THEN** both endpoints show the same `XXXX XXXX XXXX` value

### Requirement: Sensitive state erasure
When pairing ends in any way, each endpoint MUST erase the code, SPAKE2 state, shared secrets
and enrollment keys, and MUST close the core handle. Erasure is best effort in managed runtimes
and MUST claim no more than OTP-03 does.

#### Scenario: Success erasure
- **WHEN** activation completes
- **THEN** the holder of enrollment keys is zeroed and the code is gone on both sides

#### Scenario: Failure erasure
- **WHEN** pairing fails or is cancelled at any state
- **THEN** the same material is erased and the handle closed

### Requirement: Cancel semantics
Cancel MUST tear down the enrollment connection, erase pairing material, and delete a
`pending` or `committed_waiting_ack` record created by the current enrollment, on both sides.

#### Scenario: Cancel after pending
- **WHEN** cancel arrives after the record became `pending`
- **THEN** the record is deleted and incomplete material is erased

### Requirement: Constant-time confirmation comparisons
Confirmation and activation MAC comparisons MUST be constant-time on both endpoints.

#### Scenario: Comparison API
- **WHEN** the code is reviewed or scanned
- **THEN** every MAC comparison uses the constant-time helper

### Requirement: Connection routing and core build guard rails
The phone MUST choose the pairing machine or the reconnect engine from the first frame of each
connection. Pairing frame types (0x01–0x05) MUST use their own allow-list, so the reconnect
engine's accepted and emitted frame set is unchanged, and a role-mismatched frame MUST be
rejected (`st-en-role-mismatch-frame-rejected`). The `host-jni` cargo feature MUST be off by
default, the Android `.so` and the WASM package MUST be unchanged by it, and the host library
MUST NOT be packaged. The WASM core MUST load only in the connector tab, never the service
worker. `VelesCrypto` MUST NOT initialize under Robolectric tests.

#### Scenario: Reconnect set unchanged
- **WHEN** a reconnect connection receives a pairing frame
- **THEN** it is rejected exactly as before this change

#### Scenario: Host library absent from products
- **WHEN** `verify-apk-jni.sh` and the package file-set checks run
- **THEN** no host library appears in the APK or the packages

#### Scenario: Robolectric guard
- **WHEN** a Robolectric test initializes `VelesCrypto`
- **THEN** the guard test fails

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
