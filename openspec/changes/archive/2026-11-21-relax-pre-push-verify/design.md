## Context

`scripts/verify.py` has two manual gates over the same node selection (changed paths against
`git merge-base origin/master HEAD`): the task gate (default; checks plus related tests) and the branch gate
(`--branch`; checks plus all tests). The Stop hooks run the task gate with remembered passes. `run_gate` in
`scripts/verify.py` deletes `build/verify/branch-pass.json` right after taking the run lock when `gate == "branch"`,
records `(HEAD, porcelain)` before selection and after the last step, and calls `_write_branch_pass`, which writes
the record only when `_branch_pass_complete` holds. `scripts/branch_pass.py` owns the record and its check; the
hooked-rules engine binds the `branch-pass` proof to that check, and `.agents/hooked-rules/repo-pre-push-checks.md`
requires it for `git push`.

The branch gate runs every test serially and takes about 15 minutes locally; CI runs the same commands in parallel
jobs. The owner asked for the pre-push condition to accept the fast task gate instead, run fresh.

## Goals / Non-Goals

**Goals:**
- A complete manual task-gate run satisfies the push gate.
- The proof stays fresh: a failing or incomplete manual run of either gate cannot leave an older pass behind.
- The rule, pointer and docs name the command that satisfies the gate.

**Non-Goals:**
- Parallelising the branch gate.
- Letting the Stop hook write or delete the push pass, or letting a manual run read remembered passes.
- Changing the record format, its path, the check, the proof name or the push gate's patterns.
- Changing CI.

## Decisions

**D1. The manual task gate shares the branch gate's record logic.** In `run_gate`, the record delete after the lock,
the `before`/`after` `_branch_state` reads and the `_write_branch_pass` calls (zero-work return and normal end) run
when the run is *manual*: `gate == "branch"`, or `gate == "task"` and `use_passes` is false. The hook is the only
caller that passes `use_passes=True`, so a predicate `_writes_push_pass(gate, use_passes)` keeps the hook out with no
new flag. This ties "manual" to `use_passes`, which holds while the hook is the only `use_passes=True` caller; a
future manual reuse option would need an explicit manual/hook flag instead. `_branch_pass_complete` is unchanged: the same completeness facts (exit 0, no `--deadline`, no `--base`,
nothing not reached, clean and same HEAD before and after) apply to both gates. Alternative: a new `--push` mode or a
second record — rejected in the brainstorm (more surface, same behaviour).

**D2. Delete-at-lock for the task gate too.** Every manual run that holds the lock drops the old record first, so the
last manual verification of the tree decides. Cost: an in-task `verify.py` on a dirty tree drops a still-valid pass
for the same HEAD; the next clean run restores it. Alternative: delete only when about to write — rejected, it would
let a failed run leave a pass for a HEAD it just found broken.

**D3. Record unchanged.** Version 1, `{version, head, base, nodes}`, no gate field, same file and proof names. The
check proves "a complete manual verification passed for this HEAD and base"; which gate produced it does not change
the push decision. Old records written by `--branch` stay valid.

**D4. Rule text.** `repo-pre-push-checks.md` keeps its triggers, `gates`, `requires` and `exempt`. Its pointer and
body name `python3 -I scripts/verify.py`, still say to commit everything first and to run it in the background and
wait (Android checks and related tests can still take minutes), drop the "about 15 minutes" figure, and add
one line that `--branch` is the optional full local run with the same effect on the gate. The pointer block in
`AGENTS.md` is regenerated with `python3 -I scripts/agent_rules.py pointers --write`.

**D5. Docs.** `docs/verify.md` (two-gates section and "The branch pass"), `docs/hook-delivered-rules.md` (registry and
rule bullets), `CONTRIBUTING.md` and `scripts/AGENTS.md` describe the task gate as the pre-push run and `--branch` as
optional; the Stop-hook limit workaround that says "run `--branch`" stays valid as written. The "Contention"
paragraph and known limit 9 of `docs/verify.md` say "a background run" (either gate) instead of "a background
`--branch` run". The module docstring of `scripts/branch_pass.py` says a complete manual run of either gate writes
the record.

## Risks / Trade-offs

- [A change that breaks an unrelated test of an affected node passes locally; every `test` step without a
  related-test strategy is branch-only and does not run, so a node with only such tests (today `protocol`) passes
  with nothing run] → CI runs every test on the pull request; `--branch` remains available for a full local run.
- [A routine in-task `verify.py` on a dirty tree deletes a valid pass] → One clean re-run restores it; the rule says
  to commit first.
- [Contention: a background pre-push run holds the lock while the Stop hook fires] → Unchanged behaviour: the hook
  reports contention and checks nothing; the run is shorter now, so the window shrinks.

## Migration Plan

No migration: existing records stay valid. Rollback is reverting the change, after which only `--branch` writes the
record again.

## Open Questions

None.
