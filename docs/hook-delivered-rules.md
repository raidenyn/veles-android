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
  - `gates`: Python regexes for a shell command about to run;
  - `stops`: predicate names from the engine's registry, today only
    `"superspec-finalize-pending"`; any other name is an error;
  - `requires`: proof names from the engine's registry, today only `"branch-pass"`; valid only with
    a non-empty `gates`, and an unknown name is an error;
  - `exempt`: Python regexes for gated command segments that need no proof; valid only with a
    non-empty `requires`.
- An omitted list is empty, but a rule needs at least one entry in `skills`, `commands`, `gates` or
  `stops`. Neither `requires` nor `exempt` is a trigger list, and `requires` without `gates` or
  `exempt` without `requires` is an error. Duplicate keys, unknown keys, `[]` and invalid regexes
  are errors.
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
  (separators included); a rule that does not fit is left for its next moment or gate. The agent-file
  check applies the same selection and byte count to each declared trigger form separately — every
  skill name as a rule writes it, every listed command, and each stop predicate's line — so it does
  not merge a rule's `opsx:` and canonical skill names into one trigger.
- Delivery is recorded per session and per agent context (a subagent and its parent never share
  credit) under the git common directory (`agent-rules/`) of the checkout the session works in: in
  OpenCode the session's `location.directory`, else its root session's, else the plugin's. A rule's
  identity is its file name plus the sha256 of the file, so editing a rule delivers it again.
- A delivered rule is skipped only while its entry is current for the context and already holds every
  trigger the event matched; a different trigger delivers it again. The entry keeps the triggers and
  gate patterns it has gained, and a `void` of it removes it whole, earlier triggers included, so the
  duplicate delivery that follows is the safe side of the lost credit.
- The state file is version 2: entries carry their triggers, and the session carries a change set,
  the stop records and the apply flag that arms the stop gate. A version 1 file loads with those
  fields empty and the flag unset, and is written back as version 2; a version 2 file written before
  the flag existed also loads it unset.
- The store keeps the newest 50 sessions by write order. Pruning runs after an event that wrote
  state, never removes the current session or one of the newest 50, skips a busy session, and also
  removes stale lock files.

## Gates and pending entries

This section describes a rule without `requires`; a rule with `requires` follows
[Proof-requiring gates](#proof-requiring-gates) instead.

- A gated command whose rule this context has not received is denied once, with the rule text as the
  reason, and the rule is recorded as pending with the matched pattern.
- Read the text and re-run the command: a re-run matching a pending pattern confirms the rule and
  passes, even with a changed message or title.
- A sibling that matches a different gate pattern of the same rule is denied as well.
- Residuals: two same-kind gates in one parallel batch can let one of them pass, in either order,
  before the model saw the rule (Claude Code let the second pass; OpenCode let the first pass and denied
  the second); a moment plus a gate in one parallel batch passed the gate on both hosts in the final
  verification (the moment was processed first on Claude Code; OpenCode had denied it in an earlier
  observation, so the order of the two hooks is not guaranteed).

## Proof-requiring gates

- **Segments.** For a rule with `requires`, the engine splits the call string at `;`, `&`, `|` and
  newlines — the same boundary the gate regexes stop at — and matches `gates` and `exempt` per
  segment. The proofs are required when any segment matches a gate and no `exempt` pattern. So
  `git push origin feat && git branch -d tmp` is not exempt (the `-d` is in another segment), and a
  deletion push such as `git push origin --delete feat` is. Commands reached through variables,
  aliases, scripts or quoted operators stay out of scope, as for the other gates. Any process can
  write `build/verify/branch-pass.json` by hand; the gate is a guardrail for cooperative agents,
  not a security boundary.
- **Registry.** `PROOF_PREDICATES` in `scripts/agent_rules.py` holds the proofs; today only
  `branch-pass`, bound to `branch_pass.check` in `scripts/branch_pass.py`. It checks the record a
  complete manual `python3 -I scripts/verify.py` (or `--branch`) run writes for HEAD and the merge
  base with `origin/master` (`docs/verify.md`). Adding a proof is one registry entry, a callable
  `(cwd, deadline, now)` with its test; requiring it is a rule edit, with no engine change.
- **Outcome.** When no segment requires the proofs (every gated segment is exempt), the call passes
  and the rule is not denied for delivery; so does a call whose every proof holds, because the
  proof is stronger than delivery. When a proof fails, raises or reaches its deadline, the call is
  denied with the rule text followed by each failing proof's message, and the rule is marked
  delivered in that context, so its moments may deliver it again. The denial repeats on every
  matching call until the proof holds: there is no deny-once for a proof rule, and a matching
  pattern does not confirm one.
- **Deadline.** The proofs share a deadline 3.5 seconds after the engine starts handling the event,
  a 1.5-second margin under the hosts' 5-second limit. `branch_pass.check` gives each git call the
  time left; a proof that reaches the deadline denies with "proof timed out".
- **Rule.** `.agents/hooked-rules/repo-pre-push-checks.md` is the one rule that uses this:
  `git push` is denied until a complete pass of either gate exists for HEAD, with ref-deletion
  pushes exempt.

Its fail-open limits, each accepted:

- *Host timeout*: if the host's 5-second limit fires before the proofs answer (for example a slow
  checkout discovery before they start), the engine is killed and the call passes: the proof's
  denial is never produced.
- *Held state lock*: the proofs run inside the session's `Store.transaction`, so they hold that
  session's state lock for their whole run — up to 3.5 seconds of git calls; a concurrent hook event
  in the same session waits for it.
- *Version skew*: Claude Code runs the engine from `$CLAUDE_PROJECT_DIR` and the OpenCode plugin
  from its own checkout, while the rules come from the event's checkout. An old engine that meets a
  rule with `requires` gets a rule parse error and fails open, so the push passes; a new engine with
  an old rule keeps deny-once delivery and requires no proof.
- *Plain-terminal pushes*: there is no git `pre-push` hook, so only an agent's `execute.before` gate
  is checked; a push from a plain terminal is not gated.
- *Coarse matcher*: the gate regex matches `git`, anything up to a shell operator, then `push`, so a
  command string that merely mentions those words is gated too — a commit message containing
  `git push`, `git stash push`, or a quoted read-only command. A branch pass lets it through; when
  the message itself contains the literal command, use `git commit -F <file>`, and for a stash use
  `git stash` without the `push` argument.

## Stop gate

- A `stops` entry names a predicate the engine answers about the checkout; the registry has one,
  `superspec-finalize-pending`, and an unknown name is an error.
- **Session change set:** every successful shell call whose command matches `\bopenspec\b` adds the
  names of its `--change <name>` and `--change=<name>` arguments, quoted forms included, keeping the
  20 newest. A name matches `[a-z0-9][a-z0-9-]{0,63}` — at most 64 characters; a longer one is
  ignored. The set is session-wide, so a subagent's call counts too.
- **Arming:** only a session that invoked the apply skill is armed — a Skill call for
  `openspec-apply-change` (its `opsx:apply` form included) or a `/opsx:apply` prompt. Reading the
  skill's `SKILL.md` delivers the rule as a moment like any other, but it is not an invocation and
  arms nothing. A session that only named the change, such as a delegated verify or review session,
  is never reminded: the reminder belongs to the session that will finalize, normally the parent that
  applied. If the session never invoked apply, its stop passes without writing a record.
- **Predicate:** a change is pending when some copy has `apply.md`, no copy has `finalize.md`, no
  checkout has `openspec/changes/archive/<YYYY-MM-DD>-<name>/`, and the copy with the newest apply
  iteration has no `verify.md`, a `verify.md` of another iteration, or one with no FAIL box ticked. A
  receipt over 64 KiB counts as not pending for that change.
- **Checkouts:** the event's checkout, the main working tree and each linked worktree (at most 32) of
  the same common directory, one real path each. A `--separate-git-dir` checkout's main working tree
  is not resolved, so a receipt that exists only there is not seen from a linked worktree; the
  event's own checkout is always searched. A stray non-directory entry under
  `<common-dir>/worktrees/` (such as a `.DS_Store` file) is skipped, never an error.
- **Block once per phase:** a stop in the main context asks every rule naming the predicate. The
  first pending change by name whose phase — the newest apply iteration plus its verify state,
  `none` or `pass` — has no stop record is blocked, with one engine line naming the change and then
  the rule text under the cap. The record `{change, iteration, verify_state, write_id}` is written in
  the same transaction (the 40 newest kept), and the rules are not marked delivered. A subagent stop,
  an unarmed session, no rule with the predicate, a recorded phase and no pending change pass. A
  `void` carrying a stop record's write id removes that record, so the same phase can remind again.
  At most two reminders per apply iteration: one in the `none` phase after apply and one in the
  `pass` phase after a passing verify.
- **`stop_hook_active` is not consulted:** after a block the agent may continue in the same turn, run
  verify and stall again, and that post-verify phase must block once; the per-phase records bound the
  blocks, so no loop is possible.
- **Failed delivery:** a block whose record was written but whose delivery failed (a host timeout or
  error after the write) does not remind again in that phase, because the record stands; Claude
  Code's `Stop` has no `void`. OpenCode sends its `void` when the synthetic prompt fails, so there the
  same phase can remind again.
- **Claude Code wiring:** the engine runs from the `Stop` hook in `.claude/settings.json`, with the
  same command and 5-second timeout as the other hooks. `SubagentStop` holds only the agent harness
  hook runtime entry, never the engine, so a subagent's turn end is not a stop moment.
- **OpenCode:** the plugin sends `stop` for a root session's `session.execution.succeeded` only; a
  child session, `session.execution.failed` and `session.execution.interrupted` are not stop moments.
  The event name is that of the verified OpenCode 2.3.2 and may change in a later version. A `block`
  resumes the session with the text as a synthetic prompt; a failed resume sends its `void`.

## Re-arm and stale credit

- A compaction re-arms delivery. A subagent compaction re-arms that subagent only; a main-context
  compaction re-arms every key of the session, so every context gets the rule again.
- Stale credit, two cases; each can last until the next compaction of that context:
  - *stale delivery*: the host fails, cancels or times out after the engine recorded a delivery, so
    the rule counts as delivered although the agent may not have seen it; until re-armed it also
    suppresses repeats of the same trigger, while a different trigger still delivers;
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
- *Stop failures*: a stop moment fails open. An engine error, lock timeout or unreadable receipt
  passes; a non-blocking Claude Code `Stop` output has no model channel, so the read-by-hand notice
  goes in `systemMessage`, which reaches the user, and to stderr. The OpenCode plugin does nothing at
  a stop moment when the engine fails.
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

- Spike evidence: Claude Code 2.1.328, OpenCode 2.3.2, next to `opencode-rules` 2.0.0.
- Final live verification of every `hooked-rule-delivery` outcome, by host and version (2026-11-20):
  - **Claude Code 2.1.328**: outcomes 1, 2, 3, 5, 6 and 7 PASS. Outcome 4 PASS for re-arm and for
    parent↔subagent isolation in both directions; its **subagent-compaction sub-case is UNVERIFIED** —
    a subagent context could not be made to overflow on purpose (the host does carry `agent_id`). The
    spike's **interactive Esc is UNVERIFIED** as well (a headless interrupt was used).
  - **OpenCode 2.3.2**: outcomes 1, 2, 3, 4, 5, 6 and 7 PASS, including the second stop after a passing
    verify, a retained stop record across a compaction, an apply-to-finalize run that went on without
    asking, and a child-session compaction. Outcome 7's **interactive TUI Esc abort is UNVERIFIED** —
    the client exits with no terminal event, so the plugin cannot see it; accepted as a residual.
  - **Not exercised**: the two OpenCode plugins together. The disposable config loaded the tracked
    local `./scripts/opencode-hooked-rules` but not the tracked `opencode-rules` package, so the local
    hooked-rules plugin was exercised alone.
  - The runs, the residual batch results and the per-outcome table are in `spike.md` of the
    `hook-workflow-rules` change (§6).
- The OpenCode stop gate requires a kept-alive session. `opencode run --standalone` exits at the end of
  its turn, before the plugin's detached stop call can finish, so a one-shot run may end without the
  stop reminder being delivered (no block, no message). The gate was verified against a persistent
  `opencode serve` plus `run --server`; the TUI stop gate was **not exercised**. The owner accepted
  this limitation on 2026-11-20, keeping the plugin's dispose behaviour as shipped.
- The OpenCode plugin entry in `opencode.json` is a directory path string
  (`"./scripts/opencode-hooked-rules"`); a path to `index.js` or a `file:` package is rejected or not
  loaded.

## Test switches

`VELES_AGENT_RULES_FAIL_AFTER_WRITE=1` (the engine exits 1 after its transaction) and
`VELES_AGENT_RULES_FAIL_INJECTION=1` (the OpenCode plugin fails every injection) are test seams: keep
them unset outside disposable verification. `FAIL_AFTER_WRITE` also fires after a transaction that
changed nothing, so exit 1 alone is no evidence that a write occurred.
