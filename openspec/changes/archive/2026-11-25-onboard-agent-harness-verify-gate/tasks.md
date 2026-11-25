## 1. Install from the published catalog

- [x] 1.1 Install `agent-harness-hook-runtime` and `agent-harness-verify-gate` 0.5.0 from the catalog on Claude Code
  and OpenCode, and run both `install` commands against this repository.
- [x] 1.2 Remove `verify.py --hook claude` from `Stop`; set `verify-gate` in `.agents/harness.json`.

## 2. The command

- [x] 2.1 Add `scripts/verify_gate.py` (edit lint; task and turn end exec the task gate) and its tests.

## 3. Wiring checks

- [x] 3.1 Remove the OpenCode plugin's verifier slot and its tests.
- [x] 3.2 Make the agent-file check accept exactly the new Claude Code and OpenCode wiring; update its tests.
- [x] 3.3 Run both tools' `check` in the tooling CI job; add `.agents/harness/` to tooling `ci-paths`.
- [x] 3.4 Update the docs that describe the old wiring.

## 4. Live runs

- [x] 4.1 OpenCode: a failing edit, a tick while a check fails, a blocked turn end, a passing then a no-change turn end.
- [x] 4.2 Claude Code: the same sequence.
- [x] 4.3 Hook-time totals and no-change turn-end timings, before and after; a push of this branch.
