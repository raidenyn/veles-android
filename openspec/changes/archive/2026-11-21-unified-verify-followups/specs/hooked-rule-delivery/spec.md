## ADDED Requirements

### Requirement: Proof-requiring gates
For a rule with `requires`, the engine SHALL split each `tool-before` call string into segments at `;`, `&`, `|` and
newlines, match the rule's `gates` and `exempt` patterns per segment, and require the proofs when any segment matches
a gate and no `exempt` pattern. It MUST then run every required proof with the event's `cwd`. The proof registry
SHALL hold `branch-pass`, bound to the check of `scripts/branch_pass.py` (capability `affected-node-verification`),
imported from the engine's own `scripts/` directory; adding a proof MUST be a registry entry, and requiring it MUST be
a rule edit. When every proof holds, or no segment requires them, the call MUST pass and the rule MUST NOT be denied
for delivery. When any proof fails, raises or reaches its deadline, the call MUST be denied with the rule text
followed by each failing proof's message, the rule MUST be recorded as delivered in that context, and the denial
MUST repeat on every matching call until the proofs hold. The proofs MUST share a deadline 3.5 seconds after the
engine starts handling the event. A crash or a host timeout of the whole engine keeps the fail-open behaviour of the
Fail open requirement, and so does an engine that predates this requirement meeting a rule with `requires`; these
limits MUST be stated in `docs/hook-delivered-rules.md`.

#### Scenario: Push without a pass
- **WHEN** an agent of either host runs `git push origin feature` and the worktree has no branch-pass record for HEAD
- **THEN** the call is denied with the pre-push rule text and "no branch pass", and an immediate re-run is denied again

#### Scenario: Push with a pass
- **WHEN** the record matches HEAD and the merge base
- **THEN** `git push origin feature` passes without a delivery denial

#### Scenario: Deletion push exempt
- **WHEN** the call is `git push origin --delete feature`, `git push -d origin feature`, `git push origin :feature` or `git push origin +:feature`
- **THEN** a table-driven test shows that each passes without a proof, and that `git push`, `git push --tags`, `git -C dir push origin x`, `git push origin feat :old` and `git push origin feat && git branch -d tmp` are not exempt

#### Scenario: Proof deadline
- **WHEN** a fake proof does not return before the deadline
- **THEN** the call is denied with "proof timed out" before the host's 5-second limit

#### Scenario: Proof error
- **WHEN** the branch-pass check raises, or `git merge-base origin/master HEAD` fails because `origin/master` is missing
- **THEN** the call is denied with the error text

#### Scenario: Another command can require a proof
- **WHEN** a test fixture rule adds `gh pr create` to `gates` with `requires: ["branch-pass"]`
- **THEN** `gh pr create` is gated by the proof with no engine code change

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
- `requires`: proof names from the engine's proof registry; valid only with a non-empty `gates`.
- `exempt`: Python regexes for gated command segments that need no proof; valid only with a non-empty `requires`.

An omitted list is empty, and a rule MUST have at least one entry in `skills`, `commands`, `gates` or `stops`.
Duplicate keys, an unknown key, a list written as `[]`, an invalid regex, an unknown predicate or proof name,
`requires` without `gates` and `exempt` without `requires` MUST be errors. `requires` and `exempt` are not trigger
lists.

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

#### Scenario: Proof keys
- **WHEN** a rule has `gates`, `requires: ["branch-pass"]` and an `exempt` regex
- **THEN** the engine and the agent-file check both parse it; with an unknown proof name, `requires` without `gates`, or `exempt` without `requires`, the agent-file check fails and names the file

### Requirement: Gates block once
The engine SHALL match each `gates` regex against the whole shell call string. This requirement governs rules
without `requires`; a rule with `requires` follows Proof-requiring gates instead. A call that matches a gate of a
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

### Requirement: Host adapters
The repository SHALL wire the engine into Claude Code and OpenCode.

**Claude Code.** `.claude/settings.json` MUST run
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude` with `timeout: 5` from these hooks:

- `PreToolUse` on `Bash`;
- `PostToolUse` on `Bash|Skill|Read`;
- `UserPromptSubmit`;
- `PreCompact`;
- `Stop`.

The `Stop` hook's single entry MUST hold exactly one more command after the engine command, the verify hook of
the `verification-stop-hook` capability:
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true` with `timeout: 1800`. Every other event
MUST hold only the engine command. The two `Stop` commands are independent: the engine's stop gate and the verify
hook each answer for themselves.

`SubagentStop` MUST NOT be wired.

**OpenCode.** A V2 plugin in `scripts/opencode-hooked-rules/`, declared in `opencode.json` as a local entry next to
`opencode-rules`, MUST map these hooks and events to engine events, with a 5-second spawn timeout for the engine:

| OpenCode hook or event | Engine event |
|---|---|
| `context.tool.hook('execute.before')` | `tool-before` |
| `context.tool.hook('execute.after')` | `tool-after`, appending context to the tool output |
| `context.session.hook('prompt')` | `prompt` |
| `session.compaction.started` | `compacted` |
| `session.execution.succeeded` of a root session (never `failed` or `interrupted`) | `stop`; a `block` is sent as a synthetic prompt with `resume: true` that continues the session. The same event also starts the verifier's OpenCode hook in its own slot (capability `verification-stop-hook`), which never delays or suppresses this `stop` |

The plugin MUST accept `block` only for a `stop` event and MUST ignore any other response at a stop moment. It MUST
NOT count a per-call `context.session.hook('context')` injection as delivery. Antigravity gets no adapter.

OpenCode 2.3.2 never emits `session.idle` (spike, `spike.md` §2A): its turn-end event is
`session.execution.succeeded`, which this row uses. If the spike had shown that OpenCode cannot resume a session
from its turn-end event, the row would be dropped only with the owner's decision, and OpenCode would be moments-only
for the stop gate, documented as such.

#### Scenario: Claude Code wiring
- **WHEN** `.claude/settings.json` lacks one of the five hooks, omits the timeout, or wires `SubagentStop`
- **THEN** the agent-file check fails

#### Scenario: Stop holds the verify hook
- **WHEN** an otherwise valid fixture's `Stop` entry holds the engine command with timeout 5 followed by the verify command with timeout 1800
- **THEN** the agent-file check emits no hook-wiring finding

#### Scenario: Stop wiring drifts
- **WHEN** the `Stop` entry lacks the verify command, holds it before the engine command, holds a third command, gives it another timeout, or another event holds a second command
- **THEN** the agent-file check fails and names the event

#### Scenario: OpenCode plugin entry
- **WHEN** `opencode.json` lacks the local hooked-rules plugin entry
- **THEN** the agent-file check fails

#### Scenario: OpenCode child or aborted session
- **WHEN** a child session's execution succeeds, or a root session's execution ends as `interrupted` or `failed`
- **THEN** the plugin sends no `stop` event

### Requirement: Pre-push rule triggers
`.agents/hooked-rules/repo-pre-push-checks.md` SHALL be delivered at `openspec instructions finalize`, SHALL gate
`git push` with a regex of the same form as the `git commit` gate (`git`, then anything up to a shell operator, then
the word `push`), SHALL declare `requires: ["branch-pass"]`, and SHALL exempt ref-deletion pushes (`--delete`, `-d`,
and a `:<ref>` or `+:<ref>` refspec). Its text MUST tell the agent to commit everything first, to run
`python3 -I scripts/verify.py --branch` in the background and wait for it because a foreground run exceeds the shell
tools' limits, to fix every `FAIL` and unexpected `NOT RUN`, and then to push; it MUST NOT tell the agent to run node
checks by hand.

#### Scenario: Push without delivery
- **WHEN** an agent that has not received the rule and has no branch pass for HEAD runs `git push origin feature`
- **THEN** the call is denied with the rule text, and the re-run is denied again until a branch pass exists

#### Scenario: Push without a pass is denied every time
- **WHEN** an agent with no branch pass for HEAD runs `git push origin feature` twice
- **THEN** both calls are denied with the rule text

#### Scenario: Push during finalize
- **WHEN** `openspec instructions finalize` succeeded in the context, a branch pass exists for HEAD, and the agent then runs `git push`
- **THEN** the push is not denied

#### Scenario: Pass made stale by a commit
- **WHEN** the agent commits after the branch pass and runs `git push`
- **THEN** the push is denied naming the pass's commit and HEAD
