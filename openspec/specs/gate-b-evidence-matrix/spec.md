# gate-b-evidence-matrix Specification

## Purpose
The committed Gate B evidence matrix, its validator and timing rules, and the runbook that keep every row explicitly not accepted until the owner adjudicates real evidence.

## Requirements

### Requirement: Committed matrix and its row set
`docs/gate-b/matrix.json` MUST hold one row per entry of the required table below, with row id
`<platform>.<phones>.<case>`, a `journeys` list (possibly empty), a status from `NOT RUN`,
`FAIL`, `PASS` (a run that could not happen stays `NOT RUN` with its blocker), a blocker, and a `no_bond_handle_or_hint_reliance` field (null unless the
status is `PASS`, where it MUST be `true`). The matrix MUST also hold `decision`, `gate_b_accepted`
and `initial_state`. Required rows, for each of `windows` and `macos`: `none` host-missing-recovery,
`none` host-incompatible-recovery; `A` extension-search, spake2-pairing, hint-reconnect-fresh-keys,
protected-pull, push, background-delivery (ten continuous minutes, an ordinary tab focused, no audio,
WebRTC or timer-throttling exemption, a protected event and an offscreen copy after minute seven),
offscreen-copy, forced-freeze, forced-discard, connector-closure, connector-restart, host-closure,
host-restart, revocation, bluetooth-interruption; `AB` two-phone-lifecycle. For `android`, phones `A`:
locked-phone-20min and fixture-retention-expiry.

#### Scenario: Missing row
- **WHEN** a required row id is absent
- **THEN** the validator test fails and names the row id

#### Scenario: Unknown status
- **WHEN** a row has a status outside the vocabulary
- **THEN** the validator rejects it

#### Scenario: Cited journey missing
- **WHEN** a row cites a journey id absent from `protocol/conformance/gate-b-journeys.json`
- **THEN** a conformance test fails

### Requirement: Initial state and invariants
The matrix as committed by this change MUST have `decision: OPEN`, `gate_b_accepted: false`,
`initial_state: true` and every row `NOT RUN` with a blocker. A pin test MUST assert exactly that
while `initial_state` is true; the evidence commit that records a first verdict MUST set
`initial_state` false in the same commit, after which only the invariants apply. The invariants
MUST hold always: `gate_b_accepted` true requires `decision` `ACCEPTED`, a decision reference and
every row `PASS`; a `PASS` row MUST satisfy the evidence rules below.

#### Scenario: Initial commit
- **WHEN** the committed matrix is validated with `initial_state` true
- **THEN** it reports `OPEN`, `gate_b_accepted` false and every row `NOT RUN`

#### Scenario: Later evidence commit
- **WHEN** a row is `PASS` with valid evidence and `initial_state` is false
- **THEN** the pin test does not apply and the invariants hold

#### Scenario: Accepted without every PASS
- **WHEN** `gate_b_accepted` is true while any row is not `PASS`
- **THEN** the validator rejects the matrix

### Requirement: Evidence fields for any verdict
For any status other than `NOT RUN` a row MUST carry source and evaluator revisions and hashes,
executable, package and signing identities, phone and desktop models, OS builds, Chrome versions,
adapters, focused-tab identity, a confirmation that no audio, WebRTC or timer-throttling exemption
was active, controlled actions with controller-clock timestamps, phone and source bindings, return
codes, expected and actual results, a classification, cleanup, references to raw captures and a
checksum catalog, repetitions, a reproducibility classification, a record that no plaintext fallback
occurred, host process and queue observations, safely redacted logs, a `provenance_anchor`
(a reference to an external record of the checkout, including untracked bytes, the evaluator files
and the actual executable) and a reference to a separate adjudication. A `NOT RUN` row MUST carry
none of these and MUST NOT claim any.

#### Scenario: Verdict without raw captures
- **WHEN** a non-`NOT RUN` row has no raw-capture or checksum-catalog reference
- **THEN** the validator rejects it

#### Scenario: PASS requires adjudication and anchor
- **WHEN** a `PASS` row has no separate adjudication reference or no `provenance_anchor`
- **THEN** the validator rejects it

#### Scenario: NOT RUN claims evidence
- **WHEN** a `NOT RUN` row carries an evidence field
- **THEN** the validator rejects it

### Requirement: Case-specific evidence
Freeze and discard rows MUST additionally carry the forced trace, `chrome.tabs` observations, the
immediate per-tab badge and status clear, the old port generation's invalidation before queued
events, old-port closure with no host process, and the fresh-host re-pull with unexpired records
present and expired records absent. Reconnect and restart rows MUST carry evidence of fresh keys, and
restart rows fresh handles. Closure rows MUST carry evidence that no host process remains. The
offscreen-copy row MUST carry the correlated clipboard transition.

#### Scenario: Freeze row incomplete
- **WHEN** a freeze or discard row lacks the badge-clear or the retained/expired fixture result
- **THEN** the validator rejects it

#### Scenario: Closure without process evidence
- **WHEN** a closure row lacks the no-host-process evidence
- **THEN** the validator rejects it

### Requirement: Timing rules
Minute 0 MUST be the controller-clock time at which the phone's `scheduled` log line for a push
with a 1200-second delay was read, with the offset method recorded. The 20-minute `PASS` MUST have
the push's controller-side receipt within minute 19 to minute 21 inclusive, the phone locked at task
removal and not unlocked until at least 15 minutes after it, the foreground indication present
throughout, and a connector pull after minute 15. The background-delivery `PASS` MUST have ten
continuous minutes with another ordinary tab focused, no exemption active, and the protected event
and offscreen copy after minute seven. The rule functions MUST be pure over supplied timestamps
(minute 0, receipt, task removal, unlock, pull, delay) and the tests MUST NOT read a clock or sleep.

#### Scenario: Push at minute 18.9
- **WHEN** the supplied arrival is 18 minutes 54 seconds after minute 0
- **THEN** the timing rule fails

#### Scenario: Push at minute 19 and minute 21
- **WHEN** the supplied arrival is exactly 19 minutes, or exactly 21 minutes, after minute 0
- **THEN** the timing rule passes

#### Scenario: Push just after minute 21
- **WHEN** the supplied arrival is 21 minutes 1 second after minute 0
- **THEN** the timing rule fails

#### Scenario: Locked 14 minutes
- **WHEN** the phone was unlocked 14 minutes after task removal
- **THEN** the timing rule fails

#### Scenario: Pull at minute 15
- **WHEN** the only connector pull is at or before minute 15
- **THEN** the timing rule fails

#### Scenario: Wrong delay
- **WHEN** the scheduled delay is not 1200 seconds
- **THEN** the timing rule fails

### Requirement: The tooling never accepts Gate B
The validator MUST never write or upgrade a status to `PASS`, MUST treat its own pass as necessary
and not sufficient, and MUST require the provenance anchor and the separate adjudication as fields it
checks but does not perform. Recording the Gate B decision MUST remain outside this tooling and this
change. The validator MUST use only the Python standard library and run under
`python3 -m unittest discover -s docs/checks`.

#### Scenario: Tool refuses to promote
- **WHEN** the validator is run on a matrix with a row marked `PASS` by hand
- **THEN** it validates the row's evidence and its output states that Gate B is not accepted by the tool

### Requirement: Runbook
`docs/otp-12-gate-b-runbook.md` MUST contain, for every row case, a section whose heading holds the
case id, with the exact steps, the adb and debug commands and what to record. It MUST define minute 0,
state the reseed step before the post-minute-15 pull, state that a push after five pulled records
evicts one on the client, record the prerequisite state (OTP-24 physical acceptance is not complete;
the extension artifact that observes the run is recorded as an identity) and state that the
non-evidentiary smoke run is never Gate B evidence.

#### Scenario: Runbook coverage
- **WHEN** a test compares the runbook headings with the case ids and checks for the required statements
- **THEN** every case id has a section and each required statement is present
