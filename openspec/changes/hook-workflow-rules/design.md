## Context

The pilot change `hook-delivered-rules` added `scripts/agent_rules.py`, wired it into Claude Code
(`.claude/settings.json`: `PreToolUse` Bash, `PostToolUse` Bash|Skill|Read, `UserPromptSubmit`, `PreCompact`) and
OpenCode (`scripts/opencode-hooked-rules/`), and moved one rule, `.agents/hooked-rules/repo-commit-types.md`.
`brainstorm.md` records the owner's choices and the candidate inventory; this design is how to build them.

Current behaviour that matters here (read from the code):

- `decide` adds every matching rule at every moment and re-records it as delivered, even when it is already live
  (observed in this session: the commit-types guide is re-sent after each shell call whose text contains
  `openspec instructions finalize`).
- State files are version 1 (`STATE_VERSION = 1`); `Store.load` rejects any other version.
- Claude Code renders only `tool-before` as a deny; `compacted` has no model channel.
- Five always-on rules are moving: closeout (body 3,523 bytes), review gate (1,824), roster (912), real-device
  (773), pre-push (324). The root `AGENTS.md` is at its 40-line cap.
- `openspec-*` skills are generated; the apply skill's last step ("suggest review or verification") cannot be
  edited. `openspec/config.yaml` can carry lasting guidance.

## Goals / Non-Goals

**Goals:**

- An agent that finishes apply goes on to verify and finalize, and finalize pushes and creates the PR, without
  asking; when it tries to stop instead, it is reminded once with the rule text.
- Move the closeout, review-gate, roster, real-device and pre-push rules to `.agents/hooked-rules/` with
  moments, gates and (closeout only) the stop gate.
- Repeats of the same trigger no longer re-send a rule.
- Every listed trigger fits the 9,000-byte cap, checked in CI.
- The root `AGENTS.md` stays at 40 lines or fewer.

**Non-Goals:**

- Tracker intake, signing keys, no-time-based tests and the path-scoped node rules stay where they are.
- Prompt-pattern moments; `PostToolBatch`; Antigravity hooks.
- Editing any `openspec-*` skill.
- Judging whether the agent actually finalizes: the mechanism reminds, it does not enforce.

## Decisions

### 1. Rule files and triggers

Each moved file keeps its body (the closeout body gains one lead paragraph, below), drops its
`trigger: always_on` frontmatter and gains the hooked-rule frontmatter. Skill lists name both the plain skill and
its `opsx:` form, as the commit-types rule does.

| File | `skills` | `commands` | `gates` | `stops` |
|---|---|---|---|---|
| `repo-superspec-closeout.md` | apply, verify, continue, archive | `openspec instructions (verify\|finalize)` | — | `superspec-finalize-pending` |
| `repo-superspec-review-gate.md` | ff, propose, new, continue, apply, verify, archive | `openspec new change`; `openspec instructions (brainstorm\|proposal\|design\|specs\|tasks\|plan)` | — | — |
| `repo-review-models.md` | ff, propose, new, continue, archive | the same two command patterns | — | — |
| `repo-real-device-testing.md` | ff, propose, continue, apply, verify | `openspec instructions (tasks\|plan\|finalize)` | `gh pr create` | — |
| `repo-pre-push-checks.md` | — | `openspec instructions finalize` | `git … push` (same form as the commit gate) | — |

Skill names are the `openspec-<x>-change` names (`openspec-propose` for propose) plus `opsx:<x>`. Command regexes
use the word-boundary style of the existing rule, for example `\\bopenspec\\s+instructions\\s+(verify|finalize)\\b`.
The push gate is `\\bgit\\b[^;&|\\n]*?\\spush\\b`.

**Closeout lead paragraph** (new, first in the body): "After the last apply task, run verify and then finalize in
the same turn. Do not ask first: the owner durably authorizes finalize to push the feature branch, create or
update the pull request and mark it ready. Stop only when verify fails, finalize cannot create or confirm the pull
request, a rule requires you to ask (a device or browser is unavailable, a blocker, an ambiguity) or the user told
you to stop." This brings the body to about
3,850 bytes, under the 4,096 limit.

**Pointers** (one line each, always on):

- closeout: "After apply, run verify then finalize without asking: finalize pushes and creates or readies the PR
  (owner-authorized); the closeout rule is delivered at apply, verify, finalize and archive and when you try to
  stop before finalize; read this file then if it was not."
- review gate: "Every planning file and the closeout docs go through the review gate (ask for a preset first; stop
  after the plan); delivered at the openspec planning skills and commands; read this file then if it was not."
- roster: "Reviewer models and presets live only in this file; never substitute a model; delivered with the review
  gate; read this file before any review."
- real-device: "Behaviour changes need a real device, emulator or Chrome run before done, stated in the PR or
  report; delivered at planning, apply, verify and before `gh pr create`; read this file then if it was not."
- pre-push: "Before `git push`, run the checks of every node `scripts/components.py changed` selects; delivered
  before the push; read this file then if it was not."

The final wording is the builder's, within one line each and keeping these facts.

### 2. Per-trigger de-duplication

A `delivered` entry gains a `triggers` set. A trigger is:

- for a skill moment, the canonical skill name: the skill directory name (`openspec-apply-change`) for a
  `SKILL.md` read, and for a skill call or a `/<skill>` prompt the name after the fixed table in the engine
  (applied to every event form)
  (`opsx:<x>` → `openspec-<x>-change`, except `opsx:propose` → `openspec-propose` and `opsx:explore` →
  `openspec-explore`), so the three forms of one skill are one trigger. The repository has no `opsx` command
  files today; the `opsx:` entries cover contributors who install them;
- for a command moment, the source text of the matched `commands` pattern.

At a moment, a matching rule is skipped when its entry for this key is `delivered`, in the current generation,
and every trigger this event matched is already in `triggers`. Otherwise it is included under the cap; its entry
becomes `delivered` with the event's triggers added to `triggers` and its existing gate `patterns` kept. A
`pending` entry never suppresses: a moment delivers it and turns it `delivered`. Compaction re-arms (the
generation bump makes every entry not current). This replaces "re-record every time".

Effect on residuals: a stale delivery now also suppresses repeats of the same trigger until the next compaction;
a different trigger still delivers. The spec's stale-credit text and `docs/hook-delivered-rules.md` say so.

### 3. The stop gate

**Grammar.** A new optional frontmatter key `stops`: a flat list of double-quoted predicate names. A name not in
the engine's registry is an error (agent-file check). A rule must still have at least one of `skills`,
`commands`, `gates` or `stops`. The registry has one predicate, `superspec-finalize-pending`.

**Session change set.** On every successful `tool-after` shell event (any context key of the session) whose
command matches `\bopenspec\b`, the engine extracts change names from `--change <name>`, `--change=<name>` and their quoted forms, with names
matching `[a-z0-9][a-z0-9-]{0,63}` (longer names are ignored), and adds them to a session-wide `changes` set in the state file.

**Event.** A new engine event `stop` (Claude Code `Stop`; plain `{"event":"stop", …}` with an optional
`stop_hook_active`). `SubagentStop` is not wired; in OpenCode the plugin sends `stop` only for a root session (no
parent), per the spike.

**Predicate `superspec-finalize-pending`.** For each change in the session's `changes` set, gather its copies:
`openspec/changes/<name>/` in the event's checkout, then in the main working tree (the parent of the common
directory, when the repository is not bare), then in each linked worktree of the same common directory, whose roots
are read from `<common-dir>/worktrees/*/gitdir` (the parent of the path inside, which ends in `.git`). Duplicates
by real path are visited once.
The change is pending when:

- some copy has `apply.md`;
- no copy has `finalize.md`;
- no checkout has `openspec/changes/archive/<YYYY-MM-DD>-<name>/` (exact name after the date);
- in the copy with the highest iteration in `apply.md` (first in checkout order on a tie), `verify.md` is
  missing, records a different iteration, or does not have the FAIL line ticked (`- [x] ❌ FAIL`,
  case-insensitive `x`). A FAIL from an earlier iteration therefore does not hide a re-apply.

The iteration is parsed from the line `**Iteration**: \`<n>\`` in both templates (backticks optional); a missing
or unparsable value counts as 0.

Reads are bounded: the session keeps the 20 most recently named changes (older names drop out), at most 32
worktrees are visited (in `gitdir` file-name order), and a receipt larger than 64 KiB makes that copy unreadable,
which counts as not pending for that change only.

**Decision.** At a `stop` event, for each rule with a `stops` predicate that reports a pending change, the engine
checks a stop record keyed `(session, change, phase)`. The phase is the newest apply iteration plus the verify
state at that iteration: `none` (no `verify.md`, or one recording another iteration) or `pass` (same iteration, PASS or PASS WITH WARNINGS ticked; an undecided `verify.md` stays `none`). The gate can therefore remind at most twice per apply iteration — after apply and after a passing
verify, the two likeliest stalls — however often verify is re-run. With no record for the
phase it returns the new decision **block** with the text: one engine line, then the rule
text. The engine line: "Change `<name>` has an apply receipt and no finalize receipt. Run verify if it has not
run, then finalize now: it pushes and creates the PR without asking. If a rule requires you to ask the user, or
the user told you to stop, say which and end your turn; the next stop is not blocked." It writes the stop record
in the same transaction. A recorded phase or no pending change: pass. `stop_hook_active` does not suppress a block in a new phase:
after an earlier block the agent may continue in the same turn, run verify and stall again, and that is the
post-verify stall the second phase exists for. The per-phase records bound the blocks, so no loop is possible. The block does not mark
the rule delivered. Several pending changes: the first by name is named; the record is per change, so another
could block a later stop.

**Rendering.** Claude Code: `{"decision": "block", "reason": <text>}` on `Stop`. Plain: the usual
`{"decision": "block", "text": …, "entries": [...]}`, where the one entry is the stop record's identity
(`["stop", "<change>"]`) and write id, so the OpenCode plugin can `void` it when the synthetic prompt fails and the
next idle can remind again. The plugin's plain-response parser accepts `block` only for a `stop` event, and at idle it ignores a `context`
response (the plain format's engine-failure notice), so a failing engine does nothing at idle. `block` is valid only for `stop`; `deny` stays tool-before only.

**OpenCode.** The plugin maps `session.idle` for a root session to `stop`, but not an idle that follows a user
abort or a session error (the plugin tracks `session.error` and abort events for that session since its last
prompt). On `block` it sends the text as a synthetic prompt to the same session that resumes it (today's
`ctx.session.synthetic` call uses `resume: false`; the spike finds the resuming form), so the agent continues. The spike (Decision 7) must show this; otherwise the
owner decides.

**Fail open.** Any engine error, lock timeout or unreadable receipt: pass. A non-blocking Claude Code `Stop`
output has no model channel; the read-by-hand notice goes in `systemMessage`, which reaches the user, and to
stderr. Blocking to deliver the notice was rejected: a failing engine must never hold the agent. OpenCode: on
an engine failure the plugin does nothing at idle.

### 4. State version 2

The state file gains `triggers` on entries, a session `changes` list and a session-wide `stops` list of
`{change, iteration, verify_state, write_id}`, at most the 40 newest, removed with the session file. `void` also
removes a stop record whose write id matches. `STATE_VERSION` becomes 2. A version 1 file loads as version 2 with empty new
fields and is written back as version 2. An old engine meeting a version 2 file raises its existing `StoreError`
and fails open with the notice; this one-time window is accepted. Pruning and the index are unchanged.

### 5. Config layer

`openspec/config.yaml` `operations.apply.guidance` gets a second entry, next to the apply-stop entry: "When every task is done, run verify and then finalize
in the same turn without asking: finalize pushes the branch and creates or readies the PR, as
`.agents/hooked-rules/repo-superspec-closeout.md` states." Every `rules` entry and both `operations` entries that
name `.agents/rules/repo-superspec-*.md` are repointed to `.agents/hooked-rules/`.

### 6. Budget check

`docs/checks/check_agent_files.py` gains a budget check over: every skill name in any rule (normalised); each
`openspec instructions <artifact>` for the schema's artifacts; `openspec new change`; the chained call
`openspec instructions verify && openspec instructions finalize`; and each predicate's stop text (engine line with
a 64-character change name plus the rule text). For each, the joined text of every rule it matches, with
separators, must be at most 9,000 bytes. Expected worst case: the apply skill at about 8.2 KB (commit types,
review gate, real-device, closeout with its lead paragraph).

The same check enforces: every `stops` name is in the registry; the Claude Code wiring has a fifth hook, `Stop`,
with the same command and `timeout: 5`; and `.agents/rules/` holds exactly the three remaining `repo-*` rules plus
the node rules.

### 7. Spike first

Before engine work, one spike in each tool, recorded in `spike.md`:

- Claude Code: a throwaway `Stop` hook returning `decision: block` with a canary reason; the model continues and
  quotes the canary; on the next stop `stop_hook_active` is true, and a second block sent then is obeyed (the agent
  continues); a user interrupt fires no `Stop`.
- OpenCode: on `session.idle` for a root session, send a synthetic prompt with a canary; the model continues in
  the same session; a child session's idle, and an idle after a user abort or an error, can be told apart.

If either fails, stop and ask the owner (pointer and moments only for that tool, or hold).

### 8. Root AGENTS.md

`## Rules` becomes two content lines: "Always on: `.agents/rules/repo-no-time-based-tests.md`,
`.agents/rules/repo-tracker-intake.md`, `.agents/rules/repo-signing-keys.md`; node rules are path-scoped and listed
in each node's `AGENTS.md`." The OpenCode paragraph follows without a blank line. Five pointer lines are added by
`pointers --write`. Net 40 lines; the agent-file check enforces the cap.

### 9. Files

- Move: `.agents/rules/repo-{superspec-closeout,superspec-review-gate,review-models,real-device-testing,pre-push-checks}.md`
  → `.agents/hooked-rules/` (`git mv`, then frontmatter). `.claude/rules` stays a symlink to `.agents/rules`.
- Engine: `scripts/agent_rules.py` (grammar `stops`, triggers, `stop` event, block decision, change set, predicate,
  state v2); its tests.
- OpenCode plugin: `scripts/opencode-hooked-rules/index.js` and `index.test.mjs` (`session.idle`).
- Wiring: `.claude/settings.json` (`Stop` hook).
- Check: `docs/checks/check_agent_files.py` and `test_check_agent_files.py` (rule-name list, hook tuple, budget,
  registry).
- Spec deltas: `hooked-rule-delivery` (grammar `stops`, Moments with per-trigger de-duplication, "Cap at a
  moment", the `stop` event and `block` decision, state v2, Stale credit, the five Claude Code hooks scenario, the
  OpenCode `session.idle` mapping, the budget check, the stop-gate fail-open and the end-to-end outcomes);
  `agent-guidance-layout`; `real-device-testing`; `superspec-closeout`; `superspec-review-gate`.
- References: `openspec/config.yaml`; `openspec/schemas/superspec/schema.yaml` (three references);
  `openspec/schemas/superspec/templates/verify.md`; `docs/superspec-workflow.md` (opening paragraph and the "always-on
  rule" wording near the audit checklist); `docs/openspec-workflow.md`; `docs/dev/specs/real-device-testing.mdx`;
  `docs/hook-delivered-rules.md` (stops, the `Stop` hook, de-duplication, stale credit, state v2); `docs/AGENTS.md`
  if its rule list names the moved files; root `AGENTS.md`.

### 10. Verification

Unit tests per decision (no sleeps, injected state). Then a live re-run in both tools, recorded in `spike.md`:

1. A planning skill delivers review gate and roster once; a repeat of the same skill delivers nothing; a
   different planning command delivers again.
2. A disposable change with `apply.md` and no `finalize.md`, recorded through an `openspec status --change <name>`
   call: the first stop is blocked with the engine line; the second stop passes; a passing `verify.md` blocks once
   more; a new `apply.md` iteration blocks once more; a ticked FAIL at the same iteration and an archived copy do
   not block; a FAIL from an earlier iteration does block; a session that never named the change is not blocked.
3. `git push` is denied once with the pre-push rule; after `openspec instructions finalize` it is not.
4. A stale delivery (the after-write failure switch) suppresses the same trigger and not a different one.
5. An end-to-end apply-to-finalize run on a disposable change shows the agent going on to finalize without asking,
   or the stop gate catching it. Whether it reports the config guidance as conflicting with the skill's ending is
   recorded.

## Risks / Trade-offs

- [The stop gate blocks a legitimate pause] → once per phase (at most twice per apply iteration), an engine line that permits the stop,
  per-phase records bounding the blocks, armed only by the session's own `--change` calls.
- [A stale or abandoned change in a linked worktree keeps the predicate true] → only changes the session named
  count, and only once per phase.
- [OpenCode idle continuation does not work] → spike first; owner decides.
- [A command pattern matches text inside a heredoc or quoted string] → existing residual; with per-trigger
  de-duplication a false match costs one delivery per trigger, not one per call.
- [The escape hatch writes no `finalize.md`, so the change stays pending] → at most one reminder per phase; the
  engine line permits the stop. Accepted residual.
- [Mixed engine versions on one state file] → fail open with the notice; one-time window.
- [Each phase can remind once] → at most two reminders per apply iteration; the engine line permits the stop.
- [Moving the closeout off always-on weakens it between moments] → the pointer carries the authorization; the stop
  gate covers the failure moment.
- [The apply moment approaches the cap] → the budget check fails CI before a rule is silently dropped.

## Migration Plan

One PR. Rollback is a revert: the files move back to `.agents/rules/`, and a version 2 state file read by the old
engine fails open until it is pruned or the session ends.

## Open Questions

- The spike's two answers (Decision 7).
