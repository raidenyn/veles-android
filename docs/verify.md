# verify.py: affected-node checks

`scripts/verify.py` runs the checks and tests of the nodes a change affects, then reports one line
per selected node. The commands, their requirements and their related-test rules live in
`scripts/verify-commands.json`; this page is the reference for that table and for the Claude Code
`Stop` hook that runs the verifier.

Read `.agents/rules/scripts-verify-commands.md` before editing the table.

## The two gates

- **Task gate** (`python3 -I scripts/verify.py`, the default): runs every selected node's `check`
  commands and, for each `test` command with a related-test strategy, only the tests related to the
  changed files. It is the fast signal and the gate the `Stop` hook runs.
- **Branch gate** (`python3 -I scripts/verify.py --branch`): runs every selected node's checks and
  all of its tests. It runs before a push (the pre-push rule) and in CI.

Both gates select the same nodes: the changed paths against `git merge-base origin/master HEAD`
(override with `--base <sha>`), mapped through `.github/components.json`. A path no node owns
selects nothing; an unaccounted path is a selection error naming `.github/components.json`.
`--list` (or `--dry-run`) prints the selection and the plan without running a probe or a command
(the list-only target resolvers are the exception). The task gate may reuse a remembered pass; the
branch gate never does.

Exit codes: `1` for a `FAIL` or a timeout, `2` for usage, a selection error, a contended run or an
unexpected `NOT RUN`, else `0`. A run with no changed path prints
`verify: nothing changed against <base>`; only unowned paths print
`verify: only unowned paths changed (<paths>)`; both exit `0`.

## Entries and kinds

`nodes.<node>.commands` holds the entries. Every entry has an `id`, a `kind`, a `cwd` (inside the
repository), the `run` argv (a list; no shell), the `doc` that documents the command, its
`requires`, its `inputs` (or `"reuse": false`), and optional `env_exit`/`env_patterns` that turn a
known environment failure into an unexpected `NOT RUN` with a `fix`.

Kinds:

- `check` — a static check, formatting check or lint;
- `test` — a test command, with an optional `related` strategy;
- `device` — a run that needs a real device or emulator; both gates report
  `NOT RUN (device test; real-device rule)`;
- `platform` — a macOS, Windows or release-only command; both gates report it as an expected skip.

`nodes.<node>.excluded` records a documented line or CI step with no entry of its own, with the
reason. `nodes.<node>.none` states why a node has no commands. Producers are not command kinds:
they are `producers[]` entries referenced by a `generated` requirement (see below).

The `doc` is the node's `AGENTS.md` or a rule its `## Rules` lists. `docs/checks/verify_table.py`
checks both directions: every entry and producer is a documented command with the same working
directory, and every documented command in a node's `AGENTS.md` (or listed rule) is covered by an
entry or an `excluded` line. `python3 docs/checks/check_agent_files.py --root .` runs that check, so
change the doc and the table together.

The verifier's own documented lines (`python3 -I scripts/verify.py` and `--branch`) are `excluded`
in the `tooling` node: they run the table, so the table cannot gate on them.

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

`./gradlew bridgeInstall` is `excluded` because it is setup (`npm ci --ignore-scripts` into
`native-bridge/node_modules`, also represented as a requirement); the scaffold's static checks and
`bridgeTest` have their own entries. `installer.fixture` is a `platform` entry with the honest
`ci` value `"none (no CI job; a local fixture producer)"`.

## Remembered passes

Only the `Stop` hook reuses a pass, and only for the task gate. The key is a sha256 over the plan,
the node entry, the effective environment (build-affecting variables and every resolved tool's path
and version), the affected tracked and changed files, the declared input trees, and each consumed
generated output. The source side is computed before and after the run; a record is written only
when both are equal and no step declares `"reuse": false`. Records live in
`build/verify/passes.json` and are deleted on that node's `FAIL` or `NOT RUN`; any read that cannot
complete is a miss. A remembered node prints `PASS (remembered)`.

Choose `"reuse": false` when you cannot bound a step's inputs; `--branch` and CI always run fresh.

## Generated files

- `build/verify/runs/<run-id>/<step-id>.log` — one log per step (stdout and stderr merged); the
  newest 10 run directories are kept.
- `build/verify/last-run.json` — version 1, the last run's nodes and steps.
- `build/verify/passes.json` — the remembered task-gate passes.
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

## The Claude Code Stop hook

`.claude/settings.json` runs the `Stop` entry's second command,
`python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true` (timeout 1800), after the
hooked-rules engine command. The agent-file check accepts exactly that pair and rejects a missing
verify command, the wrong order, a third command or another timeout.

The hook is bounded so it cannot trap a session: the launcher relays a child with an absolute
deadline of at most 1740 seconds (45 seconds are reserved for termination and the report), and its
watchdog kills and reaps only that child 30 seconds past the child's admitted deadline. Admission
allows at most 5 blocks and 3600 seconds of run time per session; a reservation is
`min(1740, time left, time to the deadline)`. After 3 repeats of the same problem the hook passes
with a notice instead of blocking again.

Output forms: a problem is `{"decision":"block","reason":"<summary>"}` whose reason starts
`verify: the task gate found problems; fix them and stop again (attempt <n> of 3)`; a pass with a
notice is `{"systemMessage":"<text>"}`; a clean pass or a zero-work run prints nothing. Every
internal failure fails open: one stderr line, no stdout, exit `0`. `stop_hook_active` is ignored.
Hook state and reservations live under `<git-common-dir>/veles-verify/`.

## CI only

The table is local and Linux-first. These stay "CI only", each with the reason the inventory
recorded:

- **Device and emulator**: Android instrumented tests (`./gradlew connectedDebugAndroidTest`) need
  KVM and an emulator image; the table has the `device` entry, and both gates report it as an
  expected skip.
- **macOS and Windows**: Xcode/`DEVELOPER_DIR`, the NSIS, MSVC and Python 3.12 provisioning, the
  release installer builds, and the owner-only signing/notarization steps. No resolver exists for
  them; the `installer` node's `platform` entries are expected skips, and the rest is recorded in
  the inventory.
- **Build/-cleaning sequences**: CI's Rust `jni-integration` sequence (`./gradlew clean`,
  `rust/scripts/assert-clean.sh`, the APK/JNI assertions, `npm ci`) would delete `build/verify/`
  (the verifier's own state and lock), so it is not a local gate.
- **CI provisioning**: the exact Node/npm pins (26.8.1 / 11.19.0), the JDK and Android SDK/NDK
  setup, Playwright Chromium, and `npm ci`/`installCargoDeny` are represented as requirements and
  their fix text, not as commands.
- **Release and packaging**: `assembleRelease`, the extension's `npm run package`, the native
  product builds and the release-only workflows are products, not gates.
- **Duplicates and aggregates**: `./gradlew bridgeInstallerSupplyChain` is `excluded` in favour of
  the `supply-chain` node's `verify/verify-supply-chain.sh` (the same script); the supply-chain doc's
  `node --test verify/test/*.test.mjs` line is `excluded` because the expanded eight-file aggregate
  is the `tooling` entry.
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

To sidestep all four, run `python3 -I scripts/verify.py --branch`, or delete `build/verify/` and the
generated output (`web-extension/rust-wasm/pkg`, `web-extension/dist`, `build/rust/host-jni`) and
re-run.
