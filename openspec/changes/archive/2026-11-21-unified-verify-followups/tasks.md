Every unit test injects its clock, wait, runner, git reader and environment: no sleeps and no wall-clock reads
(`.agents/rules/repo-no-time-based-tests.md`). Python tests live in `scripts/tests/` (run by `./gradlew scriptsTest`)
and `docs/checks/` (run by `python3 -m unittest discover -s docs/checks -p 'test_*.py'`); plugin tests live in
`scripts/opencode-hooked-rules/index.test.mjs`. Spike evidence and real-session results go in this change's
`spike.md`.

Order: the spike first; then the proof (record, engine, rule); then the OpenCode host; then the table, the drift
check and the guidance cleanup; then docs and acceptance. Each section leaves the tree green. Once section 3 lands,
pushing this branch itself needs a branch pass, which is part of the acceptance.

## 1. Spike (design D5 S1)

- [x] 1.1 In a real OpenCode v2.3.2 session in a scratch checkout, with a throwaway plugin that resumes a session through `ctx.session.synthetic({resume: true})` a fixed number of turns after a turn end, send a new user message while the resume is pending and record what OpenCode does (queued after the current turn, rejected, or interleaved), with the observed event sequence, in `spike.md`; decide D5 as written or its fallback, and record the decision

## 2. The branch-pass record (design D2; affected-node-verification "A complete branch run writes a branch pass")

- [x] 2.1 `scripts/branch_pass.py`: path resolution from a working directory (`agent_rules.discover_checkout` semantics), the version-1 format, an atomic writer, `delete`, and `check(cwd, deadline)` returning `(ok, message)` with messages for no record, other HEAD (both short hashes), other base, unreadable record, git failure and deadline; stdlib only, Python 3.8+; extend the 3.8 source check to it; tests with a fixture git repository and an injected clock
- [x] 2.2 `verify.py --branch`: delete the record right after taking the run lock; record `git status --porcelain` and HEAD before selection and after the last step; write the record only for a complete run (exit 0, no `--deadline`, no `--base`, no `not_reached`, clean and same HEAD at both ends); tests for each spec scenario, including a contended run that leaves a concurrent pass untouched and a nothing-changed run that writes `nodes: []`

## 3. Proof-requiring gates and the pre-push rule (design D3; hooked-rule-delivery)

- [x] 3.1 Frontmatter: `requires` and `exempt` list keys in `scripts/agent_rules.py` (`LIST_KEYS`, `Rule`, `parse_rule`), the `PROOF_PREDICATES` registry with `branch-pass` bound to `branch_pass.check` imported from the engine's own `scripts/`, and the new errors (unknown proof, `requires` without `gates`, `exempt` without `requires`); parser tests, and agent-file check tests through the shared parser
- [x] 3.2 `_gate` for rules with `requires`: segment split at `;`, `&`, `|` and newlines; per-segment gate and exempt matching; proof run with the event `cwd` and a deadline 3.5 s after the event started; pass, or deny with the rule text plus each failing proof's message, recording the rule delivered; deny repeats; rules without `requires` keep deny-once; tests for every Proof-requiring gates scenario, including a fixture rule that gates another command with `requires` and a proof that misses its deadline
- [x] 3.3 `.agents/hooked-rules/repo-pre-push-checks.md`: `requires: ["branch-pass"]`, the anchored deletion `exempt` patterns, the new pointer and body (commit first, run `--branch` in the background and wait, fix every `FAIL` and unexpected `NOT RUN`, then push; no by-hand fallback); a table-driven test of the real rule over the push forms listed in design Risks (deletion forms exempt; plain, `--tags`, `-C <dir>`, mixed refspecs, chained and redirected forms gated), plus the three "Pre-push rule triggers" scenarios: a push without delivery denied every time, a push during finalize with a pass, and a pass made stale by a new commit
- [x] 3.4 Regenerate the root pointer block (`python3 -I scripts/agent_rules.py pointers --write`); the root `AGENTS.md` stays at 40 lines; the agent-file check passes

## 4. The OpenCode host (design D4, D5; verification-stop-hook)

- [x] 4.1 `verify.py`: `--hook` accepts `opencode`; the launcher passes the host to the dispatched child; hook state keys sessions `<host>:<session_id>` with `HOOK_STATE_VERSION` 2; the launcher handles SIGTERM by stopping its dispatched child and exiting 0; tests for the host key, a version-1 state file read as empty, and SIGTERM with an injected child
- [x] 4.2 Plugin: `verifiesInFlight` per-session slot, the verifier spawn (stdin event, `VERIFY_BUDGET_MS` 1 800 000), output mapping (block → `resume: true`, `systemMessage` → `resume: false`, anything else ignored), SIGTERM then SIGKILL after 5 s on budget expiry and dispose, and the S1 outcome (as written, or keep-and-send-at-next-turn-end); the engine's stop path unchanged; tests for every OpenCode turn-end verification scenario through the injected seams

## 5. The table, the drift check and the guidance (design D1; affected-node-verification, agent-guidance-layout)

- [x] 5.1 `docs/checks/verify_table.py`: drop the doc-coverage and entry-documentation checks; reject `doc` and `excluded`; add the one-way duplicate check over every shell fence of each node `AGENTS.md` and its listed rules (exact argv and cwd, or the task-only `./gradlew` subset ignoring value-less options such as `-q`; other extra arguments allowed); update `docs/checks/test_verify_table.py` for each scenario of "The table cannot drift from the docs"
- [x] 5.2 `scripts/verify.py` loader: remove `doc` and `excluded` from `_load_step`, `_load_excluded` and `_load_nodes` so either key is a table error naming it; remove both keys from `scripts/verify-commands.json`; migrate every table fixture that carries `doc` or `excluded` (`scripts/tests/test_verify_table.py`, `test_verify.py`, `test_verify_fingerprint.py`, `test_verify_plan.py`, `test_verify_hook.py`, and any under `docs/checks/`) in the same commit so `scriptsTest` stays green; tests for the new table errors
- [x] 5.3 Run the new check on the tree and remove exactly the lines it reports from the node `AGENTS.md` files and listed rules; drop a Build & Test section left empty with its "Run from" sentence; keep build, release, device, platform, producer and targeted lines
- [x] 5.4 Delete `.agents/rules/android-lint-before-push.md`; remove its line from `android/AGENTS.md` `## Rules`; reduce the `rust/AGENTS.md` and `protocol/AGENTS.md` sentences to "Android CI also runs for these changes"; update `docs/checks/test_verify_table.py` and any agent-file check fixture that names the rule; update `.agents/rules/scripts-verify-commands.md` for the one-way check
- [x] 5.5 Run `python3 docs/checks/check_agent_files.py --root .`, its unittest suites and `./gradlew scriptsTest`; all pass

## 6. Docs (design D6)

- [x] 6.1 `docs/verify.md`: the table fields without `doc`/`excluded`; the one-way duplicate check; the branch-pass record and its completeness rule; the OpenCode host; contention during a background branch run; move the `why` of each removed `excluded` entry that is recorded nowhere else into "CI only"; under Known limits, each with its workaround: the SIGKILLed launcher whose dispatched child runs to its deadline holding the lock, the step-group residual after `_stop_child`, and the contention during a background branch run (stop again after it finishes)
- [x] 6.2 `docs/opencode-rules.md`: replace "The verify Stop hook" with the D5 behaviour and the S1 result; `docs/hook-delivered-rules.md`: `requires`, `exempt`, segments, the proof registry, the repeat-deny semantics and the fail-open limits (host timeout, version skew, plain-terminal pushes)

## 7. Acceptance (verification-stop-hook "Real-session acceptance"; real-device-testing exemption)

- [x] 7.1 Real Claude Code session: a deliberately broken lint in a changed file blocks at the turn end with a readable summary and the fixed tree passes; `git push` without a branch pass is denied twice, and passes after a complete background `--branch` run on the committed tree; a ref-deletion push to a throwaway branch passes without a proof; an absent tool yields `NOT RUN` with its fix; a session moved into a linked worktree at a different revision runs that worktree's verifier
- [x] 7.2 Real OpenCode v2.3.2 session through the plugin: the same broken lint resumes the session with the summary and the fixed tree ends silently; a new user message sent during the verifier run behaves as S1 recorded; `git push` without a branch pass is denied and passes after a complete `--branch` run; a session moved into a linked worktree runs that worktree's verifier
- [x] 7.3 Record each run, what it ran on and the measured hook and branch-run times in `spike.md` for the PR description, with the statement that no product behaviour changed (tooling only, so the device and browser rule's exemption applies)
- [x] 7.4 After committing `spike.md` with the 7.3 results (the pass needs a clean tree), `python3 -I scripts/verify.py --branch` passes on the final committed tree, and this branch's own push goes through the new gate
