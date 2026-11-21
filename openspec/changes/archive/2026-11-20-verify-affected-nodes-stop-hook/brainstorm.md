## Design Summary

### Why

Agents (and the developer) have no single, fast answer to "did I break anything?". The checks exist, but
as prose spread across the nodes' `AGENTS.md` files and one Android rule, so agents guess commands, skip
some or run the wrong set, and nothing stops a turn from ending with a red tree. Source: the issue "One
verify command for affected nodes, run by a Stop hook" (untracked: the issue is on no project).

### Goals

- One stdlib command, `python3 -I scripts/verify.py`, that selects the affected nodes and runs their
  checks, with output an agent can act on.
- Two gates, both runnable on the Linux coding server:
  - **task gate** (the default, no flag): after each task, and from the Stop hook;
  - **branch gate** (`--branch`): before a push, at the end of a feature, before a PR.
- A Claude Code `Stop` hook that runs the task gate and keeps the agent working while something it can
  fix is red, within a hard bound.
- One node → commands table, kept honest in both directions by checks that fail with the exact fix.

### Non-goals

- Changing what CI jobs do. Two deliberate CI-selection or wiring effects are in scope and named below:
  the drift check joins the existing agent-file check, and the inline Android raw-`Log` guard becomes a
  script with identical effect.
- Nx/Moon/Bazel; the component map already does affected selection.
- OpenCode Stop-hook parity (follow-up).
- A whole-repository mode: `--full` and `--node` were considered and dropped; an agent that needs an
  unusual run calls the node's commands directly. The wrapper speeds up the common scenarios only.
- Device and emulator tests (`connectedDebugAndroidTest`) and macOS/Windows-only steps: outside both
  gates; the real-device rule and CI cover them.

### Anti-goals (a failure even if it works)

- Running a node's whole test suite blindly in the task gate.
- Reporting anything that did not run as a pass, including a contended or skipped run.
- A hook that traps an agent in a loop on something it cannot fix, or for hours.
- Dumping full build logs into the agent's context.
- Machine settings written into a source tree or lost with a worktree.

### Modes

| Mode | Runs | Used by |
|---|---|---|
| default (task gate) | each selected node's `check` commands plus only the tests **related to the changed files** | the Stop hook; an agent after a task |
| `--branch` (branch gate) | each selected node's `check` commands plus **all** of its `test` commands that run on Linux | before a push, a feature's end, a PR |

`--list` (alias `--dry-run`) prints the plan — nodes, why each was selected, the exact commands, what is
skipped and why — and exits 0 without running anything. `--base <sha>` overrides the base. `--hook claude`
is the Stop-hook mode (its own output contract, below).

### Selection

- **Git reads** (read-only, NUL-delimited, `--no-renames` so a move counts both its old and new path):
  `git diff --name-only --no-renames -z <base> HEAD`, `git diff --name-only --no-renames -z --cached`,
  `git diff --name-only --no-renames -z` and `git ls-files --others --exclude-standard -z`. Deleted paths
  count. Base default: `git merge-base origin/master HEAD`.
- **Module loading:** `verify.py` inserts its own directory into `sys.path` (as `scripts/tests` already
  does, because `python3 -I` does not) and imports `load_map`, `affected` and `unaccounted` from
  `components.py`, so CI's `components.py changed` stays exactly as it is.
- **Node rules follow `.github/components.json` unchanged.** Dependencies select more than the touched
  folder: `web-extension/src/*` selects `web-extension`, `protocol` and `supply-chain`;
  `web-extension/test/*` selects `web-extension` and `protocol`; `rust/` also selects `android` (and
  `protocol`, `web-extension`). A global path (`gradle/`, `gradlew`, `.java-version`, most of `.github/`)
  selects every node, but an exact `global-overrides` entry wins first (`.github/workflows/check-android.yml`
  selects `android`, `tooling`, `installer`). Each node line names a file that selected it
  (`android: selected by rust/src/lib.rs`).
- **Error policy:** an unusable base (no `origin/master`, unknown sha) or a failed git read is exit 2
  with the fix (`git fetch origin master`, or `--base`). A changed path the map does not account for is
  exit 2 listing it with the `components.py` wording (the map must be fixed first). Changes that touch
  only `unowned` paths print `verify: only unowned paths changed (…)` and exit 0. No change at all prints
  `verify: nothing changed against <base>` and exits 0. Neither is ever shown as a pass of a node.

### The command table: `scripts/verify-commands.json`

One entry per node of the component map. Each command is an object; the node lists them in three kinds:

```json
"web-extension": {
  "commands": [
    {"name": "format", "kind": "check", "cwd": "web-extension", "run": ["npm", "run", "format:check"],
     "requires": ["node22", "web-ext-deps"], "doc": "web-extension/AGENTS.md"},
    {"name": "typecheck", "kind": "check", "cwd": "web-extension", "run": ["npm", "run", "typecheck"],
     "requires": ["node22", "web-ext-deps", "rust-wasm-pkg"], "doc": "web-extension/AGENTS.md"},
    {"name": "unit", "kind": "test", "cwd": "web-extension", "run": ["npm", "test"],
     "requires": ["node22", "web-ext-deps", "rust-wasm-pkg"], "doc": "web-extension/AGENTS.md",
     "related": {"strategy": "files-arg", "run": ["npx", "vitest", "related", "--run", "{files}"],
                 "match": ["web-extension/src/", "web-extension/test/"], "within": "npm-test-list"}}
  ],
  "excluded": [
    {"doc_line": "npm run package", "why": "build output, not a check"},
    {"doc_line": "npm ci", "why": "setup; a requirement's fix text"}
  ]
}
```

- **Kinds.** `check` runs in both gates and executes no test (format, lint, typecheck, static guards).
  `test` runs in the branch gate in full and, through `related`, narrowed in the task gate; a command
  that executes any test is a `test`, even if it also lints (for example `bridgeTest`, which depends on the
  Rust format and lint tasks). `device` (emulator/browser-on-device) and `platform` (macOS/Windows-only)
  commands are never run; they print `NOT RUN (device test; real-device rule)` or
  `NOT RUN (macOS only; CI job <name>)`. Gradle invocations that the docs combine are split into one entry
  per kind (for example `bridgeInstall bridgeFormat bridgeLint bridgeTypecheck` as checks, `bridgeTest` as
  a test).
- **Inventory.** The table covers every command in each node's documented Build & Test (or `## Commands`)
  block and in the node rules that `AGENTS.md` lists: each is an entry or an `excluded` line with a
  reason. Starting from the issue's table, the builder re-reads every `AGENTS.md` and the CI workflows;
  known additions for the branch gate are the extension's `npm run test:bundle` (after `npm run build`) and
  `npm run test:browser` (Playwright Chromium, which CI runs on Ubuntu), and the native host's Python
  build-contract suite. `./gradlew scriptsTest` is a `test`, never a `check`.
- **CI-only steps.** The gates run only documented commands; CI stays the authority. The builder walks
  each node's CI job steps: a Linux-runnable static guard or test that the gates should cover (for
  example Android's `node scripts/check-mac-comparisons.mjs --scan android/app/src/main`, and the
  extension's `npm run test:browser`) is first added to the node's doc (its `AGENTS.md`, or for Android the
  lint rule, since `android/AGENTS.md` is at 38 of 40 lines) and then to the table. Every CI step left out
  of the gates is listed in `docs/verify.md` under "CI only" with its reason. The drift check covers the
  docs, not the workflows.
- **Requirements** are named once per node (`"requires": {"node22": {…}}`) and attached per command, so a
  format step never waits on the WASM package. Only the requirements of the commands in the current plan
  are checked, and they are re-checked on every run (cheap path and version probes). A requirement has a
  probe and the fix text. Probes run with the command's effective environment (the machine config applied),
  so they see the tool the command will use. A version pin is read from the file the build reads (for
  example the exact Node and npm versions the supply-chain verifier requires), never restated. Where a
  build resolves a tool itself (the NDK order in `rust/build.gradle.kts`), the probe follows that order and
  cites it, or the requirement is left to the command, whose environment failure is recognised through
  `env_exit` or an `env_patterns` regex on its output.
- **Generated prerequisites** (the WASM package in `web-extension/rust-wasm/pkg/`) are a requirement of
  kind `generated` with a producer command (`./gradlew rustWasm`). `verify.py` does not judge freshness
  itself: whenever a consuming command runs, its producer runs first, and the build tool that owns the
  output (Gradle's up-to-date checks, which see every declared input such as `rust/toolchain-tools.toml`)
  decides whether to rebuild. A check therefore never runs against stale generated code. A producer is a
  command like any other: its own `requires` are probed first, and its `env_exit` and `env_patterns`
  results are `NOT RUN` with fix text (exit 2); any other failure is the node's `FAIL` (exit 1).
- **Commands are argv lists**, run without a shell; `{files}` expands to one argument per changed path
  relative to the entry's `cwd`, so a file name cannot change what runs. A check that needs shell logic
  is a script in its node (see the Android raw-`Log` guard).
- **Environment failures inside a command.** A command may declare `env_exit` codes (the supply-chain
  verifier's exit 2) and `env_patterns` (regexes on its output) that mean "missing tool or input"; such a
  result is `NOT RUN` with the command's tail and the matching fix text, not `FAIL`. Any other non-zero
  exit is `FAIL`.

#### Related-test strategies (a fixed set in `verify.py`)

| Strategy | Mapping | Starting use and its stated limit |
|---|---|---|
| `files-arg` | pass the changed files to a command that finds dependants; `within` restricts the result to a known list | web-extension: `vitest related`, intersected with the files `npm test` names, so bundle and browser suites stay in the branch gate |
| `name-map` | source stem → test file or class pattern; a changed test runs itself | android (`Foo.kt` → `--tests '*FooTest'`), tooling (`scripts/foo.py` → `scripts/tests/test_foo*.py`, run with `python3 -I -m unittest`), docs checks (`check_x.py` → `test_check_x.py`) |
| `crate` | changed paths → their Cargo crate → that crate's documented test command | native-host, installer; the `rust` node has one crate and its documented test is `./gradlew rustTest` (host, WASM and Android targets), so any `rust/` source change runs that whole command — an intentional limit |
| `self` | a changed test file runs itself | protocol `protocol/checks/*.test.mjs`, `node --test` suites |

Only test targets that exist in the working tree are passed to a runner; a deleted or moved-away path
still counts for node selection, never as a target.

A changed file no strategy maps — a protocol validator or fixture, a Gradle file, a manifest, a Kotlin
file without a matching test — prints `tests: none related` with the file list. It does not block; the
branch gate covers it. A new strategy is a function in `verify.py` plus unit tests.

### Output and logs

- One line per selected node: `PASS`, `FAIL`, `NOT RUN (<reason>)` or, in the hook, `PASS (remembered)`.
  A node with no planned command prints `NOT RUN (nothing planned: <why>)`, never `PASS`.
- On failure: the last 60 lines of the failing command (each line cut at 300 characters, the whole
  summary capped at 8 KiB), the exact command to re-run (with its `cwd`), and the log path. Never a full log.
- Every run writes `build/verify/runs/<run-id>/` (one log per command) and `build/verify/last-run.json`
  (per-node results, reasons and re-run commands). The newest 10 run directories are kept. The run id
  comes from an injected clock plus the process id.
- A node stops at its first failing command; the other nodes still run. Commands run sequentially, each
  in its own process group from its `cwd` under the repository root.

### Exit codes (the repo's 0/1/2 contract, `verify/AGENTS.md`) — CLI only

- **1** if any command failed or timed out (wins over everything else);
- else **2** for a usage error, an unusable base or git read, an unaccounted path, or any unexpected
  `NOT RUN` (a missing or wrong-version requirement, an `env_exit` result, a contended run);
- else **0**: everything planned passed, and every `NOT RUN` is expected — `device`, `platform`,
  `tests: none related`, `nothing planned`.

The summary always lists both failures and unexpected `NOT RUN`s.

### Tool requirements and the machine config

- The coding server has several JDKs and Android SDKs and no global paths. Requirements come from the
  repository pins: `.java-version` (21.0.12), `rust/ndk-version`, the `rust-toolchain.toml` files and
  Node ≥ 22.
- A machine config sits in git's shared directory:
  `$(git rev-parse --git-common-dir)/veles-verify/tools.json`. Every linked worktree and the main
  checkout resolve the same file; it survives a deleted worktree and can never be committed (the same
  pattern as `agent-rules/` state from `scripts/agent_rules.py`).
- Keys (initial set): `java_home`, `android_sdk`, `android_ndk`, `path_prepend` (list). `verify.py` turns
  them into `JAVA_HOME`, `ANDROID_HOME` and `ANDROID_SDK_ROOT`, `ANDROID_NDK_HOME` and a `PATH` prefix for
  the commands it runs only; nothing global changes. Without the file it uses the inherited environment.
- `verify.py` never writes `local.properties`. Gradle prefers `sdk.dir` in `android/local.properties`
  over `ANDROID_HOME`, so when that file sets an `sdk.dir` different from the effective SDK, the run says
  so as a warning and `config show` reports it.
- Written only through the command, which validates the value before saving (path exists, version
  matches the pin):

  ```bash
  python3 -I scripts/verify.py config set java_home /usr/lib/jvm/java-21-openjdk-amd64
  python3 -I scripts/verify.py config show   # the path, the values and what each resolves to
  ```

- A failed requirement is `NOT RUN` with a message that teaches: what is required and where it is pinned,
  what was found, the candidates found on the machine (scanning e.g. `/usr/lib/jvm/*`, `~/.android-sdk`,
  `~/.cargo/bin`), the exact `config set` or install command, and "if several match, ask the user which
  one this project uses".

  ```text
  android: NOT RUN — JDK 21 required (.java-version 21.0.12); JAVA_HOME unset.
    Found: /usr/lib/jvm/java-21-openjdk-amd64 (21.0.12)
    Fix: python3 -I scripts/verify.py config set java_home /usr/lib/jvm/java-21-openjdk-amd64
    If several match, ask the user which one this project uses.
  ```

- No skill and no hooked rule for a missing config: hooked `commands` fire only on success (a missing
  tool exits 2), and a `gates` rule would deny the first verify of every session. The tool's own output,
  repeated as the hook's block reason, reaches the agent at the moment it matters.

### The Stop hook

- **Wiring.** A second command in the `Stop` event of `.claude/settings.json`, next to the existing
  `agent_rules.py` command (5 s, unchanged):
  `python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true` with `"timeout": 1800`. The
  `|| true` matters: a missing script makes Python exit 2, which Claude Code treats as a blocking `Stop`
  result; with it, a missing or broken launcher lets the stop through, and the JSON the hook prints on
  stdout is still read. The agent-file check (`docs/checks/check_agent_files.py`, `_hook_findings`) today
  requires exactly one entry with one command per event; it is changed to accept, for `Stop` only,
  exactly these two commands (the `agent_rules.py` one with its 5-second timeout, and the verify one),
  with fixtures and tests updated.
- **Which checkout owns a run.** The script `CLAUDE_PROJECT_DIR` names is only a launcher, because that
  variable can still name the original checkout after the session moves into a worktree. It reads the
  event JSON, resolves the git top level of the event's `cwd` and, when that checkout differs from its own,
  re-executes that checkout's `scripts/verify.py --hook claude` with the same event on stdin. The code,
  component map, table, state and logs are therefore always the active checkout's. If the event's `cwd`
  is not inside a git checkout of this repository, or that checkout has no `scripts/verify.py` (a branch
  from before this change), the hook lets the stop through silently. If the launching checkout itself
  lacks the script, the `|| true` above lets the stop through and nothing is verified — a stated limit
  until that checkout has this change. Every launcher from this change on delegates, so the supported
  cross-revision case is a launcher with this change and an active checkout with or without the verifier.
- **Output contract (hook mode only).** It always exits 0 and writes only JSON to stdout: nothing on
  success or when nothing changed; `{"decision": "block", "reason": "<summary>"}` to keep the agent
  working; `{"systemMessage": "<notice>"}` when it lets the stop through with something to say. Its own
  errors go to stderr and let the stop through. The CLI exit codes above do not apply in this mode.
- **What it runs.** The task gate, with an internal budget of about 29 minutes so it can report before
  the harness kills it, further capped by what is left of the session backstop below; the real numbers
  are measured on a real change and recorded in the PR. On the
  budget it terminates the running command's process group (TERM, then KILL after a grace period) before
  reporting, so a retry does not race a leftover child. A Gradle daemon outlives the client by design.
- **Remembered passes.** After a node passes in the task gate, `build/verify/passes.json` (local,
  gitignored via `/build`) records a fingerprint of everything that decided the result:
  - the exact planned argv lists, including the resolved related files;
  - the content of **every path that can select the node**, decided by the selector's own
    `components.owner_nodes` over the tracked and changed files (a path counts when it names the node or
    is global), so `paths`, `ci-paths`, globals such as `gradlew` and `gradle/`, `global-overrides`,
    `retired-paths` and `basename-owners` are all covered without copying the rules — as git blob ids of
    tracked files plus hashes of their edited and untracked versions;
  - every path a planned command reads beyond those: each command (and producer) declares its `inputs`
    (path prefixes) in the table — for example the docs node's Gate B matrix check declares
    `protocol/conformance/gate-b-journeys.json`, and `rustWasm` declares `rust/` (which holds its tool pin
    `rust/toolchain-tools.toml`), `protocol/` and the Gradle files. A command whose inputs cannot be bounded declares
    `"reuse": false`, and its node is never remembered. Selection is unchanged; `inputs` only widen the
    fingerprint. A consistency check requires every command to declare `inputs` or `"reuse": false`;
  - the **content of every generated output** a planned command consumes (owner's decision): a hash over
    the relative paths and bytes of every file under the output directory (`web-extension/rust-wasm/pkg/`),
    taken after its producer ran in the passing run and recomputed before a record is honoured. The
    directory is git-ignored, so the git-based scan above never sees it; any rebuild or replacement in
    place makes the node run again. A missing output is a missing requirement, never a hit;
  - the node's table entry and the effective environment (the machine config and the resolved tool paths
    and versions).

  Requirements are re-checked on every run before a record is honoured, so a `./gradlew clean` that
  removed a required output re-runs the node. A failure or `NOT RUN` deletes the node's record. Reuse is
  never allowed when the base changed since the record (the plan would differ). Branch-gate and task-gate
  records are separate. A manual default-mode pass also writes a record; manual runs never read one.
- **Blocks** on a failure, a timeout or an unexpected `NOT RUN`. The reason is the agent-readable
  summary: failing command, capped tail, re-run command, log path, or the requirement's fix. On a timeout
  it names what finished, what was cut off and that command's log tail; the agent fixes and stops again
  to retry.
- **Loop bound instead of `stop_hook_active`.** Following the existing stop gate (#188),
  `stop_hook_active` is not consulted: it would wave through the very stop that follows a fix. Instead:
  - a problem's identity is its node, command name and outcome kind (fail, timeout, missing requirement);
    the same identity blocks at most 3 times in a row;
  - a backstop per session (`session_id` from the event). Its state is shared, not per worktree:
    `$(git rev-parse --git-common-dir)/veles-verify/hook-state.json`, next to the machine config, under a
    lock, keyed by session, so moving between worktrees never grants a fresh allowance. Admission is
    atomic: under that lock a run reserves its whole budget and a block before it starts, so overlapping
    runs of one session in two worktrees cannot both spend the same allowance; on finish it returns the
    unused time and, when it does not block, the block. Sessions are pruned to the newest 50 by last
    write — a stated limit: a session resumed after 50 newer ones starts with a fresh allowance.
    Remembered passes and logs stay worktree-local. At most
    5 blocks and at most 60 minutes of cumulative hook run time since the last clean stop. Each run's
    budget is the smaller of about 29 minutes and the time left in the 60; with less than 1 minute left,
    or 5 blocks used, the hook does not start a run and lets the stop through with a `systemMessage`
    listing what is still red;
  - only a **clean** stop resets both counters: a run that executed its plan and found nothing to block.
    A contended run, an internal error, a pass-through at a limit and a "nothing changed" stop leave the
    counters as they are.
- **Concurrency.** A lock under `build/verify/`; if another verify holds it, the hook lets the stop through
  with `systemMessage: verification already running; nothing was checked` and records nothing — never a
  pass. The CLI waits for nothing either: it exits 2 with the same message.
- Subagent stops are not wired (`SubagentStop` is not used). Both `Stop` commands firing in one real
  Claude Code session is part of the real-environment check.

### Growing the table

- **New test kind in an existing node:** add a command entry (`kind`, `requires`, `related` if a strategy
  fits) and the same command to the node's `AGENTS.md` Build & Test block.
- **New node:** add it to `.github/components.json` as today, add its table entry, list its commands in
  its `AGENTS.md`.
- **The drift check**, both directions, with failure text that is the fix:
  - every entry's `run` appears in its `doc` file: a non-Gradle command as the same argv on one line of a
    fenced block (comments stripped); a `./gradlew` entry when its task names are a subset of one
    documented `./gradlew` line that has no other arguments, so splitting a combined line is allowed but
    a documented `--tests …` or `-P…` example never stands for the full suite. `related.run` templates are
    internal and exempt;
  - every command line in the node's documented Build & Test (or `## Commands`) fenced blocks, and in the
    node rules its `AGENTS.md` lists, is covered by an entry or an `excluded` line;
  - `doc` is the node's `AGENTS.md` or a rule that `AGENTS.md` lists;
  - the component map and the table name the same nodes.
- **Where it runs.** The drift check is a new check in `docs/checks/check_agent_files.py` (docs node),
  which already runs on every `AGENTS.md`, `.agents/` and `.github/components.json` edit, with tests in
  `docs/checks/test_check_agent_files.py`. A `global-overrides` entry
  `scripts/verify-commands.json: [tooling, docs]` makes a table edit run it too. This changes CI selection
  for that one file, intentionally; no job changes. The checker's expected owner sets are updated with it.
- **Guidance.** One path-scoped rule, `.agents/rules/scripts-verify-commands.md` (glob
  `scripts/verify-commands.json`; the agent-file check does not allow a `scripts` rule to glob
  `.github/components.json`, so a new node is caught by the drift check), listed in `scripts/AGENTS.md`,
  pointing to a new `docs/verify.md`: kinds, entries, strategies, requirements and fix text, the machine
  config and the hook. No skill.

### Rules and docs

- `.agents/hooked-rules/repo-pre-push-checks.md`: body and pointer become "run
  `python3 -I scripts/verify.py --branch`; the node `AGENTS.md` lists stay the reference", keeping the
  current wording for hosts where the script cannot run; then `agent_rules.py pointers --write`. The root
  `AGENTS.md` gains no line (it is at its 40-line cap).
- `CONTRIBUTING.md` and `scripts/AGENTS.md` (Build & Test; 34 of 40 lines) mention both gates. The
  verifier's own invocations in the tooling Build & Test block are `excluded` lines in the table
  ("the verifier itself"), so the reverse drift rule is satisfied and the verifier never runs itself.
- `.github/components.json`: `scripts/verify.py` stays under `tooling` (`scripts/`); the override above for
  the table; CODEOWNERS where applicable; `components.py check` passes.
- The Android raw-`Log` guard moves from inline shell (duplicated in `check-android.yml` and the Android
  rule) into one script under `android/` that the rule, CI and the table call, with the same greps and exit
  status.

### Tests

`scripts/tests/test_verify.py`, run by `./gradlew scriptsTest`, with an injected command runner, clock,
git reader and environment — no sleeps, no wall-clock reads:

- selection: committed, staged, unstaged, untracked and deleted paths; both sides of a move; dependency
  selection (`web-extension/src/*`, `rust/`); a global path; an exact override; unaccounted, unowned-only
  and empty changes; unusable base;
- task vs branch plans; each related strategy and its `within` filter; `tests: none related`; device and
  platform commands; argv expansion of unusual file names;
- exit precedence 1 > 2 > 0; `env_exit`; per-command requirements;
- failure truncation (60 lines, 300 characters, 8 KiB), re-run command, log path, run retention;
- remembered passes: match; mismatch after an edit, a table change, a config change, a removed required
  output; deletion after a failure; per-mode records;
- hook: event `cwd` resolution and re-execution of the active checkout's verifier (including a checkout
  without one); stdout JSON only and exit 0; block and silence; the 3-repeat bound; the session backstop
  (count, time and the per-run budget cap, with the injected clock); reset only on a clean stop; timeout
  with process-group termination; the lock; atomic reservation under overlapping runs of one session;
- generated prerequisites: the producer always runs before its consumer; its `env_exit`/`env_patterns`
  results are `NOT RUN`, other failures the node's `FAIL`; the fingerprint's coverage of selection paths
  and declared `inputs` (a Gate B journeys edit invalidates the docs record); `"reuse": false` never
  remembered; a generated output rebuilt or replaced in place invalidates the record; deleted test paths
  never passed to a runner;
- config: shared location across worktrees, `set` validation, `show`, environment injection, the
  `local.properties` warning.

The drift check's tests live with the agent-file check in `docs/checks/`.

### Acceptance

- On a branch touching only `web-extension/src/`, the task gate selects exactly the nodes the map gives
  (`web-extension`, `protocol`, `supply-chain`), names why, and prints a compact result; touching `rust/`
  also selects `android` and says so.
- A deliberately broken lint rule in a scratch branch makes the Stop hook block with a readable summary;
  the agent fixes it and the next stop passes.
- An absent toolchain yields `NOT RUN` with its fix and exit 2, never a pass.
- `./gradlew scriptsTest`, `python3 -I scripts/components.py check`,
  `python3 docs/checks/check_agent_files.py --root .` and `python3 -m unittest discover -s docs/checks -p 'test_*.py'`
  pass; CI is green.
- The PR states that no product behaviour changed (tooling only), what ran in the real Claude Code
  session, and the measured hook times.

### Real-environment check

Tooling only, no product behaviour change, so the device and browser rule does not apply. The acceptance
runs above are made in a real Claude Code session on the coding server, with both `Stop` commands wired,
including a session that moves from one checkout into a linked worktree at a different revision, where the
hook must run the worktree's verifier against the worktree.

## Alternatives Considered

### Alternative A: Re-check the whole branch on every Stop
- **Approach**: every Stop runs the task gate for every node the branch changed.
- **Pros**: simplest; no state.
- **Cons**: once a branch touches Android, every turn pays for Gradle lint again, even a docs-only turn.
- **Why not chosen**: too slow for a per-turn hook.

### Alternative B: Remember passes (chosen)
- **Approach**: a passing node records a fingerprint of its plan, inputs and environment; the hook skips
  nodes whose fingerprint still matches.
- **Pros**: fast repeated stops; a failure keeps re-running until fixed; manual runs stay fresh.
- **Cons**: a local state file and fingerprint code to test.
- **Why chosen**: see Agreed Approach.

### Alternative C: Check only what this turn changed
- **Approach**: diff against the previous Stop.
- **Pros**: fastest.
- **Cons**: a failure left by an earlier turn slips through.
- **Why not chosen**: it lets a red tree end a turn.

### Also considered and dropped
- `--full` (every node) and `--node <name>`: the branch gate covers what a branch touched; unusual runs
  call node commands directly.
- A gitignored config in the main checkout, or copies per worktree: lost with worktrees, fragile with
  `--separate-git-dir`, or drifting copies.
- A skill or a hooked rule to write the config: hooked `commands` miss failing runs and `gates` would fire
  every session; the tool's own message is the better channel.
- Blocking forever on `NOT RUN`, or honouring `stop_hook_active`: either traps the agent or skips the
  re-check after a fix.
- The drift test in `scripts/tests` with `tooling` added to every `AGENTS.md` owner set: it misses the
  exact overrides and the node rules; the agent-file check already runs on all of them.
- Extending `components.py changed` with working-tree files: it would change CI's selector.

## Agreed Approach

Alternative B with two gates: the default task gate (checks plus related tests, run by the Stop hook with
remembered passes) and `--branch` (checks plus every Linux-runnable test of the selected nodes). One data
table drives both, guarded in both directions by the agent-file check; tool paths come from a machine
config in git's shared directory written by `verify.py config set`; every problem the agent can act on
blocks the stop with an actionable summary, bounded per problem and per session.

## Key Decisions

- The default is the task gate; `--branch` is the pre-push and PR gate; `--full` and `--node` are dropped
  (owner).
- Task-gate tests are narrowed to the changed files; a node's full suite never runs blindly (owner). Where
  the finest unit is a whole documented command (the `rust` node's `rustTest`), that is stated as a limit.
- Remembered passes live in local gitignored files under `build/verify/` (owner), keyed to the plan,
  inputs and environment, and to a hash of every generated output a node consumes (owner, at the review
  escalation).
- Failures, timeouts and missing tools all give the agent the reason and the fix; the hook timeout is 30
  minutes, after which the agent sees what happened, fixes and retries (owner).
- Device tests and macOS/Windows-only steps are outside both gates and printed as expected `NOT RUN`
  (agent's call, from "only what can run on the Linux coding server").
- Unmapped changed files show `tests: none related` and do not block (agent's call, accepted).
- The machine config is in git's shared directory and written only by `verify.py config set` (owner).
- No skill and no hooked rule for growing the table or for a missing config; checks that teach, one
  path-scoped rule and `docs/verify.md` (agent's proposal, accepted with the write-up request).
- `stop_hook_active` is not consulted; a 3-repeat bound per problem plus a per-session backstop of 5 blocks
  or 60 minutes (agent's call; the backstop numbers are the owner's to change).
- The selection reuses `components.py` functions; CI's selector is untouched (agent's call).
- The drift check lives in the agent-file check; the agent-file check accepts the second `Stop` command
  (agent's call).

## Open Questions

- The hook's internal budget and whether 30 minutes is right: measured on a real change during
  implementation and recorded in the PR.
- The per-session backstop (5 blocks, 60 minutes) is a proposal for the owner to confirm or change.
- Exact narrowing commands per node (for example the Android `--tests` pattern, and whether
  `cargo test -p <crate>` works under the Gradle-pinned toolchains for native-host and installer) are
  settled while implementing, each within its strategy above.
