## Context

The approved `brainstorm.md` and `proposal.md` fix what is built: a local verify command with two gates, one
command table, a drift check and a Claude Code `Stop` hook. This document fixes how it is built: the module
layout, the data and state formats, the algorithms with a guarantee attached (selection, plans, generated
outputs, fingerprints, the hook's dispatch, deadline, admission and bounds), and the changes to existing tools.

Current state, as read from the repository:

- `scripts/components.py` (stdlib, Python 3.8 floor) owns selection. `owner_nodes(file, cmap)` returns `None`
  for a global or unaccounted path and a set of node names otherwise; exact `global-overrides` win first,
  then `global`/`ci-global`, node `paths`/`ci-paths`, `retired-paths`, `basename-owners`, `release-paths`
  and `unowned`. `changed` only diffs `<base>..HEAD`.
- Node commands are prose in ten `AGENTS.md` files (`docs/AGENTS.md` under `## Commands`) and in
  `.agents/rules/android-lint-before-push.md`, whose lint command is inline prose, not fenced. CI runs a
  superset (`.github/workflows/check-*.yml`); the extension's CI runs `npm run build` before its bundle and
  browser tests and provisions Playwright Chromium; `npm run test:browser` reads `web-extension/dist/`.
- `.claude/settings.json` wires `agent_rules.py hook --format claude` (5 s) on five events, `Stop` included;
  `docs/checks/check_agent_files.py` `_hook_findings` requires exactly one entry with one command per event,
  and `_check_overrides` only checks the owner set of an override that is present.
- `docs/checks/check_offline.py` lets a docs check import only an allowlisted set of stdlib modules (no
  `shlex`, no `hashlib`) or sibling modules in `docs/checks/`, and call only `git`.
- `scripts/agent_rules.py` keeps state under `<git-common-dir>/agent-rules/`, discovers the checkout with
  `git -C <cwd> rev-parse --show-toplevel --git-common-dir` after stripping `GIT_*` overrides and resolves a
  relative common dir against `cwd`, locks with `fcntl.flock`/`msvcrt`, and ignores `stop_hook_active`.
- The extension pins `vitest` 4.1.12, whose `related` subcommand treats every positional operand as a changed
  file, applies no test filter and succeeds when nothing matches.
- Tooling CI runs on `ubuntu-latest`; `scripts/tests/test_project_flow_py38.py` checks 3.8 compatibility by
  reading sources.

Constraints: standard library only; Python 3.8 syntax and APIs; `python3 -I`; no wall-clock or sleep in unit
tests; nothing committed outside tracked files; no change to what CI jobs run beyond the two coverage effects
the proposal names.

## Goals / Non-Goals

**Goals:**

- Every pass is earned: no reuse unless everything that decided the result is unchanged, and no command
  against stale generated output.
- The Stop hook cannot trap the agent: every block bounded per problem and per session, a hard deadline over
  the whole hook, and every internal failure failing open.
- One table that docs and the verifier cannot silently drift from, enforced by an offline-compatible check.
- Fully unit-testable with injected git, runner, clock, waits, filesystem roots and environment.

**Non-Goals:**

- A whole-repository mode (`--full`, `--node`), OpenCode parity, device and emulator runs, macOS/Windows
  steps, changes to CI jobs or to `components.py changed`.
- Parallel execution of commands (sequential keeps logs and Gradle daemons predictable).

## Decisions

### D1. Module layout

`scripts/verify.py` is the single entry point and module (the repository's convention, like
`project_flow.py`), with pure functions at the core and side effects behind small injectable classes:

| Part | Responsibility |
|---|---|
| `Git` | read-only queries (D3), checkout discovery (D11), blob ids and modes |
| `Table` | load and validate `verify-commands.json` (D2) |
| `select()` | changed files → selected nodes with a reason each (D3) |
| `plan()` | nodes × gate → ordered steps and notes (D4) |
| `Probe` | requirement resolvers under the effective environment (D7) |
| `Runner` | one argv in a process group under a deadline, logged, with status and tail (D9) |
| `Fingerprint` | the remembered-pass key (D6) |
| `Report` | node lines, caps, `last-run.json`, exit code (D10) |
| `Config` | `config set|show`, the machine config (D8) |
| `Hook` | `--hook claude`: dispatch, deadline, admission, bounds, JSON (D11, D12) |
| `main()` | argument parsing and wiring |

It imports `components` (and `agent_rules.discover_checkout`, D11) after inserting its own directory into
`sys.path`. A source-reading 3.8 check joins the existing one.

**Modes.** Default (task gate), `--branch`, `--list`/`--dry-run`, `--base <sha>`, `--hook claude`
(`--dispatched` and `--deadline <epoch seconds>` internal), `config set <key> <value>`, `config show`.

`--list` runs selection and planning. It runs no probe, producer or command, with one exception: the
`files-arg` target resolvers, which are list-only by contract (D4) and run under a 60-second deadline each.
It prints each node, its reason, every planned step's argv and `cwd`, the notes, and for a resolver that
could not run (for example because its generated input does not exist yet) `targets unresolved: <reason>`
in place of the narrowed argv. It exits 0, or 2 on a selection error.

Alternative considered: a `scripts/verify/` package. Rejected: no other script is a package.

### D2. The table format

`scripts/verify-commands.json`, strict JSON:

```json
{
  "version": 1,
  "requirements": {
    "<id>": {"kind": "tool", "resolver": "<resolver>", "args": {}, "fix": "<text>"},
    "<id>": {"kind": "path", "path": "<repo-relative>", "fix": "<text>"},
    "<id>": {"kind": "generated", "path": "<repo-relative output dir>", "producer": "<producer id>",
             "fix": "<text>"}
  },
  "producers": [
    {"id": "<id>", "cwd": ".", "run": ["./gradlew", "rustWasm"], "doc": "rust/AGENTS.md",
     "requires": ["<id>"], "inputs": [{"path": "rust/"}, {"path": "protocol/"}],
     "env_exit": [{"code": 2, "fix": "<text>"}], "env_patterns": [{"regex": "<re>", "fix": "<text>"}]}
  ],
  "nodes": {
    "<node>": {
      "commands": [
        {"id": "<node>.<name>", "kind": "check|test|device|platform", "cwd": "<repo-relative>",
         "run": ["argv"], "doc": "<repo-relative>", "requires": ["<id>"],
         "inputs": [{"path": "<prefix>"}, {"path": "<prefix>", "mode": "stat"}], "reuse": true,
         "env_exit": [], "env_patterns": [],
         "platforms": ["macos"], "ci": "<job name>",
         "related": {
           "strategy": "files-arg|name-map|crate|self",
           "match": ["<path prefix>"],
           "resolve": ["argv", "{changed}"],
           "sources": "<glob>", "tests": "<glob or template>",
           "crates": {"<path prefix>": ["argv"]},
           "within": "<allowlist source>",
           "run": ["argv", "{targets}"]
         }}
      ],
      "excluded": [{"doc": "<repo-relative>", "cwd": "<repo-relative, default .>",
                    "line": "<documented command>", "why": "<reason>"}],
      "none": "<why, only when commands is empty>"
    }
  }
}
```

`related` fields by strategy: `files-arg` uses `match`, `resolve` and `run`; `name-map` uses `sources`,
`tests` and `run`; `self` uses `tests` and `run`; `crate` uses `crates`. `within` is optional for every
strategy and names an allowlist the resolved targets are intersected with: `package.json:scripts.test`
means the test files that script names.

Validation (`Table`, and the subset the drift check needs, D13): exactly the component map's nodes; unique
ids; every `requires`/`producer` reference resolves; known `kind`, `resolver` and `strategy`, with exactly the
fields of that strategy; `platform` commands carry `platforms` and `ci`; every executable command and producer
declares `inputs` or `"reuse": false`; a reusable command declares as `tool` requirements every tool it
invokes (D6); `cwd` inside the repository; `run` a non-empty list of strings; `{targets}` and `{changed}` only
inside `related`; every `env_exit` and `env_patterns` entry carries a `fix`.

**Starting producers and requirements for the extension** (the branch inventory the brainstorm requires):

- `rust.wasm` (`./gradlew rustWasm`, output `web-extension/rust-wasm/pkg`), required by typecheck and every
  extension test;
- `web-extension.build` (`npm run build` in `web-extension/`, output `web-extension/dist`, itself requiring
  `rust.wasm`), required by `test:bundle` (its suite reads `dist/`; its guard build writes a different
  directory) and `test:browser` (CI builds before both);
- a `tool` requirement with the `playwright-chromium` resolver (D7) for `test:browser`.

### D3. Selection

1. Base: `--base`, else `git merge-base origin/master HEAD`; a missing ref or non-commit is a selection error
   with `git fetch origin master` or `--base <sha>` as the fix.
2. Paths, NUL-delimited and `--no-renames` (a move is a delete plus an add):
   `git diff --name-only --no-renames -z <base> HEAD`, the same with `--cached`, the same for the working
   tree, and `git ls-files --others --exclude-standard -z`. The union, sorted.
3. `components.unaccounted(files, cmap)` non-empty → selection error listing them in `components.py`'s
   wording, with "add them to .github/components.json" as the fix.
4. Per file `owner_nodes`; `None` selects every node. An empty set (unowned) selects nothing.
5. A node's reason is the first file in sorted order that selected it.

Selection errors are exit 2 in the CLI and a block with the fix in the hook (D12).

Alternative considered: extending `components.py changed`. Rejected: it changes CI's selector.

### D4. Plans and kinds

`plan(node, gate, changed)` returns ordered steps and notes:

1. every `check` and every planned test that needs no generated output, in table order;
2. then, for each planned command that needs a generated output, in table order: its producer chain
   immediately before it, then the command (D5);
3. branch gate: every `test` is planned; task gate: per `test` with `related`, the narrowed step when it has
   targets, else a `tests: none related (<files>)` note; a `test` without `related` is a branch-only note;
4. `device` and `platform` commands as expected-skip notes.

**Failure isolation.** A step whose own requirement is missing, or whose producer is `NOT RUN` or `FAIL`, is
`NOT RUN` (unexpected) or not run respectively, and nothing else is affected by that alone. A `FAIL` of a
command (not a producer) stops the node's remaining steps, as the brainstorm requires; other nodes continue.
Because steps without generated inputs run first, a missing Rust tool never prevents formatting or lint.

**Target resolution is separate from running.** A strategy resolves an explicit list of existing test
targets; it is then intersected with `within` when given; the run step receives only those (`{targets}`, one
argv item each, relative to `cwd`). An empty list is a `none related` note, never a run. Deleted paths select
nodes but are never targets.

- `name-map`: changed files matching `sources` map through the `tests` template; a changed file matching the
  `tests` glob is its own target. For Android the target is the class read from an existing file:
  `android/app/src/test/**/<Stem>Test.kt` is found by walking, its `package` line and file name give
  `<package>.<Stem>Test`, which becomes `--tests <fqn>`. No existing file → no target.
- `self`: changed files matching `tests` are their own targets.
- `crate`: the changed paths' crates, each mapped to its documented Gradle test task, which runs under the
  build's pinned toolchain (`bridgeHostTest`, `bridgeInstallerTest`; never a raw `cargo test`). Such a command
  must be narrower than the node's whole test set: the host and installer nodes also have Python suites that
  stay branch-only. The `rust` node is the stated limit: its one crate's command, `./gradlew rustTest`, is all
  of its tests.
- `files-arg`: `resolve` is a list-only argv (it must never run tests) that receives the changed files
  (`{changed}`) under `match` and prints related test files, one per line. The result is intersected with
  `within` and becomes `{targets}` for `run` (`npx vitest run <targets>`). The first implementation task checks
  whether vitest 4.1.12 offers such a resolver. If it does not, the extension uses `name-map` plus `self`,
  with the same `within: package.json:scripts.test`, and `docs/verify.md` says so.

### D5. Generated outputs

A command that `requires` a `generated` requirement has its producer chain run **immediately before it**,
every time it runs, even when an earlier step of the same run ran that producer: another step may have
rewritten the output in between (the unit suite's `package-output` test rebuilds `dist/`). The producer's own
build tool (Gradle's up-to-date checks over its declared inputs, such as `rust/toolchain-tools.toml`; Vite for
`dist/`) decides whether anything is rebuilt, so the repeat costs only its up-to-date check. `verify.py` keeps
no freshness state.

The producer is a step like any other: requirements probed first; `env_exit`/`env_patterns` matches are
unexpected `NOT RUN` with the entry's fix; any other non-zero exit is the producer's `FAIL`, which marks its
consumer not run and the node `FAIL` (D4). After it succeeds the output must exist; otherwise `NOT RUN` with
the requirement's fix. The consumed output's hash (D6) is captured right after the producer finishes and
right before the consumer starts.

### D6. Remembered passes

**What is hashed.** A sha256 over a canonical JSON document (sorted keys, `separators=(",", ":")`, UTF-8):

```json
{"v": 1, "gate": "task", "node": "<node>", "base": "<sha>",
 "plan": [["<step id>", "<cwd>", ["argv"]]],
 "entry": "<sha256 of the canonical node entry, its producers and requirements>",
 "env": {"vars": {"<name>": "<value>"}, "config": "<sha256 of tools.json or null>",
         "resolved": {"<requirement id>": "<real path>@<version output>"},
         "local_properties": "<sha256 of android/local.properties or null>"},
 "files": "<sha256 of the sorted [path, mode, id] list>",
 "inputs": "<sha256 of the sorted [path, mode, size, digest] list>",
 "generated": {"<path>": "<sha256 over the output tree>"}}
```

- `env.vars`: the values of an allowlist of build-affecting variables, read from the effective environment
  (D8): `PATH`, `HOME`, `JAVA_HOME`, `JAVA_OPTS`, `_JAVA_OPTIONS`, `JDK_JAVA_OPTIONS`, `ANDROID_HOME`,
  `ANDROID_SDK_ROOT`, `ANDROID_NDK_HOME`, `CI`, and every name starting with `GRADLE_`, `ORG_GRADLE_`, `JAVA_TOOL_`, `NODE_`, `NPM_CONFIG_`/`npm_config_`, `CARGO_`, `RUSTUP_`,
  `RUSTFLAGS`, `RUSTC`, `VELES_`, `PLAYWRIGHT_`.
- `env.resolved`: for every `tool` requirement of every planned step, the real path the resolver found and
  the full output of its version command. A reusable command must declare every tool it invokes as a `tool`
  requirement (validated, D2), so changing Node or npm at the same `PATH` location changes the key even when
  the build itself checks the pin; a command that cannot list its tools declares `"reuse": false`.
- `files`: every tracked or changed path `p` with `owner_nodes(p)` naming the node or `None`. A tracked,
  unmodified file contributes its index mode and blob id (`git ls-files -s -z`); a modified, staged or
  untracked one its working-tree mode (`stat`) and content sha256; a deleted one `deleted`.
- `inputs`: every regular file under the planned steps' declared `inputs`, walked on the filesystem so that
  ignored files count. A path may be repository-relative or start with `{gradle_user_home}/` for a machine
  input: every `./gradlew` command declares `{gradle_user_home}/gradle.properties` and
  `{gradle_user_home}/init.d/`, which Gradle reads. `{gradle_user_home}` resolves as Gradle does: the
  effective `GRADLE_USER_HOME`, else `$HOME/.gradle`; when `GRADLE_OPTS` or `JAVA_OPTS` sets
  `gradle.user.home`, the home cannot be resolved safely and the node is non-reusable for that run.
  An input entry of mode `content` (the default) contributes mode, size and content sha256. Mode `stat`, for
  large ignored trees such as `web-extension/node_modules/`, contributes device, inode, mode, size and the
  **status-change time** (`st_ctime_ns`) of every file and directory: any write to a file's contents or
  metadata updates its ctime, and unlike the modification time it cannot be set back by `touch` or by an
  archive extractor, so an edit to an installed dependency always changes the key without reading every
  file. A command whose inputs cannot be bounded this way declares `"reuse": false`.
- `generated`: per consumed output directory, the sorted list of `[relative path, mode, sha256]` for every
  regular file, captured right before each consumer starts (D5). When two consumers in one run capture
  different digests for the same path, or a symlink or special file appears anywhere in the output, the node
  is non-reusable for that run.
- **Stability.** The source side of the key (`plan`, `entry`, `env`, `files`, `inputs`) is computed before
  the run and again after it; a record is written only when both are equal. The `generated` part is the one
  captured before each consumer, because the run's producers may legitimately create or refresh it. A node
  with any `"reuse": false` step is never recorded.

**Records.** `build/verify/passes.json`, `{"<gate>/<node>": "<digest>"}`; written only after a task-gate
pass (hook or manual) with a stable key; deleted on that node's `FAIL` or `NOT RUN`. Only the hook reads
them, after re-probing the plan's requirements and recomputing the key, the `generated` part from the
outputs as they are now. Any read that cannot complete is "no hit", never an error.

### D7. Requirements and probes

Probes run under the step's effective environment (D8), under the run deadline, and in the directory the
build resolves from. Each `tool` requirement names one resolver from a fixed set:

| Resolver | Resolves like | Checks |
|---|---|---|
| `java-home` | Gradle: `JAVA_HOME`, else `java` on `PATH` | `java -version` major equals `.java-version`'s |
| `android-sdk` | `android/local.properties` `sdk.dir`, else `ANDROID_HOME`, else `ANDROID_SDK_ROOT` | directory with `platform-tools` or `cmdline-tools` |
| `android-ndk` | the order in `rust/build.gradle.kts`, cited in code: the **first existing** candidate directory is selected | that directory's `source.properties` `Pkg.Revision` equals `rust/ndk-version`; a mismatch is `NOT RUN`, never a fall-through to the next candidate |
| `path-tool` | an executable on the effective `PATH`, from `args.cwd` | `args.version_cmd` output against `args.min`, `args.exact` or a pin file |
| `rustup-toolchain` | `rustup show active-toolchain` run in the directory of `args.toolchain_file` | the file's pinned channel |
| `playwright-chromium` | `VELES_TEST_CHROMIUM` when set, else the executable path the pinned `playwright-core` reports, queried with `node` in `web-extension/` | the file exists and is executable |

Where a build checks a pin itself (the supply-chain verifier's exact Node and npm checks), the probe still
records the tool's path and version for the key (D6) with `path-tool` and no comparison, and the command's
`env_exit`/`env_patterns` carry the pin failure with its fix. The `playwright-chromium` fix names the install
command CI uses and CI's caveat that the browser build is tied to the pinned Ubuntu image.

Failure messages: required value and where it is pinned; what was found; a bounded candidate scan
(`/usr/lib/jvm/*`, `$HOME/.android-sdk`, `$HOME/Android/Sdk`, `$HOME/.cargo/bin`,
`$HOME/.nvm/versions/node/*/bin`; at most 5 entries each, with versions); the exact fix
(`verify.py config set <key> <candidate>` when one candidate matches, the requirement's `fix` otherwise); and
"if several match, ask the user which one this project uses" when several match.

### D8. Machine config and effective environment

`<git-common-dir>/veles-verify/tools.json`, version 1, keys `java_home`, `android_sdk`, `android_ndk`,
`path_prepend`. The effective environment of a step is the inherited environment with `JAVA_HOME`,
`ANDROID_HOME`/`ANDROID_SDK_ROOT`, `ANDROID_NDK_HOME` set from the config and `PATH` prefixed with
`path_prepend` then `$JAVA_HOME/bin`. `config set` validates as the matching resolver does, writes
atomically (temp file and `os.replace`) under the config lock and prints `show`. `config show` prints the
path, each key, its value and resolved version.

A difference between `android/local.properties` `sdk.dir` and the effective SDK is reported both by
`config show` and under the `android` node of every run that plans it; the `android-sdk` resolver reports
the SDK Gradle will actually use. `verify.py` never writes `local.properties`.

### D9. Running commands and deadlines

`Runner` starts each argv (commands, producers, resolvers, version commands) with `start_new_session=True`
on POSIX (`CREATE_NEW_PROCESS_GROUP` on Windows), `stdin` closed, stdout and stderr merged into the step's log,
the effective environment and `cwd`. While a group runs, its process-group id is recorded in
`build/verify/running.json` with the run id; it is removed when the group has been reaped.

**One run deadline, enforced, not just checked.**

- Every external call goes through `Runner` with `min(step limit, deadline - 45 s)`; past it: `SIGTERM` to the
  group, a 10-second grace, `SIGKILL`, reap, and the step is `timeout`. Steps not started are "not reached".
- The run lock `build/verify/lock` is tried once, non-blocking: if it is held, the run is contention at once
  (CLI exit 2, hook pass with a notice). State locks (`hook-state.json`, the machine config) are short and are
  retried through the injected wait for at most 10 seconds and never past the deadline; failing that is an
  internal error (the hook fails open).
- Filesystem walks (fingerprints, generated trees, Android test discovery) check the deadline per file and
  stop with "no hit" or "not reached".
- 45 seconds before the deadline are reserved for termination, settlement and the report; the report is
  built from what is already known and never waits on a child.
- **Orphans, one owner.** The run lock is an `flock`, released by the kernel when its holder dies. The
  next process to obtain it is the **only** cleanup owner: every group recorded in `running.json` under
  another run id belongs to a dead run, so it sends `SIGTERM` to each, then `SIGKILL` after the grace, and
  confirms each group is gone by `os.killpg(pgid, 0)` raising `ProcessLookupError` (it cannot `waitpid` a
  process it did not start). Only when every recorded group is confirmed gone does it clear those entries
  and plan; a group still alive after 15 seconds makes this run contention (nothing runs). Process liveness
  is decided by the lock, never by reservations, which only do accounting. Nobody else reads or signals
  `running.json` groups.

The tail is the last 60 lines of the log, each cut at 300 characters. The clock and the wait primitive are
injected, so tests never sleep. Gradle daemons outlive their client by design. The CLI's deadline is
unbounded unless `--hook` or `--deadline` sets it.

### D10. Report and exit codes

- Per node: `PASS`, `FAIL`, `NOT RUN (<reason>)`, hook-only `PASS (remembered)`; notes under the node for
  `tests: none related (<files>)`, branch-only tests, device and platform skips, the SDK warning. A node with
  no step that ran and no unexpected result prints `NOT RUN (nothing planned: <why>)`.
- Failure block: step id, re-run command (`cd <cwd> && <argv>` quoted with `shlex.quote`), tail, log path.
  The summary is capped at 8 KiB; past it, later nodes keep their status line and a pointer to
  `build/verify/last-run.json`.
- Zero-work outcomes: no changed path prints `verify: nothing changed against <base>`; only unowned paths
  print `verify: only unowned paths changed (<paths>)`; both exit 0 and print no node line. A run where every
  selected node is remembered (hook) or has only expected `NOT RUN`s exits 0 with its node lines.
- Exit: `1` on any `FAIL` or timeout; else `2` on usage, a selection error, an unexpected `NOT RUN` or a
  contended run; else `0`. Expected `NOT RUN`s: device, platform, branch-only, none related, nothing planned.
- Files: `build/verify/runs/<run-id>/<step-id>.log`, `build/verify/last-run.json` (version 1). Run id:
  `<UTC yyyymmddThhmmss from the injected clock>-<pid>`. The newest 10 run directories are kept.
- Concurrency: `build/verify/lock` for the run; a CLI that cannot take it exits 2 ("verification already
  running").

### D11. Hook dispatch

1. The settings command is
   `python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true`, timeout 1800.
2. The launcher notes its start time from the injected clock, reads stdin (at most 1 MiB; invalid JSON fails
   open), takes `cwd`, and discovers its own checkout and the target's with `agent_rules.discover_checkout`
   (imported, not copied). It inherits that helper's semantics as they are: it strips the helper's fixed set
   of git selection variables (`GIT_DIR`, `GIT_WORK_TREE` and the others it lists), resolves a relative common
   dir against the queried directory, and returns nothing for a checkout without `.agents/hooked-rules/`, which
   every checkout of this repository has. `verify.py` then applies `os.path.realpath` to both common dirs.
3. The target is a checkout of this repository when its resolved common dir equals the launcher's. A
   separate clone or another repository fails open silently (stated limit).
4. **The gate owner and the watchdog.** The launcher never runs the gate itself: it is always the watchdog.
   Exactly one process owns admission, the reservation, the run and settlement: a child the launcher starts,
   in its own session, as `sys.executable -I <target>/scripts/verify.py --hook claude --dispatched --deadline
   <epoch>` with the event bytes on stdin and the target as `cwd` — the launcher's own checkout or another
   one. The launcher computes the absolute hook deadline `start + 1740 s` and relays the child's stdout. The child's deadline is the smaller of the
   passed one and its own admission budget (D12). Without the script, the launcher fails open silently.
   `--dispatched` never re-dispatches.
5. **The watchdog enforces the admitted deadline.** The launcher passes the child an extra pipe (`pass_fds`);
   right after admission (D12) the child writes its admitted run deadline there, and nothing else. The
   launcher's kill time is that deadline plus 30 seconds, or, before the child reports one, the outer hook
   deadline plus 30 seconds (both inside Claude Code's 1800). At the kill time the launcher kills and reaps
   only its own direct child and fails open; it never touches `running.json`. The orphaned command groups
   are cleaned by the next run-lock holder (D9), and the child's reservation expires and is charged at the
   next admission (D12).
6. Every exception in hook mode is caught at the top: one stderr line, exit 0, no stdout.

### D12. Hook admission, bounds and output

**State.** `<git-common-dir>/veles-verify/hook-state.json`, version 1, under its own lock (taken as in D9):

```json
{"v": 1, "sessions": {"<id>": {
  "blocks": 0, "spent_s": 0,
  "reserved": [{"id": "<uuid>", "budget_s": 1740, "started": "<iso>", "pid": 0}],
  "repeat": {"id": null, "n": 0},
  "synopsis": "<the last blocking summary, at most 2 KiB>",
  "last_write": "<iso>"}}}
```

**Admission** (by the gate owner, D11, under the lock):

1. Expire reservations whose `started + budget_s + 120 s` has passed: charge `budget_s + 120 s` to
   `spent_s` (the longest a killed run can have used: its deadline, the watchdog's 30 seconds and cleanup;
   time spent is never refunded by a crash) and drop them.
2. `left = 3600 - spent_s - sum(budget_s of live reservations)`;
   `slots = 5 - blocks - count(live reservations)`.
3. If `slots < 1` or `left < 60`: do not run; emit `systemMessage` with the session's `synopsis` and pass.
4. Else append a reservation with `budget_s = min(1740, left, seconds to the passed --deadline)` (it holds one
   block slot); the run deadline is `started + budget_s`. Release the lock. Take `build/verify/lock`; if it is
   not obtained, cancel the reservation (no charge), emit "verification already running; nothing was checked"
   and pass.

**Outcomes and settlement** (under the lock): remove the reservation; add the measured run time to `spent_s`.

| Outcome | Hook answer | Counters |
|---|---|---|
| blocking: any `FAIL`, timeout, unexpected `NOT RUN` or selection error | block, unless the repeat bound is hit | charge one block; set `synopsis`; update `repeat` |
| repeat bound hit (`repeat.n > 3`) | `systemMessage`, pass | no block charged |
| clean: at least one node selected, the run completed, and nothing to block — including only expected `NOT RUN`s and every node remembered | silent pass | reset `blocks`, `spent_s`, `repeat`, `synopsis`; other live reservations stay and settle on their own |
| nothing changed, or only unowned paths changed | silent pass | unchanged (no verification ran) |
| contention, internal error, a limit reached at admission | `systemMessage` or silent pass | unchanged |

The repeat identity is `(node, step id, outcome kind)` of the first blocking problem, or
`("selection", error kind, sha256 of the sorted paths or the base)` for a selection error; the same identity
as `repeat.id` gives `n + 1`, another gives `n = 1`.

**Output.** A block prints `{"decision":"block","reason":"<summary>"}`; a pass with a notice
`{"systemMessage":"<text>"}`; a clean or silent pass nothing. Exit 0 always. The reason starts with
"verify: the task gate found problems; fix them and stop again (attempt <n> of 3)".

**Pruning.** After a write, sessions beyond the newest 50 by `last_write` are dropped, except sessions with a
live reservation (a resumed evicted session starts fresh; stated limit).

`stop_hook_active` is ignored, as in #188.

### D13. The drift check in the agent-file check

A sibling module `docs/checks/verify_table.py`, called from `check_agent_files.py`, using only the offline
guard's allowlist plus `shlex`, which this change adds to `STDLIB_ALLOWED` in `docs/checks/check_offline.py`
(stdlib, no I/O; the guard's own test is updated). It reads `.github/components.json`,
`scripts/verify-commands.json` and the `doc` files and reports `error <path>: <message>` lines whose message
is the fix.

**Reading a doc.** Only fenced blocks whose info string is empty, `bash`, `sh` or `shell` are scanned. In a
block: comments are stripped; lines ending in `\` are joined with the next; a `cd <dir>` line sets the
working directory for the following lines and is not a command; lines that start with a shell keyword
(`if`, `then`, `else`, `fi`, `for`, `do`, `done`, `while`), an assignment (`name=value`), `echo` or `exit` are
not commands. A block's starting directory is the repository root unless the nearest preceding paragraph of
its section says "Run from `<dir>`", in which case it is `<dir>`. Each remaining line is a documented command
with a working directory.

**Rules:**

- structure: the D2 validation subset (nodes equal the component map's; ids; references; kinds; `doc`
  present; `inputs` or `"reuse": false`); `verify.py` validates the full schema at load, and both validators
  are tested against the real table;
- forward: each command and producer matches one documented command of its `doc` **with the same working
  directory as its `cwd`** — an argv equal to `shlex.split` of the line (this covers non-Gradle commands and
  argument-bearing Gradle lines alike), or a `./gradlew` argv made only of task names that are a subset of a
  documented `./gradlew` line made only of task names;
- reverse: each documented command in the node's `AGENTS.md` under `## Build & Test` or `## Commands`, and in
  the node rules its `## Rules` lists, is covered. An `excluded` entry with the same `doc`, argv and working
  directory covers any line, checked first (so a setup line such as `./gradlew installGitCliff` can be
  excluded); otherwise a `./gradlew` line made only of task names is covered when the union of the task names
  of the entries pointing at it (same working directory) equals its task names; every other
  line, including a `./gradlew` line with arguments such as `--tests` or `-P…`, only by an entry with the same
  argv and working directory or by an `excluded` entry with the same `doc`, argv (`shlex.split` of its
  `line`) and working directory (its `cwd`, default `.`);
- `doc` is the node's `AGENTS.md` or a listed rule. A producer is shared, so its `doc` may name any node's
  `AGENTS.md` or listed rule, and it counts toward that node's reverse coverage (`rust.wasm` belongs to
  `rust/AGENTS.md`).

The Android rule's inline lint command moves into a fenced block under "Run from `android/`", and its
inline guard block becomes the guard script call. Docs whose working directory is ambiguous today get the
"Run from" sentence or a `cd` line in the same change.

`_hook_findings` gains a `Stop` exception: exactly one entry holding exactly two commands, the first the
`agent_rules.py` command with timeout 5, the second the verify command with timeout 1800; every other event
unchanged. `scripts/verify-commands.json` joins the checker's required override entries, so a missing
override is an error ("has no entry in .github/components.json global-overrides"), with its expected owner
set `["docs", "tooling"]`. Tests cover each finding.

### D14. Repository wiring

- `.github/components.json`: `global-overrides["scripts/verify-commands.json"] = ["tooling", "docs"]`.
- Android raw-`Log` guard: `android/scripts/check-raw-log.sh` (POSIX sh, run from the repository root, the
  same two greps, the same `::error::` lines and exit status). `check-android.yml` and the Android rule call
  it. `verify/test/workflow-contracts.test.mjs` asserts the invocation, the repository-root working directory
  and, by reading the script, the scan scope; its mutation fixtures gain cases for a changed invocation, a
  set working directory and a narrowed scan scope in the script.
- `.agents/hooked-rules/repo-pre-push-checks.md`: pointer and body name `verify.py --branch`, with the
  fallback wording; `agent_rules.py pointers --write`; the delivery-budget check re-run.
- `.agents/rules/scripts-verify-commands.md`: `trigger: glob`, globs and paths `scripts/verify-commands.json`;
  listed in `scripts/AGENTS.md` `## Rules`.
- `docs/verify.md`: kinds, entries, strategies, requirements and fix text, generated outputs, the machine
  config, the hook, "CI only" steps with reasons.
- `CONTRIBUTING.md` and `scripts/AGENTS.md` (within 40 lines) name both gates; the verifier's own lines are
  `excluded` in the table.

## Risks / Trade-offs

- [Gradle start-up makes the Android check slow on every non-remembered stop] → remembered passes; the
  budget is measured on a real change and recorded in the PR.
- [Declared `inputs` can be incomplete] → `inputs` or `"reuse": false` is mandatory; `docs/verify.md` says to
  choose `"reuse": false` when unsure; only the hook reuses; `--branch` and CI always run fresh.
- [The environment allowlist can miss a variable a build reads] → it is a list in code with tests, extended
  when a build is found reading another; `--branch` never reuses.
- [Hashing on each stop costs time] → tracked files cost one `git ls-files -s`; only modified, untracked,
  declared-input and generated files are read; large ignored trees are hashed by `stat` metadata, not content.
- [Narrowing can miss a test the change breaks] → the task gate is a fast signal; `--branch` and CI run
  everything; `none related` lists what was not covered.
- [Two validators of the table] → the drift check validates only what docs need; both are tested against the
  real table, so a disagreement fails a test.
- [A second `Stop` hook adds latency] → silent and quick when nothing changed or every node is remembered;
  measured in the real session.
- [Separate clones are not verified by the hook; an evicted session restarts its allowance] → stated limits.

**Known limits, accepted by the owner at the design review's escalation (2026-11-20) as too rare to handle:**

- A Gradle home redirected through a Java option (`JAVA_TOOL_OPTIONS` or similar setting `user.home` or
  `gradle.user.home`) is not followed: edits under that home do not invalidate a remembered pass.
- The per-session cap can be exceeded by up to 150 seconds per run: admission reserves the run's budget,
  while the watchdog grace (30 s) and the crash charge (120 s) come on top.
- A deadline that expires outside a running command (during hashing or between steps) reports the affected
  work as `no hit` or `not reached`, not as a blocking timeout.
- A producer whose build tool does not track an input (for example Cargo profile variables such as
  `CARGO_PROFILE_RELEASE_OPT_LEVEL` for `rustWasm`) can be judged up to date and leave a stale output after
  that input changes.

Each is listed in `docs/verify.md` with its workaround: run `--branch`, or delete `build/verify/` and the
generated output.

## Migration Plan

1. Land the table, `verify.py`, tests, the drift check and the offline-guard allowlist entry together, with
   the pre-push rule unchanged.
2. Measure the task gate on real changes; set the budget.
3. Wire the `Stop` hook and the checker exception; update the pre-push rule, the Android rule and guard
   script, the workflow contract, and the docs.
4. Real Claude Code acceptance run on the coding server: both `Stop` commands fire; a deliberately broken
   lint blocks once with a readable summary and the fix then passes; an absent tool is `NOT RUN` with its fix;
   a session moved into a linked worktree at a different revision runs that worktree's verifier; the times go
   in the PR.
5. Rollback: revert the second `Stop` command together with the checker exception in one commit; the CLI and
   table can stay.

## Open Questions

- The measured task-gate times per node, and whether 1740 of 1800 seconds is the right hook budget.
- Whether vitest 4.1.12 has a list-only related resolver (D4); otherwise the extension uses `name-map` plus
  `self`.
- Whether the Android CI guards beyond raw-`Log` (MAC comparisons, pairing-code) join the rule and the table
  or stay "CI only"; decided while walking the workflow and recorded in `docs/verify.md`.
