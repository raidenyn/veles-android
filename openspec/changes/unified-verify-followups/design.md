## Context

The affected-node verifier change added `scripts/verify.py`, its table `scripts/verify-commands.json`, a drift
check (`docs/checks/verify_table.py`, run by `docs/checks/check_agent_files.py`) and a Claude Code `Stop` hook.
Three review questions stayed open and are settled in `brainstorm.md`:

- **Duplication.** Every table entry names a `doc` (a node `AGENTS.md` or a rule it lists), and the drift check
  requires every fenced command in those docs to be an entry or an `excluded` line. The duplication is therefore
  mandatory, and it is why `.agents/rules/android-lint-before-push.md` still exists: six Android entries name it.
- **Push gate.** `.agents/hooked-rules/repo-pre-push-checks.md` has `gates: [git … push]`. The engine
  (`scripts/agent_rules.py`, `_gate`) denies a matching call once, until the rule is delivered in that context,
  then lets every later push through. Nothing checks that `--branch` ran.
- **OpenCode.** The plugin `scripts/opencode-hooked-rules/index.js` already maps the turn end
  (`session.execution.succeeded`, root sessions only) to the engine's `stop` event and resumes the session
  through `ctx.session.synthetic({resume: true})` on a block, inside a 5 s budget. It never runs the verifier;
  `verify.py --hook` accepts only `claude`.

Constraints: OpenCode is the primary host, so every mechanism is shared code with thin adapters. The engine's
hook calls have a 5 s budget. A full `--branch` run took 14 m 17 s; Claude Code's Bash tool caps a foreground
command at 10 minutes, and OpenCode's shell tool has its own default timeout (2 minutes per its tool
description; not measured here). The local OpenCode is v2.3.2. Unit tests
never sleep or read the wall clock (inject the clock).

## Goals / Non-Goals

**Goals:**

- Node guidance documents only commands the verifier does not run, and a check keeps it that way.
- `git push` from an agent of either host is denied until a complete branch pass exists for HEAD.
- The proof gate is generic: another command can require a proof by editing a rule, with no engine change.
- OpenCode runs the task gate at turn end with the same admission, bounds and output contract as Claude Code.

**Non-Goals:**

- Gating any command other than `git push` now (owner: push only, mechanism ready for more).
- A git `pre-push` hook or gating pushes from a plain terminal.
- An override flag for the push gate.
- Changing CI jobs, the component map, the gates' node selection or the remembered-pass rules.
- Changing what a hooked rule's moments deliver.

## Decisions

### D1. Guidance and the table: one direction only

- Remove `doc` from every `nodes.<n>.commands[]` entry and every `producers[]` entry, and remove
  `nodes.<n>.excluded`. The loader in `scripts/verify.py` (`_load_step`, `_load_excluded`, `_load_nodes`
  `_known_fields`) drops both keys; an entry that still carries one is a table error naming the field, so a
  stale table cannot load. `none` stays (a node with no commands still states why).
- `docs/checks/verify_table.py` keeps its structural checks (ids, kinds, requirement and producer references,
  `inputs`/`reuse`, node set equals the component map) and replaces both drift directions with one
  **duplicate** check. It reads, as today (`_read_entries`), every shell fenced block of each node's `AGENTS.md`
  and of every rule that file's `## Rules` lists, with the same working-directory rules
  ("Run from `<dir>`", `cd` lines). A documented line is a duplicate when:
  - its argv and working directory equal those of a `check` or `test` entry of any node; or
  - it is a task-only `./gradlew` line and every task it names is a task of `check`/`test` entries with the same
    working directory (the existing `_task_only` matcher).
  Value-less Gradle options (`-q`, `--offline`) are ignored when comparing task-only lines; a line with any
  other non-task argument (for example `--tests "<class>"`) is targeted and allowed. `device`,
  `platform` and producer commands are allowed. The finding reads
  ``<doc>: `<line>` is run by verify.py (<entry id>); remove it from the doc``.
- The removal list for the node docs is exactly what the new check reports on the current tree; the builder
  removes those lines and leaves the rest. A Build & Test section left with no lines is removed with its
  "Run from" sentence.
- `android-lint-before-push.md` is deleted. Its `## Rules` line in `android/AGENTS.md` goes, and the sentences in
  `rust/AGENTS.md` and `protocol/AGENTS.md` that tell the agent to read it are reduced to the fact that Android CI
  also runs for their changes (the verifier selects `android` for them already).
- `scripts-verify-commands.md` stays (it is about editing the table), with its text updated for the new check.
- Alternative rejected: keep `doc` as optional metadata. It would invite the duplication back and has no reader.

### D2. The branch-pass record

- New module `scripts/branch_pass.py` (stdlib only, Python 3.8+, importable under `-I` from both
  `scripts/verify.py` and `scripts/agent_rules.py`), owning the record's path, format, writer and check, so the
  writer and the gate cannot disagree.
- Path: `<worktree>/build/verify/branch-pass.json`, worktree-local like `passes.json`. Both sides resolve the
  worktree with `agent_rules.discover_checkout` semantics from a working directory.
- Written by `verify.py --branch` only when the run is **complete**: exit code 0; no `--deadline`; no `--base`;
  no step `not_reached`; and `git status --porcelain` empty with the same HEAD before selection and after the
  last step (untracked files count; ignored files do not). Every `--branch` run deletes an existing record right
  after it takes the run lock (`build/verify/lock`), so a later run that fails cannot leave an old pass in place,
  while a contended run that never gets the lock cannot erase a concurrent run's pass. The task gate and the hook never write it.
- Format, version 1: `{"version": 1, "head": "<sha>", "base": "<sha>", "nodes": ["<node>", ...]}`. No time
  field: freshness is the HEAD and base equality, so nothing depends on the clock.
- `check(cwd)` returns `(ok, message)`: it reads the record, runs `git rev-parse HEAD` and
  `git merge-base origin/master HEAD` in `cwd` (each with a 2 s timeout), and is ok only when both equal the
  record. Messages name what differs: no record, a different HEAD (`pass is for <short>, HEAD is <short>`), a
  different base, or an unreadable record. A merge-base failure (no `origin/master`) is not ok, with the git
  error.
- Alternative rejected: storing the record in the engine's state store. The verifier would need the engine's
  session keying, and a pass is a fact about the tree, not about a session.

### D3. Proof-requiring gates in the engine

- Hooked-rule frontmatter gains two list keys, both optional: `requires` is valid only with a non-empty `gates`,
  and `exempt` only with a non-empty `requires`:
  - `requires`: names from a fixed registry `PROOF_PREDICATES` (today only `branch-pass`, bound to
    `branch_pass.check`), parsed and rejected like unknown `stops` names;
  - `exempt`: regexes; a gated command segment that also matches one is not checked for proofs.
- **Segments.** For a rule with `requires`, the engine splits the call string into segments at the shell
  operators `;`, `&`, `|` and newlines (the same boundary the gate regexes already stop at), matches `gates` and
  `exempt` per segment, and requires the proofs when any segment matches a gate and does not match an `exempt`
  pattern. So `git push origin feat && git branch -d tmp` is not exempt (the `-d` is in another segment), and a
  deletion push chained after a normal push still needs the proof. Commands reached through variables, aliases,
  scripts or quoted operators stay out of scope, as for the existing gates.
- `_gate` semantics for a rule with `requires`: when such a segment exists, every predicate runs with the
  event's `cwd`. All ok: the rule passes and is not denied for
  delivery either (the proof is stronger than delivery). Any not ok: deny with the rule text followed by each
  failing predicate's message, and mark the rule delivered in the context (so its moments do not re-deliver).
  The deny repeats on every matching call until the proof holds: there is no "deny once" for these rules.
- Rules without `requires` keep today's deny-once delivery semantics unchanged.
- A predicate that raises or times out is a deny with the error text: a proof that cannot be shown is absent.
  The existing whole-engine fail-open contract (a crashed or timed-out engine lets the call through) is
  unchanged and is an accepted limit, stated in `docs/hook-delivered-rules.md`.
- **Budget.** The engine computes a predicate deadline when it starts handling the event: 3.5 s after the start,
  leaving a 1.5 s margin under the hosts' 5 s limit. `branch_pass.check` takes the deadline and gives each git call
  the remaining time; a predicate that reaches the deadline is a deny ("proof timed out"). If the host's own
  timeout still fires first (for example a slow checkout discovery before the predicates start), the host fails
  open and the push passes: an accepted limit, documented with the other fail-open cases.
- **Version skew.** Claude Code runs `$CLAUDE_PROJECT_DIR/scripts/agent_rules.py` and the OpenCode plugin its own
  checkout's engine, while the rules are read from the event's checkout. The engine imports `branch_pass` from
  its own `scripts/` directory; the record is versioned, so an engine reads only version 1. A session whose engine
  predates this change and meets a rule with `requires` gets a rule parse error and fails open (the Fail open
  requirement); a new engine with an old rule keeps deny-once. Both are accepted, documented limits of moving a
  session between checkouts at different revisions.
- `repo-pre-push-checks.md` becomes:
  - `requires: [branch-pass]`;
  - `exempt`: ref-deletion push segments only: a segment with `--delete` or `-d` as a push option, or a segment
    whose every refspec after the remote is `:<ref>` or `+:<ref>`. The regexes are anchored to the segment, never
    use an unbounded `.*` across an operator, and are fixed by a table-driven test (see Risks);
  - pointer: ``"`git push` is denied until `python3 -I scripts/verify.py --branch` passed for HEAD; delivered
    at `openspec instructions finalize` and at a denied push; read this file then if it was not."``;
  - body: commit everything first (the pass needs a clean tree, including new untracked change files); run
    `python3 -I scripts/verify.py --branch` in the background and wait for it, because it takes about 15
    minutes and a foreground run exceeds the shell tools' limits; fix every `FAIL` and unexpected `NOT RUN`
    (its fix text is the path); then push. The "where it cannot run, run the node checks by hand" paragraph
    goes.
- The root `AGENTS.md` pointer block is regenerated (`python3 -I scripts/agent_rules.py pointers --write`); the
  root stays at 40 lines. No other "run verify" line is added: both hosts run the task gate at turn end and
  the push gate's denial carries the command.
- Alternative rejected: a separate `proofs:` rule type. One rule with gates plus `requires` reuses the parser,
  the pointer block and both adapters' `tool-before` paths, which already exist for both hosts.

### D4. `verify.py --hook opencode`

- `--hook` accepts `claude` and `opencode`. The event on stdin is the same JSON object (`session_id`, `cwd`); the
  output contract is the same (`{"decision":"block","reason":…}`, `{"systemMessage":…}` or nothing; exit 0;
  every internal failure fails open).
- The launcher passes the host to the dispatched child. Hook state keys sessions as `<host>:<session_id>`, so an
  OpenCode and a Claude session id never collide. `HOOK_STATE_VERSION` goes to 2; the existing reader already
  treats a document of another version as empty, so old records start fresh (one session may get a fresh
  allowance once, which is harmless). On SIGTERM the launcher stops its dispatched child with `_stop_child` (the
  child runs in its own session, so killing the launcher alone never reaches it) and exits 0; admission (5 blocks and 3600 s per session, the 3-attempt
  per-problem bound, the 1740 s run deadline) is shared code and the same numbers.
- The agent-file check's `Stop` wiring rule for `.claude/settings.json` is unchanged.

### D5. The OpenCode plugin runs the verifier at turn end

- **Two independent slots.** The engine's `stop` call keeps today's behaviour exactly: one call per session in
  `stopsInFlight`, 5 s budget, its own resume on a block. The verifier gets a separate per-session slot,
  `verifiesInFlight`, so a long verifier run never suppresses the engine's stop gate (for example the finalize
  reminder) at a later turn end, and an engine block is never held back by the verifier.
- At a root session's turn end the plugin starts the engine stop as today and, if the session has no verifier run
  in flight, starts `python3 -I <plugin checkout>/scripts/verify.py --hook opencode` with
  `{"session_id": <sessionID>, "cwd": <session cwd>}` on stdin and a `VERIFY_BUDGET_MS` of 1 800 000 ms.
- **Output.** A block is sent as its own `synthetic({resume: true})`; a `systemMessage` as
  `synthetic({resume: false})`; empty, malformed or oversized output is ignored. A turn end that arrives while the
  verifier slot is busy starts no second run; the running one still resumes when it finishes, even if the user
  sent a new message meanwhile (owner decision).
- **Stopping.** On budget expiry or plugin dispose the plugin sends SIGTERM to the launcher, which stops its
  dispatched child (D4) and exits; after a 5 s grace it sends SIGKILL. If the launcher is SIGKILLed before it
  handles SIGTERM, the dispatched child runs on to its own `--deadline` (at most 1740 s) and holds the run lock
  meanwhile; the next run's orphan cleanup reaps it. `_stop_child` kills only the dispatched child, so a step's
  own process group (for example a running Gradle) also lives on until that cleanup, as with Claude Code's
  watchdog today. Both are accepted, documented limits.
- Unit tests use the existing seams (`engine`, `spawn`, injected timers and `sleep`) with a fake verifier child:
  block, notice, silent pass, malformed output, budget expiry (SIGTERM then SIGKILL), dispose, a second turn end
  while the verifier runs (engine stop still sent; no second verifier), and engine and verifier blocks delivered
  as two resumes. Python tests cover the launcher's SIGTERM handling with an injected child.
- **Spike first (S1).** Before the plugin change, a real OpenCode v2.3.2 session records what
  `synthetic({resume: true})` does while the session is busy with a new user request: queued after the current
  turn, rejected, or interleaved. If it is queued or interleaved, D5 stands. If it is rejected, the plugin keeps
  the verifier's block and sends it at the session's next turn end, once, which still delivers the report (owner
  intent); the observed behaviour is recorded in `docs/opencode-rules.md`.

### D6. Docs and specs

- `docs/verify.md`: the table fields without `doc`/`excluded`, the one-way duplicate check, the branch-pass
  record and its completeness rule, the OpenCode host, and the lock contention a background branch run causes
  for a turn-end hook run (reported as contention; checks nothing). The "CI only" section stops pointing at
  `excluded` entries; each removed entry's `why` that records a reason found nowhere else (for example why
  `rust/scripts/assert-clean.sh` and `./gradlew bridgeInstallerSupplyChain` are not gates) moves into that
  section as a sentence.
- `docs/opencode-rules.md`: replace "The verify Stop hook" section with the OpenCode behaviour from D5.
- `docs/hook-delivered-rules.md`: `requires`, `exempt`, the predicate registry and the repeat-deny semantics.
- Delta specs for `affected-node-verification`, `agent-guidance-layout`, `hooked-rule-delivery` and
  `verification-stop-hook`, as listed in the proposal.

## Risks / Trade-offs

- [The 14-minute branch run blocks a push and the agent may run it in the foreground and be killed] → the
  denial text says to run it in the background and wait; the acceptance runs cover both hosts doing so.
- [A crashed or timed-out engine lets a push through] → accepted; the existing fail-open contract protects
  sessions from a broken checkout. Stated in the docs.
- [Deletion-push detection can over-match and let a normal push through] → segment-wise matching anchored to
  the segment; a table-driven test covers `--delete` and `-d` before and after the remote, `:<ref>`, `+:<ref>`,
  several deletion refspecs, a mixed `feat :old` refspec list (not exempt), `--tags`, a plain push, `-C <dir>`, and
  chains with `&&`, `;` and `|` such as `git push origin feat && git branch -d tmp` (not exempt), and redirections
  such as `git push origin feat 2>&1` (gated, not exempt).
- [A push of a ref other than HEAD, or with `-C <dir>`, is checked against the event directory's HEAD] →
  accepted limit, documented.
- [Without the reverse check, stale build or release lines in docs go unchecked] → accepted cost (owner
  direction).
- [A machine where `--branch` reports an unexpected `NOT RUN` cannot push] → by design; the `NOT RUN` fix text
  names the provisioning step.
- [A background branch run holds `build/verify/lock`, so a turn-end hook run reports contention] → harmless and
  documented; the turn-end run's job is the fast signal and the branch run supersedes it.
- [OpenCode's resume while busy is unobserved] → spike S1 before the plugin change, with a defined fallback.
- [A slow checkout discovery lets the host time out and fail open on a push] → the predicate deadline denies
  within the engine where it can; the rest is an accepted, documented limit.
- [Two resumes (engine, then verifier) can arrive for one turn end] → each carries its own text; the engine's
  arrives within 5 s and the verifier's when the run ends, as Claude Code's two `Stop` commands also answer
  separately.

## Migration Plan

One PR, tooling and docs only. Order: spike S1; `branch_pass.py` and the `--branch` writer; engine
`requires`/`exempt` and the pre-push rule; `--hook opencode`; the plugin; the table and drift check with the
node-doc removals and the lint rule deletion; docs and the pointer block. Rollback is reverting the PR; the
record file is git-ignored under `build/`.

## Open Questions

- None blocking. S1 decides between D5 as written and its defined fallback.
