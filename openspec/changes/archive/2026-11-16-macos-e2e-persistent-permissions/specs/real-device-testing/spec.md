## ADDED Requirements

### Requirement: macOS Bluetooth test apps use one fixed identity and one grant

A real macOS Bluetooth E2E run SHALL use one fixed app identity: a bundle identifier and the
repository's approved macOS signing identity, never an ad-hoc or linker-only signature. It SHALL
compare the app's designated requirement with the recorded baseline before launch. A person MAY be
asked to grant Bluetooth once per identity, including a planned first-use grant; the rule MUST forbid
a TCC reset and an unplanned retry past an undetermined or denied authorization.

#### Scenario: Rebuild reuse

- **WHEN** the same bundle identifier and signing identity are rebuilt and launched again
- **THEN** the designated requirement equals the recorded baseline, the first scan starts without a
  permission prompt, and the validated TCC log window shows no `AUTHREQ_PROMPTING`,
  `CFUserNotification` or grant-write event

#### Scenario: Ad-hoc build or changed bundle identifier

- **WHEN** a Bluetooth test bundle is ad-hoc or linker-only signed, or its bundle identifier differs
  from the recorded identity
- **THEN** the rule rejects it as a new TCC identity instead of reporting the run as reusing the grant

#### Scenario: Replaced certificate with an unchanged requirement

- **WHEN** a replaced or renewed certificate still produces the recorded designated requirement
- **THEN** the run may reuse the existing grant; the designated requirement comparison, not the
  certificate's issuance, decides

#### Scenario: New identity

- **WHEN** the bundle identifier, certificate or team change makes the designated requirement differ
  from the baseline
- **THEN** the rule requires one person-approved grant and a recorded requirement comparison, and
  does not treat the old grant as reused

#### Scenario: Planned first grant

- **WHEN** a planned, attended first-use run has no grant and the person approves the Bluetooth prompt
- **THEN** the grant is recorded and later runs reuse it; no reset is performed

#### Scenario: No reset or unplanned retry

- **WHEN** a reuse run unexpectedly reaches `notDetermined`, `permission_pending` or `denied`
- **THEN** the rule forbids a TCC reset and a retry, and requires fixing the identity or launch path
  before the run is repeated

### Requirement: macOS Bluetooth test apps launch through a supported route and prove reuse

A real macOS Bluetooth E2E run SHALL launch the Bluetooth-capable process through LaunchServices
from the logged-in GUI session, or through Chrome's Native Messaging host from inside the signed
bundle, never as a bare executable from a terminal or SSH session. It SHALL verify reuse with a
validated TCC log window; an unvalidated capture is inconclusive, not evidence.

#### Scenario: Launch attribution

- **WHEN** the host is executed from a terminal or SSH session instead of LaunchServices or Chrome
- **THEN** the rule treats the run as invalid; the measured SSH case attributes the request to the
  parent process, and other terminal contexts are forbidden by policy even though unmeasured

#### Scenario: Inconclusive log

- **WHEN** the TCC log capture is empty, unreadable, wrongly filtered or lacks the positive-control
  records
- **THEN** the run reports BLOCKED or inconclusive and does not claim the grant was reused
