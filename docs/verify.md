# verify.py: affected-node checks

`scripts/verify.py` runs the checks and tests of the nodes a change affects, then reports one line
per selected node. The commands, their requirements and their related-test rules live in
`scripts/verify-commands.json`; this page is the reference for that table and for the verify gate
that runs the verifier from the Claude Code and OpenCode hooks.

Read `.agents/rules/scripts-verify-commands.md` before editing the table.

## The two gates

- **Task gate** (`python3 -I scripts/verify.py`, the default): runs every selected node's `check`
  commands and, for each `test` command with a related-test strategy, only the tests related to the
  changed files. It is the fast signal, the gate the verify gate runs, and the pre-push run: a
  complete pass writes the record the push gate checks.
- **Branch gate** (`python3 -I scripts/verify.py --branch`): runs every selected node's checks and
  all of its tests. It is the optional full local run, and its complete pass satisfies the same
  push gate; CI runs every test in its own jobs.

Both gates select the same nodes: the changed paths against `git merge-base origin/master HEAD`
(override with `--base <sha>`), mapped through `.github/components.json`. A path no node owns
selects nothing; an unaccounted path is a selection error naming `.github/components.json`.
`--list` (or `--dry-run`) prints the selection and the plan without running a probe or a command
(the list-only target resolvers are the exception). The task gate may reuse a remembered pass (in
hook mode); the branch gate never does.

Exit codes: `1` for a `FAIL` or a timeout, `2` for usage, a selection error, a contended run or an
unexpected `NOT RUN`, else `0`. A run with no changed path prints
`verify: nothing changed against <base>`; only unowned paths print
`verify: only unowned paths changed (<paths>)`; both exit `0`.

## Entries and kinds

`nodes.<node>.commands` holds the entries. Every entry has an `id`, a `kind`, a `cwd` (inside the
repository), the `run` argv (a list; no shell), its `requires`, its `inputs` (or `"reuse": false`),
and optional `env_exit`/`env_patterns` that turn a known environment failure into an unexpected
`NOT RUN` with a `fix`.

Kinds:

- `check` — a static check, formatting check or lint;
- `test` — a test command, with an optional `related` strategy;
- `device` — a run that needs a real device or emulator; both gates report
  `NOT RUN (device test; real-device rule)`;
- `platform` — a macOS, Windows or release-only command; both gates report it as an expected skip.

`nodes.<node>.none` states why a node has no commands. Producers are not command kinds: they are
`producers[]` entries referenced by a `generated` requirement (see below).

A command or producer must not carry a `doc` field and a node must not carry an `excluded` list: the
table is the single source of the commands the verifier runs. The loader rejects either key as a
table error naming the entry and the field (for example `command '<id>': unknown field(s) doc`), so
a stale table cannot load.

`docs/checks/verify_table.py` checks one direction only: the table must not repeat a command a node
documents. It reads every shell fenced block of a node's `AGENTS.md` and of every rule that file's
`## Rules` section lists, with the verifier's working-directory rules (a section's "Run from `<dir>`"
sentence, and `cd` lines for the lines after them). A documented line is a duplicate when:

- its argv and working directory equal those of a `check` or `test` entry of any node; or
- it is a task-only `./gradlew` line and every task it names is a task of `check` or `test` entries
  run from the same working directory. Value-less Gradle options (`-q`, `--offline`) are ignored
  when comparing; any other argument (for example `--tests "<class>"`, `-Pkey=value` or `-x <task>`)
  makes the line targeted, and a targeted line passes.

`device`, `platform` and producer commands pass, and so does a documented line the table does not
hold: a doc no longer needs a table entry. `python3 docs/checks/check_agent_files.py --root .` runs
the check. The comparison is exact argv, so a doc line with a shell glob that expands to table
commands is not detected: remove such a line by hand (`.agents/rules/scripts-verify-commands.md`
says the same).

The verifier's own documented lines (`python3 -I scripts/verify.py` and `--branch`) stay in
`scripts/AGENTS.md` with no entry: they run the table, so the table cannot gate on them.

## Related tests and strategies

A `test` entry with `related` runs, in the task gate, only the tests the strategy resolves from the
changed files. An empty resolution is a `tests: none related (<files>)` note, never a run. A `test`
without `related` is branch-only in the task gate (`NOT RUN (branch gate only: ...)`). Fields by
strategy:

- `name-map` — `sources` maps a changed file through the `tests` template; a changed test file is
  its own target. Android resolves a Kotlin test class to its package-qualified name and passes
  `--tests=<fqn>`.
- `self` — a changed file matching `tests` is its own target.
- `crate` — the changed paths' crates map through `crates` to their documented Gradle test task. The
  `rust` node's one crate command, `./gradlew rustTest`, is all of its tests (the stated limit).
- `files-arg` — a list-only `resolve` argv receives `{changed}` and prints related test files. No
  entry uses it today: vitest 4.1.12 has no list-only related resolver that accepts explicit paths
  (`vitest list --filesOnly` takes a git `--changed` revision, and `vitest related` executes tests),
  so the extension uses `name-map` plus `self` with `within: package.json:scripts.test`.

`within` optionally intersects the resolved targets with an allowlist; `package.json:scripts.test`
means the test files that script names, so a suite `npm test` does not run is never a task-gate
target.

`{targets}` expands once per target: a bare `{targets}` item becomes one argv item per target
(`python3 -m unittest A B`), and an item that contains the placeholder is copied once per target
with the placeholder replaced — `--tests={targets}` becomes `--tests=A --tests=B`. Use the second
form for a flag that repeats per target.

The extension's narrowed run is `./node_modules/.bin/vitest run {targets}`, not `npx vitest`:
`verify/check-remote-code.mjs` forbids `npx` in `scripts/`, and the local binary is the one
`npm test` uses. `./gradlew protocolCheck` stays one aggregate `test` entry; Gradle itself orders the
`rustHostJni`/`rustWasm` producers, so the verifier does not add a producer chain for it.

## Producers and generated outputs

A `generated` requirement names an output directory and a producer. Before every consumer runs, the
verifier runs the producer chain immediately before it, even if an earlier step of the same run ran
that producer (another step may have rewritten the output). The producer's build tool decides
whether anything is rebuilt. After it succeeds the output must exist, and its digest is captured
for the remembered-pass key (if the node is reusable).

Producers today: `rust.wasm` (`./gradlew rustWasm` → `web-extension/rust-wasm/pkg`),
`web-extension.build` (`npm run build` → `web-extension/dist`), and `rust.hostJni`
(`./gradlew rustHostJni` → `build/rust/host-jni`, documented so the line is covered; no current
consumer, because `protocolCheck` builds it itself). `web-extension.bundle` and
`web-extension.browser` require `rust.wasm` and `web-extension.build` as CI does.

## Requirements and fix text

A requirement is `tool`, `path` or `generated`. A `tool` requirement names one resolver:

| Resolver | Resolves like |
|---|---|
| `java-home` | Gradle: `JAVA_HOME`, else `java` on `PATH`; the major must match `.java-version` |
| `android-sdk` | `android/local.properties` `sdk.dir`, else `ANDROID_HOME`, else `ANDROID_SDK_ROOT` |
| `android-ndk` | the first existing candidate directory; its `source.properties` must match `rust/ndk-version` |
| `path-tool` | an executable on the effective `PATH`, from `args.cwd`, version-checked against `args.min`, `args.exact` or a pin file |
| `rustup-toolchain` | `rustup show active-toolchain` in the `args.toolchain_file` directory |
| `playwright-chromium` | `VELES_TEST_CHROMIUM`, else the executable the pinned `playwright-core` reports |

A `path` requirement is a directory or file that must exist (for example
`web-extension/node_modules`); its fix says how to provision it. A missing requirement makes the
step an unexpected `NOT RUN` with the requirement's `fix`, and the run exits `2`.

Each requirement carries the exact fix text a maintainer needs: the pinned value and where it is
pinned, what was found (with a bounded candidate scan), and the command that fixes it — often
`python3 -I scripts/verify.py config set <key> <candidate>`. `--list` prints
`targets unresolved: <reason>` when a related-target resolver cannot run.

`./gradlew bridgeInstall` is setup, not a gate: it installs `native-bridge/node_modules` (the
`native-bridge.node-modules` requirement) with `npm ci --ignore-scripts` and rewrites the stat-mode
inputs that `bridgeFormat`, `bridgeLint`, `bridgeTypecheck` and `bridgeTest` hash, so running it as a
check would defeat their remembered passes; the requirement's fix installs it once. `installer.fixture`
is a `platform` entry with the honest `ci` value `"none (no CI job; a local fixture producer)"`.

## Remembered passes

Only `--hook` mode and the verify gate's run on a tree with uncommitted changes reuse a pass, and only
for the task gate. The key is a sha256 over the plan,
the node entry, the effective environment (build-affecting variables and every resolved tool's path
and version), the affected tracked and changed files, the declared input trees, and each consumed
generated output. The source side is computed before and after the run; a record is written only
when both are equal and no step declares `"reuse": false`. Records live in
`build/verify/passes.json` and are deleted on that node's `FAIL` or `NOT RUN`; any read that cannot
complete is a miss. A remembered node prints `PASS (remembered)`.

Choose `"reuse": false` when you cannot bound a step's inputs; `--branch` and CI always run fresh.

## The branch pass

A complete manual run of either gate (`python3 -I scripts/verify.py` or `--branch`) writes
`<worktree>/build/verify/branch-pass.json` (worktree-local, like the remembered passes) only after a
**complete** run:

- exit code 0;
- no `--deadline` and no `--base` argument;
- no step reported as not reached;
- `git status --porcelain` empty, with the same HEAD before selection and after the last step
  (untracked files count; ignored files do not).

The record is version 1 JSON, `{"version": 1, "head": "<sha>", "base": "<sha>", "nodes": ["<node>",
...]}`, and holds no time: freshness is HEAD and base equality, so nothing depends on the clock.

Every manual run of either gate deletes an existing record right after it takes the run lock
(`build/verify/lock`), so a later run that fails cannot leave an old pass in place. A contended run
that never gets the lock leaves the record alone. `--hook` mode never writes or deletes it.

`scripts/branch_pass.py` owns the record's path, format, writer and check; the verifier and the
hooked-rules engine both use it. Its check resolves the worktree of a working directory, reads the
record, and passes only when the record's HEAD equals `git rev-parse HEAD` and its base equals
`git merge-base origin/master HEAD`. Every other outcome fails with a message naming what differs: no
record, a different HEAD (`pass is for <short>, HEAD is <short>`), a different base, an unreadable
record, or a git error (for example a missing `origin/master`).

The push gate requires this record; see `docs/hook-delivered-rules.md`.

## Generated files

- `build/verify/runs/<run-id>/<step-id>.log` — one log per step (stdout and stderr merged); the
  newest 10 run directories are kept.
- `build/verify/last-run.json` — version 1, the last run's nodes and steps.
- `build/verify/passes.json` — the remembered task-gate passes.
- `build/verify/branch-pass.json` — the branch pass for HEAD and the merge base it diffed against.
- `build/verify/lock` and `build/verify/running.json` — the run lock and the live process groups;
  a second CLI run exits `2` with "verification already running".

The summary is capped at 8 KiB; past it, later nodes keep their status line and a pointer to
`last-run.json`. A failure block has the step id, the re-run command, the last 60 log lines and the
log path.

## Machine config and the effective environment

The machine config is `<git-common-dir>/veles-verify/tools.json` (version 1), shared by every
worktree of the checkout. Keys: `java_home`, `android_sdk`, `android_ndk`, `path_prepend`.

- `python3 -I scripts/verify.py config set <key> <value>` validates the value as the matching
  resolver does, writes the file atomically and prints the result.
- `python3 -I scripts/verify.py config show` prints the config path, every key and its value, and
  what each resolves to.

A step's effective environment is the inherited environment with `JAVA_HOME`,
`ANDROID_HOME`/`ANDROID_SDK_ROOT` and `ANDROID_NDK_HOME` set from the config, and `PATH` prefixed
with `path_prepend` then `$JAVA_HOME/bin`. A difference between `android/local.properties`
`sdk.dir` and the effective SDK is reported by `config show` and under the `android` node; the
verifier never writes `local.properties`.

## The verify gate

The agent harness verify gate (`.agents/harness/verify-gate/`, skill `agent-harness-verify-gate`) runs
from the agent harness hook runtime (`.agents/harness/hook-runtime/`, skill
`agent-harness-hook-runtime`) on both hosts: `.claude/settings.json` holds one runtime entry per
event after the hooked-rules engine entry, and `opencode.json` loads
`./.agents/harness/hook-runtime/opencode` next to `scripts/opencode-hooked-rules`. Both are copied
tools: never edit them; update them with the skills' `update`, and CI runs both tools' `check`.

Its one command, `verify-gate.command` in `.agents/harness.json`, is
`python3 -I scripts/verify_gate.py`:

- **A file edit** (`VERIFY_TRIGGER=edit`) lints `VERIFY_FILE` alone: Python and JSON syntax,
  `sh -n`, `node --check`, `rustfmt --check`, and the package's own `eslint` and `prettier --check`
  under `web-extension/` and `native-bridge/` (skipped when its `node_modules` is missing). Kotlin,
  Markdown and other files have no single-file lint; the next full run covers them.
- **A ticked task** in `openspec/changes/*/tasks.md` and **every turn end** run the task gate of
  `scripts/verify.py` in the same process. With uncommitted changes the run reads the remembered passes,
  as `--hook` mode does, so a node whose inputs are unchanged since it passed is `PASS (remembered)`. On
  a clean tree it is the plain manual run: it never reads them and writes the branch pass.
- **Contention.** A run that finds `build/verify/lock` held (for example by a background
  `python3 -I scripts/verify.py` before a push) waits for it, polling every 2 seconds for up to the
  gate's `timeoutSeconds` less 60, and then runs; the other run's records keep that run short. Only a
  wait past that limit fails, with `verify: verification already running`.

A failure is added to the edit's tool result, or blocks the turn end at most three times for the same
failure; a turn end with nothing changed since the last full pass runs nothing. Run the same full run by
hand with `node .agents/harness/verify-gate/cli.mjs run`, and see the skill for the messages, the
limits and `AGENT_HARNESS_TRACE=1` timings. The no-change shortcut does not run the command, so after a
commit run `python3 -I scripts/verify.py` before a push (the pre-push rule says the same). The
agent-file check accepts exactly this wiring and no second command on the engine's `Stop` entry.

### `--hook` mode

`python3 -I scripts/verify.py --hook claude|opencode` is the earlier turn-end hook. Nothing wires it
now; it stays until the hooked-rules engine moves to the hook runtime. Both hosts use the same contract. The event on stdin is the same JSON object (`session_id`, `cwd`);
the output is the same, the hook always exits `0` and every internal failure fails open. Hook state
keys each session as `<host>:<session_id>`, so an OpenCode and a Claude session id never collide,
and `HOOK_STATE_VERSION` is 2; a state document of another version loads empty, so an old record
starts with a fresh allowance. The launcher passes its host to the dispatched child. On SIGTERM the
launcher stops that child with `_stop_child` and exits 0: the child runs in its own session, so
killing the launcher alone never reaches it.

The hook is bounded so it cannot trap a session: the launcher relays a child with an absolute
deadline of at most 1740 seconds (45 seconds are reserved for termination and the report), and its
watchdog kills and reaps only that child 30 seconds past the child's admitted deadline. Admission
allows at most 5 blocks and 3600 seconds of run time per session; a reservation is
`min(1740, time left, time to the deadline)`. After the third block for the same problem, the next
stop passes with a notice instead of blocking again.

Output forms: a problem is `{"decision":"block","reason":"<summary>"}` whose reason starts
`verify: the task gate found problems; fix them and stop again (attempt <n> of 3)`; a pass with a
notice is `{"systemMessage":"<text>"}`; a clean pass or a zero-work run prints nothing. Every
internal failure fails open: one stderr line, no stdout, exit `0`. `stop_hook_active` is ignored.
Hook state and reservations live under `<git-common-dir>/veles-verify/`.

**Contention.** A background run holds `build/verify/lock` for its whole run, so a
`--hook` run reports `{"systemMessage":"verification already running; nothing was checked"}`
and checks nothing; stop again after that run finishes. The same contention can happen in
hook mode when a second launcher starts during the 5-second SIGKILL grace of the first: the first
launcher's child still holds the lock.

## CI only

The table is local and Linux-first. These stay "CI only" or out of the table, each with the reason
it is not a gate:

- **Device and emulator**: Android instrumented tests (`./gradlew connectedDebugAndroidTest`) need
  KVM and an emulator image; the table has the `device` entry, and both gates report it as an
  expected skip.
- **macOS and Windows**: Xcode/`DEVELOPER_DIR`, the NSIS, MSVC and Python 3.12 provisioning, the
  release installer builds, and the owner-only signing/notarization steps. No resolver exists for
  them; the `installer` node's `platform` entries are expected skips, and the rest is recorded in
  the inventory. The `codesign --verify`/`-dvvv`/`-d -r-` inspection and the `log show` TCC capture
  in `.agents/rules/native-bridge-test-app-tcc.md` are macOS-only lines with placeholder paths, so
  they are not gates either.
- **Build/-cleaning sequences**: CI's Rust `jni-integration` sequence (`./gradlew clean`,
  `rust/scripts/assert-clean.sh`, the APK/JNI assertions, `npm ci`) would delete `build/verify/`
  (the verifier's own state and lock), so it is not a local gate; `rust/scripts/assert-clean.sh`
  only makes sense after `./gradlew clean`, which removes the verifier's state.
- **CI provisioning**: the exact Node/npm pins (26.8.1 / 11.19.0), the JDK and Android SDK/NDK
  setup, Playwright Chromium, and `npm ci`/`installCargoDeny` are represented as requirements and
  their fix text, not as commands.
- **Build artifacts and setup**: `./gradlew assembleDebug`, `bridgeBuild`, `bridgeManifests` and
  `bridgePackage` are products, not gates (`bridgeHostBuild` is compiled only on the Windows and
  macOS CI runners). `npm ci`, `npm ci --ignore-scripts --prefix verify`, `./gradlew rustInstall`,
  `installCargoDeny`, `./gradlew installGitCliff` and `./gradlew installGitleaks` are setup:
  requirements or the `cargo-deny`/`cargo-cyclonedx` fixes stand for them, and `scriptsTest`
  depends on the two install tasks. `./gradlew bridgeInstall` is setup too; its stat-mode reason is
  under "Requirements and fix text".
- **Release and packaging**: `assembleRelease`, the extension's `npm run package`, `./gradlew
  rustJni` (the product-input build for the three Android JNI ABIs, which the release pipeline
  builds), the native product builds and the release-only workflows are products, not gates.
- **Duplicates and aggregates**: `./gradlew bridgeInstallerSupplyChain` is not an entry: it runs
  the same `verify/verify-supply-chain.sh` as the canonical `supply-chain` entry, which is the one
  entry so no plan runs the script twice.
- **Examples and callers**: the `commitlint.py --message`, `components.py changed --base <sha>`,
  `last_green.py --pr … --head …` and `project_flow.py --help` lines under `scripts/` are examples
  or CI callers whose arguments the workflow supplies; they validate a message, select nodes or
  talk to GitHub, not the tree. `python3 -I scripts/agent_rules.py pointers --write` is hook-rule
  maintenance that regenerates the pointer block, not a gate. The verifier's own `python3 -I
  scripts/verify.py` lines are not entries either: they run the table.
- `npm run test:connector-browser` is in no node doc and no CI step, so it is outside the inventory.

## Known limits

Accepted at the design review; each has a workaround.

1. A Gradle home redirected through a Java option (`JAVA_TOOL_OPTIONS` or another option setting
   `user.home` or `gradle.user.home`) is not followed, so edits under that home do not invalidate a
   remembered pass.
2. The per-session cap can be exceeded by up to 150 seconds per run: admission reserves the run
   budget, while the watchdog grace (30 s) and the crash charge (120 s) come on top.
3. A deadline that expires outside a running command (during hashing or between steps) reports the
   affected work as `no hit` or `not reached`, not as a blocking timeout.
4. A producer whose build tool does not track an input (for example Cargo profile variables such as
   `CARGO_PROFILE_RELEASE_OPT_LEVEL` for `rustWasm`) can be judged up to date and leave a stale
   output after that input changes.
5. A separate clone is not verified: the hook resolves the event's checkout and treats it as this
   repository only when its git common directory matches the launcher's, so a clone with its own
   `origin` is left alone. Run `python3 -I scripts/verify.py` there.
6. A session pruned after 50 newer sessions starts with a fresh allowance, so its block and time
   counters are forgotten. Stop again; the run is checked against the current tree.
7. A launcher killed with SIGKILL leaves its dispatched child running to its own `--deadline` (at
   most 1740 seconds) and holding `build/verify/lock` all the while. Wait that long (runs meanwhile
   report contention); the next run then takes the lock and its orphan cleanup reaps the child.
8. After `_stop_child` (SIGTERM, budget expiry, dispose or the Claude Code watchdog) a step's own
   process group (for example a running Gradle) outlives the child until the next run's orphan
   cleanup. Run the verifier again.
9. A `--hook` run during a background run reports contention and checks nothing
   (see "`--hook` mode"). Stop again after that run finishes.
10. A SIGTERM that reaches the launcher before it installs its handler (between dispatch and
    `_install_sigterm`) kills the launcher alone and leaves the dispatched child running to its
    deadline, holding the lock. The next run's orphan cleanup reaps it; stop again after it ends.

To sidestep the first four, run `python3 -I scripts/verify.py --branch`, or delete `build/verify/`
and the generated output (`web-extension/rust-wasm/pkg`, `web-extension/dist`, `build/rust/host-jni`)
and re-run.
