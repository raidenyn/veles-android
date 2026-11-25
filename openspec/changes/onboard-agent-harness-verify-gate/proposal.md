## Why

Turn-end verification lives in two hand-built places: a second command on the Claude Code `Stop` hook
(`verify.py --hook claude`) and a verifier slot in the OpenCode hooked-rules plugin (`verify.py --hook opencode`).
Neither checks an edit or a ticked task. The agent harness RFC (raidenyn/agentic-development-skills, milestone M1,
task issue 14) replaces both with copied tools from the published catalog: the hook runtime and the verify gate,
which run one repo command on a file edit, a ticked task and every turn end, and skip a tree unchanged since the
last full pass.

This is a lean change: no brainstorm, design or plan, and no review gate, at the owner's choice. The task issue and
the RFC are the design.

## What Changes

**Verify wiring**
- From: `Stop` holds the engine command then `verify.py --hook claude || true`; the OpenCode plugin starts
  `verify.py --hook opencode` at a root turn end; nothing runs on an edit or a tick.
- To: `.agents/harness/hook-runtime/` and `.agents/harness/verify-gate/` are installed (lock in
  `.agents/harness/lock.json`, settings in `.agents/harness.json`). Each Claude Code engine event holds the engine
  entry then the runtime entry; `PostToolUseFailure`, `SubagentStop` and `SessionStart` hold the runtime entry
  alone. `opencode.json` loads `./.agents/harness/hook-runtime/opencode`. The OpenCode plugin's verifier slot is
  removed.
- Reason: one command, three triggers, both hosts, from copied tools that `check` can prove unedited.
- Impact: non-breaking for the engine and the push gate; `verify.py --hook` stays, unwired.

**The command**
- `scripts/verify_gate.py`: on `edit` it lints the edited file where a fast single-file lint exists; on `task` and
  `turn-end` it execs the plain task gate `python3 -I scripts/verify.py`, which still writes the branch pass.

**Checks and CI**
- The agent-file check accepts exactly the new wiring. The tooling CI job runs both tools' `check`, and
  `.agents/harness/` and `.agents/harness.json` are tooling `ci-paths`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hooked-rule-delivery`: Host adapters wire the hook runtime next to the engine, and the plugin starts no verifier.
- `verification-stop-hook`: `--hook` mode is no longer wired; the OpenCode turn-end verification requirement is
  removed.
- `agent-guidance-layout`: `opencode.json` allows the hook runtime's plugin as a third entry.

## Impact

- `.agents/harness/`, `.agents/harness.json`, `.claude/settings.json`, `opencode.json` (installed by the tools'
  `install`; never edited by hand).
- `scripts/verify_gate.py` and `scripts/tests/test_verify_gate.py`.
- `scripts/opencode-hooked-rules/index.js` and its tests (verifier slot removed).
- `docs/checks/check_agent_files.py` and its tests; `docs/verify.md`, `docs/opencode-rules.md`,
  `docs/hook-delivered-rules.md`, `CONTRIBUTING.md`, `scripts/AGENTS.md`.
- `.github/workflows/check-tooling.yml`, `.github/components.json`.
- Unchanged: `scripts/verify.py`, `scripts/verify-commands.json`, `scripts/branch_pass.py`, the engine and its rules.
