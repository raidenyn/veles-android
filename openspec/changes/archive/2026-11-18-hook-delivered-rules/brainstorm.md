## Design Summary

Rules that matter only at certain moments can move out of always-on context into **hook-delivered rules**. This
change builds the mechanism and moves one rule, the commit-type guide, as its pilot:

- The full text lives in `.agents/hooked-rules/<scope>-<topic>.md`.
- An always-on one-line pointer lives in a generated block of the root `AGENTS.md`. Every supported tool reads
  that file natively, without hooks or a rule loader.
- A shared stdlib Python engine (`scripts/agent_rules.py`) is wired into Claude Code (`.claude/settings.json`
  hooks) and OpenCode (a small V2 plugin).

The engine delivers a rule in two ways:

- **Early, without blocking,** at the moments the rule declares: a skill being invoked (by the model, by the
  user as a slash command, or by reading its `SKILL.md`), or a workflow command such as
  `openspec instructions finalize`.
- **As a safety net,** by blocking the first matching gated command (`git commit`, `gh pr create`,
  `gh pr edit`, …) with the rule text as the reason, when the rule was not delivered to that context since the
  last compaction. The agent's re-run proceeds.

Delivery state is kept per context (session, and subagent where the tool identifies one) and re-armed by
compaction. Hooks fail open. When a hook fails, the agent is told to read the rule file by hand. This mechanism
**reminds; it does not judge**. While the hooks are installed and working, the guide is in front of the agent
before a covered command, but nothing checks that the agent picks the right type. With hooks missing, failing or
degraded, a covered command can proceed without the guide, and only the always-on pointer remains.

The pilot is a commit/PR type guide for every type `commitlint.py` accepts. The closeout and tracker-intake rules
stay always-on in this change (owner's call after the design review). They move in a follow-up change once the
pilot has proved the mechanism in both tools; that follow-up adds what they need (prompt-pattern moments,
re-delivery after compaction, workflow scenario tests). The full design is in `design.md`.

## Alternatives Considered

### Alternative A: Block once per context, re-arm after compaction (safety net only)
- **Approach**: Deny the first gated command in a context with the rule as the reason; record delivery at the
  denial; clear the record on compaction.
- **Pros**: With working hooks, the guide is in front of the agent before every covered command; it targets
  forgetting directly; it needs only primitives both tools offer (subject to the OpenCode verification below).
- **Cons**: One extra round trip per context even where the rule could have arrived earlier.
- **Why not chosen alone**: Kept as the safety net; early moments remove the round trip in the common flow.

### Alternative B: Block every gated command once
- **Approach**: No per-context delivery record. Each distinct gated command is denied once (remembering only
  that attempt) and passes on its re-run.
- **Pros**: Simple; the guide is always the freshest thing in context.
- **Cons**: A retry on every commit and every PR edit; agents learn to re-run without reading, so the rule stops
  working.
- **Why not chosen**: It defeats its own purpose.

### Alternative C: Deliver earlier only (keywords, `git add`), never block
- **Approach**: Add the rule to context when the user mentions commits or PRs, or when the agent stages files.
- **Pros**: Never blocks.
- **Cons**: Unreliable triggers; it misses commits the agent decides on alone; a hook that only adds context
  after the command runs sees a title that was already written.
- **Why not chosen**: Not reliable enough to be the only mechanism.

### Alternative D: Deterministic type check from changed files
- **Approach**: Block a type that contradicts the files touched (docs-only must be `docs`, and so on).
- **Pros**: Objective, offline, repeatable.
- **Cons**: Real PRs almost always mix file kinds, so the rules would be either toothless or complex.
- **Why not chosen**: The owner chose guidance; the agent decides the type.

## Agreed Approach

Early delivery points plus the block-once safety net (A plus early moments). In the normal superspec flow the commit rule arrives at apply or finalize
and nothing is blocked, provided the moment and the gated command are separate tool calls. Ad-hoc commits, and
commits after a compaction with no early delivery in between, are caught by the gate. Tools without hooks keep the always-on pointer, which tells
them to read the file by hand at the named moments.

OpenCode support is **conditional on runtime verification of the V2 plugin API**, as the first implementation
step. The installed `@opencode-ai/plugin` 1.18.15 typings describe the V1 API and establish none of this. The
`opencode-rules` 2.0.0 source shows the V2 shape: `setup(context)`, `context.tool.hook('execute.before'|'execute.after')`,
`context.session.hook('context')` and the `session.compaction.started|ended` events.

Verification is by **end-to-end behaviour in a real OpenCode 2.x session**, not by the presence of primitives.
These are the behaviours required:

1. Each tool loads its own integration from its tracked location: the Claude Code hooks from
   `.claude/settings.json`, and the OpenCode plugin from a local path outside `.opencode/`, next to
   `opencode-rules`.
2. A gated command is stopped once, the model reads the rule text, and the re-run proceeds.
3. **Each** moment kind the rule declares adds the rule to context without blocking, verified separately for
   each kind (model-invoked skill, user-invoked skill slash command, `SKILL.md` read, workflow command), and a
   later gated command in that context is not blocked.
4. After a compaction, with no early moment in between, the next gated command is blocked again (re-arm).
5. With the hooks and the plugin disabled, the main context shows the pointer from the root `AGENTS.md`.

The same five are required of Claude Code.

**What a missing behaviour means.** The owner requires both Claude Code and OpenCode, so a missing behaviour is
a ship condition, not a silent degradation:

- **The commit-types pilot** ships only when all five behaviours work in both tools. If one cannot be made to
  work in a tool, implementation stops and the owner decides: accept that tool as pointer-only for the pilot, or
  hold the change. The pointer is a runtime fallback for broken or missing hooks, not a planned release state.

## Key Decisions

- Hook-delivered rules live outside `.agents/rules/` so no tool loads them up front.
- There is one always-on pointer line per rule (the owner chose this over hooks only). It goes in a generated
  block of the root `AGENTS.md`, not in `.agents/rules/`, so it is visible even when OpenCode's `opencode-rules`
  loader is absent. It is checked for freshness, and it tells an agent without working hooks to read the file at
  the named moment.
- Type choice is guidance, not a deterministic check, and no LLM validator is used (owner's call).
- **The owner's constraints carried into the type guide:**
  - `feat` only for user-visible features;
  - `build` for build tooling, dev dependencies and Gradle;
  - `docs` for any documentation (Markdown and similar);
  - `test` for adding or changing tests;
  - `chore` only when nothing else fits.

  The guide covers the full `commitlint.py` list (owner's call).
- **Builder's taxonomy calls:**
  - `ci` takes GitHub workflows, including release workflows and CD automation, and the scripts only CI runs;
  - `build` takes the build, dependencies, toolchains and release packaging such as signing and git-cliff config;
  - the type of a mixed change follows its purpose.
- **Gates:** `git commit`, `gh pr create` and `gh pr edit`.
- **Moments:**
  - model-invoked skills (Claude Code `Skill` tool, OpenCode `skill` tool);
  - user-invoked skill slash commands (prompt hooks);
  - reads of a skill's `SKILL.md`;
  - workflow shell commands.

  When a moment and a gate fall in the same shell call, the gate runs first and blocks once.
- **Delivered** means the rule text reached that context: as added context at a moment, or as the denial reason at
  a gate.
- **Identity:**
  - the session id is always present and keys the main context;
  - a subagent is keyed separately by its agent id: Claude Code's `agent_id` on subagent calls, and OpenCode's
    own subagent session id;
  - a call without an agent id is the main context, never "unknown".
- **Ordering:** compaction bumps a generation number, and a delivery written for an older generation is ignored.
  A late hook therefore cannot undo a reset.
- **Failure:** hooks fail open. On an internal error the command proceeds, and the agent receives a short notice
  to read the rule file by hand.
- Unit tests use no clock and no sleeps.
- **Scope (owner's call):** this change moves only the commit-type rule. The closeout, tracker-intake,
  review-gate and roster rules stay always-on and are follow-ups.
- Antigravity gets the pointer only.

## Open Questions

- Claude Code: the `Skill` hook input field that names the skill, and whether a user-typed skill slash command
  reaches `UserPromptSubmit` in a matchable form.
- Both tools: the five end-to-end behaviours under Agreed Approach, and on OpenCode how a local plugin directory is
  declared next to the existing `opencode-rules` entry.
- The final pointer wording for tools that see only the pointer.
