# hooked-rule-delivery Specification

## Purpose
Deliver agent rules only at the moments they apply, and again after a conversation is summarized, through hooks in Claude Code and a plugin in OpenCode, so that rules cost no context until needed and are not forgotten in long sessions. This capability covers the hooked-rule file format, the pointer block, the engine's decisions, delivery state, fail-open behaviour, the two host adapters and the ship gate.

## Requirements

### Requirement: Hooked rule files
The repository SHALL keep each hook-delivered rule as one file in the flat directory `.agents/hooked-rules/`, named
`<scope>-<topic>.md` with the scope vocabulary of `.agents/rules/`.

**Frontmatter.** It MUST use the restricted grammar the agent-file check parses: `key: scalar` lines, and flat
lists of double-quoted strings in which only the `\"` and `\\` escapes are allowed. It MUST use only these keys:

- `pointer` (required): a double-quoted one-line summary that names the moments in words.
- `skills`: skill names, namespaced names included.
- `commands`: Python regexes for workflow commands.
- `gates`: Python regexes for gated commands.

An omitted list is empty, and a rule MUST have at least one entry in `skills`, `commands` or `gates`. Duplicate
keys, an unknown key, a list written as `[]` and an invalid regex MUST be errors.

**Body and identity.** The body MUST be at most 4,096 bytes. A rule's identity is its file name plus the sha256 of
the file.

**Parser.** One parser in `scripts/agent_rules.py` MUST serve both the runtime and the agent-file check.

#### Scenario: Valid rule
- **WHEN** a rule has a `pointer`, a namespaced skill such as `"opsx:apply"`, a command regex and a gate regex
- **THEN** the engine and the agent-file check both parse it to the same values

#### Scenario: Invalid frontmatter
- **WHEN** a rule has a `sticky` key, a duplicate `gates` key, `gates: []`, an unbalanced regex or no trigger list
- **THEN** the agent-file check fails and names the file and the problem

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
is stale. The commit-types pointer MUST keep the Conventional Commits format rule itself always on.

#### Scenario: Stale block
- **WHEN** a hooked rule's `pointer` changes and the block is not regenerated
- **THEN** `pointers --check` and the agent-file check fail

#### Scenario: Pointer without hooks
- **WHEN** the hooks and the OpenCode plugin are disabled and a session starts at the repository root, with no parent `CLAUDE.md` or `CLAUDE.local.md`
- **THEN** the agent's context contains the pointer block

### Requirement: Engine events and decisions
`scripts/agent_rules.py hook` SHALL be a standard-library Python 3.8+ program, run with `-I`. It MUST read one event
in Claude Code's hook JSON (`--format claude`) or in a normalized plain JSON (`--format plain`).

**Events.** It MUST handle `tool-before`, `tool-after`, `prompt`, `compacted` and `void`. It MUST take rules and
state from the checkout that contains the event's `cwd`, and MUST pass silently outside a checkout that has
`.agents/hooked-rules/`.

**Decisions.** It MUST answer with exactly one of these, and none of them is a permission grant:

- **pass**: no output, exit 0.
- **context**: passes, adding the text.
- **deny**: stops the call, with the text as the reason.

It MUST NOT emit Claude Code's `permissionDecision: "allow"`.

**Combining rules.**

- When several rules match, their texts MUST be joined in filename order and capped at 9,000 bytes.
- Only rules that fit whole are included and credited.
- A rule left out of a denial by the cap MUST NOT be recorded as pending, so it still gates the retry.

**Plain response.** A `pass` emits nothing, in both formats. When the engine does emit output in the plain format,
the response MUST carry the decision, the text, and one entry per included rule. Each entry holds the rule identity and the write id of the entry the engine recorded for it.

**Index lock.** The store-level index file, which holds the write-id counter and the pruning order, MUST be updated
under its own exclusive lock.

#### Scenario: Pass leaves permissions alone
- **WHEN** a non-gated command arrives that the contributor's settings would normally ask about
- **THEN** the engine prints nothing and the host still asks

#### Scenario: Outside the repository
- **WHEN** the event's `cwd` is in a directory with no `.agents/hooked-rules/`
- **THEN** the engine passes and writes no state

#### Scenario: Cap at a moment
- **WHEN** three matching rules total more than 9,000 bytes at a moment
- **THEN** only the rules that fit whole are included and marked delivered, and the rest stay undelivered

#### Scenario: Cap at a denial
- **WHEN** a gated call matches three undelivered rules whose texts total more than 9,000 bytes, so one is left out of the denial, and the call is then retried
- **THEN** the omitted rule was not recorded as pending, and the retry is denied again with the omitted rule's text

### Requirement: Moments
The engine SHALL deliver a rule as added context, without blocking, at each moment the rule declares:

- **a skill moment:** a model-invoked skill tool call, a user prompt whose first word is `/<skill>`, or a read whose
  real path ends in `/skills/<name>/SKILL.md`;
- **a command moment:** a shell call whose whole string a `commands` regex matches with `re.search`, and that
  completed with a zero exit. A backgrounded command counts when it launches.

No other prompt text MUST be matched.

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

### Requirement: Gates block once
The engine SHALL match each `gates` regex against the whole shell call string. A call that matches a gate of a
rule not delivered in that context since the last re-arm MUST be denied, with the rule text as the reason. Each rule
included in the denial (see Combining rules) MUST then be recorded as **pending**, together with every gate pattern
the call matched.

**Pending set.**

- A later denial of the same rule MUST add its patterns to the pending set and never replace them.
- While the rule is pending, a call that matches any pattern in the set MUST pass and confirm delivery.
- A call that matches only patterns outside the set MUST be denied again, and its patterns MUST join the set.

**Matcher forms.** The `git commit` gate MUST match `git`, then anything up to a shell operator, then the word
`commit`. That covers environment-variable prefixes and git global options. Commands reached only through
variables, aliases or scripts, and quoted arguments that contain a shell operator character, are out of scope.

#### Scenario: Block once, then retry
- **WHEN** an agent with no delivery runs `git commit -m "feat: x"` and then `git commit -m "docs: x"`
- **THEN** the first call is denied with the commit-types rule, and the second passes

#### Scenario: Accumulated pending patterns
- **WHEN** the sequence is: `git commit` denied, `gh pr create` denied, then `git commit` again
- **THEN** the third call passes

#### Scenario: Matched forms
- **WHEN** the call is any of these forms:
  - `git -C "work tree" commit`;
  - `git -c k=v commit`;
  - `git --no-pager commit`;
  - `git --git-dir="a b/.git" commit`;
  - `git --work-tree . commit`;
  - `GIT_AUTHOR_NAME=x git commit`;
  - `git add -A && git commit -m x`;
  - `bash -c "git commit -m x"`;
  - `gh pr create --fill`;
  - `gh pr edit 5 --title x`.
- **THEN** a table-driven test shows that the gate matches each of them

#### Scenario: Boundary
- **WHEN** the call is `git -C "work;tree" commit`
- **THEN** the gate does not match, and a test records this as an accepted out-of-scope form

### Requirement: Delivery state and re-arm
The engine SHALL keep delivery state in one JSON file per session under
`$(git rev-parse --git-common-dir)/agent-rules/`, plus a store-level index file.

**Keys and transactions.**

- The main context's key MUST be the session id, and a subagent's key MUST be the session id plus the agent id. A
  call without an agent id belongs to the main context.
- Every delivery and pending entry MUST record the rule identity, its key's generation and a write id.
- Write ids MUST come from a monotonic counter in the index file that is never reused, including after pruning.
- Every event MUST run one read–check–update transaction under an exclusive per-session lock: `fcntl.flock` on
  POSIX and `msvcrt.locking` on Windows. Writes MUST go through a temporary file and an atomic rename.

**Re-arm.** A `compacted` event (Claude Code `PreCompact`; OpenCode `session.compaction.started`) MUST increment
the generation of the compacting key only. When the host does not identify a compacting subagent, it MUST increment
every key of the session.

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

### Requirement: Fail open
The engine and both adapters SHALL never block a tool call because of their own failure.

**Failures covered.** An internal error, a lock timeout, a 5-second hook timeout, or a host failure, cancellation or
timeout after the engine's write.

**Behaviour.**

- The call MUST pass.
- Where a channel exists, the agent MUST be told to read the hooked rules listed in `AGENTS.md` by hand. The
  OpenCode plugin MUST send that notice itself when Python is missing or the engine times out.
- In Claude Code, a missing Python MUST leave only the pointer, and this MUST be documented.

**Stale credit.** It is an accepted residual and MUST be documented: until the context's next re-arm, later gated
calls of that rule in that context may pass without the guide.

**OpenCode `void`.** When the OpenCode plugin's own injection fails, it MUST send one `void` per rule entry in that
response, each with the entry's write id. The engine MUST remove an entry only if it still carries exactly that
write id.

**Documentation.**

- `docs/AGENTS.md` MUST gain a short hook-delivered rules section limited to what an agent needs on every
  checkout: how to add a rule (location, naming, the grammar pointer and the `pointers --write` step), the
  supported configuration, and a link to `docs/hook-delivered-rules.md`.
- Its untrusted-checkout guidance MUST warn that the repository's hooks run a script on every shell command.
- `docs/hook-delivered-rules.md` MUST describe:
  - adding a rule, with the grammar;
  - delivery, gates, pending entries and re-arm;
  - the supported configuration;
  - each degraded mode and what the agent sees in it;
  - the stale-credit and parallel-batch residuals;
  - the verified tools and versions.

#### Scenario: Corrupt state
- **WHEN** the session file contains invalid JSON and the agent runs `git commit`
- **THEN** the call passes and the agent receives the read-by-hand notice

#### Scenario: Forced failure after the write
- **WHEN** a test-only environment switch makes `hook` fail after writing
- **THEN** the tool call proceeds on both hosts, and the stale credit is observed for the first and a second later gated call

#### Scenario: Late void
- **WHEN** a sibling call rewrote the entry before the plugin's `void` for an older write id arrives
- **THEN** `void` changes nothing

### Requirement: Host adapters
The repository SHALL wire the engine into Claude Code and OpenCode.

**Claude Code.** `.claude/settings.json` MUST run
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude` with `timeout: 5` from these hooks:

- `PreToolUse` on `Bash`;
- `PostToolUse` on `Bash|Skill|Read`;
- `UserPromptSubmit`;
- `PreCompact`.

**OpenCode.** A V2 plugin in `scripts/opencode-hooked-rules/`, declared in `opencode.json` as a local entry next to
`opencode-rules`, MUST map these hooks and events to engine events, with a 5-second spawn timeout:

| OpenCode hook or event | Engine event |
|---|---|
| `context.tool.hook('execute.before')` | `tool-before` |
| `context.tool.hook('execute.after')` | `tool-after`, appending context to the tool output |
| `context.session.hook('prompt')` | `prompt` |
| `session.compaction.started` | `compacted` |

The plugin MUST NOT count a per-call `context.session.hook('context')` injection as delivery. Antigravity gets no
adapter.

#### Scenario: Claude Code wiring
- **WHEN** `.claude/settings.json` lacks one of the four hooks, or omits the timeout
- **THEN** the agent-file check fails

#### Scenario: OpenCode plugin entry
- **WHEN** `opencode.json` lacks the local hooked-rules plugin entry
- **THEN** the agent-file check fails

### Requirement: End-to-end verification and ship condition
The first implementation task SHALL be a spike in a real session of each of Claude Code and OpenCode. The spike MUST
show all of these outcomes, and the same outcomes MUST be re-run on the finished implementation:

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
4. **Re-arm and isolation.**
   - After a compaction with no moment in between, the next gated command is blocked again.
   - Delivery is not credited from parent to subagent, or from subagent to parent.
   - When a subagent compacts, the parent's delivery survives if the host identifies the subagent. Otherwise the
     whole-session fallback is observed.
5. **Pointer fallback.** With the hooks and the plugin disabled, the pointer block is visible in the main context.
6. **Failure after the write.**
   - A forced failure after the engine's write never blocks the call on either host.
   - The stale credit is observed for the first and a second later gated call.
   - On OpenCode, a forced injection failure sends `void`, and the next gated call is denied again.

The completion report and the PR MUST state each outcome, in which tool and version, for both the spike and the
re-run.

**Ship condition.** If a behaviour cannot be made to work in a tool, implementation MUST stop and the owner MUST
decide whether that tool is pointer-only for the pilot or the change is held.

#### Scenario: Behaviour fails in one tool
- **WHEN** OpenCode cannot stop a call from `execute.before` with text the model reads
- **THEN** implementation stops and the owner is asked before any further task

#### Scenario: Evidence recorded
- **WHEN** the change is finalized
- **THEN** the PR names the Claude Code and OpenCode versions and the result of every outcome in both the spike and the re-run
