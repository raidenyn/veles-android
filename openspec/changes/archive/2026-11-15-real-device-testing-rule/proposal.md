## Why

Unit tests and linters pass while the real app or extension still breaks: notification access, the CDM grant, extension messaging and pairing only show up on a device or in Chrome. Agents currently decide ad hoc whether to test there. A short always-on rule makes real-environment testing part of every behaviour-affecting change.

## What Changes

- Add `.agents/rules/repo-real-device-testing.md`: plan and run tests on a real device or emulator for Android logic, in real Chrome for web-extension logic, through each consumer for shared logic, and end to end when more than one component is affected; docs and minor edits are exempt; unavailable environments are reported, never claimed as tested.
- List the rule in the root `AGENTS.md`.
- Update the `agent-guidance-layout` requirement: the initial `repo-*` rules grow from eight to nine.

## Capabilities

### New Capabilities
- `real-device-testing`: when a change must be planned for and tested on a real device, emulator or Chrome, and how the result is reported.

### Modified Capabilities
- `agent-guidance-layout`: the root `AGENTS.md` lists nine `repo-*` rules, including the real-device-testing rule.

## Impact

- New: `.agents/rules/repo-real-device-testing.md`, one capability spec.
- Edited: root `AGENTS.md`; the `agent-guidance-layout` delta lives in this change's `specs/` and reaches `openspec/specs/` at sync/archive, not during implementation.
- No code, CI or check-script change; `docs/checks/check_agent_files.py` already covers a new `repo-*` rule.
- Out of scope: device-farm setup, new test suites, per-node commands, CI enforcement.
