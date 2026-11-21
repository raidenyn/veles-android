## Why

Agents and the developer have no single, fast answer to "did I break anything?". The checks exist only as
prose spread across the node `AGENTS.md` files and one Android rule, so agents guess commands, skip some or
run the wrong set, and nothing stops a turn from ending with a red tree. One local command that selects the
affected nodes the way CI does and runs their checks, with a Stop hook that keeps the agent working while
something it can fix is red, gives every agent the same entry point at the speed of the common case.

## What Changes

- **`scripts/verify.py`** (stdlib, `python3 -I`): selects the nodes the component map gives for the branch's
  committed changes plus the working tree, and runs their commands with a compact, agent-readable result
  (`PASS`, `FAIL`, `NOT RUN (<reason>)` per node; on failure a capped tail, the re-run command and the log
  path) and the repository's 0/1/2 exit contract.
  - **Task gate** (default): each node's checks plus only the tests related to the changed files.
  - **`--branch`**: each node's checks plus all of its tests that run on Linux; the pre-push and PR gate.
  - **Outside both gates:** device and emulator tests and macOS/Windows-only steps print as expected
    `NOT RUN`. A changed file with no related test is listed as `tests: none related` under the node and
    does not make the node's result `NOT RUN`. Expected `NOT RUN`s never change the exit code;
    a missing or wrong-version tool is an unexpected `NOT RUN` (exit 2).
  - `--list`/`--dry-run`, `--base <sha>`, `--hook claude`, `config set|show`.
- **`scripts/verify-commands.json`**: one table of node commands (`check`, `test`, `device`, `platform`),
  per-command requirements with fix text, related-test strategies, declared inputs and `excluded` doc lines.
- **Drift check** in the agent-file check: the table and the node docs must agree in both directions, and
  the table must name exactly the component map's nodes; failures state the fix.
- **Machine config** in git's shared directory, written only by `verify.py config set`: tool paths (JDK,
  Android SDK and NDK, `PATH` prefix) for the commands it runs, shared by every worktree. Missing or
  wrong-version tools are `NOT RUN` with what is required, what was found and the exact fix.
- **Claude Code `Stop` hook**: a second `Stop` command (30-minute timeout) runs the task gate in the active
  checkout's own verifier and blocks with the summary on a failure, a timeout or any unexpected `NOT RUN` (a
  missing or wrong-version tool, a missing input, an environment failure a command reports), each with its
  fix, bounded per problem and per session. Its contract is the hook's, not the CLI's: it always exits 0, answers only with
  JSON, and lets the stop through when it cannot run (the event's directory is not a checkout of this
  repository, that checkout has no verifier, a contended run, its own error, a limit reached). It skips nodes with a remembered pass, and a remembered pass is reused only while
  everything that decided it is unchanged, including the contents of every generated output its commands
  consume (the git-ignored WASM package among them).

**Pre-push rule**
- From: run the checks listed in the `AGENTS.md` of every node `components.py changed` selects.
- To: run `python3 -I scripts/verify.py --branch`; the node `AGENTS.md` lists remain the reference where the
  script cannot run.
- Reason: one command instead of prose agents interpret differently.
- Impact: a stricter local obligation (every Linux-runnable test of the selected nodes, with its tool
  requirements); the rule's delivery triggers are unchanged; required CI checks stay the authority.

**Claude Code `Stop` wiring**
- From: exactly one command (`agent_rules.py`, 5 s) on every hook event, enforced by the agent-file check.
- To: `Stop` holds exactly two commands, the existing one unchanged and the verify hook; every other event
  keeps exactly one.
- Reason: the verify hook needs its own timeout and output.
- Impact: the agent-file check accepts this one exception.

- The inline Android raw-`Log` guard (duplicated in `check-android.yml` and the Android lint rule) becomes one
  script under `android/` that both call, from the repository root, scanning the same sources.
- A path-scoped rule for editing the table and a `docs/verify.md` reference; `CONTRIBUTING.md` and
  `scripts/AGENTS.md` mention both gates.
- **CI coverage.** CI jobs and the selector logic are unchanged. Two coverage effects are deliberate: the
  drift check joins the existing agent-file check, and a `.github/components.json` override makes a
  table-only edit select `tooling` and `docs`.

## Capabilities

### New Capabilities

- `affected-node-verification`: the verify command — selection, the two gates and what lies outside them,
  the command table and its drift check, related-test strategies, requirements and the machine config,
  output, logs and CLI exit codes; and the guarantee that a command never runs against stale generated
  output (its producer runs first and the owning build tool decides freshness).
- `verification-stop-hook`: the Claude Code `Stop` hook — dispatch to the verifier of the active checkout
  (only a checkout of this repository), the
  hook output contract and fail-open cases, remembered passes and their invariant (reuse only while the
  plan, inputs, environment and consumed generated outputs are unchanged), blocking, the per-problem and
  per-session bounds, concurrency and the time budget.

### Modified Capabilities

- `hooked-rule-delivery`: the Claude Code wiring requirement and its scenario allow exactly a second `Stop`
  command (the verify hook); the pre-push rule's pointer and body name `verify.py --branch`, its triggers
  unchanged.
- `agent-guidance-layout`: the pre-push rule's described content becomes the verify command; the
  `Android lint rule` scenario names the shared raw-`Log` guard script instead of the workflow's inline
  step; the agent-file check's hook rules allow the one `Stop` exception and run the drift check; a new
  path-scoped `scripts` rule for the command table, listed in `scripts/AGENTS.md`.
- `ci-pipeline`: a new selection scenario — a change to only `scripts/verify-commands.json` selects exactly
  `tooling` and `docs`, through the existing `global-overrides` mechanism (no new selector or job).

## Impact

- Code: new `scripts/verify.py`, `scripts/verify-commands.json`, `scripts/tests/test_verify.py`; a new
  Android guard script; `docs/checks/check_agent_files.py` and its tests (drift check, `Stop` exception).
- CI and its contracts: `.github/workflows/check-android.yml` calls the guard script, and
  `verify/test/workflow-contracts.test.mjs` asserts the script invocation while keeping its repository-root
  and scan-scope assertions; `.github/components.json` (one override); CODEOWNERS where applicable.
- Configuration: `.claude/settings.json` (`Stop`).
- Rules and docs: `.agents/hooked-rules/repo-pre-push-checks.md` and the generated pointer block,
  `.agents/rules/android-lint-before-push.md`, a new `.agents/rules/scripts-verify-commands.md`,
  `docs/verify.md`, `CONTRIBUTING.md`, node `AGENTS.md` files where a CI-covered command is added.
- Closeout, after the main specs exist: the site wrappers for the two new capabilities
  (`docs/dev/specs/*.mdx`) and their `docs.json` navigation entries.
- Local state: `build/verify/` (logs, last run, remembered passes) and `<git-common-dir>/veles-verify/`
  (machine config, hook session state); nothing committed.
- No product code changes (Android app, extension, Rust core, native bridge).
- Real-environment check in a real Claude Code session on the coding server: both `Stop` commands fire; a
  session moved into a linked worktree at a different revision runs that worktree's verifier; the hook's
  measured times go in the PR.
