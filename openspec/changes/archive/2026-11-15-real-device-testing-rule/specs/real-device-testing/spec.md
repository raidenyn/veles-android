## ADDED Requirements

### Requirement: Real-environment testing rule
The repository SHALL provide an always-on `repo-*` rule `.agents/rules/repo-real-device-testing.md` that requires an agent to plan, and before calling the work done to execute, real-environment tests for the logic a change affects: Android on a real device or emulator, the web-extension in a real Chrome, and shared `rust/`, `protocol/` and `native-bridge/` logic through each consumer it affects. It MUST NOT name tools or commands.

#### Scenario: Android logic change
- **WHEN** a change alters Android code behaviour
- **THEN** the plan includes a run on a real device or emulator and it is executed before the work is called done

#### Scenario: Web-extension logic change
- **WHEN** a change alters web-extension behaviour
- **THEN** the plan includes a run in a real Chrome with the extension loaded and it is executed before the work is called done

#### Scenario: Shared logic change
- **WHEN** a change touches only shared logic in `rust/`, `protocol/` or `native-bridge/` that Android and Chrome both consume
- **THEN** it is run through both consumers even though nothing under `android/` or `web-extension/` changed

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
