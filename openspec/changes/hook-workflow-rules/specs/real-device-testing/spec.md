## MODIFIED Requirements

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
