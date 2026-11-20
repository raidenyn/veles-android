## Context

`scripts/components.py` selects CI nodes from the changed paths using `.github/components.json`. The rule
in `docs/ci.md` is that a check that reads a file must run when that file changes. Three entries make
docs-procedure PRs start the slow nodes:

| Path | Selects today | Reader that causes it |
|---|---|---|
| `docs/checks/**` | `docs`, `supply-chain` | `REMOTE_CODE_SCOPES` in `verify/check-remote-code.mjs` (via supply-chain `ci-paths`) |
| `.github/workflows/check-docs.yml` | `docs`, `tooling`, `installer`, `supply-chain` | `check_gate_boundary` in `native-bridge/installer/scripts/test_build_contract.py` loops over every workflow; the scan lists the file |
| `verify/test/**` | `tooling`, `supply-chain` | nothing in supply-chain reads it: supply-chain owns the `verify/` prefix, and the scanner filters `verify/test/` |

Existing coverage that this design relies on:

- `verify/test/workflow-contracts.test.mjs` already enforces the release boundary on every workflow
  except `publish-release.yml`. One test covers signing names and release environments; another covers
  release and tag publishing. Their signing-name regex is a superset of the installer's.
- `assertDocsCheckWorkflow` in the same file pins `check-docs.yml`: reusable, `contents: read`, no
  job-level permissions, pinned checkout, no secrets, jobs `agent-files` and `unit-tests`, every
  `run:` starting with `python3 docs/checks/` or `python3 -m unittest`, exactly three runs, and no
  npm, npx, pip, curl, wget or setup actions.
- The `docs` node installs nothing and runs only the system `python3`.

Constraint: the owner chose scope "core". The gate-read documents `docs/otp-25-installers.md`,
`docs/reproducible-builds.md` and `native-bridge/host/AGENTS.md` keep their couplings.

## Goals / Non-Goals

**Goals:**
- A PR that changes only files in `docs/checks/`, `.github/workflows/check-docs.yml` or `verify/test/`
  selects no `installer` and no `supply-chain` node.
- Every guarantee those nodes gave for these files is still enforced on the same PR by a node that runs.
- The map stays fail-closed: an unmapped path still selects every node and fails `changes`.

**Non-Goals:**
- Decoupling the gate-read documents listed above.
- Changing `scripts/components.py` or adding job-level selection.
- Running the `verify/test/*.test.mjs` suites that no CI job runs today (only
  `workflow-contracts.test.mjs` runs, in `tooling`). This gap predates the change and is unchanged.
- Making `scripts/agent_rules.py`, which the docs check executes, select supply-chain. It stays in
  the scan's `scripts` scope and selects `tooling` and `docs`, as it does today.

## Decisions

### D1. The docs node guards its own scripts: `docs/checks/check_offline.py`

A stdlib-only script, run as `python3 docs/checks/check_offline.py --root .`, is the first step of
the `agent-files` job in `check-docs.yml`. It runs in its own process, so no test module loaded by
`unittest discover` can patch it. It prints `error <path>:<line>: <message>` per violation and exits
1 on any violation, 2 on a usage or I/O error, and 0 when clean.

It is Python 3.8 compatible like its siblings (no `ast.unparse`, no 3.9+ syntax). It lists the files
with `git ls-files docs/checks` and fails on:

1. Any tracked file in `docs/checks/` that is not a `*.py` file, except a file named `AGENTS.md`
   (guidance prose, which the old scan also skipped).
2. A `*.py` file that does not parse: it is reported as a violation, exit 1.
3. For each `*.py` file, parsed with `ast`:
   - an `import` or `from` import whose top-level module is in neither `STDLIB_ALLOWED` nor the
     sibling modules (the stem of each `*.py` file in `docs/checks/`). `STDLIB_ALLOWED` is
     `__future__`, `argparse`, `ast`, `collections`, `contextlib`, `copy`, `importlib`, `io`, `json`,
     `math`, `os`, `re`, `shutil`, `subprocess`, `sys`, `tempfile` and `unittest`. Of `importlib`,
     only the dotted name `importlib.util` is allowed;
   - any star import (`from X import *`);
   - `import os as …`, `import subprocess as …`, `import sys as …`, any `from subprocess import …`,
     and a `from os import` of a name that starts with `system`, `popen`, `exec`, `spawn`,
     `posix_spawn` or `fork`;
   - any `subprocess.<attr>` reference that is not the direct `func` of a call, except the constant
     attributes `subprocess.PIPE`, `subprocess.DEVNULL`, `subprocess.STDOUT`,
     `subprocess.CalledProcessError` and `subprocess.CompletedProcess`. This stops aliasing such as
     `r = subprocess.run`;
   - a call to a process-starting `subprocess` function (`run`, `call`, `check_call`,
     `check_output`, `Popen`, `getoutput`, `getstatusoutput`) with no positional argument, or whose
     first positional argument is neither a `List` literal whose first element is `Constant("git")`
     nor a `BinOp(Add)` whose left operand is such a list. Any other `subprocess.<attr>(…)` call
     fails, except calls to the allowed constant attributes above (for example
     `subprocess.CalledProcessError(...)`). A `shell=` keyword with any value other than the constant
     `False` also fails;
   - a forbidden `git` global option or subcommand. The tokens checked are the constant strings that
     follow `"git"` in a literal list, or the constant positional string arguments of a call to a
     local helper named `_git` or `git`. The **subcommand** is the first token that does not start
     with `-`. Tokens before it are **global options**; a global option fails when it equals `-c`,
     `-C`, `--config`, `--exec-path` or `--git-dir`, or starts with `--config=`, `--config-env`, `--exec-path=`,
     `--upload-pack` or `--receive-pack`. Options after the subcommand are not checked, so
     `ls-files -c` (meaning `--cached`) at `check_site.py:68` passes. The subcommand fails when it is
     `clone`, `fetch`, `pull`, `push`, `submodule`, `ls-remote`, `remote` or `archive`. Tokens are
     matched exactly, not as prefixes;
   - any reference to an `os.` attribute whose name starts with `system`, `popen`, `exec`, `spawn`,
     `posix_spawn` or `fork`, called or not;
   - any reference to `sys.modules`, `__dict__`, `__builtins__` or `builtins`, and any call to the
     bare names `vars`, `globals` or `locals`;
   - a call to the bare builtin names `eval`, `exec`, `compile` or `__import__`. Only a call through
     a `Name` node counts, so attribute calls such as `re.compile(...)` or
     `spec.loader.exec_module(...)` are not affected;
   - a call to the bare name `getattr` whose first argument is the name `os`, `subprocess`, `sys`
     or `importlib`, or whose second argument is not a string constant. Today's uses
     (`getattr(check_module, "RULE_SIZE_MAX", None)` in `test_check_agent_files.py`) pass;
   - any `importlib` attribute use inside a (file, enclosing top-level function) pair that is not
     listed in `IMPORTLIB_EXEMPT`. Today the list is `("check_agent_files.py", "_load_engine")` and
     `("test_check_agent_files.py", "_load_engine")`; both load the agent-rules engine
     `scripts/agent_rules.py`. The `import importlib.util` statements themselves are allowed by the
     import allow-list. A module-level use is keyed with the function name `<module>`.

Why AST over regex: Python has many spellings for one call. The AST sees the real import and call
structure, and the allow-list fails closed on any module nobody reviewed. Limits, stated rather than
hidden:
- `git` arguments built at run time (for example `list(args)` forwarded from a caller) are not
  inspected beyond the constant strings above. Only helpers named `_git` or `git` have their call
  arguments checked; a helper with another name, such as `git_output` in
  `test_check_agent_files.py`, forwards arguments the guard does not check;
- `git config` is allowed, because the tests set keys such as `core.autocrlf` and `core.symlinks` in
  temporary repositories. A config key
  such as `core.sshCommand` or `alias.*` could make a later `git` call run a program. The guard does
  not inspect config keys, so review covers them;
- a determined author could still reach a process function through constructs the rules don't name.
Review covers both, as it did under the regex scan, which had the same limits and more.

Tests, in `docs/checks/test_check_offline.py` and run by the existing `unittest discover` step. They
import the guard module and call its `scan(root)` function in-process; they never start the guard
through `subprocess`, so the test file passes the guard itself:
- the real tree passes;
- the check rejects each forbidden form: a network import, an unknown module, a star import, each
  alias, `from subprocess import run`, `r = subprocess.run`, a call with `args=` only, a non-`git`
  argv, `shell=True`, `["git", "-c", "x", "y"]`, `_git(root, "clone", url)`, `os.system` called and
  uncalled, `getattr(os, "system")`, `sys.modules`, `vars(os)`, `eval`, an unlisted `importlib` use,
  an unparsable file and a tracked `*.sh` file. Each form goes in as a fixture file in a temporary
  git repository and must fail;
- the accepted forms pass: `["git"] + list(args)`, `["git"] + args`, `["git", "ls-files"]`,
  `_git(root, "ls-files", "-z", "-c", "-i")`, `subprocess.PIPE`,
  `subprocess.CalledProcessError(1, "git")`, `re.compile(...)`, `getattr(module, "NAME", None)` and
  a tracked `docs/checks/AGENTS.md`;
- a rejected global option: `["git", "-c", "core.sshCommand=x", "status"]` and
  `_git(root, "--exec-path=/tmp", "status")`.

The first test builds its temporary repositories with `git init`, like `test_check_site.py`, and
reads no clock.

Alternative rejected: guard tests that run only inside `unittest discover`, with no new command.
They can be monkeypatched by sibling tests (brainstorm review M1).

### D2. The tooling suite owns the docs workflow's release boundary

- `assertDocsCheckWorkflow`:
  - expects four runs instead of three, with
    `python3 docs/checks/check_offline.py --root .` as the first `run:` of `agent-files`, checked by
    an explicit index-order assertion against the agent-file check. The count message changes to
    mention the offline guard, and the existing mutation tests that match
    `/exactly the agent-file check, the site guard/` follow the new message. A dropped-guard
    mutation and a reordered-guard mutation must throw;
  - adds `assert.doesNotMatch(source, /^\s*environment:/m)` and
    `assert.doesNotMatch(executable, /\bgit push\b/)`, each with a mutation that must throw.
- A new tooling test, "the all-workflow release boundary covers the installer boundary", reads
  `native-bridge/installer/scripts/test_build_contract.py`. It locates the all-workflows loop of
  `check_gate_boundary` by its anchor line `    for name, source in workflows.items():`. The loop
  body is every following line until the first non-empty line indented four spaces or less (today
  `    return errors`). The test extracts the `r"…"` regex literals in that body and requires exactly
  three, in order: signing, release environment, publishing. It fails if the anchor is missing or the
  count is not three, so it fails closed. Then:
  - each `|`-separated term of the installer signing regex must match the tooling signing regex;
  - `environment: android-release` and `environment: release-publish` must match both environment
    regexes;
  - the installer publishing regex's source must equal the tooling publishing regex's source after
    the `\/` → `/` escape is normalised. Any divergence fails.

  The two existing tooling boundary tests' regexes are lifted to module-level constants that both
  the old tests and the new test use. Because tooling now reads that installer file, it is added to
  `tooling.ci-paths` (D3). An edit to the installer's boundary therefore runs `tooling` too.
- `check_gate_boundary` gains `TOOLING_OWNED_WORKFLOWS = ("check-docs.yml",)` and skips those names
  in its all-workflows loop, with a comment naming the tooling all-workflow tests. A new installer
  test asserts that the skip covers exactly that tuple. It also asserts that a credential string added
  to any other enumerated workflow still fails, using `pr-title.yml` as the sample.
- `REMOTE_CODE_SCOPES` drops `'docs/checks/'` and `'.github/workflows/check-docs.yml'`. In the scope
  test in `workflow-contracts.test.mjs` (around lines 1355–1358), `check-docs.yml` comes out of the
  must-cover loop, and the `docs/checks/` assertion is inverted: both must be absent.
- The test "flags a docs/checks script …" in `verify/test/remote-code.test.mjs` is deleted. The test
  just before it already covers the AGENTS.md skip with `scripts/AGENTS.md`.

Alternative rejected: moving the installer's whole all-workflows loop into tooling. That duplicates
work the tooling suite already does and leaves the installer suite's `project-flow.yml` tests without
a home. One named exemption is smaller.

### D3. Map changes in `.github/components.json`

- `global-overrides[".github/workflows/check-docs.yml"]` becomes `["docs", "tooling"]`.
- `supply-chain.ci-paths` drops `docs/checks/` and becomes empty, so the key is removed.
- `tooling.ci-paths` gains `native-bridge/installer/scripts/test_build_contract.py` (read by the
  superset test in D2).
- `supply-chain.paths` replaces `verify/` with `verify/lib/` and the exact files below (from
  `git ls-files verify` at design time; the builder re-checks the list). `verify/package.json` and
  `verify/package-lock.json` are already listed further down in `supply-chain.paths`; they stay
  there once and are not added a second time:
  `verify/cargo-build-script-policy.json`, `verify/cargo-tools.toml`,
  `verify/check-android-layout.sh`, `verify/check-cargo-build-scripts.mjs`,
  `verify/check-npm-install-scripts.mjs`, `verify/check-npm-licenses.mjs`,
  `verify/check-remote-code.mjs`, `verify/check-root-aliases.sh`,
  `verify/check-vendored-patches.mjs`, `verify/generate-sboms.sh`,
  `verify/install-script-policy.json`, `verify/normalize-sbom-paths.mjs`,
  `verify/package-lock.json`, `verify/package.json`, `verify/verify-sboms.mjs`,
  `verify/verify-supply-chain.sh`.
- `verify/AGENTS.md` keeps its `["docs"]` override. `verify/test/` stays in `tooling.paths`.

`scripts/tests/test_components.py`:
- the `check-docs.yml` override test expects `["docs", "tooling"]` and
  `affected([...]) == {"docs", "tooling"}`;
- new: every file from `git ls-files docs/checks` selects exactly `{"docs"}`;
- new: every file from `git ls-files verify/test` selects exactly `{"tooling"}`;
- new: every file from `git ls-files verify`, except `verify/test/**` and `verify/AGENTS.md`, selects
  supply-chain;
- new: a hypothetical `verify/new-check.mjs` is unaccounted (fail-closed).

The docs agent-file check also reads the map. `docs/checks/check_agent_files.py` hard-codes
`OVERRIDE_TARGETS[".github/workflows/check-docs.yml"]` as the four-node tuple and fails when the map
differs, so it must change in the same commit:
- `check_agent_files.py`: the tuple becomes `("docs", "tooling")`;
- `test_check_agent_files.py`: `EXPECTED_OVERRIDE_TARGETS` changes to `["docs", "tooling"]`. In
  `test_override_target_sets_exact`, the `check-docs.yml` case currently uses `["docs", "tooling"]`
  as the wrong value with the fragment `"supply-chain"`. It changes to the wrong value `["docs"]`
  with the fragment `"tooling"`.

Without these edits `docs / agent-files` fails on this change's own PR.

### D4. Documentation and the spec

- `docs/ci.md`:
  - the `docs / agent-files` row of the command table gains the offline guard;
  - the override paragraph (around lines 139–146) and the workflow paragraph (around line 162) state
    that `check-docs.yml` selects `docs` and `tooling`, because the tooling all-workflow boundary and
    the docs contract own it;
  - a sentence states that `docs/checks/` is guarded by the docs node, not the remote-code scan.
- `check-docs.yml` header comment: replace "Any workflow edit is a global path, so the nodes that
  read this file run too" with a statement that the file selects `docs` and `tooling`, and name the
  offline guard.
- `docs/AGENTS.md` Commands block gains `python3 docs/checks/check_offline.py --root .` (34 lines
  today, so it stays under the 40-line cap). The command list in `docs/agent-guidance.md` ("The
  agent-file check", around lines 27–28) gains the same line, with one sentence on what the guard
  enforces.
- The `ci-pipeline` delta modifies these requirements:
  - "Component-tree CI": the "Docs node" scenario (the `agent-files` steps now start with the offline
    guard) and the "Site guard step in the workflow contract" scenario (four pinned commands, the
    guard first);
  - "Workflow contract suites move with the workflows": the requirement body gains a carve-out. A
    workflow the tooling suite owns (today only `check-docs.yml`) is covered by
    `workflow-contracts.test.mjs`, and is exempt by name from the installer all-workflows boundary
    and from the scanner's workflow scopes. The "New docs workflow" scenario requires the offline
    guard in place of the scanner scopes;
  - "Change-based node selection": the "Docs workflow edit" scenario changes to `docs` and
    `tooling`, and new scenarios cover a `docs/checks/` edit, a `verify/test/` edit, and an edit to
    the installer's `test_build_contract.py`, which also selects `tooling`.

## Risks / Trade-offs

- [Last-green evidence resets once for every node, because `selection_spec` includes
  `global-overrides`, and for supply-chain also because its `paths` change.] → Expected and one-time:
  open PRs run their nodes in full once after this merges. The PR note will say so.
- [The guard can be loosened in a docs-only PR.] → Review catches it, as it does for an edit to the
  scanner today. Neither has a CODEOWNERS entry, so the bar is unchanged.
- [The installer and tooling release boundaries could drift apart.] → The superset test in D2 reads
  the installer's regexes from its source. It fails when the installer gains a signing term the
  tooling regex does not match, when the publishing regexes diverge, or when the loop's shape changes
  so the extraction fails. The installer file selects `tooling`, so this runs on the PR that changes
  it.
- [A new top-level `verify/` file is unaccounted until it is mapped.] → This is the fail-closed
  behaviour we want: `changes` fails and names the path.
- [The AST guard rejects a legitimate new stdlib import.] → The allow-list is one constant; adding
  to it is a reviewed one-line change, and it never admits network modules.

## Migration Plan

One PR. Rollback is a revert: the old map, scan scopes and loop come back together. No data and no
release attribution change: supply-chain has `release: false`, and ci-paths and overrides never
count for releases.

## Open Questions

None.
