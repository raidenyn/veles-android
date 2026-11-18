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
- The body is at most 4,096 UTF-8 bytes and must hold text: an empty or blank body is an error.
- The rule is a regular file of at most 64 KiB; a symlink is an error.
- A gate regex runs against the raw command text, so it also matches words inside a quoted string.
- Run `python3 -I scripts/agent_rules.py pointers --write`, then the agent-file check
  (`python3 docs/checks/check_agent_files.py --root .`). A hooked rule is not listed in any
  `## Rules` section; the pointer block lists it.

## Pointer block

The root `AGENTS.md` carries a generated `## Hook-delivered rules` block: the markers and one line
per rule (its `pointer`, then its path), nothing else. It is the always-on fallback, so each pointer
says when to read its file and states anything that must never be missed (the commit-types pointer
keeps the Conventional Commits format rule). `pointers --check` and the agent-file check fail on a
stale block. Each rule adds one line to the root, which is capped at 40 lines; see the hook section in
[`docs/AGENTS.md`](AGENTS.md#hook-delivered-rules) for the compression step.

## Delivery

- A matching skill (model-invoked, a user-typed `/<skill>` or a read of its `SKILL.md`) or a
  successful matching workflow command adds the rule text to the context without blocking. A failing
  command is not a moment.
- The text shown is the body with its line endings normalised to LF and its leading and trailing
  blank lines removed; indentation and all other Markdown are kept. Several rules are joined in
  file-name order with one blank line between them, up to 9,000 UTF-8 bytes of the emitted text
  (separators included); a rule that does not fit is left for its next moment or gate.
- Delivery is recorded per session and per agent context (a subagent and its parent never share
  credit) under the git common directory (`agent-rules/`) of the checkout the session works in: in
  OpenCode the session's `location.directory`, else its root session's, else the plugin's. A rule's
  identity is its file name plus the sha256 of the file, so editing a rule delivers it again.
- The store keeps the newest 50 sessions by write order. Pruning runs after an event that wrote
  state, never removes the current session or one of the newest 50, skips a busy session, and also
  removes stale lock files.

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

The exact hook command needs a usable `python3` on `PATH` and, in Claude Code, a Bash-compatible hook
shell: Claude Code's PowerShell fallback does not expand the bare `$CLAUDE_PROJECT_DIR`, so native
Windows with PowerShell is not supported or verified. The engine's Windows lock branch
(`msvcrt.locking`) is unverified as well.

## Degraded modes

The hooks never block a call because of their own failure.

- *No Python*: Claude Code shows the user a non-blocking hook error and the agent has only the
  pointer; the OpenCode plugin sends the read-by-hand notice itself.
- *Recoverable engine error* (malformed event, unreadable rule, corrupt state, lock timeout): the call
  passes with the read-by-hand notice ("Rule hooks failed: read the hooked rules listed in AGENTS.md
  by hand …") where the format has a channel: the plain format (OpenCode appends it to the
  `execute.after` output or sends it as a synthetic prompt message) and Claude Code `PreToolUse`,
  `PostToolUse` and `UserPromptSubmit` events whose JSON still names the event. For `PreToolUse` the
  notice is `additionalContext` with no permission decision, so the call goes through the normal
  permission flow. `PreCompact` has no model-visible channel, nor has an undecodable or
  unidentifiable Claude Code event, so there the notice goes to stderr only.
- *Probe behind the `PreToolUse` notice* (Claude Code 2.1.326, a disposable clone with no remote): a
  `PreToolUse` hook printing only `hookSpecificOutput.additionalContext` with a random canary, no
  `permissionDecision`. The model quoted the canary verbatim as hook context, the command ran under
  `--allowedTools Bash`, and without that flag a command needing approval was still refused for
  permission.
- *Claude Code timeout (5 s, enforced by the host) or hard crash*: nothing is left to emit a notice;
  the host shows a non-blocking hook error and the agent has the pointer only.
- *OpenCode plugin spawn failure, timeout, nonzero exit, more than 1 MiB of output, undecodable
  output or a deny without text*: the plugin sends the notice itself where it has a channel
  (`execute.after`, prompt); `execute.before` (gates) passes silently.
- *OpenCode host stall*: each hook call has one 5-second budget covering the session lookups, the wait
  for an announced compaction, the engine runs, the injection and the voids. A host call that never
  answers lets the original call or prompt proceed when the budget runs out (with the notice where a
  channel exists); a late answer is ignored and an engine child still running is killed.
- *OpenCode injection failure*: when appending to the tool output or sending the synthetic prompt
  message fails, the plugin sends one best-effort `void` per recorded rule entry, within what is left
  of the same budget. A void that does not run (budget spent, engine failure) leaves the credit
  (stale delivery). An injection still unanswered when the budget runs out has an unknown outcome and
  is not voided.
- *Hooks disabled* (`disableAllHooks` outside the repository, since the agent-file check rejects it
  in `.claude/settings.json`) or *plugin disabled*: the pointer block only.
- *Antigravity and other tools*: the pointer block only. Whether Antigravity reads the root
  `AGENTS.md` natively is unverified (see `spike.md` of the `hook-delivered-rules` change).

## Verified tools

- Spike evidence (Claude Code 2.1.326, OpenCode 2.3.2, next to `opencode-rules` 2.0.0); the final live
  verification is recorded in `spike.md` of the `hook-delivered-rules` change after plan Task 10.
- The OpenCode plugin entry in `opencode.json` is a directory path string
  (`"./scripts/opencode-hooked-rules"`); a path to `index.js` or a `file:` package is rejected or not
  loaded.

## Test switches

`VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` (the engine exits 1 after its transaction) and
`VELES_AGENT_RULES_FAIL_INJECTION=1` (the OpenCode plugin fails every injection) are test seams: keep
them unset outside disposable verification. `FAIL_AFTER_WRITE` also fires after a transaction that
changed nothing, so exit 1 alone is no evidence that a write occurred.
