# Hook-delivered rules

A hook-delivered rule reaches the agent only at the moment it matters (a skill, a workflow command or
a gated shell command), not at session start. The engine is `scripts/agent_rules.py` (standard library,
Python 3.8+, run as `python3 -I`). Claude Code runs it from the hooks in `.claude/settings.json`, and
OpenCode runs it from the local plugin `scripts/opencode-hooked-rules/`.

## Adding a rule

- One file in the flat `.agents/hooked-rules/` directory (no subdirectories), named
  `<scope>-<topic>.md` with the scopes of `.agents/rules/`.
- Frontmatter uses the restricted grammar: `key: value` lines and flat lists of double-quoted
  strings, with only the `\"` and `\\` escapes, so a regex doubles its backslashes. Only these keys:
  - `pointer` (required): a double-quoted one-line summary that says when to read the file;
  - `skills`: skill names, namespaced names such as `"opsx:apply"` included;
  - `commands`: Python regexes (`re.search`) for a workflow command that succeeded;
  - `gates`: Python regexes for a shell command about to run.
- An omitted list is empty, but a rule needs at least one entry in `skills`, `commands` or `gates`.
  Duplicate keys, unknown keys, `[]` and invalid regexes are errors.
- The body is at most 4,096 UTF-8 bytes.
- A gate regex runs against the raw command text, so it also matches words inside a quoted string.
- Run `python3 -I scripts/agent_rules.py pointers --write`, then the agent-file check
  (`python3 docs/checks/check_agent_files.py --root .`). A hooked rule is not listed in any
  `## Rules` section; the pointer block lists it.

## Pointer block

The root `AGENTS.md` carries a generated `## Hook-delivered rules` block: the markers and one line
per rule (its `pointer`, then its path), nothing else. It is the always-on fallback, so each pointer
says when to read its file and states anything that must never be missed (the commit-types pointer
keeps the Conventional Commits format rule). `pointers --check` and the agent-file check fail on a
stale block.

## Delivery

- A matching skill (model-invoked, a user-typed `/<skill>` or a read of its `SKILL.md`) or a
  successful matching workflow command adds the rule text to the context without blocking. A failing
  command is not a moment.
- Delivery is recorded per session and per agent context (a subagent and its parent never share
  credit) under the git common directory (`agent-rules/`). A rule's identity is its file name plus the
  sha256 of the file, so editing a rule delivers it again.

## Gates and pending entries

- A gated command whose rule this context has not received is denied once, with the rule text as the
  reason, and the rule is recorded as pending with the matched pattern.
- Read the text and re-run the command: a re-run matching a pending pattern confirms the rule and
  passes, even with a changed message or title.
- A sibling that matches a different gate pattern of the same rule is denied as well.
- Residuals: two same-kind gates in one parallel batch can let the second pass as a "re-run" before
  the model saw the rule; a moment plus a gate in one parallel batch passed the gate on Claude Code
  (it ran the moment first) and was denied on OpenCode.

## Re-arm and stale credit

- A compaction re-arms delivery. A subagent compaction re-arms that subagent only; a main-context
  compaction re-arms every key of the session, so every context gets the rule again.
- Stale credit, two cases; each can last until the next compaction of that context:
  - *stale delivery*: the host fails, cancels or times out after the engine recorded a delivery, so
    the rule counts as delivered although the agent may not have seen it;
  - *stale pending denial*: the engine recorded a pending denial but the host never showed or applied
    the deny (cancel, timeout, crash or error). A later call that matches a recorded pending pattern
    confirms the entry, so this rule no longer denies it or later gates (another matching rule still
    can); a call matching only a different gate pattern is denied again while the entry is pending.
- A passing retry therefore does not prove the rule was shown.

## Supported configuration

The agent starts at the repository root and no parent folder has a `CLAUDE.md` or `CLAUDE.local.md`.
Outside it, or in a subagent that does not load `AGENTS.md`, the pointer block may be absent and the
hooks are the only delivery.

## Degraded modes

The hooks never block a call because of their own failure.

- *No Python*: Claude Code shows the user a non-blocking hook error and the agent has only the
  pointer; the OpenCode plugin sends the read-by-hand notice itself.
- *Recoverable engine error* (malformed event, unreadable rule, corrupt state, lock timeout): the call
  passes with the read-by-hand notice ("Rule hooks failed: read the hooked rules listed in AGENTS.md
  by hand …") where the format has a channel: the plain format (OpenCode appends it to the
  `execute.after` output or sends it as a synthetic prompt message) and Claude Code `PostToolUse` and
  `UserPromptSubmit` events whose JSON still names the event. Claude Code `PreToolUse` (gates) and
  `PreCompact` have no model-visible channel, nor has an undecodable or unidentifiable Claude Code
  event, so there the notice goes to stderr only.
- *Claude Code timeout (5 s, enforced by the host) or hard crash*: nothing is left to emit a notice;
  the host shows a non-blocking hook error and the agent has the pointer only.
- *OpenCode plugin spawn failure, timeout, nonzero exit or undecodable output*: the plugin sends the
  notice itself where it has a channel (`execute.after`, prompt); `execute.before` (gates) passes
  silently.
- *Hooks disabled* (`disableAllHooks`) or *plugin disabled*: the pointer block only.
- *Antigravity and other tools*: the pointer block only. Whether Antigravity reads the root
  `AGENTS.md` natively is unverified (see `spike.md` of the `hook-delivered-rules` change).

## Verified tools

- Claude Code 2.1.326 and OpenCode 2.3.2 (next to `opencode-rules` 2.0.0).
- The OpenCode plugin entry in `opencode.json` is a directory path string
  (`"./scripts/opencode-hooked-rules"`); a path to `index.js` or a `file:` package is rejected or not
  loaded.
