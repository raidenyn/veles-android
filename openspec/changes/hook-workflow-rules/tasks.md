Every unit test injects its clock, `sleep` and timers: no real waits and no wall-clock dependence
(`.agents/rules/repo-no-time-based-tests.md`). Spike and live-run evidence goes in this change's `spike.md`.

## 1. Spike the stop gate (design §7)

- [x] 1.1 Claude Code: in a disposable clone, add a throwaway `Stop` hook that blocks with a canary reason; record that the model quotes and acts on it, that the next stop reports `stop_hook_active` true, that a second block sent then is obeyed, and that a user interrupt fires no `Stop`; record the version
- [x] 1.2 OpenCode: in a disposable clone, a throwaway plugin answers a root session's turn-end event (`session.idle` is absent in 2.3.2; it uses `session.execution.succeeded`) with a resuming synthetic prompt carrying a canary; record that the model continues in the same session, how a child session's success, an `interrupted` execution and a `failed` execution are told apart, and the resuming call form; record the version
- [x] 1.3 If either spike fails, stop and ask the owner (moments-only for that tool, or hold) before any further task

## 2. Engine: grammar, triggers and state (design §2, §3 grammar, §4)

- [ ] 2.1 Parser: add the `stops` key and the predicate registry (`superspec-finalize-pending`); unknown names are errors; a `stops`-only rule is valid; tests for each
- [ ] 2.2 State version 2: `triggers` on delivered entries, a session-wide change set (20 newest) and stop records (40 newest); load version 1 as version 2; re-arm leaves the change set and the stop records intact; tests
- [ ] 2.3 Canonical skill triggers (fixed `opsx:` table) and command triggers; per-trigger de-duplication at moments; a pending entry never suppresses; re-delivery adds triggers and keeps patterns; tests for every Moments scenario and for "Cap at a moment" (only rules the moment will send)
- [ ] 2.4 Session change set: extract `--change <name>`, `--change=<name>` and quoted forms from successful shell calls matching `\bopenspec\b`, in any context; tests for each form, a 65-character name (ignored), the `grep` non-match and a subagent call
- [ ] 2.5 Stale credit with triggers: under `VELES_AGENT_RULES_FAIL_AFTER_WRITE=1`, a later moment of the same trigger is skipped and a different trigger delivers; tests

## 3. Engine: the stop gate (design §3)

- [ ] 3.1 Checkout discovery for the predicate: the event's checkout, the main working tree (none for a bare repository) and linked worktrees from `<common-dir>/worktrees/*/gitdir`, de-duplicated by real path, at most 32; tests with fixture trees
- [ ] 3.2 The `superspec-finalize-pending` predicate and phase: receipts, archived copies, iteration parsing, verify state (`none` / `pass`) at the newest iteration, the 64 KiB bound; table-driven tests for the predicate scenarios (stall after apply, failing verify, earlier failing verify, finalized or archived, receipts in a linked worktree), plus an undecided `verify.md` staying `none`, a missing or unreadable iteration counting as 0, and the 32-worktree cap
- [ ] 3.3 The stop decision and records: the `stop` event in both formats, per-phase records written in the same transaction, the first unrecorded change by name, `stop_hook_active` read but never suppressing a new phase, and the block never marking the rule delivered; tests for "Second stop passes", "Stall after a passing verify", "New apply iteration", "Stop hook already active in the same phase", "Continued turn reaches a new phase", "Compaction does not reset the record" and "Unrelated session"
- [ ] 3.4 Rendering: the engine line, Claude Code `{"decision":"block","reason":…}`, the plain `block` with the `["stop", <change>]` entry and its write id, and `block` rejected for any other event; tests
- [ ] 3.5 `void` of stop records by write id (a late `void` changes nothing); tests
- [ ] 3.6 Fail open at a stop: pass with the notice in `systemMessage` and stderr; tests for corrupt state, an unreadable receipt and a lock timeout with an injected `sleep`

## 4. Hosts (design §3 OpenCode, hooked-rule-delivery Host adapters)

- [ ] 4.1 `.claude/settings.json`: add the `Stop` hook with the engine command and `timeout: 5`; no `SubagentStop`
- [ ] 4.2 OpenCode plugin: map a root session's `session.execution.succeeded` (`interrupted` and `failed` never map) to `stop`; send a `block` as a resuming synthetic prompt (the form task 1.2 found); `void` the stop entry when it fails; accept `block` only for `stop` and ignore any other response at a stop moment; do nothing at a stop moment when the engine times out or Python is missing; tests in `index.test.mjs` with injected timers

## 5. Move the rules (design §1, §8)

Sections 5 and 6 land together: between them the agent-file check fails on the real tree, so do not push in
between.

- [ ] 5.1 `git mv` the closeout, review-gate, roster, real-device and pre-push rules to `.agents/hooked-rules/`, replace their frontmatter with the hooked-rule frontmatter of design §1 (skills, commands, gates, stops, pointer)
- [ ] 5.2 Add the closeout lead paragraph before its procedure instruction; confirm with `wc -c` that every body is at most 4,096 bytes
- [ ] 5.3 Rewrite the root `AGENTS.md` `## Rules` to the three always-on paths, join the OpenCode paragraph, run `python3 -I scripts/agent_rules.py pointers --write`; confirm 40 lines or fewer

## 6. Agent-file check (design §6)

- [ ] 6.1 Wiring check: the five hooks with the matchers the `hooked-rule-delivery` Host adapters requirement lists, no `SubagentStop`; stop predicate names against the engine registry; positive and negative tests
- [ ] 6.2 Delivery budget check over the listed triggers and the stop text, failing above 9,000 bytes; boundary tests at 9,000 and 9,001; the check passes on the tree after section 5
- [ ] 6.3 Layout checks: exactly three always-on `repo-*` rules in `.agents/rules/`, the moved files absent there (add the five to the check's `MOVED_RULES` map), the root listing of the three; update the rule-name list and the hook tuple in `test_check_agent_files.py`; tests; the check passes on the real tree

## 7. References and docs (design §5, §9)

- [ ] 7.1 `openspec/config.yaml`: repoint the six artifact rules and the archive guidance; add the separate apply entry that runs verify and finalize without asking
- [ ] 7.2 Repoint `openspec/schemas/superspec/schema.yaml` (three references) and `templates/verify.md`
- [ ] 7.3 Docs: `docs/superspec-workflow.md` (opening paragraph with current paths; the "always-on rule" wording near the audit checklist), `docs/openspec-workflow.md`; `docs/dev/specs/real-device-testing.mdx` only imports the main spec, so no edit is expected there; read `docs/opencode-rules.md` and `docs/agent-guidance.md` and edit them only where they name a moved rule as always-on
- [ ] 7.4 `docs/hook-delivered-rules.md`: `stops` and the registry, the `Stop` hook and `session.execution.succeeded`, per-trigger repeats, the stop gate, state version 2, the amended stale credit, the stop fail-open; record `--separate-git-dir` checkouts as unhandled by the predicate
- [ ] 7.5 Verification greps, each outside `openspec/changes/` and outside `openspec/specs/` (main specs change at sync): no roster model name outside the roster (including `.agents/hooked-rules/` and `docs/hook-delivered-rules.md`); no model or concrete project in `docs/superspec-workflow.md`; every procedure section a workflow rule cites exists; the closeout, review-gate and tracker-intake rules carry their read-the-section instruction at the opening (the closeout right after its lead paragraph); no reference to the old `.agents/rules/` paths of the moved files

## 8. Checks and live verification (design §10)

- [ ] 8.1 Run the checks of every node `scripts/components.py changed` selects (at least `scripts` and `docs`: `./gradlew scriptsTest`, `node --test scripts/opencode-hooked-rules/index.test.mjs`, the agent-file check and its unit tests, `openspec validate --all`)
- [ ] 8.2 Final live run in Claude Code of outcomes 1–6 of `hooked-rule-delivery`; record the version and results
- [ ] 8.3 Final live run in Claude Code of outcome 7 (stop gate, push gate, apply-to-finalize) on a disposable change; record results
- [ ] 8.4 Final live run in OpenCode of outcomes 1–7; record the version and results
- [ ] 8.5 If any outcome fails in a tool, stop and ask the owner (pointer-only or moments-only for that tool, or hold) before continuing
- [ ] 8.6 Update the verified tools and versions in `docs/hook-delivered-rules.md`; state what ran, on which tool and version, in the completion report and the PR
