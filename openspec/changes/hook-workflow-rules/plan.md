# Hook Workflow Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** Move five workflow rules to hook delivery, de-duplicate moments per trigger, and add a stop gate that
reminds the agent once per phase to run verify and finalize (which creates the PR) instead of stopping to ask.

**Architecture:** The stdlib engine `scripts/agent_rules.py` gains a `stops` key, a `stop` event, a `block`
decision, per-trigger delivery records, a session change set and per-phase stop records (state version 2). Claude
Code wires a fifth hook (`Stop`); the OpenCode plugin maps a root session's `session.execution.succeeded` to `stop`.
The agent-file
check enforces the wiring, the predicate registry, the layout and a delivery budget.

**Tech Stack:** Python 3.8+ standard library (`python3 -I`), `unittest`; Node ESM plugin with `node --test`;
OpenSpec CLI 2.x.

**Spec:** `openspec/changes/hook-workflow-rules/` — `design.md` (decisions §1–§10), `specs/*/spec.md`,
`tasks.md`.

## Global Constraints

- Engine: standard library only, Python 3.8+, run as `python3 -I`; one parser serves runtime and check.
- Unit tests never sleep or read the wall clock: inject `sleep`, timers and state (`FileLock(sleep=…)`, the
  plugin's `setTimer`/`clearTimer`).
- `CONTEXT_CAP = 9000` UTF-8 bytes, `RULE_SEPARATOR = "\n\n"`; hooked rule body ≤ 4,096 bytes.
- Hook command: `python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude`, `timeout: 5`.
- Root `AGENTS.md` ≤ 40 lines; pointers are one line each.
- The engine never emits `permissionDecision: "allow"`; `block` only for `stop`, `deny` only for `tool-before`.
- Change names: `[a-z0-9][a-z0-9-]{0,63}`; change set keeps 20 newest; stop records keep 40 newest; at most 32
  worktrees; receipts over 65,536 bytes make that change not pending.
- No model name outside `.agents/hooked-rules/repo-review-models.md` in the workflow files; no concrete project
  identifiers anywhere committed.
- Commit subjects are Conventional Commits: `chore(agents)` for engine, plugin, wiring and check commits,
  `docs(agents)` for rule and doc moves, `docs(openspec)` for change records. PR title:
  `chore(agents): deliver more workflow rules through hooks`.
- Push only after Task 10: Task 9 moves rules whose check expectations Task 10 updates.

## Review Focus

1. **Heredoc or quoted text that contains a command pattern** (e.g. a file body mentioning
   `openspec instructions finalize`): a false command moment must cost one delivery per trigger, not one per call.
   Test in Task 3: `test_heredoc_mention_delivers_once`.
2. **A session that edits receipts by hand** (writes `apply.md` while discussing, never running openspec with
   `--change`): no block. Test in Task 6: `test_no_block_without_change_set`.
3. **A `verify.md` copied from another change** with a different `**Change**:` but the same iteration: accepted
   residual (the predicate reads only iteration and decision); test in Task 5 pins current behaviour:
   `test_verify_from_other_change_counts`.
4. **Windows-style or CRLF receipts**: iteration and checkbox parsing must accept CRLF. Test in Task 5:
   `test_crlf_receipts`.
5. **A worktree whose `gitdir` file points to a deleted path**: skipped, never an error. Test in Task 5:
   `test_stale_worktree_gitdir_skipped`.
6. **An archived change whose name ends with the pending change's name** (`2026-01-01-hook-stop-gate` vs
   `stop-gate`): must not clear it. Test in Task 5: `test_archive_suffix_does_not_clear`.

---

### Task 1: Spike the stop gate in both hosts

**Files:**
- Create: `openspec/changes/hook-workflow-rules/spike.md`

**Interfaces:**
- Produces: the OpenCode resuming-prompt call form and the abort/error event names used by Task 8.

- [x] **Step 1:** Clone the repo to a disposable directory with no remote. Add a throwaway `Stop` hook in its
  `.claude/settings.json` running a short script that appends its stdin JSON to `stop-log.jsonl` and keeps a
  counter file: run 1 prints `{"decision":"block","reason":"CANARY-1-<random>: say the canary"}`, run 2 (expected
  with `stop_hook_active: true`) prints the same with `CANARY-2-<random>`, run 3 and later print nothing (pass).
- [x] **Step 2:** Run a Claude Code session there with a trivial prompt. Expected: the model quotes canary 1,
  continues, then quotes canary 2 (a block with `stop_hook_active` true is obeyed). Reset the counter, start a
  run and interrupt it headless (SIGINT; Esc in the TUI stays unverified, `spike.md` §1.3): `stop-log.jsonl`
  gains no line. Record version and transcript excerpts in `spike.md`.
- [x] **Step 3:** In a disposable OpenCode setup, add a throwaway plugin that answers the session's turn-end event
  (`session.idle` is absent in 2.3.2; the run used `session.execution.succeeded`) for a session with no parent by
  calling the session's synthetic-prompt API with a canary and resume enabled. Find the resuming form and whether it
  resolves before the resumed run ends. Log the event types seen on user abort and on a provoked error, and on a child
  (task) session's turn end.
- [x] **Step 4:** Record in `spike.md`: the resuming call and whether it resolves before the resumed run ends, the event names for abort and error, how a child
  session is identified (parent id), versions. If either host fails, stop and ask the owner (task 1.3).
- [x] **Step 5: Commit** `git add openspec/changes/hook-workflow-rules/spike.md && git commit -m "docs(openspec): stop gate spike for hook-workflow-rules"`

### Task 2: Grammar `stops` and the predicate registry

**Files:**
- Modify: `scripts/agent_rules.py` (`LIST_KEYS`, `Rule`, `parse_rule`, new `STOP_PREDICATES`)
- Test: `scripts/tests/test_agent_rules.py` (`ParseRuleTest`)

**Interfaces:**
- Produces: `Rule` gains field `stops: tuple[str, ...]` (after `gates`); `STOP_PREDICATES = ("superspec-finalize-pending",)`;
  `RuleError` for an unknown name with message containing `unknown stop predicate`.

- [ ] **Step 1: Write failing tests** `test_stops_parsed` (rule with `stops:\n  - "superspec-finalize-pending"` →
  `rule.stops == ("superspec-finalize-pending",)`), `test_unknown_stop_predicate` (raises `RuleError`, message has
  `unknown stop predicate`), `test_stops_only_rule_is_valid`, `test_no_trigger` updated to name `stops` in its message.
- [ ] **Step 2:** Run `python3 -I -m unittest scripts.tests.test_agent_rules.ParseRuleTest -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Add `"stops"` to `LIST_KEYS`, the field to `Rule`, validation in `parse_rule`; the trigger
  requirement counts `stops`.
- [ ] **Step 4:** Re-run. Expected: PASS (all of `ParseRuleTest`).
- [ ] **Step 5: Commit** `chore(agents): add stops key to hooked rules`.

### Task 3: State version 2, triggers and per-trigger de-duplication

**Files:**
- Modify: `scripts/agent_rules.py` (`STATE_VERSION`, `SessionState`, `_moment_rules`, `decide`, new `SKILL_ALIASES`, `canonical_skill`)
- Test: `scripts/tests/test_agent_rules.py` (new `StateV2Test`, `MomentRepeatTest`)

**Interfaces:**
- Produces:
  - `STATE_VERSION = 2`; `SessionState` attributes `changes: list[str]` (newest last, ≤ 20) and
    `stops: list[dict]` (`{"change", "iteration", "verify_state", "write_id"}`, ≤ 40); delivered entries carry
    `"triggers": sorted list[str]`.
  - `SessionState.put(key, identity, state, patterns, triggers=())` — merges into an existing entry of the same
    generation: `patterns |= new`, `triggers |= new`; a new generation starts fresh.
  - `canonical_skill(name: str) -> str`: `opsx:propose`→`openspec-propose`, `opsx:explore`→`openspec-explore`,
    `opsx:<x>`→`openspec-<x>-change`, else `name`; the table covers only skills the rules list (`opsx:sync` is
  not mapped correctly and no rule lists it; a test comment pins this).
  - `_moment_rules(rules, event) -> list[tuple[Rule, frozenset[str]]]` (rule and the triggers this event matched:
    `"skill:<canonical>"` or `"command:<pattern source>"`).

- [ ] **Step 1: Write failing tests**
  - `test_v1_file_loads_as_v2` (a version 1 file with one delivered entry → loads; entry `triggers == []`;
    `json.loads(state.dump())["version"] == 2`; the file on disk becomes version 2 at the next event that writes —
    an unmodified load is not rewritten, which the spec's "written back" means).
  - `test_old_engine_rejects_v2` is not needed (old engine is not in the tree); instead `test_unknown_version_fails_open` for `version: 3`.
  - `test_repeat_trigger_skipped` (`openspec instructions finalize` twice → second decision `pass`).
  - `test_skill_forms_are_one_trigger` (Skill `openspec-apply-change`, then Read of its `SKILL.md`, then prompt
    `/opsx:apply` → only the first adds text).
  - `test_different_trigger_redelivers` (apply skill then `openspec instructions finalize` → both add commit-types).
  - `test_pending_rule_delivered_at_moment` (deny `git commit`, then apply skill → context; then `gh pr create` passes).
  - `test_redelivery_keeps_patterns` (pending with commit pattern, moment, then `git commit` passes and entry keeps the pattern).
  - `test_compaction_redelivers_same_trigger`.
  - `test_heredoc_mention_delivers_once` (two calls whose command text is a heredoc containing
    `openspec instructions finalize` → text once).
  - `test_cap_counts_only_sent_rules` (three rules over 9,000 bytes, two already delivered by this trigger → the third is sent).
  - `test_stale_delivery_same_trigger_skipped` (with `VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` the first call exits 1; a second same-trigger call adds nothing; a different trigger adds the rule).
- [ ] **Step 2:** Run `python3 -I -m unittest scripts.tests.test_agent_rules -k Repeat -k StateV2 -v`. Expected: FAIL.
- [ ] **Step 3:** Implement: load v1 → v2 with empty `changes`, `stops`, `triggers`; `decide` for moments skips
  a rule when its entry is `delivered`, current generation, and `matched ⊆ entry.triggers`; otherwise includes it
  under `_capped` and `put(..., "delivered", existing_patterns, matched)`. `compacted` bumps generations only
  (leave `changes` and `stops`).
- [ ] **Step 4:** Replace the existing `test_moment_redelivers_with_a_fresh_write_id` (it asserts the old
  re-send) with `test_repeat_trigger_skipped`; keep `test_moment_after_rearm_delivers_again`. Run the full suite
  `python3 -I -m unittest discover -s scripts/tests -p 'test_agent_rules.py' -v`. Expected: PASS.
  Add `test_void_after_new_trigger_removes_entry` pinning that a `void` of the merged entry removes it whole (an
  extra delivery later, the safe side); Task 11 documents it.
- [ ] **Step 5: Commit** `chore(agents): deliver a hooked rule once per trigger`.

### Task 4: Session change set

**Files:**
- Modify: `scripts/agent_rules.py` (new `CHANGE_ARG_RE`, `record_changes(state, event)`, called from `decide` for `tool-after`)
- Test: `scripts/tests/test_agent_rules.py` (`ChangeSetTest`)

**Interfaces:**
- Produces: `record_changes(state: SessionState, event: Event) -> None`; `CHANGE_ARG_RE = re.compile(r"--change(?:=|\s+)[\"']?([a-z0-9][a-z0-9-]{0,63})(?![a-z0-9-])")`; only when `event.ok is True`, `event.command` matches `\bopenspec\b`. Also `SessionState.applied: bool` (session-wide, default `False`), set at an apply-skill moment (any of the three forms) — the stop gate passes when it is unset.

- [ ] **Step 1: Write failing tests** `test_change_forms` (each of `--change demo`, `--change=demo`,
  `--change "demo"`, `--change 'demo'` → `state.changes == ["demo"]`), `test_long_name_ignored` (65 chars),
  `test_non_openspec_command_ignored` (`grep -- "--change demo" notes.txt`), `test_failed_command_ignored`,
  `test_subagent_call_counts` (event with `agent` set → session set), `test_keeps_20_newest` (21 names → first dropped; re-naming moves to newest),
  `test_apply_skill_arms_the_session` (a Skill event for `openspec-apply-change`, and a `/opsx:apply` prompt, and a read of its `SKILL.md` → `state.applied` true), `test_named_change_without_apply_is_not_armed` (`openspec status --change demo` and `openspec instructions apply --change demo` → `applied` false).
- [ ] **Step 2:** Run `-k ChangeSet`. Expected: FAIL.
- [ ] **Step 3:** Implement; call `record_changes` at the start of the `tool-after` path of `decide`, before the
  "no matching rule → pass" return, so `openspec status --change demo` (which matches no rule) is recorded; the
  write counts as a state change.
- [ ] **Step 4:** Run `-k ChangeSet`. Expected: PASS.
- [ ] **Step 5: Commit** `chore(agents): record openspec changes per session`.

### Task 5: Checkout discovery and the finalize-pending predicate

**Files:**
- Create: `scripts/agent_rules_stop.py` is not created — keep one module; add a section `# Stop predicates` to `scripts/agent_rules.py`
- Test: `scripts/tests/test_agent_rules.py` (`PredicateTest`, fixtures built in a temp dir with fake `.git` layout)

**Interfaces:**
- Produces:
  - `change_roots(toplevel: str, common_dir: str) -> list[str]`: event checkout, then main working tree
    (`dirname(common_dir)` when `common_dir` ends in `/.git` and `core.bare` is not true — read `common_dir/config`
    text for `bare = true`), then linked worktrees from `common_dir/worktrees/*/gitdir` (sorted, ≤ 32, `dirname` of
    the path inside), unique by `os.path.realpath`, missing paths skipped.
  - `PendingChange = namedtuple("PendingChange", ["name", "iteration", "verify_state"])`.
  - `finalize_pending(roots: list[str], changes: list[str]) -> list[PendingChange]` (sorted by name).
  - `ITERATION_RE = re.compile(r"^\*\*Iteration\*\*:\s*`?(\d+)`?", re.M)`;
    `FAIL_TICKED_RE = re.compile(r"^- \[[xX]\] ❌ FAIL", re.M)`; `PASS_TICKED_RE = re.compile(r"^- \[[xX]\] (✅ PASS|⚠\ufe0f? PASS WITH WARNINGS)", re.M)`.

- [ ] **Step 1: Write failing tests** (one per spec scenario; fixture helper `make_change(root, name, apply_iter=None, verify=None, finalize=False)`):
  `test_pending_after_apply`, `test_finalize_anywhere_clears`, `test_archived_copy_clears`
  (`openspec/changes/archive/2026-01-01-demo/`; the directory name must match `^\d{4}-\d{2}-\d{2}-<name>$`),
  `test_archive_suffix_does_not_clear`, `test_fail_same_iteration_clears`,
  `test_earlier_fail_is_pending` (verify iter 1 FAIL, apply iter 2), `test_verify_pass_state`,
  `test_undecided_verify_is_none`, `test_missing_iteration_is_zero`, `test_receipts_in_linked_worktree`,
  `test_main_tree_searched_from_worktree`, `test_bare_repo_has_no_main_tree`, `test_32_worktree_cap`,
  `test_oversized_receipt_not_pending` (65,537 bytes), `test_crlf_receipts`, `test_stale_worktree_gitdir_skipped`,
  `test_verify_from_other_change_counts`, `test_tie_takes_first_in_checkout_order`.
- [ ] **Step 2:** Run `-k Predicate`. Expected: FAIL.
- [ ] **Step 3:** Implement with plain file reads (no subprocess). An oversized receipt makes that change not
  pending, silently. An `OSError`/`UnicodeDecodeError` while reading a receipt raises `StoreError` (a recoverable
  engine failure), so the stop passes with the notice (Task 7).
- [ ] **Step 4:** Run `-k Predicate`. Expected: PASS.
- [ ] **Step 5: Commit** `chore(agents): add the finalize-pending stop predicate`.

### Task 6: The `stop` event, `block` decision, records, rendering and `void`

**Files:**
- Modify: `scripts/agent_rules.py` (`EVENT_KINDS`, `DECISION_KINDS`, `CLAUDE_EVENTS`, `Event` gains `stop_hook_active`, `_parse_claude`, `_parse_plain`, `decide`, `render`, `_void`, new `STOP_LINE`)
- Test: `scripts/tests/test_agent_rules.py` (`StopGateTest`, `RenderTest`)

**Interfaces:**
- Consumes: `finalize_pending`, `change_roots` (Task 5), `state.changes`, `state.stops` (Task 3).
- Produces: `decide(rules, state, key, event, roots=None)` — `roots` is the `change_roots(...)` list, computed in
  `handle()` from `discover_checkout(event.cwd)` only for a `stop` event; tests pass fixture roots directly.
  `Event` gains a last field `stop_hook_active` with default `None` (a non-boolean value in either format is
  ignored, i.e. treated as `None`) (`Event.__new__.__defaults__`), so existing
  positional constructions keep working. `EVENT_KINDS` adds `"stop"`; `DECISION_KINDS` adds `"block"`; `CLAUDE_EVENTS["Stop"] = "stop"`; plain
  `{"event":"stop", "session", "cwd", "stop_hook_active"?}`; stop entry identity `("stop", <change>)` in
  `Decision.entries`; `STOP_LINE` (exact copy below).

`STOP_LINE` (format with the change name):

```text
Change `{name}` has an apply receipt and no finalize receipt. Run verify if it has not run, then finalize now: it pushes and creates the PR without asking. If a rule requires you to ask the user, or the user told you to stop, say which and end your turn; the next stop is not blocked.
```

- [ ] **Step 1: Write failing tests**
  - `test_stall_after_apply_blocks` (text starts with the formatted `STOP_LINE`, then the closeout rule text; one
    stop record `{change: "demo", iteration: 1, verify_state: "none"}`; rule not marked delivered). These gate
    tests set `state.applied` true; `test_not_armed_without_apply_skill` (a session that named the change but did
    not invoke apply → pass, no record) pins the arming rule.
  - `test_second_stop_passes`, `test_pass_phase_blocks_again`, `test_new_iteration_blocks_again`,
    `test_stop_hook_active_same_phase_passes`, `test_stop_hook_active_new_phase_blocks`,
    `test_compaction_keeps_record`, `test_no_block_without_change_set`, `test_first_unrecorded_change_named`
    (`alpha` recorded, `beta` not → names `beta`), `test_subagent_stop_event_not_gated` (an event with `agent` set → pass).
  - `RenderTest`: `test_claude_block` → `{"decision": "block", "reason": text}`; `test_plain_block_entry` →
    entries `[{"rule": ["stop", "demo"], "write_id": n}]`; `test_block_rejected_for_tool_after` → `ValueError`.
  - `test_void_removes_stop_record` and `test_late_void_keeps_newer_record`.
- [ ] **Step 2:** Run `-k StopGate -k Render`. Expected: FAIL.
- [ ] **Step 3:** Implement: at `stop` with no agent id and `state.applied` true, take rules with `"superspec-finalize-pending"` in
  `stops`; compute pending; skip changes with a record for `(change, iteration, verify_state)`; block on the first
  remaining; write the record with `store.next_write_id()`; cap records at 40.
- [ ] **Step 4:** Run the full engine suite. Expected: PASS.
- [ ] **Step 5: Commit** `chore(agents): add the stop gate decision`.

### Task 7: Fail open at a stop and the Claude Code `Stop` hook

**Files:**
- Modify: `scripts/agent_rules.py` (failure output for `stop`), `.claude/settings.json`
- Test: `scripts/tests/test_agent_rules.py` (`StopFailOpenTest`)

**Interfaces:**
- Produces: on any recoverable failure at a Claude `Stop` event, stdout `{"systemMessage": FAILURE_NOTICE}`, the
  notice on stderr, exit 0; plain format unchanged (`context` notice, which the plugin ignores at a stop moment).

- [ ] **Step 1: Write failing tests** `test_stop_corrupt_state_passes_with_system_message`,
  `test_stop_unreadable_receipt_passes` (a receipt path that is a directory), `test_stop_lock_timeout_passes`
  (`FileLock` with injected `sleep` that records calls; never real sleeps).
- [ ] **Step 2:** Run `-k StopFailOpen`. Expected: FAIL.
- [ ] **Step 3:** Implement; add to `.claude/settings.json` a `"Stop"` entry with one `hooks` item: `type: command`,
  the exact engine command, `timeout: 5`; no matcher; no `SubagentStop`.
- [ ] **Step 4:** Run the engine suite. Expected: PASS.
- [ ] **Step 5: Commit** `chore(agents): wire the stop hook and fail open at a stop`.

### Task 8: OpenCode plugin `session.execution.succeeded`

**Files:**
- Modify: `scripts/opencode-hooked-rules/index.js` (`validEntry`/decode accept `block` for `stop`, `createHandlers` turn-end handler)
- Test: `scripts/opencode-hooked-rules/index.test.mjs`

**Interfaces:**
- Consumes: the resuming call form and the event names from Task 1's `spike.md` (`session.execution.succeeded`,
  `interrupted`, `failed`; child sessions carry `parentID`).
- Produces: a turn-end handler that sends `{event: "stop", session, cwd}` for root sessions only (root/child is
  tested with `ctx.session.get({sessionID}).parentID`; a failed or timed-out `session.get` sends nothing, fail
  open), ignores `interrupted`/`failed`, sends a `block` text as a resuming synthetic prompt
  (`ctx.session.synthetic({sessionID, id, text, resume: true})`), and on failure sends `void` with the stop entry's
  write id.

- [ ] **Step 1: Write failing tests** (fake engine and fake host, injected `setTimer`/`clearTimer`):
  `succeeded on root session sends stop and resumes with block text`, `succeeded on child session sends nothing`,
  `session get failure sends nothing`, `second success while one is in flight sends nothing`,
  `interrupted sends nothing`, `failed sends nothing`, `failed synthetic prompt voids the stop entry`,
  `context response at a stop moment is ignored`, `engine timeout at a stop moment does nothing`,
  `missing python at a stop moment does nothing`, `block for a non-stop event is rejected as a malformed response`,
  `second success after the first budget ended sends stop`.
- [ ] **Step 2:** Run `node --test scripts/opencode-hooked-rules/index.test.mjs`. Expected: new tests FAIL.
- [ ] **Step 3:** Implement within the existing 5-second budget helper (`startBudget`). The turn-end handler does not
  await inside the event-stream loop: `onEvent` starts the work detached (one in flight per session, the slot freed
  when that call's budget ends, at most 5 s, not when the resume promise settles), so compaction and later events are
  not delayed. `decode(bytes, kind)` gains the event kind and rejects `block` unless
  `kind === "stop"`.
- [ ] **Step 4:** Re-run. Expected: PASS.
- [ ] **Step 5: Commit** `chore(agents): map OpenCode session turn end to the stop gate`.

### Task 9: Move the rules and the root AGENTS.md

**Files:**
- Move: `.agents/rules/repo-{superspec-closeout,superspec-review-gate,review-models,real-device-testing,pre-push-checks}.md` → `.agents/hooked-rules/`
- Modify: root `AGENTS.md`

**Interfaces:**
- Produces: five hooked rules with the frontmatter below; the pointer block regenerated.

Frontmatter (regexes double their backslashes in the file):

| File | skills | commands | gates | stops |
|---|---|---|---|---|
| closeout | `openspec-apply-change`, `opsx:apply`, `openspec-verify-change`, `opsx:verify`, `openspec-continue-change`, `opsx:continue`, `openspec-archive-change`, `opsx:archive` | `\bopenspec\s+instructions\s+(verify\|finalize)\b` | — | `superspec-finalize-pending` |
| review gate | `openspec-ff-change`, `opsx:ff`, `openspec-propose`, `opsx:propose`, `openspec-new-change`, `opsx:new`, `openspec-continue-change`, `opsx:continue`, `openspec-apply-change`, `opsx:apply`, `openspec-verify-change`, `opsx:verify`, `openspec-archive-change`, `opsx:archive` | `\bopenspec\s+new\s+change\b`; `\bopenspec\s+instructions\s+(brainstorm\|proposal\|design\|specs\|tasks\|plan)\b` | — | — |
| roster | ff, propose, new, continue, archive (both forms) | the same two | — | — |
| real-device | ff, propose, continue, apply, verify (both forms) | `\bopenspec\s+instructions\s+(tasks\|plan\|finalize)\b` | `\bgh\s+pr\s+create\b` | — |
| pre-push | — | `\bopenspec\s+instructions\s+finalize\b` | `\bgit\b[^;&\|\n]*?\spush\b` | — |

Pointers: the five lines of design §1 (shorten only to fit one line; keep each fact).

Closeout lead paragraph (exact, before the "Before closing out, read…" line):

```text
After the last apply task, run verify and then finalize in the same turn. Do not ask first: the owner durably authorizes finalize to push the feature branch, create or update the pull request and mark it ready. Stop only when verify fails, finalize cannot create or confirm the pull request, a rule requires you to ask (a device or browser is unavailable, a blocker, an ambiguity) or the user told you to stop.
```

Root `## Rules` (exact):

```text
Always on: `.agents/rules/repo-no-time-based-tests.md`, `.agents/rules/repo-tracker-intake.md`,
`.agents/rules/repo-signing-keys.md`; node rules are path-scoped and listed in each node's `AGENTS.md`.
OpenCode needs the `opencode-rules` plugin pinned in `opencode.json` (`docs/opencode-rules.md`); do not run
`/init`; a parent `CLAUDE.md` hides `AGENTS.md`. Tool and checkout setup: `CONTRIBUTING.md`.
```

The table shows regexes as they read in Python: `|` is alternation (the `\|` is Markdown table escaping only).
In the frontmatter string each `\` is doubled, as in `repo-commit-types.md`, e.g.
`"\\bopenspec\\s+instructions\\s+(verify|finalize)\\b"`.

- [ ] **Step 0: Write failing tests** in `RealRepoRuleTest` (`scripts/tests/test_agent_rules.py`), loading the real
  `.agents/hooked-rules/`: `test_push_denied_without_delivery` (`git push origin feature` → deny with the pre-push
  text), `test_push_after_finalize_passes`, `test_gh_pr_create_denied_with_real_device` (fresh context),
  `test_finalize_command_delivers_closeout_real_device_pre_push`, `test_ff_skill_delivers_gate_and_roster`,
  `test_verify_skill_delivers_gate_not_roster`, `test_closeout_declares_stop_predicate`. Run them: FAIL.
- [ ] **Step 1:** `git mv` the five files; replace `trigger: always_on` frontmatter with the table's; add the lead paragraph.
- [ ] **Step 2:** `wc -c` each; body ≤ 4,096 bytes (closeout ≈ 3,900).
- [ ] **Step 3:** Rewrite `## Rules`; run `python3 -I scripts/agent_rules.py pointers --write`; `wc -l AGENTS.md` ≤ 40.
- [ ] **Step 4:** `python3 -I scripts/agent_rules.py pointers --check` exits 0, and the Step 0 tests PASS.
- [ ] **Step 5: Commit** `docs(agents): deliver the workflow rules through hooks`.

### Task 10: Agent-file check

**Files:**
- Modify: `docs/checks/check_agent_files.py` (`CLAUDE_HOOKS`, `MOVED_RULES`, `ENGINE_NAMES`, new `check_budget`, `check_always_on_rules`, `check_stop_predicates`), `docs/checks/test_check_agent_files.py` (rule-name list at ~184-190, hook tuple)

**Interfaces:**
- Consumes: `STOP_PREDICATES`, `_moment_rules`, `Event`, `rule_text`, `STOP_LINE`, `CONTEXT_CAP`, `RULE_SEPARATOR` from the engine (add to `ENGINE_NAMES`); the budget builds synthetic `Event`s (a Skill `tool-after` per skill name, a successful Bash `tool-after` per listed command) and sums `_moment_rules` results, so check and runtime match the same way.
- Produces: `ALWAYS_ON_REPO_RULES = ("repo-no-time-based-tests.md", "repo-tracker-intake.md", "repo-signing-keys.md")`;
 the stop text is `STOP_LINE` formatted with a 64-character change name plus the closeout text;
  `BUDGET_COMMANDS` = `openspec instructions <id>` for `brainstorm proposal design specs tasks plan apply verify finalize`,
  `openspec new change x`, and `openspec instructions verify && openspec instructions finalize`.

- [ ] **Step 1: Write failing tests** `test_stop_hook_required`, `test_subagent_stop_rejected`,
  `test_unknown_stop_predicate_rejected`, `test_budget_at_cap_passes` (9,000 bytes), `test_budget_over_cap_fails`
  (9,001; message names trigger and rules), `test_stop_text_counted`, `test_extra_repo_rule_rejected`,
  `test_missing_always_on_listing_rejected`, `test_moved_rule_left_behind` (each of the five), and update the
  existing clean-tree fixtures to the new layout.
- [ ] **Step 2:** Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`. Expected: new tests FAIL.
- [ ] **Step 3:** Implement; `CLAUDE_HOOKS` gains `("Stop", None)`; `MOVED_RULES` gains the five → `.agents/hooked-rules/<same name>`.
- [ ] **Step 4:** Run the unit tests and `python3 docs/checks/check_agent_files.py --root .`. Expected: PASS, no findings.
- [ ] **Step 5: Commit** `chore(agents): check the stop hook, layout and delivery budget`.

### Task 11: References, config and docs

**Files:**
- Modify: `openspec/config.yaml`, `openspec/schemas/superspec/schema.yaml` (lines ~265, ~528, ~753), `openspec/schemas/superspec/templates/verify.md:81`, `docs/superspec-workflow.md` (lines 4-6, ~194), `docs/openspec-workflow.md:53`, `docs/hook-delivered-rules.md`; read `docs/opencode-rules.md`, `docs/agent-guidance.md`

- [ ] **Step 1:** Replace `.agents/rules/repo-superspec-` and `.agents/rules/repo-review-models.md` with their `.agents/hooked-rules/` paths in those files;
  `docs/superspec-workflow.md` opening names the four rules by current path; the audit-checklist line says "directly
  by the review-gate rule".
- [ ] **Step 2:** `openspec/config.yaml` `operations.apply.guidance` becomes a two-item list: the existing apply-stop
  sentence (repointed) and "When every task is done, run verify and then finalize in the same turn without asking: finalize pushes the branch and creates or readies the PR, as .agents/hooked-rules/repo-superspec-closeout.md states."
- [ ] **Step 3:** `docs/hook-delivered-rules.md`: add `stops` to "Adding a rule"; a "Stop gate" section (predicate,
  phases, records, `stop_hook_active`, OpenCode `session.execution.succeeded`, and that only a session that invoked
  the apply skill is armed, so a delegated verify or review session is not reminded); a `void` of a merged entry removes it whole; per-trigger repeats in "Delivery"; state version 2; amended
  stale credit; stop fail-open in "Degraded modes"; `--separate-git-dir` unhandled.
- [ ] **Step 4:** Run `openspec instructions apply --change hook-workflow-rules --json` and confirm the guidance shows
  both entries; run the greps:
  - `git grep -nE '\.agents/rules/repo-(superspec-closeout|superspec-review-gate|review-models|real-device-testing|pre-push-checks)' -- . ':!openspec/changes' ':!openspec/specs'` → no output;
  - `git grep -niE '\b(sol|opus)\b|gpt-6|claude-opus' -- .agents/rules .agents/hooked-rules ':!.agents/hooked-rules/repo-review-models.md' openspec/schemas openspec/config.yaml openspec/specs openspec/changes/hook-workflow-rules/specs docs/superspec-workflow.md docs/AGENTS.md docs/agent-guidance.md docs/openspec-workflow.md docs/opencode-rules.md docs/hook-delivered-rules.md` → no output;
  - `grep -n 'read the sections' .agents/hooked-rules/repo-superspec-{closeout,review-gate}.md .agents/rules/repo-tracker-intake.md` → one hit each, in the opening (closeout: after the lead paragraph), and each cited heading exists in `docs/superspec-workflow.md`.
- [ ] **Step 5: Commit** `docs(agents): repoint workflow references to hooked rules`.

### Task 12: Checks and live verification

- [ ] **Step 1:** `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`; run each
  selected node's checks (at least `./gradlew scriptsTest`, `node --test scripts/opencode-hooked-rules/index.test.mjs`,
  `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py'`,
  `openspec validate --all --json`). Expected: all pass.
- [ ] **Step 2:** Claude Code, outcomes 1–6 of `hooked-rule-delivery` in a disposable clone; record in `spike.md`.
- [ ] **Step 3:** Claude Code, outcome 7, in the disposable clone: a disposable change `demo` (`openspec new change demo`, hand-written
  `apply.md` iteration 1), `openspec status --change demo`, then end turns: blocked once; second stop passes; write a
  passing `verify.md`: blocked once, including in a continued turn; `apply.md` iteration 2: blocked once; FAIL at the
  same iteration, `finalize.md`, an archived copy and a fresh session: pass; a linked-worktree copy: blocked;
  compaction keeps the record; `git push` denied once, not after `openspec instructions finalize`. Then an
  apply-to-finalize run on a tiny disposable change; record whether the agent finalized without asking, and its
  reaction to the config guidance.
- [ ] **Step 4:** OpenCode, outcomes 1–7, including an interactive TUI abort (Esc; record it unverified if the TUI
  cannot produce the event); record.
- [ ] **Step 5:** Any failure: stop and ask the owner before continuing.
- [ ] **Step 6:** Update the verified versions in `docs/hook-delivered-rules.md`; commit
  `docs(agents): record hook-workflow-rules verification`; state tools, versions and results in the completion report and the PR.
