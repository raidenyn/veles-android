Every unit test injects its clock, wait, runner, git reader and environment: no sleeps and no wall-clock reads
(`.agents/rules/repo-no-time-based-tests.md`). Verifier tests live in `scripts/tests/test_verify.py` (run by
`./gradlew scriptsTest`); drift-check tests live with the agent-file check in `docs/checks/`. Evidence from the spike,
the measurements and the real-session runs goes in this change's `spike.md`.

The rollout follows the design's Migration Plan in three phases, each a commit or commit series that leaves the
tree green: **A** the CLI, the table and the drift check, with the pre-push rule and `.claude/settings.json`
unchanged (sections 1–10); **B** measurement on real changes (section 11); **C** remembered passes, the hook and
the wiring (sections 12–15). Until phase C, pushes follow the current pre-push rule (the checks in each touched
node's `AGENTS.md`).

## 1. Spike and inventory (design D4, D2, Open Questions)

- [x] 1.1 Check whether vitest 4.1.12 offers a list-only related resolver (prints related test files without running them); record the command and its output in `spike.md`, or record that none exists and that the extension uses `name-map` plus `self` (design D4)
- [x] 1.2 Walk every node's `AGENTS.md`, its listed rules and its CI workflow steps; record per node which commands become `check`, `test`, `device`, `platform` or `excluded`, and which Android guards beyond raw-`Log` join the table. Every CI test or static check that runs on Linux MUST map to a table entry or to an aggregate entry that runs it (for example the tooling workflow's `node --test verify/test/*.test.mjs`); "CI only" is reserved for device, macOS/Windows, signing and release steps, and for destructive sequences that clean the repository's `build/` directory (such as the Rust build–`./gradlew clean`–`rust/scripts/assert-clean.sh` contract in `check-rust.yml`), which would delete the verifier's own state and lock under `build/verify/`; each is recorded with its reason. CI provisioning and setup steps the verifier represents as requirements or fix text rather than commands are listed under "CI only" too, with the requirement that stands for them

## 2. Docs and the Android guard first (design D13, D14; agent-guidance-layout)

- [x] 2.1 Document the commands 1.2 found missing from the node docs (for example `npm run test:browser`, and any Android guard that joins the table) and add "Run from" sentences or `cd` lines where a block's directory is ambiguous; every `AGENTS.md` stays within 40 lines
- [x] 2.2 `android/scripts/check-raw-log.sh`: the same greps, `::error::` lines and exit status as today's inline guard; behavioural tests in `scripts/tests/` running it on fixture trees: a clean tree (exit 0), a fully-qualified `android.util.Log`, a wildcard `android.util` import and a bare `Log.d/w/e/i/v` call (exit 1 with the line), the `LogSink.kt` exemption, and sources under both `main` and `debug`
- [x] 2.3 `check-android.yml` calls the script from the repository root; `verify/test/workflow-contracts.test.mjs` asserts the invocation, the root working directory and, by reading the script, the scan scope, with mutation cases for each; the Android rule fences `./gradlew spotlessCheck detekt lintDebug` under "Run from `android/`" and the guard call

## 3. Skeleton and the command table (design D1, D2; affected-node-verification "One command table")

- [x] 3.1 `scripts/verify.py` skeleton: module layout of design D1, `sys.path` insert, `components` and `agent_rules.discover_checkout` imports, argument parsing for every mode, injected `Clock`, `Wait`, `Runner`, `Git` and environment; extend the source-reading 3.8 check (as in `test_project_flow_py38.py`) to `verify.py` and `test_verify.py`
- [x] 3.2 `Table`: strict JSON load and the full D2 validation (nodes equal the component map, ids, references, kinds, strategy fields, `platform` fields, `inputs` or `"reuse": false` on commands and producers, declared tools of reusable steps, `cwd`, argv, placeholders, `fix` on every environment match); exit 2 naming the problem; a test for each rule
- [x] 3.3 Table content, Android, Rust and protocol: lint and the raw-`Log` guard as `check`s, unit tests with `related`, `connectedDebugAndroidTest` as `device`, the JDK, SDK and NDK requirements, `rustTest` with its stated limit, the protocol suites, `inputs` (Gradle user home) or `"reuse": false`, `excluded` lines with reasons
- [x] 3.4 Table content, web extension: format, lint, typecheck, unit, bundle and browser suites; the `rust.wasm` and `web-extension.build` producers; the Node, `node_modules/` (`stat` mode) and Playwright Chromium requirements; `within: package.json:scripts.test`; exclusions
- [x] 3.5 Table content, native bridge, installer, supply-chain, tooling and docs: the split of `bridgeInstall bridgeFormat bridgeLint bridgeTypecheck` (checks) from `bridgeTest` (test), the host's Python build-contract suite, macOS/Windows steps as `platform`, the supply-chain verifier with `env_exit`, the tooling and docs suites, the verifier's own documented lines as `excluded`

## 4. Selection (design D3; "Selection follows the component map…")

- [x] 4.1 `select()`: base resolution, the four NUL-delimited `--no-renames` reads, `unaccounted`, `owner_nodes`, reasons; selection errors with fixes; nothing-changed and only-unowned messages; tests for committed, staged, unstaged, untracked and deleted paths, both sides of a move, `web-extension/src/*`, `rust/`, a global path, an exact override, unaccounted, an unusable base

## 5. Plans and related tests (design D4, D5; "Related tests…", "Generated outputs…")

- [x] 5.1 `plan()`: ordering (steps without generated outputs first, then each consumer with its whole producer chain immediately before it), task and branch gates, branch-only, `device` and `platform` notes, failure isolation and the node-result precedence (`FAIL` over `NOT RUN`, details keep every unexpected `NOT RUN`); tests with fixture tables
- [x] 5.2 Strategies: `name-map` (self-targets; Android FQN discovery from existing test files), `self`, `crate` (Gradle test tasks), `files-arg` (per 1.1) and `within` for all; existing targets only; argv expansion of unusual names; a failing resolver is an unexpected `NOT RUN`, and a resolver the runner kills at its cutoff is a `timeout` (exit 1, its own repeat identity); tests for each spec scenario and for a resolver timeout in a real run
- [x] 5.3 `--list`: selection and plan only, resolvers allowed under a 60-second deadline, `targets unresolved: <reason>`; exit 0, or 2 on a selection error; tests
- [x] 5.4 Real-table plan regressions: `--branch --list` for `web-extension` shows unit, bundle and browser suites with `rust.wasm` then `web-extension.build` immediately before each of bundle and browser; `bridgeTest` is a test and the bridge checks are checks; `connectedDebugAndroidTest` is a device skip

## 6. Probes and the machine config (design D7, D8; "Requirements are probed…", "Machine tool config…")

- [x] 6.1 Resolvers `java-home`, `android-sdk`, `android-ndk` (first existing candidate, as `rust/build.gradle.kts`), `path-tool`, `rustup-toolchain`, `playwright-chromium` (honouring `VELES_TEST_CHROMIUM`); effective environment per step; tool and path diagnostics with the bounded candidate scan and the `config set` fix; tests with fixture filesystems
- [x] 6.2 `config set|show` at `<git-common-dir>/veles-verify/tools.json`: validation per key, atomic write under a lock, `show` output, the `local.properties` `sdk.dir` warning there and under the `android` node; tests including a linked-worktree fixture sharing the file and a JDK 17 refused
- [x] 6.3 `env_exit` and `env_patterns` classification with fix text; tests including the supply-chain exit 2

## 7. Running, the run lock and the report (design D9, D10; "Compact output, logs and exit codes")

- [x] 7.1 `Runner`: a process group per call, logs, tail (60 lines, 300 characters), cutoff at the earlier of the step limit and deadline − 45 s, `SIGTERM`/grace/`SIGKILL`, reap, `running.json` entries; tests with a fake process API
- [x] 7.2 Run lock (`flock`, tried once); orphan cleanup by the lock holder only, confirmed with `killpg(pgid, 0)`, else contention; state locks retried at most 10 s; tests
- [x] 7.3 `Report`: node lines, notes, `nothing planned`, failure blocks with re-run commands, the 8 KiB cap keeping every status line, `last-run.json`, run directories (newest 10), exit precedence 1 > 2 > 0; tests for "Failure summary", "Summary overflow", "Missing tool exit", "Nothing planned is not a pass"

## 8. The CLI end to end (all of the above)

- [x] 8.1 Wire `main()` from selection through plan, probes, producers, steps and report; end-to-end tests with a fake git, runner and filesystem: a passing run, a failing lint (exit 1 with the summary), a missing tool (exit 2 with the fix), "Formatting does not wait on WASM", "Producer failure", "Mixed failure and skip", "Rebuilt between consumers", a producer that exits 0 without creating its output (consumer unexpected `NOT RUN` with the requirement's fix), a timeout naming finished and unreached steps with the tail, and a contended run (exit 2)

## 9. The drift check (design D13; "The table cannot drift…", agent-guidance-layout)

- [x] 9.1 Add `shlex` to `STDLIB_ALLOWED` in `docs/checks/check_offline.py` and update its test
- [x] 9.2 `docs/checks/verify_table.py`, doc reading: shell fences, comments, continuations, `cd`, keywords and assignments, "Run from" sentences, the root default; tests
- [x] 9.3 Matching: the structural subset, forward and reverse matching with working directories, exclusions first, the Gradle task union per directory, producer `doc` ownership; positive and negative tests for each finding, including the spec scenarios
- [x] 9.4 Wire it into `check_agent_files.py`; make `scripts/verify-commands.json` a required override with owners `docs` and `tooling`; negative tests for an absent override and a wrong owner set

## 10. Phase A landing (design Migration Plan step 1)

- [x] 10.1 `.github/components.json`: `global-overrides["scripts/verify-commands.json"] = ["tooling", "docs"]`; CODEOWNERS entries for the new files; a test in `scripts/tests/test_components.py` that a change to only that file selects exactly `tooling` and `docs`; `python3 -I scripts/components.py check`
- [x] 10.2 Phase A is green: `./gradlew scriptsTest`, `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py'`, `node --test verify/test/*.test.mjs`, the checks of every node 2.x touched, and `python3 -I scripts/verify.py` and `--branch` on this branch; the pre-push rule and `.claude/settings.json` are unchanged

## 11. Phase B: measure (design Migration Plan step 2)

- [ ] 11.1 Run the task gate with the real verifier on real changes (an extension-only edit, a `rust/` edit, an Android edit, a docs edit), cold and warm; record the times in `spike.md`
- [ ] 11.2 If the numbers call for a budget other than 1,740 seconds, stop and ask the owner before changing the design and specs; otherwise record that the budget stands

## 12. Remembered passes (design D5, D6; "Remembered passes")

- [ ] 12.1 Generated-output capture before each consumer; disagreeing snapshots and links make the node non-reusable; tests
- [ ] 12.2 `Fingerprint`: canonical JSON with base, plan, entry, environment allowlist and resolved tools, `local.properties`, selecting paths via `owner_nodes` (index modes and blob ids, working-tree hashes, deletions), declared inputs (`content`; `stat` with device, inode, mode, size and `st_ctime_ns`; `{gradle_user_home}` and its non-reusable case), generated digests; source-side stability before and after; tests for each invalidation (edit, base change, config change, tool version change, removed output, replaced package, edited dependency, edit during the run)
- [ ] 12.3 `passes.json`: write after a stable task-gate pass, delete on `FAIL`/`NOT RUN`, never for a `"reuse": false` command or producer or in `--branch`; manual runs never read; tests
- [ ] 12.4 The hit path: re-probe, recompute against current outputs, skip the node's commands, record `PASS (remembered)` in `last-run.json`; tests for "Unchanged node is skipped" (silent when every node is remembered), for a mixed stop whose block reason lists a remembered node, and for a mixed stop at the repeat bound whose `systemMessage` lists it (one formatter for both)

## 13. The Stop hook (design D11, D12; verification-stop-hook)

Where design D12's outcome table and the spec differ on time charges, the spec governs: every admitted run that
started is charged its measured time, whatever its outcome; contention cancels without a charge.

- [ ] 13.1 Launcher and watchdog: stdin event (1 MiB cap), checkout discovery and common-dir comparison, the child in its own session with `--dispatched --deadline`, the deadline pipe, kill and reap of the direct child at the admitted deadline + 30 s or at 1,770 s; tests for same checkout, linked worktree, separate clone, non-checkout, missing verifier, invalid JSON, a child that never reports, and a child that reports an earlier admitted deadline and then stalls
- [ ] 13.2 Admission in `<git-common-dir>/veles-verify/hook-state.json`: expiry first (charged budget + 120 s), slots counting live reservations, the three-way budget minimum, limit notices with the `synopsis`, cancellation on contention; tests for "Session limit", "Worktree move keeps the allowance" and overlapping reservations
- [ ] 13.3 Settlement: measured time charged for every started run (tests for a no-work run and an internal-error run), clean reset of blocks, repeat, time and `synopsis`, a blocking run's block and repeat update, contention uncharged; tests for each outcome and "Nothing changed keeps the counters"
- [ ] 13.4 Repeat identity and bound (node, step id and outcome kind; or selection error kind with its paths or base; first in report order; a timeout after three failures of the same step is a new problem), and pruning to 50 sessions keeping live reservations; tests including "Fourth repeat passes"
- [ ] 13.5 Hook output: exit 0, stdout JSON only (block, `systemMessage`, silent), the reason's first line, stderr for internal errors, `stop_hook_active` ignored; tests
- [ ] 13.6 Hook end to end with fakes: "Lint failure blocks", "Missing tool blocks with its fix", "Command exceeds the budget" (finished, cut off and not reached steps with the tail), "Orphan from a killed run" (cleanup before planning), "Concurrent run" (notice, no node recorded as passed)

## 14. Phase C wiring (design Migration Plan step 3; hooked-rule-delivery, agent-guidance-layout)

- [ ] 14.1 One commit: the agent-file check's `Stop` exception (exactly the engine command with timeout 5, then the verify command with timeout 1800) with tests for "Stop holds the verify hook" and "Stop wiring drifts", and `.claude/settings.json` gaining the verify command; the rollback is reverting this commit, which removes both together
- [ ] 14.2 `.agents/hooked-rules/repo-pre-push-checks.md`: pointer and body name `python3 -I scripts/verify.py --branch` with the fallback wording; `python3 -I scripts/agent_rules.py pointers --write`; tests or check output showing the triggers are unchanged (`openspec instructions finalize` and the `git push` gate) and the delivery budget holds
- [ ] 14.3 `.agents/rules/scripts-verify-commands.md` (glob `scripts/verify-commands.json`), pointing to `docs/verify.md`, listed in `scripts/AGENTS.md` `## Rules`; the agent-file check accepts it; a scoping check that it matches `scripts/verify-commands.json` and not `scripts/verify.py`
- [ ] 14.4 `docs/verify.md` (kinds, entries, strategies, requirements and fix text, generated outputs, the machine config, the hook, "CI only" steps with reasons, the known limits with their workarounds); `CONTRIBUTING.md` and `scripts/AGENTS.md` name both gates

## 15. Verification and handoff

- [ ] 15.1 Run `./gradlew scriptsTest`, `python3 -I scripts/components.py check`, `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py'`, `node --test verify/test/*.test.mjs` and `python3 -I scripts/verify.py --branch`; all pass
- [ ] 15.2 Acceptance on this machine: a branch touching only `web-extension/src/` selects exactly `web-extension`, `protocol`, `supply-chain`; a `rust/` change also selects `android` and says why; an absent toolchain gives `NOT RUN` with its fix and exit 2
- [ ] 15.3 Real Claude Code session on the coding server with both `Stop` commands: a deliberately broken lint blocks with a readable summary and the fixed tree then passes; an absent tool blocks with its fix; a session moved into a linked worktree at a different revision runs that worktree's verifier; record the runs and measured hook times in `spike.md`
- [ ] 15.4 PR notes: no product behaviour changed (tooling only), what ran in 15.1–15.3 and on what, the measured times, and the known limits
- [ ] 15.5 Closeout handoff (not implementation): after the archive creates the main specs, add the site wrappers `docs/dev/specs/affected-node-verification.mdx` and `docs/dev/specs/verification-stop-hook.mdx` and their `docs.json` entries
