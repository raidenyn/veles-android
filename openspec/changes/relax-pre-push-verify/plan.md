# Relax Pre-Push Verify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A complete manual `python3 -I scripts/verify.py` (task gate) run writes the push-pass record, so the push
gate no longer needs the ~15-minute `--branch` run.

**Architecture:** One predicate in `run_gate` extends the branch gate's existing record delete/write path to the
manual task gate; the Stop hook (the only `use_passes=True` caller) stays out. The record, its check, the
`branch-pass` proof and the push gate's patterns are unchanged. The pre-push rule, its generated pointer and four docs
name the task gate.

**Tech Stack:** Python 3.8 stdlib (`scripts/verify.py`, `unittest`), Markdown hooked rules.

**Spec:** `openspec/changes/relax-pre-push-verify/` — `design.md` (D1–D5), `specs/affected-node-verification/spec.md`,
`specs/hooked-rule-delivery/spec.md`, `specs/agent-guidance-layout/spec.md`, `tasks.md`.

## Global Constraints

- Standard library only, Python 3.8 compatible, run under `python3 -I`.
- Unit tests never sleep or read the wall clock (`.agents/rules/repo-no-time-based-tests.md`): use the fixtures'
  injected `FakeClock`, `FakeGit`, `FakeRunner`.
- Record stays version 1 `{"version", "head", "base", "nodes"}`; file `build/verify/branch-pass.json`; proof
  `branch-pass`; `_branch_pass_complete` unchanged.
- The manual task gate still never reads remembered passes; `--branch` still never writes them.
- Commit subjects are Conventional Commits; scope `scripts` for code, `docs` type for doc-only commits.

## Review Focus

- A hook run that *passes* (not only one that blocks) must leave a seeded record untouched — Task 1 tests both.
- A manual task run with `--list` takes no gate path and must not delete the record — Task 1 adds
  `test_task_list_keeps_record` (`main(["--list"], ctx)` leaves the seeded bytes).
- A contended task run (lock held) must keep the record — Task 1.
- A task-gate run whose plan has a branch-only test note is still complete and writes — `fixture_table()` has only
  `check` steps, so Task 1 adds `test_task_branch_only_note_writes_record` on a fixture variant with one extra `test`
  step without `related` (planned as `NOT RUN (branch gate only: ...)`); assert exit 0, the note, and the record.
- The pointer block must be regenerated, or the agent-file check fails at verify — Task 2 step 4.

---

### Task 1: The manual task gate writes and deletes the push pass

**Files:**
- Modify: `scripts/verify.py` (`run_gate` ~1686–1940, `_branch_pass_complete` docstring ~1658, module docstring line 2)
- Test: `scripts/tests/test_verify_run.py` (`BranchPassRunTest`, ~799–911)

**Interfaces:**
- Produces: `_writes_push_pass(gate: str, use_passes: bool) -> bool` in `scripts/verify.py`: `gate == "branch" or
  (gate == "task" and not use_passes)`.

- [ ] **Step 1: Write the failing tests** in `BranchPassRunTest`, replacing `test_task_gate_never_touches_record`:
  - `test_task_complete_writes_record`: `main([], self.context(git=self.git()))` returns 0 and
    `read_json(self.record_path) == {"version": 1, "head": HEAD, "base": BASE, "nodes": ["web-extension"]}`; the same
    via `run_gate("task", None, ctx, self.table, self.cmap)`.
  - `test_task_nothing_changed_writes_empty_nodes`: `git(diff=())` → `nodes == []`.
  - `test_task_incomplete_no_record`: parametrise `test_branch_incomplete_no_record` over `gate in ("branch",
    "task")` (extract the case list into a helper), each case seeded first, asserting no record afterwards.
  - `test_task_contended_keeps_record`: as `test_branch_contended_keeps_record` with `"task"`.
  - `test_task_list_keeps_record`: seeded; `main(["--list"], ctx)` → bytes unchanged.
  - `test_hook_never_touches_record`: seeded; the hook invocation from the old test with a failing lint (decision
    `"block"`) first, then with the default passing runner (no stdout, and the runner's calls include
    `("npm", "run", "lint")`, so the gate really ran) → bytes unchanged both times.
  - `test_task_branch_only_note_writes_record`: see Review Focus.
- [ ] **Step 2: Run and see them fail**
  Run: `python3 -I -m unittest scripts.tests.test_verify_run -v` from the repo root (or
  `cd scripts/tests && python3 -m unittest test_verify_run -v`).
  Expected: the new `test_task_*` writing tests FAIL (no record); `test_hook_never_touches_record` and the
  contended/list tests pass.
- [ ] **Step 3: Implement** `_writes_push_pass` and use it in `run_gate` in place of each `gate == "branch"` that
  guards `branch_pass.delete`, `_branch_state` (before, zero-work after, end after) and `_write_branch_pass` (both
  calls). Update the `run_gate` docstring ("a manual run of either gate deletes ... and writes ... only when complete;
  the hook never touches it"), the `_branch_pass_complete` docstring ("a manual run"), and line 2 of the module
  docstring ("`--branch` for a full local run").
- [ ] **Step 4: Run the verifier suites**
  Run: `python3 -m unittest discover -s scripts/tests -p "test_*.py"` (the whole suite, `test_verify_py38` included)
  Expected: all PASS, including the unchanged `test_manual_never_reads` and `test_branch_never_writes`.
- [ ] **Step 5: Commit**
  `git add scripts/verify.py scripts/tests/test_verify_run.py && git commit -m "chore(scripts): let a complete task-gate run write the push pass"`

### Task 2: The pre-push rule names the task gate

**Files:**
- Modify: `.agents/hooked-rules/repo-pre-push-checks.md` (pointer + body only)
- Modify: `AGENTS.md` (generated block only)
- Test: `scripts/tests/test_agent_rules.py` (`RealRepoRuleTest.test_pre_push_rule_declares_the_proof`, ~3989)

- [ ] **Step 1: Update the test**: replace `assertIn("python3 -I scripts/verify.py --branch", rule.pointer)` with
  `assertIn("python3 -I scripts/verify.py`", rule.pointer)` (note the closing backtick, so `--branch` cannot satisfy
  it) and add `assertIn("python3 -I scripts/verify.py\n", rule.body)` (the fenced command line),
  `assertIn("--branch", rule.body)`, `assertIn("CI", rule.body)`; keep `"background"` and no `"by hand"`.
- [ ] **Step 2: Run and see it fail**
  Run: `cd scripts/tests && python3 -m unittest test_agent_rules.RealRepoRuleTest -v` → FAIL on the pointer.
- [ ] **Step 3: Rewrite the rule.** Pointer: ``"`git push` is denied until `python3 -I scripts/verify.py` passed for
  HEAD; delivered at `openspec instructions finalize` and at a denied push; read this file then if it was not."``
  Body: commit everything first (the pass needs a clean tree, including new untracked change files); run the fenced
  `python3 -I scripts/verify.py` in the background and wait for it (Android checks and related Gradle tests can take
  minutes; a foreground run can exceed the shell tools' limits — say "related tests", naming no build tool); fix every `FAIL` and unexpected `NOT RUN` (its fix
  text is the path), then push. One closing line: `python3 -I scripts/verify.py --branch` is the optional full local
  run and satisfies the gate as well; CI runs every test on the pull request. Frontmatter `commands`, `gates`,
  `requires`, `exempt` unchanged.
- [ ] **Step 4: Regenerate and check**
  Run: `python3 -I scripts/agent_rules.py pointers --write && python3 -I scripts/agent_rules.py pointers --check &&
  python3 docs/checks/check_agent_files.py --root .` and the Step 2 test.
  Expected: all exit 0; the test passes; `AGENTS.md` diff is the one pointer line.
- [ ] **Step 5: Commit**
  `git add .agents/hooked-rules/repo-pre-push-checks.md AGENTS.md scripts/tests/test_agent_rules.py && git commit -m "chore(scripts): require the task gate before a push"`

### Task 3: Docs

**Files:** `docs/verify.md`, `docs/hook-delivered-rules.md`, `CONTRIBUTING.md`, `scripts/AGENTS.md`,
`scripts/branch_pass.py` (docstring only)

- [ ] **Step 1: Edit** per design D5:
  - `docs/verify.md` "The two gates": the task gate is also the pre-push run; the branch gate is the optional full
    local run; CI runs every test. "The branch pass": "A complete manual run of either gate (`python3 -I
    scripts/verify.py` or `--branch`) writes ..."; "Every manual run of either gate deletes ... right after it takes
    the run lock"; "The Stop hook never writes or deletes it." Leave the known-limits workaround as is.
  - `docs/hook-delivered-rules.md` registry bullet: "the record a complete manual `python3 -I scripts/verify.py` (or
    `--branch`) run writes"; rule bullet: "until a complete pass of either gate exists for HEAD".
  - `docs/verify.md` "Contention" paragraph (~248) and known limit 9 (~321): "a background run" instead of "a
    background `--branch` run".
  - `scripts/branch_pass.py` module docstring line 3: "a complete manual run of `scripts/verify.py` (either gate)
    writes the record".
  - `CONTRIBUTING.md` ~line 75: run `python3 -I scripts/verify.py` before finishing a task and before pushing;
    `--branch` (all tests of the affected nodes) is optional locally, CI runs it.
  - `scripts/AGENTS.md` lines 16–17 comments: `# task gate: affected nodes' checks and related tests; before a push`
    and `# optional full local run: all of affected nodes' tests`. Keep the file within 40 lines.
- [ ] **Step 2: Check**
  Run: `python3 docs/checks/check_agent_files.py --root .` and `grep -rn "verify.py --branch" AGENTS.md CONTRIBUTING.md docs/verify.md docs/hook-delivered-rules.md scripts/AGENTS.md .agents/hooked-rules`
  Expected: check exits 0; every remaining hit describes `--branch` as optional, never as required before a push.
- [ ] **Step 3: Commit**
  `git add docs/verify.md docs/hook-delivered-rules.md CONTRIBUTING.md scripts/AGENTS.md && git commit -m "docs(scripts): describe the task gate as the pre-push run"`

### Task 4: Real-environment check (at finalize)

- [ ] **Step 1:** With everything committed, attempt a push before any pass: expect the denial naming
  `python3 -I scripts/verify.py` and "no branch pass" (or "pass is for").
- [ ] **Step 2:** Run `python3 -I scripts/verify.py` in the background and wait; expect exit 0 and
  `build/verify/branch-pass.json` with `head == git rev-parse HEAD`. Note the elapsed time.
- [ ] **Step 3:** Finalize's push succeeds without a denial. Record steps 1–3 and the elapsed time in the PR body.
