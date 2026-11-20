# Verify Affected Nodes and the Stop Hook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One local command, `python3 -I scripts/verify.py`, that selects the affected nodes and runs their checks
(task gate by default, `--branch` before a push), plus a Claude Code `Stop` hook that runs the task gate and blocks
while something the agent can fix is red.

**Architecture:** A single standard-library module `scripts/verify.py` (design D1) with pure planning functions and
injectable side-effect classes (`Git`, `Runner`, `Clock`, `Wait`, `Probe`, filesystem). The node → command table is
data (`scripts/verify-commands.json`); the agent-file check gains a drift check (`docs/checks/verify_table.py`). The
hook is the same script in `--hook claude` mode: a launcher/watchdog that runs the gate in a child of the active
checkout, with session state in `<git-common-dir>/veles-verify/`.

**Tech Stack:** Python 3.8+ standard library (`argparse`, `json`, `hashlib`, `subprocess`, `selectors`, `fcntl`,
`shlex`, `os`, `signal`), `unittest`; POSIX `sh` for the Android guard; Node `node:test` for the workflow contract
suite.

**Spec:** this change's `design.md` (HOW: decisions D1–D14 and Known limits) and `specs/` —
`affected-node-verification`, `verification-stop-hook`, `hooked-rule-delivery`, `agent-guidance-layout`,
`ci-pipeline`. `tasks.md` is the approved task list; every task below names the `tasks.md` ids it implements.

## Global Constraints

- Standard library only; Python 3.8 syntax and APIs (no `match`, no `str.removeprefix`, no `list[str]` outside
  annotations, no `zip(strict=)`); run as `python3 -I` (the script inserts its own directory into `sys.path`).
- Unit tests never sleep or read the wall clock: inject the clock, the wait, the runner, git and the environment.
- Exit codes (CLI): 1 on any `FAIL` or timeout; else 2 for usage, selection error, unexpected `NOT RUN` or
  contention; else 0. Hook mode always exits 0 and prints only JSON.
- Output caps: tail 60 lines, each 300 characters; summary 8,192 UTF-8 bytes; newest 10 run directories; hook reason
  starts "verify: the task gate found problems; fix them and stop again (attempt <n> of 3)".
- Hook numbers: settings timeout 1800 s; outer deadline launcher start + 1740 s; kill at admitted deadline + 30 s
  or 1770 s; step cutoff deadline − 45 s; grace 10 s; session cap 5 blocks and 3600 s; < 60 s left → no run;
  expiry start + budget + 120 s, charged budget + 120 s; repeat bound 3; 50 sessions kept; stdin cap 1 MiB.
- State paths: `build/verify/` (runs, `last-run.json`, `passes.json`, `running.json`, `lock`) and
  `<git-common-dir>/veles-verify/` (`tools.json`, `hook-state.json`).
- Commit subjects are Conventional Commits (all below pass `scripts/commitlint.py`).
- Phases (tasks.md preamble): A = Tasks 1–10 with the pre-push rule and `.claude/settings.json` unchanged; B =
  Task 11; C = Tasks 12–15. Every commit leaves `./gradlew scriptsTest` and the docs checks green. Until Phase C,
  pushes follow the current pre-push rule.

## Review Focus

- A path with spaces, quotes or a leading `-` in the changed set must reach a runner as one literal argv item →
  `test_argv_expansion_unusual_names` (Task 5).
- A changed file that is unreadable or not UTF-8 mid-hash must give "no hit", never a crash or a pass →
  `test_fingerprint_unreadable_file_is_no_hit` (Task 12).
- A repository with no `origin/master` must exit 2 with the `git fetch`/`--base` fix, not select every node →
  `test_select_no_origin_master` (Task 4).
- A hook event with an empty or missing `session_id` must fail open, never share another session's allowance →
  `test_hook_missing_session_id_fails_open` (Task 13).
- A test file deleted in the working tree but still in the index must not be passed as a target →
  `test_deleted_test_not_targeted` (Task 5).

---

## File structure

| File | Responsibility |
|---|---|
| `scripts/verify.py` | the verifier and the hook (D1 parts) |
| `scripts/verify-commands.json` | the command table (D2) |
| `scripts/tests/test_verify.py` | CLI end to end: the spec-named suite; it covers every CLI-observable scenario of `affected-node-verification` through `main()` |
| `scripts/tests/test_verify_table.py`, `_select.py`, `_plan.py`, `_probe.py`, `_run.py`, `_fingerprint.py`, `_hook.py` | unit suites per part |
| `scripts/tests/test_verify_py38.py` | source-reading 3.8 check over `verify.py` and `*verify*.py` tests (glob, like `test_project_flow_py38.py`) |
| `scripts/tests/verifyfake.py` | shared fakes |
| `scripts/tests/test_check_raw_log.py` | behaviour of the Android guard script |
| `android/scripts/check-raw-log.sh` | the raw-`Log` guard |
| `docs/checks/verify_table.py`, `docs/checks/test_verify_table.py` | drift check and its tests |
| `docs/verify.md` | maintainer and agent reference |
| `.agents/rules/scripts-verify-commands.md` | path-scoped guidance for the table |

## Shared types (defined in Task 3, used everywhere)

- `Outcome(kind: str, code: Optional[int] = None, tail: List[str] = (), fix: Optional[str] = None, error: Optional[str] = None, stdout: Optional[List[str]] = None, stderr: Optional[List[str]] = None, elapsed_s: float = 0.0)`
  with `kind` ∈ `ok`, `fail`, `timeout`, `not_run`, `not_reached`. Every resolver, probe, producer and step result is
  an `Outcome`; only `fail` and `timeout` give exit 1; `not_run` is an unexpected `NOT RUN` (exit 2) unless a note
  marks it expected. `not_reached` (the deadline passed before the step started) is reported but is neither a
  timeout nor blocking and does not change the exit code — the accepted known limit in design.md and the
  `verification-stop-hook` spec. Only a command the runner cut off is a `timeout`.
- `Context(git, runner, clock, wait, fs, env: Dict[str, str], repo: str, common_dir: str, run_dir: str,
  deadline: Optional[float], log: Callable[[str], None])`. `deadline` is absolute (injected clock seconds); `None` in
  the CLI unless `--deadline` is given.
- `Run = Callable[[List[str], str, Dict[str, str], str, float, bool], Outcome]` — argv, cwd, env, log path, cutoff
  time, `capture`; `Runner.run` has this signature and is the only way anything external is started. With
  `capture=True` (resolvers and version commands) stdout and stderr are each read in full, separately, into
  `Outcome.stdout` and `Outcome.stderr` (`List[str]`) and copied to the log; resolvers read only `stdout`, version
  probes read both (`java -version` prints to stderr). Otherwise both streams are merged into the log and the two
  fields are None.
- Log paths: `<run_dir>/<step id>.<n>.log`, `n` counting invocations of that step in the run (a producer run before
  two consumers has `rust.wasm.1.log` and `rust.wasm.2.log`); report entries name the invocation, repeat identities
  use the stable step id.

---

### Task 1: Spike and inventory (tasks 1.1, 1.2)

**Files:** Create: `openspec/changes/verify-affected-nodes-stop-hook/spike.md`

**Interfaces:** Produces `spike.md` §Inventory, one row per documented command or CI step: node, source (doc
line or workflow job/step), kind decision, **local argv and cwd that run it** (for an inline CI guard such as the
pairing-code scans: the documented command or the script that will hold it), doc, CI job, reason. §Vitest records
the resolver decision.

- [ ] **Step 1:** In `web-extension/`, test candidate list-only resolvers (`npx vitest list --filesOnly …`, `npx vitest related --run --reporter=json --passWithNoTests …`). A candidate qualifies only if, given explicit changed paths, it prints one existing test path per line (or JSON from which that list is read) **without executing any test**; prove it on a throwaway edit with a test that would fail if run.
- [ ] **Step 2:** Record the decision: the qualifying `resolve` argv with `{changed}`, or "none; extension uses `name-map` (`web-extension/src/**/<stem>.ts` → `web-extension/test/**/<stem>.test.ts`) plus `self`".
- [ ] **Step 3:** Fill §Inventory for every node from its `AGENTS.md` fences, listed rules and `.github/workflows/check-*.yml` steps, classified per tasks.md 1.2 ("CI only" only for device, macOS/Windows, signing, release, `build/`-cleaning sequences, and setup represented as requirements — named).
- [ ] **Step 4:** Commit: `git add openspec/changes/verify-affected-nodes-stop-hook/spike.md && git commit -m "docs(openspec): record the verify inventory spike"`

### Task 2: Docs and the guard scripts (tasks 2.1–2.3)

**Files:**
- Create: `android/scripts/check-raw-log.sh`, `android/scripts/check-pairing-code.sh`,
  `web-extension/scripts/check-pairing-code.sh`, `scripts/tests/test_check_raw_log.py`,
  `scripts/tests/test_check_pairing_code.py`
- Modify: `.github/workflows/check-android.yml` (the raw-`Log` and pairing-code steps),
  `.github/workflows/check-web-extension.yml` (the pairing-code step), `verify/test/workflow-contracts.test.mjs`
  (`assertAndroidCheckLayout` at :3536, its mutation loop at :3584–3601, and the web-extension lint contract at
  :3650–3670), `.agents/rules/android-lint-before-push.md`, `web-extension/AGENTS.md`, node `AGENTS.md` files
  §Inventory names

**Interfaces:** Produces `android/scripts/check-raw-log.sh` (no arguments, run from the repository root; exit 0
clean, 1 on a match). Its two greps are exactly today's:
`grep -rn "android.util.Log\|import android.util\.\*" android/app/src/main android/app/src/debug --include=*.kt | grep -v "LogSink.kt"`
and `grep -rnE "Log\.[dweiv]\(" android/app/src/main android/app/src/debug --include=*.kt | grep -v "LogSink.kt"`,
each followed by its existing `::error::` line. `android/scripts/check-pairing-code.sh` and
`web-extension/scripts/check-pairing-code.sh` hold today's inline pairing-code greps from `check-android.yml` and
`check-web-extension.yml` verbatim (same patterns, paths, `::error::` text and exit status), run from the
repository root. Any other inline Linux guard §Inventory lists gets a script the same way.

- [ ] **Step 1: Failing tests** in `test_check_raw_log.py` (fixture trees in a temp dir with the script copied in, `cwd` = fixture root): `test_clean_tree_exits_0` (exit 0, empty stdout); `test_fully_qualified_log_exits_1` and `test_wildcard_util_import_exits_1` (exit 1, stdout has the file path and "Raw android.util.Log usage or wildcard import found outside LogSink.kt. Use VelesLog instead."); `test_bare_log_call_exits_1` (exit 1, "Bare Log.d/w/e/i/v call found outside LogSink.kt. Use VelesLog instead."); `test_logsink_exempt` (both patterns only in `LogSink.kt` → exit 0); `test_debug_sources_scanned_by_both_greps` (one match of each kind only under `android/app/src/debug` → exit 1 with both messages).
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_check_raw_log.py' -v`; expect FAIL (script missing).
- [ ] **Step 3:** Write the script (`fail=0`, the two blocks, `exit $fail`); `chmod +x`.
- [ ] **Step 4:** Run the Step 2 command; expect PASS. Repeat Steps 1–4 for the pairing-code scripts in `test_check_pairing_code.py`: per script a clean tree (exit 0), one sink-plus-code line (exit 1 with the existing `::error::` text), and a code value without a sink (exit 0).
- [ ] **Step 5:** The three workflow steps become `run: <script>` with no `working-directory`. Raw-`Log` step becomes `run: android/scripts/check-raw-log.sh` with no `working-directory`. Change `assertAndroidCheckLayout(source)` to `assertAndroidCheckLayout(source, scriptText)` reading `android/scripts/check-raw-log.sh`: the step's run text equals `android/scripts/check-raw-log.sh`, its working directory is `null`, and **each** of the two grep lines in the script contains `android/app/src/main android/app/src/debug --include=*.kt` and `grep -v "LogSink.kt"`. Mutation loop: workflow mutations (changed invocation; a `working-directory`) and script mutations (first grep narrowed to `main`; second grep narrowed to `main`; `LogSink.kt` filter dropped), each passed separately and each asserted to throw. The two pairing-code assertions (Android :3566–3569, extension :3658–3659) read their scripts the same way: the Android script scans `android/app/src/debug --include=*.kt` and not `web-extension/src/debug`; the extension script inspects `PairingCode` under `web-extension/src/debug`; the "pairing guard dropped" mutation becomes "pairing step invocation changed", and every existing mutation that edits the removed inline Android blocks (the "web pairing guard back in android" case anchored on `exit $fail`) moves to the matching script's text, so none becomes a no-op under the suite's `assert.notEqual`.
- [ ] **Step 6:** Run `node --test verify/test/workflow-contracts.test.mjs`; expect PASS.
- [ ] **Step 6a:** Document the pairing-code scripts in fenced blocks: the Android one in the Android rule, the extension one in `web-extension/AGENTS.md` (within 40 lines).
- [ ] **Step 7:** Android rule: "Run from `android/`:" + fenced `./gradlew spotlessCheck detekt lintDebug`; "From the repository root:" + fenced `android/scripts/check-raw-log.sh`. Add §Inventory's missing commands and "Run from" sentences to node docs; `wc -l` of every `AGENTS.md` ≤ 40.
- [ ] **Step 8:** Run `python3 docs/checks/check_agent_files.py --root .` and `./gradlew scriptsTest`; expect exit 0.
- [ ] **Step 9:** Commit: `git commit -m "ci(android): run the Android source guards from scripts"` and `git commit -m "ci(web-extension): run the pairing-code guard from a script"` (scripts, workflows, contract test, tests), then `git commit -m "docs(agents): document the commands the verifier runs"` (rule and AGENTS.md edits).

### Task 3: Skeleton, shared types, table validation and table content (tasks 3.1–3.5)

**Files:** Create: `scripts/verify.py`, `scripts/verify-commands.json`, `scripts/tests/verifyfake.py`,
`scripts/tests/test_verify_table.py`, `scripts/tests/test_verify_py38.py`

**Interfaces:**
- Produces the Shared types above; `class TableError(ValueError)`; `load_table(path: str, cmap: dict) -> Table`
  with `Table(requirements: Dict[str, dict], producers: Dict[str, Step], nodes: Dict[str, NodeEntry])`,
  `NodeEntry(commands: List[Step], excluded: List[dict], none: Optional[str])`,
  `Step(id, kind, cwd, run: List[str], doc, requires: List[str], inputs: List[dict], reuse: bool, env_exit: List[dict], env_patterns: List[dict], platforms: List[str], ci: Optional[str], related: Optional[dict])` (producers: `kind="producer"`).
- `verifyfake.py`: `FakeClock(now)` with `.advance(s)`; `FakeWait(clock)`; `FakeGit(...)`;
  `FakeRunner(script: Dict[tuple, Outcome], stdout: Dict[tuple, List[str]])` recording calls and cutoffs;
  `FakeFs(files: Dict[str, bytes], modes, links)`; `make_context(**overrides) -> Context`.
- `main(argv: List[str], ctx: Context) -> int`.

- [ ] **Step 1: Failing tests** in `test_verify_table.py`, one per D2 rule, each asserting `TableError` whose message contains the offending id or node: `test_nodes_must_equal_map`, `test_duplicate_id`, `test_unknown_requirement_ref`, `test_unknown_kind`, `test_strategy_fields_exact`, `test_platform_needs_platforms_and_ci`, `test_inputs_or_reuse_false_on_command`, `test_inputs_or_reuse_false_on_producer`, `test_reusable_step_declares_tools`, `test_cwd_outside_repo`, `test_targets_placeholder_only_in_related`, `test_env_match_needs_fix`; plus `test_invalid_table_cli_exit_2` (`main([])` with a bad table returns 2 and prints the offending id, runs nothing) and `test_real_table_valid`. `test_verify_py38.py`: the checks of `test_project_flow_py38.py` over `SOURCES = [scripts/verify.py] + glob(scripts/tests/*verify*.py)`, asserting `verify.py` is listed.
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify_*.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement the skeleton (D1 sections, `argparse` for every mode, `--report-fd <n>` internal), strict JSON (`object_pairs_hook` rejecting duplicate keys, `parse_constant` rejecting `NaN`/`Infinity`) and `load_table`.
- [ ] **Step 4:** Write `scripts/verify-commands.json` from §Inventory in three passes (3.3 Android/Rust/protocol, with the raw-`Log` and pairing-code scripts as Android `check`s; 3.4 extension, with its pairing-code script as a `check` with `rust.wasm` and `web-extension.build`; 3.5 bridge/installer/supply-chain/tooling/docs with the `bridgeTest` split and the verifier's own lines excluded). `web-extension/node_modules/` input in `stat` mode; every `./gradlew` step declares `{gradle_user_home}/gradle.properties` and `{gradle_user_home}/init.d/`.
- [ ] **Step 5:** Run the Step 2 command; expect PASS. Run `./gradlew scriptsTest`; expect PASS.
- [ ] **Step 6:** Commit: `git commit -m "chore(scripts): add the verify command table and its validation"`

### Task 4: Selection (task 4.1)

**Files:** Modify: `scripts/verify.py`; Test: `scripts/tests/test_verify_select.py`

**Interfaces:** Produces `select(git: Git, cmap: dict, base: Optional[str]) -> Selection` with
`Selection(base: str, files: List[str], reasons: Dict[str, str], only_unowned: bool)`;
`class SelectionError(Exception)` with `.kind` ∈ `base`, `git`, `unaccounted`, `.paths: List[str]`, `.fix: str`,
`.identity -> tuple` (`("selection", kind, sha256 of sorted paths or the base)`).

- [ ] **Step 1: Failing tests:** `test_union_of_four_reads`, `test_deleted_path_counts`, `test_move_counts_both_sides`, `test_extension_src_selects_three` (exactly `{"web-extension","protocol","supply-chain"}`, each reason `web-extension/src/background.ts`), `test_rust_selects_android_with_reason` (`android` reason `rust/veles-crypto/src/lib.rs`), `test_global_path_selects_all`, `test_exact_override_wins` (`.github/workflows/check-android.yml` → exactly `android`, `tooling`, `installer`), `test_unaccounted_is_error` (kind `unaccounted`, message "not owned, global or unowned", fix names `.github/components.json`), `test_select_no_origin_master` (kind `base`, fix contains `git fetch origin master` and `--base`), `test_failed_git_read` (kind `git`, fix present), `test_nothing_changed`, `test_only_unowned` (the CLI form is in Task 8).
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify_select.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement `Git.diff_names(base)`, `.staged()`, `.unstaged()`, `.untracked()` with `-z --no-renames`, and `select` over `components.unaccounted` and `components.owner_nodes`.
- [ ] **Step 4:** Run; expect PASS. Commit: `git commit -m "chore(scripts): select verify nodes from the branch and the working tree"`

### Task 5: Plans, strategies and `--list` (tasks 5.1–5.4)

**Files:** Modify: `scripts/verify.py`; Test: `scripts/tests/test_verify_plan.py`

**Interfaces:** Produces `plan(table: Table, sel: Selection, gate: str, ctx: Context, resolve: Run) -> Plan`;
`Plan(nodes: Dict[str, NodePlan])`; `NodePlan(steps: List[PlannedStep], notes: List[str], unresolved: List[Tuple[str, Outcome]])`;
`PlannedStep(step: Step, argv: List[str], consumer_of: Optional[str])`;
`resolve_targets(test: Step, changed: List[str], ctx: Context, resolve: Run) -> Tuple[List[str], Optional[Outcome]]`
(targets, or the resolver's `Outcome` when it did not complete: `not_run` for a failure, `timeout` when the runner
cut it off). Resolvers go through `resolve` with cutoff `min(now + 60, ctx.deadline - 45)` and `capture=True`; targets
are read from `Outcome.stdout` only, never from the log or tail.

- [ ] **Step 1: Failing tests:** `test_order_independent_steps_first`, `test_chain_before_each_consumer` (bundle and browser each immediately preceded by `rust.wasm`, `web-extension.build`), `test_branch_gate_all_tests`, `test_task_gate_related_only`, `test_test_without_related_branch_only_note`, `test_device_and_platform_notes` (note text `NOT RUN (device test; real-device rule)` for `connectedDebugAndroidTest`; `NOT RUN (macOS only; CI job <job>)` for a macOS step), `test_name_map_self_target` (`scripts/tests/test_components.py` → that file only), `test_android_fqn_from_existing_file` (argv contains `--tests` `me.nagaev.veles.otp.handlers.RegexMessageHandlerTest`, cwd `android`), `test_crate_uses_gradle_task`, `test_within_filters_all_strategies`, `test_none_related_note` (`tests: none related (web-extension/eslint.config.js)`), `test_resolver_failure_unexpected_not_run`, `test_resolver_timeout_outcome` (fake runner returns `timeout` → `Outcome.kind == "timeout"`, no narrowed step), `test_argv_expansion_unusual_names` (`"a b.ts"`, `"-x.ts"` and `it's \"q\".ts` each one argv item, after a `--` separator where the run template has one), `test_deleted_test_not_targeted`, `test_list_unresolved_targets` (`--list` prints `targets unresolved: <reason>`, exit 0, no non-resolver call), `test_real_table_extension_branch_plan`, `test_real_table_bridge_split`, `test_real_table_connected_is_device`.
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify_plan.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement `plan`, the four strategies, `within` (`package.json:scripts.test`: the test files named in that script), Android FQN discovery (walk `android/app/src/test/`, read the `package` line), and `--list` printing.
- [ ] **Step 4:** Run; expect PASS. Commit: `git commit -m "chore(scripts): plan verify steps and related tests"`

### Task 6: Probes and the machine config (tasks 6.1–6.3)

**Files:** Modify: `scripts/verify.py`; Test: `scripts/tests/test_verify_probe.py`

**Interfaces:** Produces `effective_env(inherited: Dict[str, str], config: dict) -> Dict[str, str]`;
`probe(req_id: str, req: dict, ctx: Context, env: Dict[str, str], run: Run) -> Tuple[Outcome, Optional[str]]`
(the `Outcome` is `ok` or `not_run` with `fix`; the string is `"<real path>@<version output>"` for the fingerprint;
version commands go through `run` under the deadline, a cut-off probe is `timeout`);
`Config(common_dir)` with `.load() -> dict`, `.set(key, value) -> None` (raises `ConfigError`), `.show() -> str`;
`classify_env_failure(step: Step, outcome: Outcome, log_path: str) -> Outcome` (turns a matching `fail` into
`not_run` with the fix; `env_patterns` are matched against the whole log, read line by line, not only the tail).

- [ ] **Step 1: Failing tests:** `test_java_home_unset_message` (message contains `.java-version`, `21.0.12`, `/usr/lib/jvm/java-21-openjdk-amd64` and `python3 -I scripts/verify.py config set java_home /usr/lib/jvm/java-21-openjdk-amd64`), `test_several_candidates_ask_user` ("ask the user which one this project uses"), `test_android_sdk_prefers_local_properties`, `test_ndk_first_existing_no_fallthrough`, `test_path_tool_min_and_exact`, `test_rustup_toolchain_in_file_dir`, `test_playwright_honours_env`, `test_path_requirement_message` (path and fix text only, no `config set`), `test_probe_timeout_outcome`, `test_java_version_read_from_stderr`, `test_config_shared_across_worktrees`, `test_config_refuses_jdk17` (CLI `config set java_home <jdk17>` exits 2, message names `.java-version`, `tools.json` unchanged), `test_config_atomic_write`, `test_sdk_dir_warning_in_show_and_run`, `test_env_exit_supply_chain` (exit 2 → `not_run` with its fix), `test_env_pattern_match`, `test_env_pattern_before_long_output` (the diagnostic is followed by 500 lines, so it is outside the tail, and still matches).
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify_probe.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement the six resolvers (D7 table), the candidate scan (≤ 5 per root), messages, `Config` with `os.replace` under an `fcntl` lock, `classify_env_failure`.
- [ ] **Step 4:** Run; expect PASS. Commit: `git commit -m "chore(scripts): probe verify requirements and the machine config"`

### Task 7: Runner, run lock and report (tasks 7.1–7.3)

**Files:** Modify: `scripts/verify.py`; Test: `scripts/tests/test_verify_run.py`

**Interfaces:** Produces `Runner.run` (the `Run` signature); `RunLock(path).try_acquire() -> bool`;
`cleanup_orphans(ctx: Context, run_id: str) -> bool` (False → contention); `NodeResult(status: str, reason: Optional[str], notes: List[str], failures: List[Tuple[str, Outcome, str]])` (step id, outcome, re-run command);
`render(results: Dict[str, NodeResult], errors: List[str], cap: int = 8192) -> str`;
`exit_code(results, errors) -> int`; `write_last_run(ctx, results, steps: List[dict]) -> None` (each step dict has
`id`, `status`, `elapsed_s`, `rerun`, `log`).

- [ ] **Step 1: Failing tests:** `test_tail_60_lines_300_chars`, `test_cutoff_45s_before_deadline`, `test_term_grace_kill_sequence` (fake process: TERM, advance 10 s, KILL, reaped), `test_running_json_recorded_and_cleared`, `test_lock_tried_once`, `test_orphans_killed_and_confirmed`, `test_orphan_alive_after_15s_is_contention`, `test_state_lock_retry_10s`, `test_failure_summary` (500 short lines → the last 60, `cd web-extension && npm run lint`, the log path, exit 1), `test_summary_overflow_keeps_status_lines` (three failing nodes, `len(text.encode("utf-8")) <= 8192`, every node's status line present, a line containing `build/verify/last-run.json`), `test_missing_tool_exit_2`, `test_nothing_planned_not_pass` (`protocol: NOT RUN (nothing planned: …)`, exit unaffected), `test_fail_precedence_over_not_run`, `test_runs_pruned_to_10`, `test_last_run_json_shape` (version 1, per-step `elapsed_s`).
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify_run.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement with `subprocess.Popen(start_new_session=True, stdin=DEVNULL, stdout=log, stderr=STDOUT)`, polling through `ctx.wait`; `os.killpg`; `fcntl.flock(LOCK_EX | LOCK_NB)`; D10 rendering and exit precedence.
- [ ] **Step 4:** Run; expect PASS. Commit: `git commit -m "chore(scripts): run verify steps under a deadline and report them"`

### Task 8: CLI end to end (task 8.1)

**Files:** Modify: `scripts/verify.py`; Test: `scripts/tests/test_verify.py`

**Interfaces:** Produces `run_gate(gate: str, base: Optional[str], ctx: Context, table: Table, cmap: dict) -> RunOutcome`
with `RunOutcome(results: Dict[str, NodeResult], errors: List[str], selection: Optional[Selection], plan: Optional[Plan], contended: bool, problems: List[tuple], incomplete: bool)`
(`problems` = repeat identities in report order: `(node, step id, outcome kind)` or a selection identity).
**Order inside `run_gate` (fixed):** take the run lock (contended → return) → `cleanup_orphans` (False → contended)
→ `select` → `plan` (resolvers) → per node in table order, per step: probe its requirements, run, classify → stop
the node at a command `FAIL`. **Launch cutoff:** no step, probe or resolver starts once `deadline - 45` has passed;
it and every later step are `not_reached` (reported, not a problem) → `write_last_run` → release the lock.
`RunOutcome.incomplete: bool` is True when any step is `not_reached`; a node with a `not_reached` step is never
recorded as a pass, and the hook settles an incomplete run as `incomplete` (below) — unless the run also has a
blocking problem (`FAIL`, `timeout`, unexpected `NOT RUN`), which always wins: the stop blocks and lists the steps not
reached in its reason. `--list` takes the same run lock
(tried once; held → exit 2 "verification already running") and runs `cleanup_orphans` before any resolver, so every
runner call and every `running.json` write happens under the lock. `main` renders and
returns `exit_code`.

- [ ] **Step 1: Failing tests** (real table; fake git, runner and filesystem): `test_passing_run_exit_0`, `test_lint_failure_exit_1`, `test_missing_tool_exit_2`, `test_formatting_does_not_wait_on_wasm`, `test_producer_failure`, `test_mixed_failure_and_skip` (Android lint `NOT RUN` without JDK 21, raw-`Log` guard fails → `FAIL`, details list the lint skip with its fix, exit 1), `test_rebuilt_between_consumers`, `test_producer_without_output` (consumer `NOT RUN` with the requirement's fix), `test_timeout_names_finished_and_unreached` (the cut-off step is `timeout`, later steps `not_reached`), `test_deadline_between_steps_not_blocking` (deadline passes between two steps: no `timeout` problem, later steps `not_reached`, exit code unaffected, `incomplete` True), `test_no_launch_inside_reserve` (no step starts after `deadline - 45`), `test_selection_error_exit_2_runs_nothing` (fake runner records no call), `test_list_takes_run_lock` (a held lock → exit 2, no resolver call), `test_list_selection_error` (exit 2, prints the fix, prints no node line), `test_resolver_timeout_end_to_end` (a fixture table whose extension unit test uses `files-arg`, whatever Task 1 chose; the resolver times out → exit 1, no narrowed vitest call, `problems[0] == ("web-extension", "<test id>", "timeout")`), `test_contended_exit_2`, `test_device_note_in_output`, plus the CLI forms of every `affected-node-verification` scenario not covered above (task gate narrows tests, branch gate runs every Linux test, nothing changed, no related test).
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement `run_gate` in the order above and wire `main`; `last-run.json` gains a top-level `elapsed_s` measured around the whole invocation.
- [ ] **Step 3a:** Add `test_producer_invocations_logged_separately` (two consumers → `rust.wasm.1.log` and `rust.wasm.2.log`, both in the report).
- [ ] **Step 4:** Run; expect PASS. Commit: `git commit -m "chore(scripts): run the verify gates end to end"`

### Task 9: The drift check and its routing (tasks 9.1–9.4, 10.1)

**Files:** Create: `docs/checks/verify_table.py`, `docs/checks/test_verify_table.py`; Modify:
`docs/checks/check_offline.py` (`STDLIB_ALLOWED` gains `"shlex"`), `docs/checks/test_check_offline.py`,
`docs/checks/check_agent_files.py` (call `verify_table.findings(root)`; `scripts/verify-commands.json` in the
required overrides with owners `["docs", "tooling"]`), `docs/checks/test_check_agent_files.py` (the shared clean
fixture built by `make_repo` gains a minimal valid `scripts/verify-commands.json`, the docs it references, the
override, and in its component-map builder (:419) a `nodes` map naming exactly the fixture table's nodes), `.github/components.json`, `.github/CODEOWNERS`, `scripts/tests/test_components.py`

**Interfaces:** Produces `read_doc_commands(text: str, doc: str) -> List[DocCommand]` with
`DocCommand(argv: List[str], cwd: str, line: str)`; `findings(root: str) -> List[Finding]` (the checker's
`Finding(severity, path, message)`).

- [ ] **Step 1: Failing tests:** reading — `test_only_shell_fences`, `test_comments_stripped`, `test_continuation_joined`, `test_cd_sets_cwd`, `test_keywords_and_assignments_skipped`, `test_run_from_sentence`, `test_root_default`; matching — `test_structural_subset`, `test_forward_exact_argv_and_cwd`, `test_forward_gradle_task_subset`, `test_reverse_missing_entry_message` (message contains `npm run test:e2e` and "add it to scripts/verify-commands.json or exclude it"), `test_wrong_working_directory` (message names `.` and `android`), `test_exclusion_first` (`./gradlew installGitCliff`), `test_gradle_union_per_directory`, `test_argument_line_needs_exact_entry` (a `--tests` example), `test_producer_doc_ownership`; checker — `test_override_absent` ("has no entry in .github/components.json global-overrides"), `test_override_wrong_owners`, `test_clean_fixture_has_no_findings` (existing clean-tree tests still pass on the migrated fixture); offline — `test_shlex_allowed`; components — `test_verify_table_only_selects_tooling_and_docs` (`affected(["scripts/verify-commands.json"], CMAP)` true exactly for `tooling` and `docs`).
- [ ] **Step 2:** Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` and `python3 -I -m unittest discover -s scripts/tests -p 'test_components.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement `verify_table.py` with allowlisted imports only (the structural subset is reimplemented, not imported from `scripts/`); add `"scripts/verify-commands.json": ["tooling", "docs"]` to `global-overrides`; CODEOWNERS lines for `scripts/verify.py`, `scripts/verify-commands.json`, `docs/checks/verify_table.py`, `android/scripts/check-raw-log.sh`, `android/scripts/check-pairing-code.sh`, `web-extension/scripts/check-pairing-code.sh` with their neighbours' owners.
- [ ] **Step 4:** Run the Step 2 commands, `python3 docs/checks/check_offline.py --root .`, `python3 docs/checks/check_agent_files.py --root .` and `python3 -I scripts/components.py check`; expect PASS.
- [ ] **Step 5:** Commit: `git commit -m "docs(docs-check): check the verify table against the node docs"`

### Task 10: Phase A landing (task 10.2)

- [ ] **Step 1:** Run `./gradlew scriptsTest`, `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py'`, `node --test verify/test/*.test.mjs`, the checks of every node Task 2 touched (per their `AGENTS.md`), `python3 -I scripts/verify.py` and `python3 -I scripts/verify.py --branch`; expect exit 0 (verify may exit 2 only for a missing tool: apply its printed fix, record it in `spike.md`, re-run). `.claude/settings.json` and the pre-push rule are unchanged (`git diff origin/master -- .claude/settings.json .agents/hooked-rules/repo-pre-push-checks.md` empty).

### Task 11: Phase B — measure (tasks 11.1, 11.2)

**Files:** Modify: `spike.md`

- [ ] **Step 1:** Let `V` be the current `HEAD` (it carries the verifier). Per scenario — one scratch commit on `V` touching only `web-extension/src/background.ts`; `rust/veles-crypto/src/lib.rs`; an `android/app/src/main/` Kotlin file; or `docs/ci.md` — create a **fresh** disposable worktree (`git worktree add --detach ../verify-scratch-<n> V`), provision it (`npm ci` in `web-extension/` and `npm ci --ignore-scripts --prefix verify`, as their `AGENTS.md` say), commit the scratch edit, then run `python3 -I scripts/verify.py --base V`: **cold** = first run in that fresh worktree after `./gradlew --stop` and `(cd android && ./gradlew --stop)` (no build outputs, no daemons); **warm** = the same command again in the same worktree. Record a time only from a run that exits 0; after a failed cold attempt, fix the environment, remove that worktree and repeat the cold run in a new one; read the top-level and per-step `elapsed_s` from `build/verify/last-run.json`. Remove each worktree with `git worktree remove --force ../verify-scratch-<n>`.
- [ ] **Step 2:** Record a table (scenario, nodes, cold total s, warm total s, slowest step) in `spike.md`. If any cold total exceeds 1,695 s (the 1,740-s budget less the 45-s reserve), stop and ask the owner; otherwise write "budget stands". Commit: `git commit -m "docs(openspec): record verify task-gate timings"`

### Task 12: Remembered passes (tasks 12.1–12.4)

**Files:** Modify: `scripts/verify.py`; Test: `scripts/tests/test_verify_fingerprint.py`

**Interfaces:**
- Consumes `Selection`, `Plan`, `Table`, `Context`, `probe` results.
- Produces `FingerprintInput(gate, node, base, changed: List[str], node_plan: NodePlan, entry_digest: str, env: Dict[str, str], config_digest: Optional[str], resolved: Dict[str, str], local_properties: Optional[str], generated: Dict[str, str])`;
  `fingerprint(fi: FingerprintInput, ctx: Context) -> Optional[str]` (None = not reusable; unreadable → None);
  `snapshot_tree(path: str, ctx: Context) -> Optional[str]`;
  `Passes(path)` with `.get(gate, node) -> Optional[str]`, `.put(gate, node, digest) -> None`, `.drop(gate, node) -> None`;
  `remembered(node, gate, sel, plan, table, ctx) -> Tuple[bool, Optional[Tuple[str, Outcome]]]` — the hook-only lookup:
  re-probe the node's requirements, build the input with generated digests taken from the outputs as they are now,
  compare with `Passes.get`. A probe the runner cut off returns `(False, (<step id whose requirement it probed>, <timeout Outcome>))`,
  which `run_gate` reports as that step's blocking `timeout` with identity `(node, step id, "timeout")`; a fingerprint read that could not finish is `(False, None)`, a plain miss.
- `run_gate` gains `use_passes: bool` (True only in hook mode): remembered nodes are skipped with
  `NodeResult("PASS (remembered)", …)`; after a task-gate pass it recomputes the source-side key from **fresh
  observations** (`select` re-run: the four git reads and `git ls-files -s -z` again, then the walks) and calls `put`
  only when it equals the pre-run key and the node has no `not_reached` step; on `FAIL`/`NOT RUN` it calls `drop`; `--branch` never calls `put`.

- [ ] **Step 1: Failing tests:** `test_key_changes_on_edit`, `test_key_changes_on_base`, `test_key_changes_on_config`, `test_key_changes_on_tool_version`, `test_removed_output_is_missing_requirement`, `test_replaced_package_invalidates`, `test_edited_dependency_ctime_invalidates`, `test_edit_during_run_no_record`, `test_clean_tracked_file_edited_during_run_no_record`, `test_new_selecting_file_during_run_no_record`, `test_gradle_user_home_resolved`, `test_gradle_home_in_options_not_reusable`, `test_snapshots_disagree_not_reusable`, `test_link_in_output_not_reusable`, `test_reuse_false_producer_not_recorded`, `test_record_dropped_on_fail`, `test_record_dropped_on_not_run`, `test_incomplete_node_not_recorded` (lint passes, tests `not_reached` → no record), `test_probe_timeout_on_lookup_blocks` (identity `(node, step id, "timeout")`), `test_branch_never_writes`, `test_manual_never_reads`, `test_fingerprint_unreadable_file_is_no_hit`, `test_unchanged_node_skipped` (through `run_gate(use_passes=True)`: none of the node's commands or producers run, probes may; its `NodeResult` and `last-run.json` say `PASS (remembered)`); the hook-output forms are in Task 13.
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify_fingerprint.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement the D6 document with `json.dumps(sort_keys=True, separators=(",", ":"))`, `git ls-files -s -z` for index modes and blobs, `os.lstat` for `stat` inputs (`st_dev`, `st_ino`, `st_mode`, `st_size`, `st_ctime_ns`), snapshots before each consumer, `Passes`, `remembered`, and the `run_gate` changes.
- [ ] **Step 4:** Run this suite and `test_verify.py`; expect PASS. Commit: `git commit -m "chore(scripts): remember verify passes by fingerprint"`

### Task 13: The Stop hook (tasks 13.1–13.6)

**Files:** Modify: `scripts/verify.py`; Test: `scripts/tests/test_verify_hook.py`

**Interfaces:**
- `read_event(stream, cap: int = 1048576) -> Optional[dict]` — reads at most `cap + 1` bytes; more than `cap`,
  invalid JSON, or no non-empty `session_id`/`cwd` → None (fail open).
- `launcher(event_bytes: bytes, ctx: Context, spawn: Callable) -> int` (always 0; writes the relayed stdout). It
  creates a pipe, passes its write end to the child with `pass_fds=(w,)` **and** `--report-fd <w>` on the child's
  argv, closes its own copy of `w`, and waits with `selectors.DefaultSelector` on the read end and the child's
  stdout (both non-blocking), so it never blocks on either; the first line from the pipe is the admitted deadline. The event goes to the
  child's stdin through the same selector (non-blocking writes, `EVENT_WRITE`), so a child that never reads cannot
  stall the launcher before its kill time.
- `gate(event: dict, deadline: float, report_fd: Optional[int], ctx: Context) -> HookAnswer` with
  `HookAnswer(kind: str, text: Optional[str])`, `kind` ∈ `silent`, `block`, `notice`; writes `"<deadline>\n"` to
  `report_fd` right after admission, then closes it.
- `HookState(common_dir: str, clock)` with
  `.admit(session_id: str, outer_deadline: float) -> Admission(ok: bool, reservation: Optional[str], deadline: Optional[float], notice: Optional[str])` and
  `.settle(session_id: str, reservation: str, outcome: str, elapsed_s: float, identity: Optional[tuple], summary: Optional[str]) -> HookAnswer`
  (`outcome` ∈ `block`, `clean`, `incomplete`, `no_work`, `internal_error`, `contention`; `incomplete`: time charged,
  blocks, repeat and `synopsis` unchanged, a `systemMessage` naming the steps not reached; never `clean`). Settlement follows the spec (tasks.md
  §13 note): every started run is charged its measured time; contention cancels without a charge.
- `gate` calls `run_gate(..., use_passes=True)` with `deadline`; `main` in hook mode reads stdin through
  `read_event` and catches every exception (one stderr line, empty stdout, exit 0).

- [ ] **Step 1: Failing tests:** launcher — `test_same_checkout_spawns_child`, `test_linked_worktree_runs_its_verifier`, `test_separate_clone_silent`, `test_non_checkout_silent`, `test_missing_verifier_silent`, `test_invalid_json_fails_open`, `test_oversized_stdin_fails_open` (through `main` with a 1 MiB + 1 byte stream), `test_hook_missing_session_id_fails_open`, `test_report_fd_passed_on_argv`, `test_child_never_reports_killed_at_1770`, `test_child_reports_deadline_killed_30s_after`, `test_launcher_never_blocks_on_child_output`, `test_child_never_reads_large_event_killed_at_1770` (a valid 1 MiB event larger than the pipe buffer); admission — `test_expiry_charged_budget_plus_120`, `test_slots_count_live_reservations`, `test_budget_three_way_minimum`, `test_session_limit_notice_with_synopsis`, `test_less_than_60s_no_run`, `test_worktree_move_keeps_allowance`, `test_contention_cancels_uncharged`; settlement — `test_no_work_run_charged_counters_kept`, `test_internal_error_charged`, `test_clean_resets_all_and_synopsis`, `test_block_charges_and_sets_repeat`; repeat/pruning — `test_fourth_repeat_passes`, `test_timeout_after_failures_is_new_problem`, `test_selection_error_identity`, `test_prune_keeps_live`; output — `test_stdout_json_only_exit_0`, `test_reason_first_line` (starts with "verify: the task gate found problems; fix them and stop again (attempt 1 of 3)"), `test_internal_error_one_stderr_line` (stdout empty, exactly one stderr line, exit 0), `test_stop_hook_active_ignored`, `test_all_remembered_stop_silent`, `test_mixed_block_lists_remembered`, `test_repeat_bound_notice_lists_remembered` (one formatter for block and notice); end to end — `test_lint_failure_blocks`, `test_missing_tool_blocks_with_fix`, `test_command_exceeds_budget` (reason names the timed-out step, the finished and the not-reached steps, and the tail), `test_deadline_between_steps_does_not_block` (an `incomplete` notice, counters not reset), `test_blocking_wins_over_incomplete` (a `FAIL` then `not_reached` steps → block listing both), `test_incomplete_notice_lists_remembered` (shared formatter), `test_orphan_cleanup_before_planning`, `test_concurrent_run_notice_no_pass` (`systemMessage` "verification already running; nothing was checked", no `passes.json` write).
- [ ] **Step 2:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_verify_hook.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement `read_event`, the launcher (`agent_rules.discover_checkout`, `os.path.realpath` comparison, `subprocess.Popen([sys.executable, "-I", target_script, "--hook", "claude", "--dispatched", "--deadline", str(outer), "--report-fd", str(w)], start_new_session=True, pass_fds=(w,), stdin=PIPE, stdout=PIPE)`, selector loop, kill and reap), `HookState` under an `fcntl` lock with the D12 JSON, and `gate`.
- [ ] **Step 4:** Run; expect PASS. Commit: `git commit -m "chore(scripts): add the verify Stop hook"`

### Task 14: Phase C wiring (tasks 14.1–14.4)

**Files:** Modify: `docs/checks/check_agent_files.py` (`_hook_findings`), `docs/checks/test_check_agent_files.py`
(`hooks_block()` at :334 gives `Stop` the two commands; new tests), `.claude/settings.json`,
`.agents/hooked-rules/repo-pre-push-checks.md`, `AGENTS.md` (regenerated block), `scripts/AGENTS.md`,
`CONTRIBUTING.md`; Create: `.agents/rules/scripts-verify-commands.md`, `docs/verify.md`

- [ ] **Step 1: Failing tests** in `test_check_agent_files.py`: `test_stop_holds_verify_hook` (otherwise valid fixture, no hook-wiring finding), `test_stop_without_verify`, `test_stop_verify_first`, `test_stop_third_command`, `test_stop_wrong_timeout`, `test_other_event_second_command`; update `hooks_block()` so the existing clean and mutation tests use the two-command `Stop`.
- [ ] **Step 2:** Run `python3 -m unittest discover -s docs/checks -p 'test_check_agent_files.py' -v`; expect FAIL.
- [ ] **Step 3:** Implement the `Stop` exception and add to `.claude/settings.json` `Stop[0].hooks` the second entry `{"type": "command", "command": "python3 -I \"$CLAUDE_PROJECT_DIR/scripts/verify.py\" --hook claude || true", "timeout": 1800}`.
- [ ] **Step 4:** Run the Step 2 command, `python3 -m unittest discover -s docs/checks -p 'test_*.py'` and `python3 docs/checks/check_agent_files.py --root .`; expect PASS. Commit (the rollback unit: checker exception, fixture and settings together): `git commit -m "chore(agents): wire the verify Stop hook"`
- [ ] **Step 5:** Pre-push rule: pointer "Before `git push`, run `python3 -I scripts/verify.py --branch` (the node `AGENTS.md` lists where it cannot run); delivered at `openspec instructions finalize` and before the push; read this file then if it was not."; body: the command, then today's fallback text; frontmatter `commands` and `gates` unchanged. Run `python3 -I scripts/agent_rules.py pointers --write`; `wc -l AGENTS.md` ≤ 40.
- [ ] **Step 6:** Add two tests to `scripts/tests/test_agent_rules.py` with the real rule, each in its own event context as the existing gate tests do: `test_pre_push_rule_denies_push_before_delivery` (`git push origin feature` denied once with the rule text, then passes on re-run) and `test_pre_push_rule_delivered_at_finalize` (after a successful `openspec instructions finalize`, `git push` is not denied).
- [ ] **Step 7:** `.agents/rules/scripts-verify-commands.md`: frontmatter `trigger: glob`, `globs:` and `paths:` both `["scripts/verify-commands.json"]`, body pointing to `docs/verify.md`; list it in `scripts/AGENTS.md` `## Rules`; add `test_scripts_rule_scope` to `test_check_agent_files.py` (its patterns match `scripts/verify-commands.json` and not `scripts/verify.py`).
- [ ] **Step 8:** Write `docs/verify.md` (kinds, entries, strategies, requirements and fix text, generated outputs, the machine config, the hook, "CI only" with reasons from `spike.md`, the Known limits with workarounds); mention both gates in `CONTRIBUTING.md` and `scripts/AGENTS.md` Build & Test (those lines are `excluded` in the table).
- [ ] **Step 9:** Run `python3 -I -m unittest discover -s scripts/tests -p 'test_agent_rules.py' -v`, `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`, `python3 docs/checks/check_agent_files.py --root .` (includes `check_budget`) and `python3 docs/checks/check_offline.py --root .`; expect PASS. Commit: `git commit -m "docs(agents): point the pre-push rule at verify.py --branch"`

### Task 15: Verification and handoff (tasks 15.1–15.5)

**Files:** Modify: `spike.md`

- [ ] **Step 1:** Run `./gradlew scriptsTest`, `python3 -I scripts/components.py check`, `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py'`, `node --test verify/test/*.test.mjs`, `python3 -I scripts/verify.py --branch`; expect exit 0 each.
- [ ] **Step 2:** With `V` = the current `HEAD`, in a disposable worktree (`git worktree add --detach ../verify-scratch V`, removed afterwards), scratch commits on `V` checked with `--base V`: only `web-extension/src/background.ts` → `--list` shows exactly `web-extension`, `protocol`, `supply-chain`; only `rust/veles-crypto/src/lib.rs` → `android` with that reason; `PATH` without Node 22 → `NOT RUN` with the fix, exit 2. `git reset --hard V` inside the scratch worktree after each.
- [ ] **Step 3:** Real Claude Code session on this machine with both `Stop` commands, in a disposable worktree on a branch at `V` (the hook uses the default base, so its selection contains this branch's own nodes). First establish the known-green baseline: a stop with no further edit is a clean, silent pass. Then add a lint error to `web-extension/src/background.ts` and end the turn → block naming the extension lint step with its tail; fix it → the next stop is a clean, silent pass (no stdout, the turn ends); make Node 22 absent from the verifier's **effective** environment → block with the Node fix: run `python3 -I scripts/verify.py config show` first; if `path_prepend` supplies Node 22, note the value, remove it with `config set path_prepend <list without it>` for this run only, hide it from `PATH`, confirm `config show` no longer resolves Node 22, and restore the noted value afterwards (confirmed with `config show`); start in the main checkout, `cd` into a linked worktree at another revision that carries the verifier → that worktree's `build/verify/last-run.json` is updated and the main checkout's is not. Record each run and the hook times in `spike.md`; commit `git commit -m "docs(openspec): record the verify hook acceptance runs"`.
- [ ] **Step 4:** Draft the PR notes: tooling only, no product behaviour change; what ran in Steps 1–3 and on what; the timings; the Known limits.
- [ ] **Step 5:** Record for the closeout: after the archive, add `docs/dev/specs/affected-node-verification.mdx`, `docs/dev/specs/verification-stop-hook.mdx` and their `docs.json` entries.
