## Why

`git push` is denied until a complete `python3 -I scripts/verify.py --branch` run passed for HEAD. That run executes
every test of every affected node one after another and takes about 15 minutes locally, while CI runs the same
commands in parallel on every pull request, much faster. The local wait buys little coverage CI does not already
give. Accepting the fast task gate agents already run between tasks keeps a fresh pre-push check at a fraction of
the time.

## What Changes

**Who writes the push pass**
- From: only a complete `--branch` run writes `build/verify/branch-pass.json`; the task gate never touches it.
- To: a complete manual run of either gate writes it, and either gate's manual run deletes an old one once it holds
  the run lock. The Stop hook still never writes or deletes it, and the manual task gate still never reads a
  remembered pass.
- Reason: lets the task gate prove a push.
- Impact: non-breaking; a record `--branch` writes still satisfies the push gate.

**The pre-push rule and docs**
- From: `.agents/hooked-rules/repo-pre-push-checks.md`, its root pointer and the docs tell the agent to run
  `python3 -I scripts/verify.py --branch` (about 15 minutes) before a push.
- To: they name `python3 -I scripts/verify.py`, still committed first and run in the background; `--branch` is
  described as the optional full local run, with CI as the full gate.
- Reason: the rule must name the command that now satisfies the gate.
- Impact: agents push sooner; failures in tests the task gate skips surface in CI instead of locally. Steps with no
  related-test strategy do not run at all before a push, so a node with only such tests (today `protocol`) runs
  nothing locally.

Unchanged: the record format (version 1), its path, `scripts/branch_pass.py`'s check, the `branch-pass` proof and
the push gate's patterns and exemptions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `affected-node-verification`: the branch-pass record is written and deleted by a complete manual run of either
  gate, not only `--branch`.
- `hooked-rule-delivery`: the pre-push rule and its pointer name the task gate.
- `agent-guidance-layout`: the pre-push rule's stated content names the task gate.

`verification-stop-hook` keeps its requirements: the hook already never writes the push pass, and its acceptance
record of a `--branch`-backed push stays true.

## Impact

- `scripts/verify.py` (`run_gate` record delete/write for the manual task gate) and its tests
  (`scripts/tests/test_verify_run.py`); the module docstring of `scripts/branch_pass.py`.
- `.agents/hooked-rules/repo-pre-push-checks.md`, the generated pointer block in `AGENTS.md`,
  `scripts/tests/test_agent_rules.py` (pointer assertion).
- Docs: `docs/verify.md`, `docs/hook-delivered-rules.md`, `CONTRIBUTING.md`, `scripts/AGENTS.md`.
- No product code, CI workflow, dependency or protocol change.
