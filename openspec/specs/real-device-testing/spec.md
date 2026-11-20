# real-device-testing Specification

## Purpose
This capability defines the always-on rule that requires agents to plan and run tests for behaviour-affecting changes on a real device or emulator, in a real Chrome, through each consumer of shared logic, and end to end across components, and to report honestly when an environment is unavailable.

## Requirements

### Requirement: Real-environment testing rule
The repository SHALL provide a hook-delivered rule `.agents/hooked-rules/repo-real-device-testing.md` that
requires an agent to plan, and before calling the work done to execute, real-environment tests for the logic a
change affects: Android on a real device or emulator, the web-extension in a real Chrome, and shared `rust/`,
`protocol/` and `native-bridge/` logic through each consumer it affects. Its body MUST NOT name tools or commands; its frontmatter and pointer may name the gated command.

The rule MUST be delivered at the planning moments (the fast-forward, propose and continue skills, and
`openspec instructions tasks|plan`), at the apply and verify skills, at `openspec instructions finalize`, and by a
gate on `gh pr create`, so that work outside the openspec workflow meets it before its pull request. Its always-on
pointer MUST state the rule itself: a behaviour change needs a real device, emulator or Chrome run before it is
called done, stated in the PR or report.

#### Scenario: Android logic change
- **WHEN** a change alters Android code behaviour
- **THEN** the plan includes a run on a real device or emulator and it is executed before the work is called done

#### Scenario: Web-extension logic change
- **WHEN** a change alters web-extension behaviour
- **THEN** the plan includes a run in a real Chrome with the extension loaded and it is executed before the work is called done

#### Scenario: Shared logic change
- **WHEN** a change touches only shared logic in `rust/`, `protocol/` or `native-bridge/` that Android and Chrome both consume
- **THEN** it is run through both consumers even though nothing under `android/` or `web-extension/` changed

#### Scenario: Work outside the workflow
- **WHEN** an agent that never ran an openspec skill or command runs `gh pr create`
- **THEN** the call is denied once with the rule text, and the re-run proceeds

#### Scenario: Pointer keeps the rule
- **WHEN** the hooks are disabled
- **THEN** the root `AGENTS.md` pointer still states that a behaviour change needs a real-environment run before it is called done

### Requirement: End-to-end coverage across components
A change that affects more than one component MUST also be covered by an end-to-end scenario across those components, planned and executed under the same rule.

#### Scenario: Multi-component change
- **WHEN** a change affects more than one component, such as Android and the web-extension
- **THEN** the plan and the execution include an end-to-end scenario across those components

### Requirement: Exemption and honest reporting
Changes limited to documentation, comments, formatting or other edits with no behaviour change SHALL be exempt. The rule MUST require the result to state what ran and on what, and when a required device or browser is unavailable the agent MUST report that and ask, and MUST NOT report the change as tested.

#### Scenario: Documentation-only change
- **WHEN** a change only edits documentation, comments or formatting
- **THEN** no real-environment run is required

#### Scenario: Environment unavailable
- **WHEN** a required device or browser cannot be used
- **THEN** the agent reports it and asks how to obtain the environment or to proceed with explicitly incomplete testing, and does not state the change was tested

### Requirement: macOS Bluetooth test apps use one fixed identity and one grant
A real macOS Bluetooth E2E run SHALL use one fixed app identity: a bundle identifier and the repository's approved macOS signing identity, never an ad-hoc or linker-only signature. It SHALL compare the app's designated requirement with the recorded baseline before launch. A person MAY be asked to grant Bluetooth once per identity, including a planned first-use grant; the rule MUST forbid a TCC reset and an unplanned retry past an undetermined or denied authorization.

#### Scenario: Rebuild reuse
- **WHEN** the same bundle identifier and signing identity are rebuilt and launched again
- **THEN** the designated requirement equals the recorded baseline, the first scan starts without a permission prompt, and the validated TCC log window shows no `AUTHREQ_PROMPTING`, `CFUserNotification` or grant-write event

#### Scenario: Ad-hoc build or changed bundle identifier
- **WHEN** a Bluetooth test bundle is ad-hoc or linker-only signed, or its bundle identifier differs from the recorded identity
- **THEN** the rule rejects it as a new TCC identity instead of reporting the run as reusing the grant

#### Scenario: Replaced certificate with an unchanged requirement
- **WHEN** a replaced or renewed certificate still produces the recorded designated requirement
- **THEN** the run may reuse the existing grant; the designated requirement comparison, not the certificate's issuance, decides

#### Scenario: New identity
- **WHEN** the bundle identifier, certificate or team change makes the designated requirement differ from the baseline
- **THEN** the rule requires one person-approved grant and a recorded requirement comparison, and does not treat the old grant as reused

#### Scenario: Planned first grant
- **WHEN** a planned, attended first-use run has no grant and the person approves the Bluetooth prompt
- **THEN** the grant is recorded and later runs reuse it; no reset is performed

#### Scenario: No reset or unplanned retry
- **WHEN** a reuse run unexpectedly reaches `notDetermined`, `permission_pending` or `denied`
- **THEN** the rule forbids a TCC reset and a retry, and requires fixing the identity or launch path before the run is repeated

### Requirement: macOS Bluetooth test apps launch through a supported route and prove reuse
A real macOS Bluetooth E2E run SHALL launch the Bluetooth-capable process through LaunchServices from the logged-in GUI session, or through Chrome's Native Messaging host from inside the signed bundle, never as a bare executable from a terminal or SSH session. It SHALL verify reuse with a validated TCC log window; an unvalidated capture is inconclusive, not evidence.

#### Scenario: Launch attribution
- **WHEN** the host is executed from a terminal or SSH session instead of LaunchServices or Chrome
- **THEN** the rule treats the run as invalid; the measured SSH case attributes the request to the parent process, and other terminal contexts are forbidden by policy even though unmeasured

#### Scenario: Inconclusive log
- **WHEN** the TCC log capture is empty, unreadable, wrongly filtered or lacks the positive-control records
- **THEN** the run reports BLOCKED or inconclusive and does not claim the grant was reused
