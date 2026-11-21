## ADDED Requirements

### Requirement: A complete branch run writes a branch pass
`scripts/verify.py --branch` SHALL write the branch-pass record `build/verify/branch-pass.json` of its worktree only
after a **complete** run: exit code 0; no `--deadline` and no `--base` argument; no step reported as not reached; and
`git status --porcelain` empty with the same HEAD both before selection and after the last step. The record MUST be
version 1 JSON holding the HEAD commit, the merge base it diffed against and the selected nodes, and MUST hold no
time. Every `--branch` run MUST delete an existing record right after it takes the run lock, so only a complete
run leaves one; a run that does not get the lock MUST leave the record alone. The
task gate and the hook MUST NOT write or delete it. One module, `scripts/branch_pass.py` (standard library only,
Python 3.8+), MUST own the record's path, format, writer and check, and both the verifier and the hooked-rules engine
MUST use it. Its check MUST, for a working directory, resolve the worktree, read the record, and pass only when the
record's HEAD equals `git rev-parse HEAD` and its base equals `git merge-base origin/master HEAD` there; every other
outcome, including a git failure or an unreadable record, MUST fail with a message naming what differs.

#### Scenario: Complete run
- **WHEN** `verify.py --branch` exits 0 on a clean tree with no `--base` or `--deadline` and HEAD unchanged
- **THEN** `build/verify/branch-pass.json` names that HEAD and base, and the check in that worktree passes

#### Scenario: Incomplete run
- **WHEN** a run that took the run lock exits 1 or 2, was given `--deadline` or `--base`, left a step not reached, started or ended with a dirty tree, or HEAD changed during it
- **THEN** no record exists afterwards, and the check fails with "no branch pass"

#### Scenario: Contended run keeps a pass
- **WHEN** a complete run wrote a record and a second `--branch` run then exits 2 with "verification already running"
- **THEN** the record is unchanged

#### Scenario: New commit after the pass
- **WHEN** a record exists for commit A and HEAD is now B
- **THEN** the check fails with a message naming both short hashes

#### Scenario: Base moved
- **WHEN** the record's base differs from the current merge base with `origin/master`
- **THEN** the check fails naming the base

## MODIFIED Requirements

### Requirement: One command table
The node commands SHALL live in `scripts/verify-commands.json`, strict JSON, with one entry per node of the
component map. Each command MUST have an id, a kind (`check`, `test`, `device` or `platform`), a working directory
inside the repository, an argv list run without a shell, its requirements,
and either its declared `inputs` or `"reuse": false`; `platform` commands MUST name their platforms and CI job.
A command that executes any test MUST be a `test`, except that a test needing a device or emulator MUST be a
`device` command and a step that runs only on macOS or Windows MUST be a `platform` command. Shared producers of generated outputs and named requirements
MUST be declared once and referenced by id. A command or producer MUST NOT carry a `doc` field and a node MUST NOT carry
an `excluded` list: the table is the single source of the commands it runs, and the verifier MUST reject either key as
a table error naming it. The verifier MUST refuse to run with an invalid table, naming the
problem. The initial table MUST cover, in the branch gate, every Linux-runnable test that CI runs for a node,
including the extension's `npm run test:bundle` and `npm run test:browser` and the native host's Python
build-contract suite; every CI step the gates leave out MUST be listed in
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

#### Scenario: Stale doc field
- **WHEN** a command or producer carries `doc`, or a node carries `excluded`
- **THEN** the verifier exits 2 naming the entry and the field

### Requirement: The table cannot drift from the docs
The agent-file check SHALL run a check from the sibling module `docs/checks/verify_table.py`, within the offline
guard (which allows `shlex`), whose findings state the fix. It MUST fail on the table's structural problems —
duplicate or unknown ids, unresolved requirement or producer references, unknown kinds, a `doc` or `excluded` key,
and a command or producer with neither `inputs` nor `"reuse": false` — and when the table's nodes differ from the
component map's. It MUST also fail when any shell fenced block of a node's `AGENTS.md`, or of a node rule that file
lists, documents a command the verifier runs as a `check` or `test`: a line whose argv and working directory
equal those of a `check` or `test` entry of any node, or a task-only `./gradlew` line every task of which is a task of
`check` or `test` entries with the same working directory; Gradle option arguments that start with `-` and take no
value (for example `-q` or `--offline`) are ignored when comparing task-only lines. A line with any other argument
(for example `--tests "<class>"`) is a targeted line and MUST pass, and so MUST `device`, `platform` and producer
commands and lines the table does not hold. The check MUST NOT require any documented line to be in the table. Only
shell fenced blocks are read; a block's working directory is the repository root unless a preceding "Run from
`<dir>`" sentence of its section names another, and a `cd` line changes it for the following lines.

#### Scenario: Doc repeats a check
- **WHEN** `android/AGENTS.md` documents `./gradlew spotlessCheck detekt lintDebug` under "Run from `android/`" and each task is a `check` entry run from `android/`
- **THEN** the agent-file check fails with "`./gradlew spotlessCheck detekt lintDebug` is run by verify.py (<entry ids>); remove it from the doc"

#### Scenario: Targeted line allowed
- **WHEN** `android/AGENTS.md` documents `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"`
- **THEN** the check passes, although `./gradlew testDebugUnitTest` is a `test` entry

#### Scenario: Build and producer lines allowed
- **WHEN** `rust/AGENTS.md` documents `./gradlew rustWasm` (a producer) and `./gradlew rustJni` (not in the table)
- **THEN** the check passes

#### Scenario: Documented command missing from the table
- **WHEN** `web-extension/AGENTS.md` documents `npm run test:e2e` and the table has no entry for it
- **THEN** the check passes: a documented line no longer needs a table entry or an exclusion

#### Scenario: Wrong working directory
- **WHEN** the table runs `./gradlew testDebugUnitTest` only from `android/` and a doc documents it with the repository root as its working directory
- **THEN** the line is not a duplicate and the check passes

#### Scenario: Excluded setup line
- **WHEN** `scripts/AGENTS.md` documents `./gradlew installGitCliff` and the table neither runs nor excludes it
- **THEN** the check passes
