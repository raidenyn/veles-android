## ADDED Requirements

### Requirement: One verify command with two gates
The repository SHALL provide `scripts/verify.py`, a standard-library Python 3.8 script run as
`python3 -I scripts/verify.py`, that selects the affected nodes and runs their commands. With no gate flag it
MUST run the **task gate**: each selected node's `check` commands plus only the tests related to the changed
files. With `--branch` it MUST run the **branch gate**: each selected node's `check` commands plus all of its
`test` commands. Neither gate may run a `device` or `platform` command; such commands MUST be reported as expected
`NOT RUN`, and a changed file with no related test MUST be reported as a `tests: none related` note. It MUST NOT offer a mode that runs nodes the selection did not
select. It MUST accept `--base <sha>`, `--list` (alias `--dry-run`), `--hook claude`, `config set <key> <value>`
and `config show`.

#### Scenario: Task gate narrows tests
- **WHEN** a branch changes `scripts/components.py` and the default mode runs
- **THEN** the `tooling` node runs its checks and only the tests mapped to `components.py`, not the whole `scripts/tests` suite

#### Scenario: Branch gate runs every Linux test
- **WHEN** the same branch runs `verify.py --branch`
- **THEN** the `tooling` node runs all of its `test` commands

#### Scenario: Device test outside both gates
- **WHEN** the `android` node is selected in either gate
- **THEN** `connectedDebugAndroidTest` does not run and the node shows it as `NOT RUN (device test; real-device rule)` without changing the exit code

#### Scenario: Dry run
- **WHEN** `verify.py --list` runs on a branch whose selection succeeds
- **THEN** it prints each selected node with its reason, every planned step's argv and working directory and the notes, runs no probe, producer or command other than list-only target resolvers, shows `targets unresolved: <reason>` for a resolver that could not run, and exits 0

#### Scenario: Dry run with a selection error
- **WHEN** `verify.py --list` runs and the base cannot be resolved
- **THEN** it exits 2 with the fix and prints no plan

### Requirement: Selection follows the component map and the working tree
The command SHALL select nodes by applying `components.owner_nodes` from `scripts/components.py` to the union of
the paths changed between the base and `HEAD`, the staged paths, the unstaged paths and the untracked paths that
are not ignored, read NUL-delimited without rename detection, so that a move counts its old and new path and a
deleted path counts. The base MUST default to `git merge-base origin/master HEAD`. A global path MUST select every
node, an exact `global-overrides` entry MUST win over path ownership, and each selected node MUST be reported with a
file that selected it. An unusable base, a failed git read and a path the map does not account for MUST be
selection errors with the fix (`git fetch origin master` or `--base <sha>`; "add it to .github/components.json").
Only-unowned and empty change sets MUST be reported as such, never as a node pass. `scripts/components.py changed`
MUST NOT change.

#### Scenario: Uncommitted edit selects its node
- **WHEN** the only change is an unstaged edit to `web-extension/src/background.ts`
- **THEN** `web-extension`, `protocol` and `supply-chain` are selected, each naming that file

#### Scenario: Rust selects Android
- **WHEN** the branch changes `rust/veles-crypto/src/lib.rs`
- **THEN** `android` is selected and its line says it was selected by that file

#### Scenario: Moved file counts both sides
- **WHEN** a file is moved from `web-extension/src/` to `native-bridge/host/src/`
- **THEN** the nodes of both paths are selected

#### Scenario: Unaccounted path
- **WHEN** a changed path is not owned, global or unowned in the component map
- **THEN** the command exits 2 naming the path and the fix, and runs nothing

#### Scenario: Nothing changed
- **WHEN** no path differs from the base
- **THEN** it prints `verify: nothing changed against <base>`, prints no node line and exits 0

### Requirement: One command table
The node commands SHALL live in `scripts/verify-commands.json`, strict JSON, with one entry per node of the
component map. Each command MUST have an id, a kind (`check`, `test`, `device` or `platform`), a working directory
inside the repository, an argv list run without a shell, the `doc` file where it is documented, its requirements,
and either its declared `inputs` or `"reuse": false`; `platform` commands MUST name their platforms and CI job.
A command that executes any test MUST be a `test`, except that a test needing a device or emulator MUST be a
`device` command and a step that runs only on macOS or Windows MUST be a `platform` command. Shared producers of generated outputs and named requirements
MUST be declared once and referenced by id. Documented lines that are not run MUST be listed as `excluded`, keyed by
doc, argv and working directory, with a reason. The verifier MUST refuse to run with an invalid table, naming the
problem. The initial table MUST cover, in the branch gate, every Linux-runnable test that CI runs for a node,
including the extension's `npm run test:bundle` and `npm run test:browser` and the native host's Python
build-contract suite, each documented in its node's docs first; every CI step the gates leave out MUST be listed in
`docs/verify.md` under "CI only" with its reason.

#### Scenario: Mixed Gradle task is a test
- **WHEN** the table classifies `./gradlew bridgeTest`, which depends on Rust format and lint tasks
- **THEN** it is a `test`, and `bridgeInstall bridgeFormat bridgeLint bridgeTypecheck` are `check` entries split from the same documented line

#### Scenario: Extension branch inventory
- **WHEN** `verify.py --branch --list` runs with `web-extension` selected
- **THEN** the plan includes the unit, bundle and browser suites, and immediately before each of the bundle and browser suites it lists `rust.wasm` then `web-extension.build`

#### Scenario: Missing reuse declaration
- **WHEN** a command declares neither `inputs` nor `"reuse": false`
- **THEN** the verifier exits 2 naming the command

### Requirement: Related tests are resolved before running
In the task gate, a `test` with a `related` strategy SHALL be narrowed by resolving an explicit list of existing
test targets for the changed files, intersecting it with the strategy's `within` allowlist when given, and running
only those targets as separate argv items. The strategies MUST be `name-map` (source to test name, a changed test
being its own target, Android targets read from existing test files as fully qualified class names), `self`,
`crate` (the documented Gradle test task of each changed crate) and `files-arg` (a list-only resolver that never
runs tests). A resolver that cannot complete in a real run MUST make that test an unexpected `NOT RUN` with the resolver's error,
never a `none related` note. An empty target list MUST be a `tests: none related (<files>)` note under the node, not a run and not a
change to the node's result. Deleted paths MUST select nodes but MUST NOT be targets. A `test` without `related`
MUST run only in the branch gate.

#### Scenario: Changed test runs itself
- **WHEN** only `scripts/tests/test_components.py` changed
- **THEN** the task gate runs that test file and no other test of the node

#### Scenario: Android source with a test
- **WHEN** `android/app/src/main/.../RegexMessageHandler.kt` changed and `RegexMessageHandlerTest.kt` exists
- **THEN** the task gate runs `testDebugUnitTest --tests <package>.RegexMessageHandlerTest` from `android/`

#### Scenario: No related test
- **WHEN** only `web-extension/eslint.config.js` changed
- **THEN** the extension's checks run, its tests do not, and the node lists `tests: none related (web-extension/eslint.config.js)`

#### Scenario: Allowlist excludes branch-only suites
- **WHEN** a changed source file is imported by a bundle test that `npm test` does not name
- **THEN** that bundle test is not a task-gate target

### Requirement: Generated outputs are never stale
A command that requires a generated output SHALL have that output's whole producer chain (for example `rust.wasm`
then `web-extension.build`) run immediately before it, every time it runs, including before each of several
consumers in one run, and the producer's own build tool MUST decide whether to rebuild. Commands that need no generated
output MUST run before any producer. A producer's `FAIL` MUST make the node `FAIL` and its consumers not run, and a
producer's `NOT RUN` MUST make its consumers not run and the node `NOT RUN` unless another step of the node failed,
while steps that do not need its output still run. A node's result MUST be `FAIL` when any of its steps failed or
timed out, else `NOT RUN` when any was unexpectedly not run, and its details MUST keep every unexpected `NOT RUN`. After the producer succeeds the output MUST exist, else the consumer is `NOT RUN` with the fix.

#### Scenario: Formatting does not wait on WASM
- **WHEN** the Rust toolchain is missing and the extension is selected
- **THEN** the extension's format and lint checks run, `rust.wasm` is `NOT RUN` with its fix, and typecheck and the tests that need the package do not run

#### Scenario: Producer failure
- **WHEN** `./gradlew rustWasm` fails with a compile error while the extension is selected
- **THEN** the extension is `FAIL` naming `rust.wasm`, its format and lint checks still ran, and typecheck and the tests that need the package did not run

#### Scenario: Mixed failure and skip
- **WHEN** the `android` node's Gradle lint check is `NOT RUN` because no JDK 21 is found, and its raw-`Log` guard check, which needs no JDK, then fails
- **THEN** the node is `FAIL`, its details still list the lint check as `NOT RUN` with its fix, and the command exits 1

#### Scenario: Rebuilt between consumers
- **WHEN** the unit suite rebuilds `web-extension/dist/` and the bundle suite runs next in `--branch`
- **THEN** `web-extension.build` runs again immediately before the bundle suite

### Requirement: Requirements are probed and teach the fix
Before a step runs, the command SHALL probe that step's requirements under the step's effective environment,
using the resolver the table names (`java-home`, `android-sdk`, `android-ndk`, `path-tool`, `rustup-toolchain`,
`playwright-chromium`), which MUST resolve the tool the build itself would use. A failed requirement MUST make the
step an unexpected `NOT RUN`. For a tool, the message MUST state what is required and where it is pinned, what was
found, the candidates found on the machine, the exact fix (a `config set` command when one candidate matches and
the tool has a config key) and, when several match, to ask the user which one the project uses. For a missing path
or generated output, the message MUST name the path and the requirement's fix text. Environment failures a command reports itself, matched
by its declared exit codes or output patterns, MUST be `NOT RUN` with the matching fix.

#### Scenario: JDK unset
- **WHEN** `JAVA_HOME` is unset, no JDK 21 is on `PATH`, and `/usr/lib/jvm/java-21-openjdk-amd64` holds 21.0.12
- **THEN** `android` is `NOT RUN` naming `.java-version`, that candidate and `python3 -I scripts/verify.py config set java_home /usr/lib/jvm/java-21-openjdk-amd64`

#### Scenario: Supply-chain pin reported by the script
- **WHEN** `verify/verify-supply-chain.sh` exits 2 because the npm version differs from its pin
- **THEN** the step is `NOT RUN` with the declared fix, not `FAIL`

### Requirement: Machine tool config shared by every worktree
Tool paths SHALL be read from `<git-common-dir>/veles-verify/tools.json` (keys `java_home`, `android_sdk`,
`android_ndk`, `path_prepend`), applied only to the commands the verifier runs. The file MUST be written only by
`verify.py config set`, which validates the value against the repository's pins before saving, and read back by
`config show`. Without the file the inherited environment MUST be used. The verifier MUST NOT write
`android/local.properties`, and MUST warn under the `android` node and in `config show` when its `sdk.dir` differs
from the effective SDK.

#### Scenario: Shared across worktrees
- **WHEN** `config set java_home <path>` runs in a linked worktree
- **THEN** the main checkout and every other linked worktree resolve the same value

#### Scenario: Wrong version refused
- **WHEN** `config set java_home` names a JDK 17
- **THEN** nothing is saved and the command exits 2 naming `.java-version`

### Requirement: Compact output, logs and exit codes
The command SHALL print one line per selected node — `PASS`, `FAIL` or `NOT RUN (<reason>)` — with notes under it;
a selected node with no step that ran and no unexpected result MUST be `NOT RUN (nothing planned: <why>)`, never
`PASS`;
and for a failure the failing step, the exact re-run command with its working directory, the last 60 lines of its
output (each at most 300 characters) and the log path. The whole summary MUST be at most 8 KiB: when the details
do not fit, every node keeps its status line, the failure details that do not fit are dropped, and a line points to
`build/verify/last-run.json`. It MUST stop a node at its
first failing command and continue with other nodes. It MUST write one log per step under
`build/verify/runs/<run-id>/`, keep the newest 10 runs, and write `build/verify/last-run.json`. It MUST exit 1 when a
step failed or timed out; else 2 for a usage or selection error, an unexpected `NOT RUN` or a contended run; else 0.
A second run while one holds `build/verify/lock` MUST exit 2 at once.

#### Scenario: Failure summary
- **WHEN** `npm run lint` fails with 500 short lines of output and no other node fails
- **THEN** the summary shows its last 60 lines, `cd web-extension && npm run lint` and the log path, and the command exits 1

#### Scenario: Nothing planned is not a pass
- **WHEN** the `protocol` node is selected in the task gate and has no check and no related test for the changed files
- **THEN** it shows `protocol: NOT RUN (nothing planned: …)` and the exit code is unaffected

#### Scenario: Summary overflow
- **WHEN** three nodes fail with long tails that together exceed 8 KiB
- **THEN** every selected node's status line is printed, the summary is at most 8 KiB, and it points to `build/verify/last-run.json`

#### Scenario: Missing tool exit
- **WHEN** every planned step passes except one whose tool is missing
- **THEN** the command exits 2, never 0

#### Scenario: Unit tests inject time and processes
- **WHEN** `scripts/tests/test_verify.py` runs under `./gradlew scriptsTest`
- **THEN** it uses an injected clock, wait, runner and git reader, never sleeps or reads the wall clock, and covers selection, plans, strategies, exit codes, truncation and the config

### Requirement: The table cannot drift from the docs
The agent-file check SHALL run a drift check from the sibling module `docs/checks/verify_table.py`, within the
offline guard (which allows `shlex`), whose findings state the fix. It MUST fail on the table's structural
problems — duplicate or unknown ids, unresolved requirement or producer references, unknown kinds, a missing `doc`,
and a command or producer with neither `inputs` nor `"reuse": false` — and when the table's nodes differ from the component
map's; when a command or producer does not match a documented command of its `doc` with the same
argv and working directory (a task-only `./gradlew` entry may be a subset of a documented task-only line); when a
documented command in a node's `AGENTS.md` Build & Test or Commands section, or in a node rule that file lists, is
covered neither by an exact `excluded` entry, nor (a task-only `./gradlew` line) by the union of the entries'
tasks, nor by an entry with the same argv and working directory; and when `doc` is not the node's `AGENTS.md` or a
rule it lists (a producer's `doc` may be any node's, and counts for that node). Only shell fenced blocks are read; a
block's working directory is the repository root unless a preceding "Run from `<dir>`" sentence of its section
names another, and a `cd` line changes it for the following lines. The union of tasks that covers a task-only
`./gradlew` line MUST count only entries with the same working directory as that line.

#### Scenario: Documented command missing from the table
- **WHEN** `web-extension/AGENTS.md` documents `npm run test:e2e` and the table has no entry or exclusion for it
- **THEN** the agent-file check fails with "add `npm run test:e2e` to scripts/verify-commands.json or exclude it"

#### Scenario: Wrong working directory
- **WHEN** the table runs `./gradlew testDebugUnitTest` from `.` but `android/AGENTS.md` documents it under "Run from `android/`"
- **THEN** the drift check fails naming both directories

#### Scenario: Excluded setup line
- **WHEN** `scripts/AGENTS.md` documents `./gradlew installGitCliff` and the table excludes it with a reason
- **THEN** the drift check passes
