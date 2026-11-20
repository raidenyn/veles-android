## Why

After implementation in the superspec workflow, agents keep stopping to ask permission to push and open the pull
request, although finalize is defined to do that without asking. The always-on closeout rule says so, but at the
stop it loses to the generated apply skill's "suggest verification" ending and the harness's default of
confirming outward-facing actions. The pilot proved hook delivery for one rule; five more workflow rules can now
move, and a stop gate can put the closeout rule in front of the agent at the moment it would stall.

## What Changes

- **Stop gate (new trigger kind).** A hooked rule may declare `stops`: named predicates from an engine registry.
  `superspec-finalize-pending` is true when a change this session named has an apply receipt, no finalize
  receipt, no archived copy and no failing verify at that iteration. Then the main agent's stop is blocked once
  per phase (after apply; after a passing verify) with a line naming the change and the closeout rule — but only
  in a session that itself invoked the apply skill (a Skill call or `/opsx:apply`, never a `SKILL.md` read), so a
  delegated verify or review session is not reminded. Claude
  Code: a new `Stop` hook. OpenCode: the turn-end event (`session.execution.succeeded` in 2.3.2, where
  `session.idle` is not emitted) answered with a synthetic prompt. Both are spiked first; if
  either fails, the owner decides.
- **Per-trigger de-duplication.**
  - From: a moment re-sends a rule every time it matches.
  - To: a moment skips a rule the same trigger already delivered in this context and generation; a different
    trigger delivers again; a pending rule is still delivered.
  - Reason: repeated moments (and command text inside heredocs) re-sent kilobytes per call.
  - Impact: non-breaking; the stale-credit residual is reworded.
- **Five rules move** from `.agents/rules/` to `.agents/hooked-rules/`: closeout (with a new lead paragraph that
  authorizes verify and finalize without asking), review gate, reviewer roster, real-device testing (with a
  `gh pr create` gate) and pre-push checks (a `git push` gate). Each keeps a one-line always-on pointer that
  states its must-never-miss fact.
- **Config layer.** `openspec/config.yaml` apply guidance says to run verify and finalize without asking; its
  references to the moved files are repointed.
- **State version 2** for the new fields; a version 1 file is read as version 2.
- **Budget check.** The agent-file check sums the rule texts for every skill, a fixed list of workflow commands
  and the stop text against the 9,000-byte cap, and enforces the `Stop` wiring and the `stops` registry.
- Tracker intake, signing keys and no-time-based tests stay always-on; the node rules stay path-scoped.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hooked-rule-delivery`: `stops` grammar and registry, the `stop` event and `block` decision, the session change
  set, the apply flag that arms the gate, and the `superspec-finalize-pending` predicate, per-trigger de-duplication (and the "cap at a moment" wording), the pointer-block requirement naming each
  moved rule's always-on fact (the closeout pointer's authorization among them), state version 2, the fifth
  Claude Code hook, the OpenCode `session.execution.succeeded` mapping, the budget check, stop-gate fail-open, amended stale
  credit and end-to-end outcomes.
- `agent-guidance-layout`: three always-on `repo-*` rules remain; the workflow, pre-push and real-device rules are
  hooked rules; the root `AGENTS.md` lists the three and carries six pointers.
- `real-device-testing`: the rule is a hooked rule delivered at planning, apply, verify and before
  `gh pr create`, with its fact in the pointer.
- `superspec-closeout`: the closeout rule is a hooked rule with the stop gate, and it states that verify and
  finalize run without asking after apply.
- `superspec-review-gate`: the review-gate and roster rules are hooked rules delivered at the planning moments;
  the single-source-of-models ban extends to `.agents/hooked-rules/`; the audit scenario that relies on an
  always-on rule is reworded.

Every requirement and scenario of these specs that names a moved rule or calls it always-on is rewritten,
including the `agent-guidance-layout` procedure-pointer and cited-sections requirements, which must also read
`.agents/hooked-rules/`.

## Impact

- Code: `scripts/agent_rules.py` and its tests; `scripts/opencode-hooked-rules/index.js` and its tests;
  `docs/checks/check_agent_files.py` and its tests.
- Configuration: `.claude/settings.json` (`Stop` hook); `openspec/config.yaml`.
- Rules: five files move to `.agents/hooked-rules/`; root `AGENTS.md` (`## Rules` and the pointer block).
- References: `openspec/schemas/superspec/schema.yaml`, `templates/verify.md`, `docs/superspec-workflow.md`,
  `docs/openspec-workflow.md`, `docs/hook-delivered-rules.md`, `docs/dev/specs/real-device-testing.mdx` and the
  other site pages of the modified capabilities where they describe the rules as always-on.
- Real-environment verification in Claude Code and OpenCode (spike and re-run), recorded in `spike.md`.
- No product code (Android, extension, Rust, native bridge) changes.
