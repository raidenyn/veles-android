## MODIFIED Requirements

### Requirement: Host adapters
The repository SHALL wire the engine into Claude Code and OpenCode.

**Claude Code.** `.claude/settings.json` MUST run
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/agent_rules.py" hook --format claude` with `timeout: 5` from these hooks:

- `PreToolUse` on `Bash`;
- `PostToolUse` on `Bash|Skill|Read`;
- `UserPromptSubmit`;
- `PreCompact`;
- `Stop`.

Each of these events MUST hold the engine entry, with only the engine command, and after it the agent harness hook
runtime's entry: no matcher and the one command `node "$CLAUDE_PROJECT_DIR/.agents/harness/hook-runtime/claude.mjs"`,
with the timeout the runtime's `install` writes (10 for `PreToolUse`, `UserPromptSubmit` and `PreCompact`, 1800 for
`PostToolUse` and `Stop`). `PostToolUseFailure` and `SubagentStop` (1800) and `SessionStart` (10) MUST hold only the
runtime entry. The runtime runs the agent harness verify gate, which verifies at turn ends; the engine and the runtime
each answer for themselves.

The engine MUST NOT be wired to `SubagentStop`.

**OpenCode.** A V2 plugin in `scripts/opencode-hooked-rules/`, declared in `opencode.json` as a local entry next to
`opencode-rules` and the hook runtime's `./.agents/harness/hook-runtime/opencode`, MUST map these hooks and events to engine events, with a 5-second spawn timeout for the engine:

| OpenCode hook or event | Engine event |
|---|---|
| `context.tool.hook('execute.before')` | `tool-before` |
| `context.tool.hook('execute.after')` | `tool-after`, appending context to the tool output |
| `context.session.hook('prompt')` | `prompt` |
| `session.compaction.started` | `compacted` |
| `session.execution.succeeded` of a root session (never `failed` or `interrupted`) | `stop`; a `block` is sent as a synthetic prompt with `resume: true` that continues the session. The plugin starts no verifier: the hook runtime's plugin verifies turn ends |

The plugin MUST accept `block` only for a `stop` event and MUST ignore any other response at a stop moment. It MUST
NOT count a per-call `context.session.hook('context')` injection as delivery. Antigravity gets no adapter.

OpenCode 2.3.2 never emits `session.idle` (spike, `spike.md` §2A): its turn-end event is
`session.execution.succeeded`, which this row uses. If the spike had shown that OpenCode cannot resume a session
from its turn-end event, the row would be dropped only with the owner's decision, and OpenCode would be moments-only
for the stop gate, documented as such.

#### Scenario: Claude Code wiring
- **WHEN** `.claude/settings.json` lacks one of the five hooks or one of the three runtime-only hooks, omits a timeout, or wires the engine to `SubagentStop`
- **THEN** the agent-file check fails

#### Scenario: Stop holds the verify hook
- **WHEN** an otherwise valid fixture's five engine events each hold the engine entry with timeout 5 followed by the runtime entry, and the three runtime-only events hold the runtime entry alone
- **THEN** the agent-file check emits no hook-wiring finding

#### Scenario: Stop wiring drifts
- **WHEN** an event lacks the runtime entry, holds it before the engine entry, holds a third entry, gives the runtime another timeout, or an engine entry holds a second command such as the earlier `verify.py --hook claude`
- **THEN** the agent-file check fails and names the event

#### Scenario: Runtime missing
- **WHEN** the hooks run the runtime and `.agents/harness/hook-runtime/claude.mjs` does not exist
- **THEN** the agent-file check fails and names that file

#### Scenario: OpenCode plugin entry
- **WHEN** `opencode.json` lacks the local hooked-rules plugin entry or the hook runtime's plugin entry
- **THEN** the agent-file check fails

#### Scenario: OpenCode child or aborted session
- **WHEN** a child session's execution succeeds, or a root session's execution ends as `interrupted` or `failed`
- **THEN** the plugin sends no `stop` event
