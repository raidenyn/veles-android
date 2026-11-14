# Retrospective (`consolidate-agent-files`)

> Written at finalize. Evidence first, opinion second.

## Wins

- The layout is enforced, not just documented: `docs/checks/check_agent_files.py` carries 79 tests covering every machine-checkable invariant, and the `docs` node runs it (plus its tests) through `check-docs.yml`.
- Live scoping was exercised in an outside fixture for Claude Code 2.1.325 and OpenCode 2.3.0 + `opencode-rules` 2.0.0, including the no-read baseline, the negative scope case, the parent-scope case and manual discovery with the plugin unavailable.
- Per-task reviews caught defects that would otherwise have shipped: lost `VELES_*_VERSION` facts, a permissive frontmatter parser, prefix-based rule-path matching, disguised exact pins in the plugin range, and CODEOWNERS checks that ignored last-match ownership.
- Fact preservation is auditable: `traceability.md` maps all 13 `CLAUDE.md` sections plus six facts that existed nowhere else, and the host build-contract gate was repointed without weakening its assertions.

## Misses

- Antigravity was never exercised empirically (the T3 driver loads no `.agents/rules`; `homepc agy` was unavailable on the PC), and Windows was not run.
- A CI-only test failure slipped through: an assertion depended on Git's own "not a git repository" wording, which differs on the GitHub runner.
- The planning artifacts briefly overclaimed verification: the spec, `tasks.md` and `CONTRIBUTING.md` described Antigravity and the OpenCode ancestor chain as verified until the final review required reconciliation.
- Two `opencode-rules` 2.0.0 defects are real and only mitigated: a repeated read of the same file can drop its scoped rule, and a duplicate user-level plugin entry suppresses project delivery.

## Plan deviations

- The plugin pin became the bounded range `>=2.0.0 <3` (owner decision), applied across the spec, design, plan and checker before implementation.
- The real tree showed eleven override findings at Task 8 instead of the planned ten, because `.github/components.json` still needed its `docs` target until Task 9.
- Platform setup detail moved from the root `AGENTS.md` to `CONTRIBUTING.md` (Task 4 fix) while the required `core.symlinks=true` and OpenSpec range stayed in the root, stated neutrally.
- The final review's residual CODEOWNERS gap was completed by amending the same unpushed fix wave rather than parking it.

## Skill/workflow compliance

- `subagent-driven-development` with a fresh implementer and a task-scoped review per plan task; three fix rounds were needed (Tasks 5, 6, 7) and the final fix wave plus one scoped re-review closed the branch review.
- Test-driven development was followed for every checker change (RED shown, then GREEN) and for the scanner exclusion.
- The final whole-branch review ran on a capable model over a frozen diff package; its three Critical findings were fixed before merge.

## Surprises

- `opencode-rules` scoping breaks on a repeated read of the same file (4/4 reproductions) — the opposite of the expected "no duplicate" behaviour.
- The managed OpenCode background service ignores an isolated `OPENCODE_CONFIG_DIR`; `--standalone` is required to verify the repo config.
- OpenCode does not load the root `AGENTS.md` when started inside a node folder, while Claude Code does.
- The user-wide OpenSpec workflow list yields 11 skills (including `openspec-update-change`), not the 10 the design assumed.

## Promote candidates

- Never assert a third-party tool's message text in tests; assert our own diagnostics (learned from the Git wording failure).
- Run the CI job's Python test suite under a clean, CI-like environment before pushing, not only locally.
- Keep a "known upstream defects" subsection in contributor docs when a dependency is load-bearing (done for `opencode-rules`).
- When a design depends on a format a tool only documents, make the owner-accepted blocker explicit in the spec, not just in the verification notes.
