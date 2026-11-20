## ADDED Requirements

### Requirement: Stop gate
The engine SHALL support a third trigger kind, the **stop gate**, for the main context only. A **stop moment** means
a `stop` event: Claude Code's `Stop`, or an OpenCode root session's `session.execution.succeeded`.

**Grammar.** A hooked rule MAY declare `stops`: a flat list of double-quoted predicate names. Each name MUST be in
the engine's predicate registry; an unknown name MUST be an error of the agent-file check. The registry SHALL hold
`superspec-finalize-pending`.

**Session change set and arming.** On every successful `tool-after` shell event of any context of a session whose
command matches `\bopenspec\b`, the engine MUST add to a session-wide change set each name given as
`--change <name>` or `--change=<name>`, quoted or not, that matches `[a-z0-9][a-z0-9-]{0,63}`. The set MUST keep
the 20 most recently named changes.

The stop gate MUST consider the session's change set only when the same session invoked the apply skill — a Skill call
for `openspec-apply-change` or its `/opsx:apply` form — recorded as a session-wide flag. The flag is session-wide: a
session that applied one change is armed for every change it later names. A session that only named the change, such
as a delegated verify or review session, MUST pass: the reminder belongs to the session that will finalize, which is
normally the parent that applied. Reading the apply `SKILL.md` MUST NOT arm a session, so a review that opens it
stays unarmed.

**Predicate `superspec-finalize-pending`.** For each change in the set, the copies are
`openspec/changes/<name>/` in the event's checkout, then in the main working tree (the parent of the git common
directory when the repository is not bare), then in each linked worktree read from
`<common-dir>/worktrees/*/gitdir` (at most 32, in file-name order), each real path once. A change MUST be pending
when all of these hold:

- some copy has `apply.md`;
- no copy has `finalize.md`;
- no checkout has `openspec/changes/archive/<YYYY-MM-DD>-<name>/` (the date prefix and the exact name);
- in the copy whose `apply.md` has the highest iteration (the first in checkout order on a tie), `verify.md` is
  missing, records a different iteration, or does not have its Overall Decision FAIL checkbox ticked (the line
  starting `- [x] ❌ FAIL`, with `x` in either case).

The iteration MUST be read from the `**Iteration**:` line, backticks optional, and a missing or unparsable value
MUST count as 0. A receipt over 64 KiB MUST make that change not pending.

**Phase and record.** A change's phase is its newest apply iteration plus its verify state at that iteration:
`pass` when `verify.md` records the same iteration and has its PASS or PASS WITH WARNINGS box ticked, and `none`
otherwise (no `verify.md`, another iteration, or no decision ticked yet). The engine MUST keep session-wide stop
records `{change, iteration, verify_state, write_id}`, at most the 40 newest, removed with the session file.

**Decision.** At a `stop` event the engine MUST return **block** when a rule's predicate reports a pending change
whose phase has no stop record. Otherwise it MUST pass. `stop_hook_active` MUST NOT suppress a block in a new phase:
the per-phase records bound the blocks to two per apply iteration, so a stop after the agent continued from an
earlier block (Claude Code reports `stop_hook_active` true) is still blocked when the phase changed.

- The text MUST be one engine line naming the change, followed by the rule text. The engine line MUST say to run
  verify if it has not run and then finalize, which pushes and creates the PR without asking. It MUST also say
  that if a rule requires asking the user, or the user said to stop, the agent says which and ends its turn, and
  that the next stop is not blocked.
- The block MUST write the stop record in the same transaction, and MUST NOT mark the rule delivered.
- With several pending changes, the first by name among those whose phase has no record is named; the others keep
  their own records.

**Rendering.** Claude Code: `{"decision": "block", "reason": <text>}`. Plain: `decision` `block`, the text, and one
entry carrying the identity `["stop", <change>]` and the stop record's write id. `block` MUST be valid only for a
`stop` event. `void` MUST also remove a stop record whose write id matches.

Every scenario below that expects a block assumes the session invoked the apply skill; a session that only names the
change passes (see "Delegated verify or review session").

#### Scenario: New apply iteration
- **WHEN** the stop was blocked in both phases of iteration 1 and apply writes iteration 2
- **THEN** the next stop is blocked once

#### Scenario: Stall after apply
- **WHEN** the session ran `openspec status --change demo`, `apply.md` exists, there is no `verify.md` and no `finalize.md`, and the main agent stops
- **THEN** the stop is blocked with the engine line naming `demo` and the closeout rule

#### Scenario: Second stop passes
- **WHEN** the stop was blocked and the agent stops again in the same phase
- **THEN** the stop passes

#### Scenario: Stall after a passing verify
- **WHEN** after that, verify writes a passing `verify.md` at the same iteration and the agent stops
- **THEN** the stop is blocked once more

#### Scenario: Failing verify
- **WHEN** `verify.md` at the newest iteration has the FAIL line ticked
- **THEN** the stop passes

#### Scenario: Earlier failing verify
- **WHEN** `verify.md` records iteration 1 with FAIL ticked and `apply.md` records iteration 2
- **THEN** the stop is blocked

#### Scenario: Finalized or archived
- **WHEN** any copy has `finalize.md`, or an archived copy of the change exists in any checkout
- **THEN** the stop passes

#### Scenario: Receipts in a linked worktree
- **WHEN** the session runs in the main working tree and `apply.md` exists only in a linked worktree
- **THEN** the change is found and the stop is blocked

#### Scenario: Unrelated session
- **WHEN** a session never ran an `openspec` command naming the change, and the change has `apply.md` and no `finalize.md`
- **THEN** that session's stop passes

#### Scenario: Delegated verify or review session
- **WHEN** a session that did not invoke the apply skill ran `openspec status --change demo` and
  `openspec instructions apply --change demo`, and `demo` has `apply.md` and no `finalize.md`
- **THEN** that session's stop passes

#### Scenario: Name in another command
- **WHEN** the only mention of the change is in `grep -- "--change demo" notes.txt`
- **THEN** the change is not added to the set

#### Scenario: Stop hook already active in the same phase
- **WHEN** Claude Code sends `Stop` with `stop_hook_active` true and the phase already has a record
- **THEN** the stop passes and no record is written

#### Scenario: Continued turn reaches a new phase
- **WHEN** the stop after apply was blocked, the agent continued in the same turn, wrote a passing `verify.md` and stops with `stop_hook_active` true
- **THEN** the stop is blocked once more

#### Scenario: Compaction does not reset the record
- **WHEN** the stop was blocked, the context compacts, and the agent stops in the same phase
- **THEN** the stop passes

### Requirement: Pre-push rule triggers
`.agents/hooked-rules/repo-pre-push-checks.md` SHALL be delivered at `openspec instructions finalize` and SHALL
gate `git push` with a regex of the same form as the `git commit` gate (`git`, then anything up to a shell
operator, then the word `push`).

#### Scenario: Push without delivery
- **WHEN** an agent that has not received the rule runs `git push origin feature`
- **THEN** the call is denied once with the rule text, and the re-run proceeds

#### Scenario: Push during finalize
- **WHEN** `openspec instructions finalize` succeeded in the context and the agent then runs `git push`
- **THEN** the push is not denied

### Requirement: Delivery budget check
The agent-file check SHALL compute, for each listed trigger, the joined text of every hooked rule it matches,
with separators, and MUST fail when any exceeds 9,000 bytes. The listed triggers MUST be:

- every skill name any rule lists, canonicalised;
- `openspec instructions <artifact>` for every artifact of the superspec schema;
- `openspec new change`;
- the chained call `openspec instructions verify && openspec instructions finalize`;
- each stop predicate's text: the engine line with a 64-character change name, plus the text of every rule that
  declares that predicate.

The runtime cap MUST remain the backstop for commands outside the list.

#### Scenario: Over budget
- **WHEN** the rules matching the apply skill total 9,001 bytes with separators
- **THEN** the agent-file check fails and names the trigger and the rules

#### Scenario: At budget
- **WHEN** they total exactly 9,000 bytes
- **THEN** the check passes

#### Scenario: Current rules
- **WHEN** the check runs on the tree this change produces
- **THEN** every listed trigger is within the cap

## MODIFIED Requirements

### Requirement: Hooked rule files
The repository SHALL keep each hook-delivered rule as one file in the flat directory `.agents/hooked-rules/`, named
`<scope>-<topic>.md` with the scope vocabulary of `.agents/rules/`.

**Frontmatter.** It MUST use the restricted grammar the agent-file check parses: `key: scalar` lines, and flat
lists of double-quoted strings in which only the `\"` and `\\` escapes are allowed. It MUST use only these keys:

- `pointer` (required): a double-quoted one-line summary that names the moments in words.
- `skills`: skill names, namespaced names included.
- `commands`: Python regexes for workflow commands.
- `gates`: Python regexes for gated commands.
- `stops`: predicate names from the engine's registry.

An omitted list is empty, and a rule MUST have at least one entry in `skills`, `commands`, `gates` or `stops`.
Duplicate keys, an unknown key, a list written as `[]`, an invalid regex and an unknown predicate name MUST be
errors.

**Body and identity.** The body MUST be at most 4,096 bytes. A rule's identity is its file name plus the sha256 of
the file.

**Parser.** One parser in `scripts/agent_rules.py` MUST serve both the runtime and the agent-file check.

#### Scenario: Valid rule
- **WHEN** a rule has a `pointer`, a namespaced skill such as `"opsx:apply"`, a command regex, a gate regex and the stop predicate `"superspec-finalize-pending"`
- **THEN** the engine and the agent-file check both parse it to the same values

#### Scenario: Invalid frontmatter
- **WHEN** a rule has a `sticky` key, a duplicate `gates` key, `gates: []`, an unbalanced regex, an unknown stop predicate or no trigger list
- **THEN** the agent-file check fails and names the file and the problem

#### Scenario: Stop-only rule
- **WHEN** a rule's only trigger list is `stops`
- **THEN** it is valid

#### Scenario: Body too large
- **WHEN** a rule body is 4,097 bytes
- **THEN** the agent-file check fails, and at 4,096 bytes it passes

#### Scenario: Not loaded up front
- **WHEN** a session starts in Claude Code, OpenCode or Antigravity and no moment or gate has fired
- **THEN** no file under `.agents/hooked-rules/` is in the agent's context

### Requirement: Pointer block in the root AGENTS.md
The root `AGENTS.md` SHALL carry a generated block between `<!-- hooked-rules:begin -->` and
`<!-- hooked-rules:end -->`, under a `## Hook-delivered rules` heading. It MUST hold one line per hooked rule (its
`pointer`, then its path) and nothing else. Each `pointer` MUST itself say when to read the rule's file (it names
the moments in words), so the block needs no separate explanatory sentence.

`python3 -I scripts/agent_rules.py pointers --write` MUST write the block, and `pointers --check` MUST fail when it
is stale. Each pointer MUST keep its rule's must-never-miss fact always on:

- commit types: the Conventional Commits format rule;
- closeout: after apply, verify and then finalize run without asking, and finalize pushes and creates or readies
  the PR (owner-authorized);
- review gate: every planning file and the closeout docs go through the review gate, the preset is asked first,
  and the workflow stops after the plan;
- roster: models and presets live only in the roster, and no model is substituted;
- real-device testing: a behaviour change needs a real device, emulator or Chrome run before it is called done,
  stated in the PR or report;
- pre-push checks: before a push, run the checks of every node `scripts/components.py changed` selects.

#### Scenario: Stale block
- **WHEN** a hooked rule's `pointer` changes and the block is not regenerated
- **THEN** `pointers --check` and the agent-file check fail

#### Scenario: Pointer without hooks
- **WHEN** the hooks and the OpenCode plugin are disabled and a session starts at the repository root, with no parent `CLAUDE.md` or `CLAUDE.local.md`
- **THEN** the agent's context contains the pointer block, including the closeout pointer's authorization to finalize without asking

### Requirement: Engine events and decisions
`scripts/agent_rules.py hook` SHALL be a standard-library Python 3.8+ program, run with `-I`. It MUST read one event
in Claude Code's hook JSON (`--format claude`) or in a normalized plain JSON (`--format plain`).

**Events.** It MUST handle `tool-before`, `tool-after`, `prompt`, `compacted`, `stop` and `void`. It MUST take rules
and state from the checkout that contains the event's `cwd`, and MUST pass silently outside a checkout that has
`.agents/hooked-rules/`. A `stop` event MAY carry `stop_hook_active`.

**Decisions.** It MUST answer with exactly one of these, and none of them is a permission grant:

- **pass**: no output, exit 0.
- **context**: passes, adding the text.
- **deny**: stops a `tool-before` call, with the text as the reason.
- **block**: keeps the agent from ending its turn after a `stop` event, with the text as the reason.

It MUST NOT emit Claude Code's `permissionDecision: "allow"`.

**Combining rules.**

- When several rules match, their texts MUST be joined in filename order and capped at 9,000 bytes.
- Only rules that fit whole are included and credited.
- A rule left out of a denial by the cap MUST NOT be recorded as pending, so it still gates the retry.

**Plain response.** A `pass` emits nothing, in both formats. When the engine does emit output in the plain format,
the response MUST carry the decision, the text, and one entry per included rule or stop record. Each entry holds
the identity and the write id of the entry the engine recorded for it.

**Index lock.** The store-level index file, which holds the write-id counter and the pruning order, MUST be updated
under its own exclusive lock.

#### Scenario: Pass leaves permissions alone
- **WHEN** a non-gated command arrives that the contributor's settings would normally ask about
- **THEN** the engine prints nothing and the host still asks

#### Scenario: Outside the repository
- **WHEN** the event's `cwd` is in a directory with no `.agents/hooked-rules/`
- **THEN** the engine passes and writes no state

#### Scenario: Cap at a moment
- **WHEN** three rules a moment will send (not skipped as repeats) total more than 9,000 bytes
- **THEN** only the rules that fit whole are included and marked delivered, and the rest stay undelivered

#### Scenario: Cap at a denial
- **WHEN** a gated call matches three undelivered rules whose texts total more than 9,000 bytes, so one is left out of the denial, and the call is then retried
- **THEN** the omitted rule was not recorded as pending, and the retry is denied again with the omitted rule's text

#### Scenario: Block only at a stop
- **WHEN** a decision of kind `block` is rendered for any event other than `stop`
- **THEN** rendering raises an error and the engine fails open

### Requirement: Moments
The engine SHALL deliver a rule as added context, without blocking, at each moment the rule declares:

- **a skill moment:** a model-invoked skill tool call, a user prompt whose first word is `/<skill>`, or a read whose
  real path ends in `/skills/<name>/SKILL.md`;
- **a command moment:** a shell call whose whole string a `commands` regex matches with `re.search`, and that
  completed with a zero exit. A backgrounded command counts when it launches.

No other prompt text is matched.

**Triggers and repeats.** A skill moment's trigger MUST be the canonical skill name: the skill directory name, and
an `opsx:<x>` name mapped by a fixed table to its `openspec-*` skill (`openspec-<x>-change`, except
`opsx:propose` → `openspec-propose` and `opsx:explore` → `openspec-explore`; the table covers the skills the rules
list), in every event form. A command moment's
trigger MUST be the source of the matched `commands` pattern. A `delivered` entry MUST record its triggers. At a
moment, a matching rule MUST be skipped when its entry for this context is `delivered` in the current generation
and already records every trigger the event matched; otherwise it MUST be included, and its entry becomes
`delivered` with the triggers added and its gate patterns kept. A `pending` entry MUST NOT suppress a moment.

#### Scenario: Skill invoked by the model
- **WHEN** the model invokes `openspec-apply-change`
- **THEN** the commit-types rule is added to context, and a later `git commit` in that context is not blocked

#### Scenario: Skill invoked by the user
- **WHEN** the user's prompt starts with `/opsx:apply`
- **THEN** the rule is added to that prompt's context

#### Scenario: Skill file read
- **WHEN** the agent reads `.agents/skills/openspec-apply-change/SKILL.md`
- **THEN** the rule is added to context

#### Scenario: Successful workflow command
- **WHEN** `openspec instructions finalize --change x` exits 0, and the agent then runs `git commit`
- **THEN** the rule was added to the first call's output, and the commit is not blocked

#### Scenario: Failed workflow command
- **WHEN** `openspec instructions finalize` exits non-zero
- **THEN** no delivery is recorded

#### Scenario: Repeat of the same trigger
- **WHEN** `openspec instructions finalize` succeeds twice in one context and generation
- **THEN** the second call adds no text

#### Scenario: Same skill in another form
- **WHEN** the model invokes `openspec-apply-change` and then reads its `SKILL.md`
- **THEN** the read adds no text

#### Scenario: Different trigger
- **WHEN** the closeout rule was delivered by the apply skill and `openspec instructions finalize` then succeeds
- **THEN** the closeout rule is added again

#### Scenario: Pending rule at a moment
- **WHEN** `git commit` was denied, leaving the commit-types rule pending, and the apply skill is then invoked
- **THEN** the rule is added to context and becomes delivered, and a later `gh pr create` is not blocked

#### Scenario: After compaction
- **WHEN** a rule was delivered by a trigger, the context compacts, and the same trigger fires
- **THEN** the rule is added again

### Requirement: Delivery state and re-arm
The engine SHALL keep delivery state in one JSON file per session under
`$(git rev-parse --git-common-dir)/agent-rules/`, plus a store-level index file.

**Keys and transactions.**

- The main context's key MUST be the session id, and a subagent's key MUST be the session id plus the agent id. A
  call without an agent id belongs to the main context.
- Every delivery and pending entry MUST record the rule identity, its key's generation and a write id; a delivery
  entry MUST also record its triggers.
- The session change set and the stop records are session-wide, not per key.
- Write ids MUST come from a monotonic counter in the index file that is never reused, including after pruning.
- Every event MUST run one read–check–update transaction under an exclusive per-session lock: `fcntl.flock` on
  POSIX and `msvcrt.locking` on Windows. Writes MUST go through a temporary file and an atomic rename.

**Version.** The state file MUST be version 2. A version 1 file MUST load as version 2 with empty triggers, change
set and stop records and an unset apply flag, and is written back as version 2.

**Re-arm.** A `compacted` event (Claude Code `PreCompact`; OpenCode `session.compaction.started`) MUST increment
the generation of the compacting key only. When the host does not identify a compacting subagent, it MUST increment
every key of the session. Re-arm MUST NOT clear the change set, the stop records or the apply flag.

**Pruning.**

- The newest 50 session files are kept, by the index counter and never by the clock.
- A session file MUST be pruned only while its own lock is held, and is skipped when that lock is busy.
- The current session MUST never be pruned.

#### Scenario: Re-arm after compaction
- **WHEN** the rule was delivered, the context compacts, and no moment fires before the next `git commit`
- **THEN** that `git commit` is denied again

#### Scenario: Subagent isolation
- **WHEN** the parent context received the rule and a subagent then runs `git commit`
- **THEN** the subagent's call is denied, and a compaction of the identified subagent leaves the parent's delivery intact

#### Scenario: Differing rule content
- **WHEN** a sibling worktree has a different `repo-commit-types.md` and shares the git common directory
- **THEN** a delivery recorded for one rule identity does not count for the other

#### Scenario: Busy prune victim
- **WHEN** pruning selects a session whose lock is held
- **THEN** that file is skipped and left intact

#### Scenario: Version 1 state
- **WHEN** a session's state file is version 1 with a delivered entry
- **THEN** the engine loads it, keeps the entry with no triggers, and writes it back as version 2

#### Scenario: Subagent names a change
- **WHEN** a subagent runs `openspec instructions apply --change demo` successfully
- **THEN** `demo` is in the session's change set for the main context's stop

### Requirement: Fail open
The engine and both adapters SHALL never block a tool call or a stop because of their own failure.

**Failures covered.** An internal error, a lock timeout, a 5-second hook timeout, an unreadable receipt, or a host
failure, cancellation or timeout after the engine's write.

**Behaviour.**

- The call or the stop MUST pass.
- Where a channel exists, the agent MUST be told to read the hooked rules listed in `AGENTS.md` by hand. The
  OpenCode plugin MUST send that notice itself when Python is missing or the engine times out, except at a stop
  moment, where it MUST do nothing.
- At a Claude Code `Stop`, which has no model channel without blocking, the notice MUST go in `systemMessage` and to
  stderr.
- In Claude Code, a missing Python MUST leave only the pointer, and this MUST be documented.

**Stale credit.** It is an accepted residual and MUST be documented: until the context's next re-arm, later gated
calls of that rule in that context may pass without the guide, and later moments of the same trigger may skip it;
a different trigger still delivers it.

**OpenCode `void`.** When the OpenCode plugin's own injection or synthetic prompt fails, it MUST send one `void` per
entry in that response, each with the entry's write id. The engine MUST remove an entry or stop record only if it
still carries exactly that write id.

**Documentation.**

- `docs/AGENTS.md` MUST gain a short hook-delivered rules section limited to what an agent needs on every
  checkout: how to add a rule (location, naming, the grammar pointer and the `pointers --write` step), the
  supported configuration, the root 40-line cap and that each rule adds one pointer line, and a link to
  `docs/hook-delivered-rules.md`.
- `docs/opencode-rules.md` MUST warn that the repository configuration (`.claude/settings.json`,
  `opencode.json`) can make an agent tool install and run code and that the hooks run a script on every
  shell command; `CONTRIBUTING.md` MUST cross-link the warning, and `docs/AGENTS.md` MUST link that page.
- `docs/hook-delivered-rules.md` MUST describe:
  - adding a rule, with the grammar, including `stops` and the registry;
  - delivery, per-trigger repeats, gates, pending entries, the stop gate and re-arm;
  - the supported configuration;
  - each degraded mode and what the agent sees in it;
  - the stale-credit and parallel-batch residuals;
  - the verified tools and versions.

#### Scenario: Corrupt state
- **WHEN** the session file contains invalid JSON and the agent runs `git commit`
- **THEN** the call passes and the agent receives the read-by-hand notice

#### Scenario: Forced failure after the write
- **WHEN** a test-only environment switch makes `hook` fail after writing
- **THEN** the tool call proceeds on both hosts, and the stale credit is observed for the first and a second later gated call, and for a later moment of the same trigger

#### Scenario: Late void
- **WHEN** a sibling call rewrote the entry before the plugin's `void` for an older write id arrives
- **THEN** `void` changes nothing

#### Scenario: Failed synthetic prompt
- **WHEN** OpenCode's synthetic prompt for a `block` fails and the plugin sends `void` with the stop record's write id
- **THEN** the stop record is removed, and the next root-session stop moment in that phase is blocked again

#### Scenario: Engine failure at a stop
- **WHEN** the engine fails while handling a Claude Code `Stop`
- **THEN** the stop passes and `systemMessage` carries the read-by-hand notice

### Requirement: Host adapters
The repository SHALL wire the engine into Claude Code and OpenCode.

**Claude Code.** `.claude/settings.json` MUST run
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude` with `timeout: 5` from these hooks:

- `PreToolUse` on `Bash`;
- `PostToolUse` on `Bash|Skill|Read`;
- `UserPromptSubmit`;
- `PreCompact`;
- `Stop`.

`SubagentStop` MUST NOT be wired.

**OpenCode.** A V2 plugin in `scripts/opencode-hooked-rules/`, declared in `opencode.json` as a local entry next to
`opencode-rules`, MUST map these hooks and events to engine events, with a 5-second spawn timeout:

| OpenCode hook or event | Engine event |
|---|---|
| `context.tool.hook('execute.before')` | `tool-before` |
| `context.tool.hook('execute.after')` | `tool-after`, appending context to the tool output |
| `context.session.hook('prompt')` | `prompt` |
| `session.compaction.started` | `compacted` |
| `session.execution.succeeded` of a root session (never `failed` or `interrupted`) | `stop`; a `block` is sent as a synthetic prompt with `resume: true` that continues the session |

The plugin MUST accept `block` only for a `stop` event and MUST ignore any other response at a stop moment. It MUST
NOT count a per-call `context.session.hook('context')` injection as delivery. Antigravity gets no adapter.

OpenCode 2.3.2 never emits `session.idle` (spike, `spike.md` §2A): its turn-end event is
`session.execution.succeeded`, which this row uses. If the spike had shown that OpenCode cannot resume a session
from its turn-end event, the row would be dropped only with the owner's decision, and OpenCode would be moments-only
for the stop gate, documented as such.

#### Scenario: Claude Code wiring
- **WHEN** `.claude/settings.json` lacks one of the five hooks, omits the timeout, or wires `SubagentStop`
- **THEN** the agent-file check fails

#### Scenario: OpenCode plugin entry
- **WHEN** `opencode.json` lacks the local hooked-rules plugin entry
- **THEN** the agent-file check fails

#### Scenario: OpenCode child or aborted session
- **WHEN** a child session's execution succeeds, or a root session's execution ends as `interrupted` or `failed`
- **THEN** the plugin sends no `stop` event

### Requirement: End-to-end verification and ship condition
The first implementation task of a change that adds a trigger kind or a host event SHALL be a spike in a real
session of each of Claude Code and OpenCode that shows the new behaviours (for this change, the spike part of
outcome 7). All of these outcomes MUST then be run on the finished implementation:

1. **Loading and permissions.** Each tool's integration loads from its tracked location, and a command that
   normally asks for permission still asks.
2. **Block once.**
   - A gated command is stopped once with the rule text, and its re-run proceeds, including a re-run whose title
     the model changed.
   - In a parallel batch, a sibling matching a different gate pattern is also stopped.
   - The two residual batch cases (a moment with a gate, and two same-kind gates) are run and their outcome
     recorded.
3. **Moments.**
   - Each moment kind is verified separately: a model-invoked skill, a user `/<skill>`, a `SKILL.md` read, and a
     successful workflow command.
   - After each of them, a later gated command in that context is not blocked.
   - A failing workflow command is not a moment.
   - A repeat of the same trigger adds no text, and a different trigger adds the rule again.
4. **Re-arm and isolation.**
   - After a compaction with no moment in between, the next gated command is blocked again.
   - Delivery is not credited from parent to subagent, or from subagent to parent.
   - When a subagent compacts, the parent's delivery survives if the host identifies the subagent. Otherwise the
     whole-session fallback is observed.
5. **Pointer fallback.** With the hooks and the plugin disabled, the pointer block is visible in the main context.
6. **Failure after the write.**
   - A forced failure after the engine's write never blocks the call on either host.
   - The stale credit is observed for the first and a second later gated call, and for a later moment of the same
     trigger.
   - On OpenCode, a forced injection failure sends `void`, and the next gated call is denied again.
7. **Stop gate.**
   - The spike: a throwaway stop hook blocks once with a canary the model quotes and acts on; the next stop reports
     `stop_hook_active`, and a user interrupt fires no `Stop` (Claude Code); a root-session
     `session.execution.succeeded` resumes with a synthetic prompt, and a child session's success, an interrupted
     execution and a failed execution are told apart (OpenCode).
   - The final run: on a disposable change, a stop after apply and a stop after a passing verify are each blocked
     once; a second stop passes; a new apply iteration blocks once more; a FAIL from an earlier iteration blocks;
     a failing verify, a finalized or archived change and an unrelated session pass; receipts found only in a linked
     worktree block; a compaction does not reset a record; `git push` is denied once with the pre-push rule and not
     after `openspec instructions finalize`; in Claude Code, a stall after a passing verify within the same
     continued turn (`stop_hook_active` true) is blocked and the agent continues; in OpenCode, an interactive abort
     in the TUI (Esc) sends no `stop`, or is recorded unverified as an accepted residual if the TUI cannot produce
     the interrupt event.
   - An apply-to-finalize run on a disposable change shows the agent going on to finalize without asking, or the
     stop gate catching it; whether the agent reports the config guidance as conflicting with the apply skill's
     ending is recorded.

The completion report and the PR MUST state each outcome, in which tool and version, for both the spike and the
final run.

**Ship condition.** If a behaviour cannot be made to work in a tool, implementation MUST stop and the owner MUST
decide whether that tool is pointer-only (or, for the stop gate, moments-only) or the change is held.

#### Scenario: Behaviour fails in one tool
- **WHEN** OpenCode cannot resume a session from `session.execution.succeeded` with text the model reads
- **THEN** implementation stops and the owner is asked before any further task

#### Scenario: Evidence recorded
- **WHEN** the change is finalized
- **THEN** the PR names the Claude Code and OpenCode versions and the result of every outcome in both the spike and the final run
