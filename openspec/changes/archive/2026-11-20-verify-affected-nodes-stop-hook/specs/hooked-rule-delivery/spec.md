## MODIFIED Requirements

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
- pre-push checks: before a push, run `python3 -I scripts/verify.py --branch`.

#### Scenario: Stale block
- **WHEN** a hooked rule's `pointer` changes and the block is not regenerated
- **THEN** `pointers --check` and the agent-file check fail

#### Scenario: Pointer without hooks
- **WHEN** the hooks and the OpenCode plugin are disabled and a session starts at the repository root, with no parent `CLAUDE.md` or `CLAUDE.local.md`
- **THEN** the agent's context contains the pointer block, including the closeout pointer's authorization to finalize without asking

#### Scenario: Pre-push pointer names the verify command
- **WHEN** the pointer block is generated from `.agents/hooked-rules/repo-pre-push-checks.md`
- **THEN** its line names `python3 -I scripts/verify.py --branch` and keeps the rule's moments, `openspec instructions finalize` and before `git push`

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
