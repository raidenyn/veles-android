# Unified Verify Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/verify.py` the single deterministic gate: node docs stop repeating its commands, `git push` needs a branch-pass proof in both hosts, and OpenCode runs the turn-end task gate.

**Architecture:** A new stdlib module `scripts/branch_pass.py` owns the proof record; `verify.py --branch` writes it and the shared hooked-rules engine (`scripts/agent_rules.py`) checks it through a proof registry that any gated rule can name. `verify.py --hook opencode` reuses the Claude hook machinery, and the OpenCode plugin runs it in its own per-session slot. The table drift check becomes one-way.

**Tech Stack:** Python 3.8+ stdlib (`unittest`), Node ESM (`node --test`), OpenCode v2.3.2, Claude Code hooks.

**Spec:** `openspec/changes/unified-verify-followups/design.md` (D1–D6) and `specs/{affected-node-verification,agent-guidance-layout,hooked-rule-delivery,verification-stop-hook}/spec.md`; tasks in `tasks.md`.

## Global Constraints

- Unit tests never sleep or read the wall clock; inject clock, wait, runner, git and timers (`.agents/rules/repo-no-time-based-tests.md`).
- Python modules are stdlib only, Python 3.8+, importable under `python3 -I` (the `sys.path` insert pattern of `scripts/verify.py`).
- Engine hook budget 5 s; proof deadline = event start + 3.5 s.
- `VERIFY_BUDGET_MS = 1800000`; SIGTERM, then SIGKILL after 5000 ms.
- `HOOK_STATE_VERSION = 2`; session key `"<host>:<session_id>"`.
- Branch-pass record: `<worktree>/build/verify/branch-pass.json`, `{"version": 1, "head", "base", "nodes"}`, no time field.
- Every `AGENTS.md` ≤ 40 lines; root `AGENTS.md` stays at 40.
- Commit subjects are Conventional Commits; this change is tooling: `chore(scripts)` for code, `docs(agents)`, `docs(scripts)` or `docs(openspec)` for docs, as each task names.
- Once Task 4 lands, a push of this branch needs a branch pass: commit, run `python3 -I scripts/verify.py --branch` in the background, wait, then push.

## Review Focus

1. A push typed with extra whitespace, quotes or a trailing comment (`git push  origin feat # wip`) must still be gated → Task 4 test `test_push_forms_gated` includes these.
2. A branch pass made on a detached HEAD or in a linked worktree must be found from that worktree and only there → Task 2 test `test_check_from_linked_worktree`.
3. A record file that is truncated or of another version must deny with "branch pass record unreadable: …", never crash the engine → Task 2 test `test_check_unreadable_record`.
4. An OpenCode verifier child that prints partial JSON and exits must produce no message → Task 6 test `verifier: truncated JSON is ignored`.
5. A node `AGENTS.md` line that differs from a `check` entry only by quoting (`./gradlew 'lintDebug'`) is still a duplicate (shlex) → Task 7 test `test_quoted_duplicate`.

---

### Task 1: Spike S1 — OpenCode resume while busy

**Files:**
- Create: `openspec/changes/unified-verify-followups/spike.md`
- Scratch only (not committed): a throwaway plugin in a disposable checkout

- [ ] **Step 1:** In a disposable checkout, add a throwaway OpenCode plugin that, on `session.execution.succeeded`, waits for a counter of later events (no wall clock in committed code; the spike is not committed) and then calls `ctx.session.synthetic({sessionID, id, text: "SPIKE-RESUME", resume: true})`.
- [ ] **Step 2:** Run `opencode` v2.3.2 in that checkout; end a turn, send a new user message before the resume fires, and capture the event stream (`session.*` events) and the transcript.
- [ ] **Step 3:** Record in `spike.md` §S1: the command lines, the OpenCode version, the event sequence, and the verdict — queued, interleaved, or rejected — and the decision "D5 as written" or "fallback: keep the block and send it at the next turn end".
- [ ] **Step 4:** Commit: `git add openspec/changes/unified-verify-followups/spike.md && git commit -m "docs(openspec): record the OpenCode busy-resume spike"`

### Task 2: `scripts/branch_pass.py`

**Files:**
- Create: `scripts/branch_pass.py`
- Create: `scripts/tests/test_branch_pass.py`
- Modify: `scripts/tests/test_verify_py38.py` (add `branch_pass.py` and its test to the 3.8 source check)

**Interfaces:**
- Consumes: nothing from `agent_rules` (no import: `agent_rules` imports this module, so a back-import would load a
  second engine copy inside the hook budget). It resolves the worktree itself with
  `git rev-parse --show-toplevel` through `run_git`, matching `discover_checkout`'s result for a checkout.
- Produces:
  - `RECORD_VERSION = 1`
  - `record_path(cwd: str, run_git=default_run_git, timeout: float = 2.0) -> Optional[str]` — `<toplevel>/build/verify/branch-pass.json`
  - `write(cwd: str, head: str, base: str, nodes: List[str]) -> None` — atomic (temp file + `os.replace`)
  - `delete(cwd: str) -> None` — missing file is fine
  - `default_run_git(args, cwd, timeout) -> (rc, stdout, stderr)` using `subprocess.run`
  - `check(cwd: str, deadline: float, now=time.monotonic, run_git=default_run_git) -> Tuple[bool, str]` — `deadline` is on the `now` clock

- [ ] **Step 1: Write the failing tests** in `test_branch_pass.py` against a fixture git repo (`scripts/tests/gitfixture.py`) and a fake `now`:
  - `test_write_then_check_passes`: write(head=HEAD, base=merge-base) → `check` returns `(True, "")`.
  - `test_check_no_record`: → `(False, "no branch pass for HEAD <short>")`.
  - `test_check_other_head`: record head A, HEAD B → message contains `"pass is for %s, HEAD is %s" % (A[:12], B[:12])`.
  - `test_check_other_base`: message contains `"base"` and both short hashes.
  - `test_check_unreadable_record`: truncated JSON, and `{"version": 2,...}` → `(False, "branch pass record unreadable: ...")`.
  - `test_check_git_failure`: `run_git` returns rc 128 for merge-base → `(False, <stderr text>)`.
  - `test_check_deadline`: clock already past deadline → `(False, "proof timed out")`; git calls get `timeout=max(0, deadline - now)`.
  - `test_check_from_linked_worktree`: record written in worktree W is found from W, not from the main checkout.
- [ ] **Step 2:** Run `python3 -m unittest scripts/tests/test_branch_pass.py -v` → FAIL (module missing).
- [ ] **Step 3:** Implement the module per Interfaces; `check` runs `git rev-parse HEAD` then `git merge-base origin/master HEAD`.
- [ ] **Step 4:** Run the same command → PASS; run `python3 -m unittest scripts/tests/test_verify_py38.py -v` → PASS.
- [ ] **Step 5:** Commit `chore(scripts): add the branch-pass record module`.

### Task 3: `verify.py --branch` writes the record

**Files:**
- Modify: `scripts/verify.py` (`main`, `run_gate` or a wrapper around it for `gate == "branch"`)
- Test: `scripts/tests/test_verify_run.py`

**Interfaces:**
- Consumes: `branch_pass.write`, `branch_pass.delete`
- Produces: `_branch_pass_complete(args, result, before, after) -> bool` where `before`/`after` are `(head, porcelain)` tuples read through `ctx.git`.

- [ ] **Step 1: Write failing tests** (fake git, runner, filesystem):
  - `test_branch_complete_writes_record`: exit 0, clean, same HEAD → record with head, base, selected nodes.
  - `test_branch_nothing_changed_writes_empty_nodes`: → `nodes == []`.
  - `test_branch_incomplete_no_record`: one sub-case each for exit 1, exit 2 (unexpected `NOT RUN`), `--deadline`, `--base`, a `not_reached` step, dirty at start, dirty at end, HEAD changed → no record, and a pre-existing record is gone.
  - `test_branch_contended_keeps_record`: lock held by another run → exit 2 and the pre-existing record unchanged.
  - `test_task_gate_never_touches_record`: default gate and `--hook` leave a record as is.
- [ ] **Step 2:** Run `python3 -m unittest scripts/tests/test_verify_run.py -v` → new tests FAIL.
- [ ] **Step 3:** Implement: after the run lock is acquired in the branch gate, `branch_pass.delete`; read `before` ahead of selection and `after` after the last step; write only when `_branch_pass_complete` holds.
- [ ] **Step 4:** Run → PASS; `./gradlew scriptsTest` → PASS.
- [ ] **Step 5:** Commit `chore(scripts): write a branch pass after a complete branch run`.

### Task 4: Proof-requiring gates and the pre-push rule

**Files:**
- Modify: `scripts/agent_rules.py` (`LIST_KEYS`, `Rule`, `parse_rule`, `_gate`, `decide`, `handle`)
- Modify: `.agents/hooked-rules/repo-pre-push-checks.md`
- Modify: `AGENTS.md` (regenerated pointer block)
- Test: `scripts/tests/test_agent_rules.py`, `docs/checks/test_check_agent_files.py` (or the existing hooked-rule fixtures there)

**Interfaces:**
- Consumes: `branch_pass.check(cwd, deadline, clock, run_git)`
- Produces:
  - `Rule` gains `requires: Tuple[str, ...]` and `exempt: List[Pattern]`
  - `PROOF_PREDICATES: Dict[str, Callable[[str, float, Callable[[], float]], Tuple[bool, str]]]` with
    `"branch-pass": lambda cwd, deadline, now: branch_pass.check(cwd, deadline, now=now)` (real `run_git`)
  - `PROOF_DEADLINE_SECONDS = 3.5`
  - `_segments(command: str) -> List[str]` — split at `;`, `&`, `|`, `\n`
  - `handle(stdin_text, fmt, env, store_factory, now=time.monotonic, proofs=PROOF_PREDICATES)`; `handle` reads
    `start = now()` once on entry and passes `deadline = start + PROOF_DEADLINE_SECONDS`, `now` and `proofs` through
    `decide(rules, state, key, event, roots=None, proofs=None, deadline=None, now=None)` to `_gate`. `hook_main`
    and `main` keep their signatures and use the defaults. Tests pass a fake `now` and a fake `proofs` dict.
  - `branch_pass` is imported at module level (it imports nothing from `agent_rules`), after `agent_rules` inserts its
    own `scripts/` directory on `sys.path`, because `docs/checks/check_agent_files.py` loads `agent_rules.py` by path;
    the agent-file check tests in Step 1 prove the import works through that path.

- [ ] **Step 1: Write failing parser tests:** `test_requires_and_exempt_parse`, `test_unknown_proof_rejected`, `test_requires_without_gates_rejected`, `test_exempt_without_requires_rejected` (each error names the file), plus the same cases through the agent-file check fixtures.
- [ ] **Step 2: Write failing gate tests** with a fake proof registry:
  - `test_proof_gate_denies_repeatedly`: two calls, both deny, text = rule text + `"\n\n"` + proof message; rule recorded delivered.
  - `test_proof_gate_passes_with_proof`: no delivery denial.
  - `test_proof_gate_error_denies`: proof raises → deny with the error text.
  - `test_proof_gate_deadline`: fake proof sees `deadline == start + 3.5`; one that returns after it → deny `"proof timed out"`.
  - `test_other_command_can_require_proof`: fixture rule gating `gh pr create` with `requires`.
  - `test_rules_without_requires_keep_deny_once`.
- [ ] **Step 3: Write the failing real-rule table test** `test_push_forms` loading `.agents/hooked-rules/repo-pre-push-checks.md`:
  - exempt (pass without proof): `git push origin --delete feat`, `git push --delete origin feat`, `git push -d origin feat`, `git push origin -d feat`, `git push origin :feat`, `git push origin +:feat`, `git push origin :a :b`.
  - gated (`test_push_forms_gated`): `git push`, `git push origin feat`, `git push --tags`, `git -C dir push origin x`, `git push origin feat :old`, `git push origin feat && git branch -d tmp`, `git push origin feat; echo -d`, `git push origin feat 2>&1`, `git push  origin feat # wip`, `GIT_TRACE=1 git push origin feat`.
  - the three Pre-push rule triggers scenarios: without delivery denied every time; during finalize with a pass not denied; a commit after the pass → deny naming both short hashes.
- [ ] **Step 4:** Run `python3 -m unittest scripts/tests/test_agent_rules.py -v` → new tests FAIL.
- [ ] **Step 5:** Implement the parser keys and errors, `_segments`, and the `requires` branch of `_gate` (D3). Rewrite `repo-pre-push-checks.md`: keep `commands` and `gates`; add `requires: ["branch-pass"]`; `exempt` as two segment-anchored patterns (an option form `--delete`/`-d` among the push arguments, and a refspec form where every argument after the remote is `\+?:\S+`), double-escaped for the frontmatter grammar; pointer and body text per design D3.
- [ ] **Step 6:** Run `python3 -I scripts/agent_rules.py pointers --write`; confirm `wc -l AGENTS.md` prints 40.
- [ ] **Step 7:** Run `python3 -m unittest scripts/tests/test_agent_rules.py -v`, `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py'` → all PASS.
- [ ] **Step 8:** Commit `chore(scripts): gate git push on a branch pass`.

### Task 5: `verify.py --hook opencode`

**Files:**
- Modify: `scripts/verify.py` (`make_parser`, `launcher`, `_hook_mode`, `HookState`, `HOOK_STATE_VERSION`)
- Test: `scripts/tests/test_verify_hook.py`

**Interfaces:**
- Produces: `launcher(event_bytes, ctx, spawn, host: str)`; the dispatched argv carries `"--hook", host`; `HookState` keys `"%s:%s" % (host, session_id)`.

- [ ] **Step 1: Write failing tests:** `test_hook_accepts_opencode`; `test_launcher_passes_host_to_child` (spawn fake sees `--hook opencode`); `test_state_keys_by_host` (same session id under two hosts → two entries); `test_state_v1_reads_empty`; `test_launcher_sigterm_stops_child` (invoke the installed SIGTERM handler directly with a fake child; `_stop_child` called, return 0).
- [ ] **Step 2:** Run `python3 -m unittest scripts/tests/test_verify_hook.py -v` → FAIL.
- [ ] **Step 3:** Implement; the SIGTERM handler is installed only in the non-dispatched launcher process and calls `_stop_child(child)` for the live child, then exits 0.
- [ ] **Step 4:** Run → PASS; `./gradlew scriptsTest` → PASS.
- [ ] **Step 5:** Commit `chore(scripts): add the OpenCode host to the verify hook`.

### Task 6: OpenCode plugin verifier slot

**Files:**
- Modify: `scripts/opencode-hooked-rules/index.js`
- Test: `scripts/opencode-hooked-rules/index.test.mjs`

**Interfaces:**
- Consumes: Task 5's `--hook opencode` contract (stdin `{session_id, cwd}`; stdout block / `systemMessage` / nothing).
- Produces:
  - `export const VERIFY_BUDGET_MS = 1800000`, `export const VERIFY_KILL_GRACE_MS = 5000`, `export const VERIFY_PATH` (resolved from the plugin directory like `ENGINE_PATH`)
  - `export function runVerifier(event, {spawn, setTimer, clearTimer, signal, budgetMs}) -> Promise<{kind: "block"|"notice"|"pass", text}>`
  - `createHandlers(ctx, {..., verifier = runVerifier})`

- [ ] **Step 1: Write failing tests** with a fake `spawn` and fake timers (no sleep):
  - `verifier: a block resumes with resume:true`
  - `verifier: a systemMessage is sent with resume:false`
  - `verifier: empty, malformed or oversized output sends nothing`, and `verifier: truncated JSON is ignored`
  - `verifier: budget expiry sends SIGTERM, then SIGKILL after 5000 ms, and sends nothing`
  - `verifier: dispose stops a running verifier`
  - `verifier: a turn end while one runs still sends the engine stop and starts no second verifier`
  - `verifier: engine and verifier blocks are two separate resumes`
  - `verifier: child sessions and interrupted or failed executions start nothing`
  - `verifier: runVerifier sends {session_id, cwd} on stdin to python3 -I <VERIFY_PATH> --hook opencode`
  - if S1 chose the fallback: `verifier: a rejected resume is sent once at the next turn end`
- [ ] **Step 2:** Run `node --test scripts/opencode-hooked-rules/index.test.mjs` → new tests FAIL.
- [ ] **Step 3:** Implement `runVerifier` (spawn normally and signal the launcher's pid with `child.kill("SIGTERM")` then `child.kill("SIGKILL")`: the launcher's SIGTERM handler stops its own dispatched child, so no group kill is needed; tests assert those two `kill` calls; stdout capped at `MAX_STDOUT_BYTES`), `verifiesInFlight`, and the call from the `session.execution.succeeded` branch of `onEvent` next to `stopTurn`, leaving `stopTurn` unchanged.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `chore(scripts): run the verify hook at OpenCode turn ends`.

### Task 7: One-way duplicate check and table cleanup

**Files:**
- Modify: `docs/checks/verify_table.py`, `docs/checks/test_verify_table.py`
- Modify: `scripts/verify.py` (`_load_step`, `_load_excluded` removed, `_load_nodes`)
- Modify: `scripts/verify-commands.json` (drop every `doc` and `excluded`)
- Modify: fixtures in `scripts/tests/test_verify_table.py`, `test_verify.py`, `test_verify_fingerprint.py`, `test_verify_plan.py`, `test_verify_hook.py`

**Interfaces:**
- Produces: `duplicate_findings(table, docs) -> List[str]`, each ``"<doc>: `<line>` is run by verify.py (<ids>); remove it from the doc"``.

- [ ] **Step 1: Write failing tests** in `docs/checks/test_verify_table.py`: `test_doc_repeats_check` (spotlessCheck detekt lintDebug under android/ → finding with three ids); `test_targeted_line_allowed`; `test_build_and_producer_lines_allowed`; `test_documented_command_missing_from_table_passes`; `test_wrong_working_directory_not_duplicate`; `test_excluded_setup_line_passes`; `test_value_less_gradle_option_ignored` (`./gradlew -q lintDebug` is a duplicate); `test_quoted_duplicate`; `test_doc_key_rejected`; `test_excluded_key_rejected`; and in `scripts/tests/test_verify_table.py` `test_loader_rejects_doc_and_excluded` (exit 2 naming entry and field).
- [ ] **Step 2:** Run both suites → new tests FAIL.
- [ ] **Step 3:** Implement the check (reuse `_read_entries`, `_task_only`), remove the two old drift directions, remove both keys from the loader and the table, and migrate every fixture in one go.
- [ ] **Step 4:** Run `python3 -m unittest discover -s docs/checks -p 'test_*.py'` and `./gradlew scriptsTest` → the new tests PASS; `python3 docs/checks/check_agent_files.py --root .` now lists the real duplicates, and any docs/checks test that runs the check on the repository root fails the same way. Both are expected until Task 8, which shares the commit.
- [ ] **Step 5:** Do not commit yet; Task 8 finishes the same commit.

### Task 8: Guidance cleanup and the lint rule

**Files:**
- Modify: node `AGENTS.md` files and listed rules named by Task 7's findings
- Delete: `.agents/rules/android-lint-before-push.md`
- Modify: `android/AGENTS.md` (`## Rules`), `rust/AGENTS.md`, `protocol/AGENTS.md`, `.agents/rules/scripts-verify-commands.md`

- [ ] **Step 1:** Remove exactly the lines the check reports; remove an emptied Build & Test section with its "Run from" sentence.
- [ ] **Step 2:** Delete the lint rule, its `## Rules` line, and reduce the rust/protocol sentences to "Android CI also runs for these changes."; update `scripts-verify-commands.md` for the one-way check.
- [ ] **Step 3:** Run `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py'`, `./gradlew scriptsTest`, `python3 -I scripts/components.py check` → all PASS.
- [ ] **Step 4:** Commit Tasks 7 and 8 together: `docs(agents): keep node guidance to commands the verifier does not run`.

### Task 9: Docs

**Files:**
- Modify: `docs/verify.md`, `docs/opencode-rules.md`, `docs/hook-delivered-rules.md`

- [ ] **Step 1:** `docs/verify.md` per tasks 6.1 (fields, one-way check, branch pass and completeness, OpenCode host, contention, moved `why` reasons in "CI only", the three new Known limits with workarounds).
- [ ] **Step 2:** `docs/opencode-rules.md` and `docs/hook-delivered-rules.md` per tasks 6.2, including the S1 result.
- [ ] **Step 3:** Run `python3 docs/checks/check_agent_files.py --root .` → PASS.
- [ ] **Step 4:** Commit `docs(scripts): document the branch pass, proof gates and the OpenCode verify hook`.

### Task 10: Acceptance

**Files:**
- Modify: `openspec/changes/unified-verify-followups/spike.md` (§Acceptance)

- [ ] **Step 1:** Claude Code real session: a broken lint in a changed file blocks with a readable summary, the fix passes; an absent tool → `NOT RUN` with its fix; a linked worktree at another revision runs its own verifier; `git push` denied twice without a pass; a ref-deletion push of a throwaway branch passes.
- [ ] **Step 2:** OpenCode v2.3.2 real session: the broken lint resumes with the summary, the fix ends silently; a new message during the run behaves as S1 recorded; a linked worktree runs its own verifier; `git push` denied without a pass.
- [ ] **Step 3:** Record each run, the host, and the measured hook and branch-run times in `spike.md`, with "no product behaviour changed"; commit `docs(openspec): record the verify follow-up acceptance`.
- [ ] **Step 4:** With the tree clean, run `python3 -I scripts/verify.py --branch` in the background and wait → exit 0 and `build/verify/branch-pass.json` names HEAD; then push this branch through the new gate (both hosts already denied it earlier, Steps 1–2).
