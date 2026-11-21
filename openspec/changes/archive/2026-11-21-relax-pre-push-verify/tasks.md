## 1. Verifier: the manual task gate writes the push pass

- [x] 1.1 In `scripts/tests/test_verify_run.py`, replace `test_task_gate_never_touches_record` with failing tests (spec
  `affected-node-verification`, "A complete branch run writes a branch pass"): a complete manual task-gate run
  (`main([], ctx)` and `run_gate("task", None, ...)`) writes `{"version": 1, "head", "base", "nodes"}`; a
  nothing-changed task run writes empty `nodes`; the incomplete cases of `test_branch_incomplete_no_record` (exit 1,
  unexpected NOT RUN, `--deadline`, `--base`, not reached, dirty at start, dirty at end, HEAD changed) leave no
  record for the task gate too, each starting from a seeded record; a contended task run keeps the record; a
  complete task run whose plan has a branch-only test note writes the record; and the hook run
  (`--hook claude --dispatched`, blocking then passing) leaves a seeded record byte-for-byte unchanged.
- [x] 1.2 In `scripts/verify.py` `run_gate`, add `_writes_push_pass(gate, use_passes)` (true for `"branch"`, and for
  `"task"` when `use_passes` is false) and use it for the delete after the lock, both `_branch_state` reads and both
  `_write_branch_pass` calls (design D1, D2); update the `run_gate` and `_branch_pass_complete` docstrings and the
  module docstring's gate line. Make 1.1 pass.
- [x] 1.3 Confirm `test_verify_fingerprint.py`'s `test_manual_never_reads` and `test_branch_never_writes` still pass
  unchanged (the manual task gate still never reads remembered passes), and run the whole `scripts/tests` suite.

## 2. The pre-push rule and its pointer

- [x] 2.1 Update `scripts/tests/test_agent_rules.py`'s pre-push rule test to expect `python3 -I scripts/verify.py`
  in the pointer and body, `--branch` only as the optional full run, and still "background" and no "by hand"
  (spec `hooked-rule-delivery`, "Pre-push rule triggers"; `agent-guidance-layout`, "Pre-push rule has no platform
  detail"); see it fail.
- [x] 2.2 Rewrite `.agents/hooked-rules/repo-pre-push-checks.md`'s `pointer` and body (design D4); keep its
  `commands`, `gates`, `requires` and `exempt` unchanged. Make 2.1 pass.
- [x] 2.3 Regenerate the root `AGENTS.md` block with `python3 -I scripts/agent_rules.py pointers --write`, then run
  `python3 -I scripts/agent_rules.py pointers --check` and `python3 docs/checks/check_agent_files.py --root .`
  (delivery budget included).

## 3. Docs

- [x] 3.1 `docs/verify.md`: in "The two gates" make the task gate the pre-push run and `--branch` the optional full
  local run (CI is the full gate); in "The branch pass" say a complete manual run of either gate writes the record,
  either gate's manual run deletes it at lock, and the hook never touches it; the "Contention" paragraph and known
  limit 9 say "a background run". Update `scripts/branch_pass.py`'s module docstring the same way.
- [x] 3.2 `docs/hook-delivered-rules.md`: the registry and rule bullets name the record a complete manual
  `python3 -I scripts/verify.py` (or `--branch`) run writes.
- [x] 3.3 `CONTRIBUTING.md` and `scripts/AGENTS.md`: the pre-push command is `python3 -I scripts/verify.py`;
  `--branch` is the optional full local run. Re-run the agent-file check.

## 4. Real-environment check

- [ ] 4.1 In this Claude Code session, with everything committed and no record for HEAD (commit or delete
  `build/verify/branch-pass.json` first): run a push attempt and see it denied,
  run `python3 -I scripts/verify.py` in the background to a complete pass, confirm `build/verify/branch-pass.json`
  names HEAD, and see the push allowed (this is finalize's own push). Record what ran in the PR body.
