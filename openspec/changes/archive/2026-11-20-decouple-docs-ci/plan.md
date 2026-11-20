# Decouple Docs Procedures from the Installer and Supply-Chain CI Nodes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A PR that only changes `docs/checks/`, `.github/workflows/check-docs.yml` or `verify/test/` must not start the `installer` or `supply-chain` CI nodes, and every guarantee those nodes gave for these files must still hold.

**Architecture:** Each guarantee moves to a node that already runs:
- `docs/checks/` gets an AST guard of its own, `docs/checks/check_offline.py`, run as the first `run:` of `docs / agent-files`.
- `check-docs.yml` gets its release boundary from the tooling all-workflow tests, plus a superset test that reads the installer's regexes. The installer suite exempts the file by name.
- The component map narrows `supply-chain` to the `verify/` files it reads.

**Tech Stack:** Python 3.8+ standard library (`ast`, `unittest`), Node `node:test` (Node 26 in CI), the JSON component map read by `scripts/components.py`, GitHub Actions YAML.

**Spec:** `openspec/changes/decouple-docs-ci/specs/ci-pipeline/spec.md`. The design is `design.md` (D1–D4) and the tasks are `tasks.md`, both in the same directory. The plan's task N implements `tasks.md` group N.

## Global Constraints

- The guard is Python 3.8 compatible and uses only the standard library: no `ast.unparse`, no `match`, no 3.9+ generics.
- The `docs` node installs nothing; `check-docs.yml` uses only `python3 docs/checks/…` and `python3 -m unittest` commands.
- No unit test sleeps or reads the wall clock (`.agents/rules/repo-no-time-based-tests.md`). Temporary git repositories set `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`.
- Tests never pass a global `git -c`, which the guard forbids. Set any configuration with `git config <key> <value>` inside the temporary repository, or through the env above.
- Commit subjects are Conventional Commits: `ci(ci): …` for map, workflow and contract work, `test(…)` for test-only commits, and `docs(docs): …` for documentation. Read `.agents/hooked-rules/repo-commit-types.md` before the first commit.
- `docs/AGENTS.md` stays at 40 lines or fewer.
- The map edit (Task 4, Step 3) and the `OVERRIDE_TARGETS` edit (Task 4, Step 5) go in **one commit**.
- Never touch signing keys or `publish-release.yml`.

## Review Focus

1. **A docs check that legitimately needs a new stdlib module** (for example `pathlib`). The guard must fail with a message naming the module and `STDLIB_ALLOWED`, so the fix is obvious. Task 1's `test_unknown_module_message_names_allow_list` pins this.
2. **The guard's own files.** `check_offline.py` imports `ast` and `argparse`, and `test_check_offline.py` builds git repositories with `subprocess`. Both must pass the guard. Task 1's `test_real_tree_passes` (run after `git add`) pins this.
3. **Windows line endings or a BOM in a `docs/checks/*.py` file.** Parsing must not report a false syntax error. Task 1's `test_crlf_and_bom_parse` pins this.
4. **The installer loop is reformatted** (re-indented, or a fourth regex added). The superset test must fail loudly rather than compare the wrong regexes. Task 2's `extracts exactly three literals` negative cases pin this.
5. **A brand-new top-level `verify/` file.** It must be unaccounted, so `changes` fails and selects every node. Task 4's `test_new_verify_file_is_unaccounted` pins this.

---

### Task 0: Branch and planning files

- [ ] **Step 1:** Confirm the current branch is `chore/decouple-supply-installer-ci` and based on `origin/master` (`git fetch origin && git merge-base --is-ancestor origin/master HEAD || echo REBASE`). If it is behind, stop and ask before rebasing.
- [ ] **Step 2:** Commit the change directory: `git add openspec/changes/decouple-docs-ci && git commit -m "docs(openspec): plan decoupling docs checks from installer and supply-chain CI"`.

### Task 1: Docs offline guard

**Files:**
- Create: `docs/checks/check_offline.py`
- Create test: `docs/checks/test_check_offline.py`

**Interfaces:**
- Produces:
  - `scan(root: str) -> list` of `Finding(path: str, line: int, message: str)` (a `collections.namedtuple`). Raises `OSError` or `RuntimeError` on an I/O or git failure.
  - `main(argv: list) -> int`: parses `--root`, prints `error <path>:<line>: <message>` per finding, and returns 0 when clean, 1 on any finding, 2 on a usage, I/O or git error. The file ends with `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.
  - Module constants `STDLIB_ALLOWED`, `SUBPROCESS_CONSTANTS`, `PROCESS_FUNCTIONS`, `OS_PROCESS_PREFIXES`, `GIT_FORBIDDEN_GLOBAL_EXACT`, `GIT_FORBIDDEN_GLOBAL_PREFIXES`, `GIT_FORBIDDEN_SUBCOMMANDS`, `IMPORTLIB_EXEMPT`, holding the values from design D1.

- [ ] **Step 1: Write the failing tests.** In `test_check_offline.py`, the helper `make_repo(files: dict) -> str` writes the files into a `tempfile.mkdtemp()` directory under `docs/checks/`, then runs `git init -q` and `git add -A` there through `subprocess.run(["git"] + args, …)` with the env from the Global Constraints. `tearDown` removes the directory. The tests are:
  - `test_real_tree_passes`: `scan(REPO)` returns `[]`.
  - One `test_rejects_<form>` per rejected form in design D1. Each builds a one-file repo (`docs/checks/check_x.py`) and asserts that `scan` returns exactly one finding, whose message contains a keyword. The forms and keywords are:
    - `import urllib.request` → `"urllib"`;
    - `from X import *` → `"star import"`;
    - `import subprocess as s` → `"alias"`;
    - `from subprocess import run` → `"from subprocess"`;
    - `r = subprocess.run` → `"subprocess.run"`;
    - `subprocess.run(args=["git"])` → `"positional"`;
    - `subprocess.run(["curl", u])` → `"git"`;
    - `subprocess.run(["git"], shell=True)` → `"shell"`;
    - `["git", "-c", "core.sshCommand=x", "status"]` in a run → `"-c"`;
    - `_git(root, "clone", u)` → `"clone"`;
    - `_git(root, "--exec-path=/tmp", "status")` → `"--exec-path"`;
    - `os.system("x")` and a bare `f = os.system` → `"os.system"`;
    - `getattr(os, "system")` → `"getattr"`;
    - `sys.modules` → `"sys.modules"`;
    - `vars(os)` → `"vars"`;
    - `eval("1")` → `"eval"`;
    - `importlib.util.find_spec("x")` inside `def other():` → `"importlib"`;
    - `def (` (a syntax error) → `"parse"`;
    - a tracked `docs/checks/run.sh` → `"not a Python file"`.
  - `test_accepts_allowed_forms`: one repo holding every accepted form from design D1 returns `[]`. The forms are `["git"] + list(args)`, `["git"] + args`, `["git", "ls-files"]`, `_git(root, "ls-files", "-z", "-c", "-i")`, `subprocess.PIPE`, `subprocess.CalledProcessError(1, "git")`, `re.compile("x")`, `getattr(m, "NAME", None)`, a sibling import `import check_y` with `check_y.py` present, and a tracked `docs/checks/AGENTS.md`.
  - `test_rejects_every_listed_form`: a table-driven `subTest` loop that completes the cases above, one fixture each and exactly one finding each:
    - `import os as o` and `import sys as s`, and `from os import system`;
    - each of `GIT_FORBIDDEN_GLOBAL_EXACT`, one value per `GIT_FORBIDDEN_GLOBAL_PREFIXES` entry, and each of `GIT_FORBIDDEN_SUBCOMMANDS`, as `["git", <token>, "status"]` or `["git", <subcommand>]`;
    - `x.__dict__`, `import builtins`, `globals()` and `locals()`;
    - `exec("1")`, `compile("1", "f", "exec")` and `__import__("os")`;
    - `getattr(m, name)`;
    - `subprocess.Popen(["curl"])` and `subprocess.check_output(["curl"])`;
    - a module-level `importlib.util.find_spec("x")`.

    Use `from os.path import *` for the star-import fixture, so that `os` keeps the import allowed and only the star rule fires. The visitor reports a chained attribute such as `importlib.util.find_spec` once.
  - `test_finding_has_path_and_line`: the `import urllib.request` fixture on line 3 yields `Finding(path="docs/checks/check_x.py", line=3, …)`.
  - `test_unknown_module_message_names_allow_list`: `import pathlib` gives a message containing `"pathlib"` and `"STDLIB_ALLOWED"`.
  - `test_crlf_and_bom_parse`: a file written as bytes `b"\xef\xbb\xbfimport os\r\n"` gives `[]`.
  - `test_main_exit_codes`: `main(["--root", clean])` returns 0, `main(["--root", dirty])` returns 1, and `main(["--root", "/nonexistent"])` returns 2. Each call wraps stdout and stderr with `contextlib.redirect_stdout`/`redirect_stderr(io.StringIO())`.
- [ ] **Step 2: Run them to see them fail.** Run `python3 -m unittest discover -s docs/checks -p 'test_check_offline.py' -v` from the repository root. Expected: `ModuleNotFoundError: check_offline`.
- [ ] **Step 3: Implement `check_offline.py`.**
  - It is one `ast.NodeVisitor` subclass per file. It tracks the enclosing top-level function name for `IMPORTLIB_EXEMPT`, and a set of `subprocess.<attr>` nodes that are the `func` of a call, so the non-call rule can skip them.
  - Read files with `open(path, "rb")`, then decode with `utf-8-sig` and pass the text to `ast.parse`.
  - List files with `subprocess.run(["git", "ls-files", "-z", "docs/checks"], cwd=root, …)`. Use `cwd=`, never `git -C`, because the guard forbids `-C`.
  - Report findings sorted by `(path, line)`.
- [ ] **Step 4: Run the tests.** Run `git add docs/checks/check_offline.py docs/checks/test_check_offline.py`, then `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`. Expected: all pass, including the existing suites.
- [ ] **Step 5: Run on the real tree.** `python3 docs/checks/check_offline.py --root .`, expected exit 0 and no output.
- [ ] **Step 6: Run under Python 3.8.** `uv run --python 3.8 python -m unittest discover -s docs/checks -p 'test_*.py' -v`. If `uv` or a 3.8 interpreter cannot be fetched, run `python3 -I -c "import ast,sys; ast.parse(open(sys.argv[1]).read(), feature_version=(3, 8))" docs/checks/check_offline.py` for both new files instead, and record in `apply.md` that the 3.8 run was NOT RUN and why.
- [ ] **Step 7: Commit.** `git commit -m "ci(docs): guard docs check scripts against network and process use"`.

### Task 2: Docs workflow and the tooling contract

**Files:**
- Modify: `verify/test/workflow-contracts.test.mjs`: `assertDocsCheckWorkflow` (~3676–3710), its test (~3711–3751), the two all-workflow boundary tests (~2098–2120) and the remote-code scope test (~1350–1362)
- Modify: `.github/workflows/check-docs.yml`
- Modify: `verify/check-remote-code.mjs` (`REMOTE_CODE_SCOPES`, ~111–112)
- Modify: `verify/test/remote-code.test.mjs` (delete the test at ~69)

**Interfaces:**
- Consumes: the Task 1 command `python3 docs/checks/check_offline.py --root .`.
- Produces:
  - module constants `SIGNING_PATTERN`, `RELEASE_ENVIRONMENT_PATTERN`, `PUBLISHING_PATTERN` (RegExp);
  - `installerBoundaryPatterns(source: string) -> {signing: string, environment: string, publishing: string}`, which throws `Error` when the anchor is missing or the loop holds a number of `r"…"` literals other than three;
  - `assertBoundarySuperset(source: string)`, which throws an `AssertionError` on any gap.

- [ ] **Step 1: Write the failing contract tests.**
  - `assertDocsCheckWorkflow`:
    - `runs.length === 4`, with the message `must run exactly the offline guard, the agent-file check, the site guard and their tests`;
    - `agent-files` contains `- run: python3 docs/checks/check_offline.py --root .`, and its index is lower than the index of the agent-file check;
    - `assert.doesNotMatch(source, /^\s*environment:/m)`;
    - `assert.doesNotMatch(executable, /\bgit push\b/)`.

    The `environment:` and `git push` checks run **before** the count check, and their mutations are asserted with `assert.throws(…, /environment/)` and `assert.throws(…, /git push/)`. The `git push` mutation replaces the existing site-guard run line with `python3 docs/checks/check_site.py --root . ; git push`, so the count stays at four and only the `git push` check can catch it.
  - Update the regexes in the existing mutation `assert.throws` calls to `/exactly the offline guard, the agent-file check, the site guard/`.
  - Add these mutations, each of which must throw:
    - the dropped guard line;
    - the guard moved after the agent-file line;
    - `--root .` changed to `--root docs`;
    - `    environment: docs\n` added to a job;
    - the site-guard line with `; git push` appended (see above).
- [ ] **Step 2: Run them to see them fail.** `node --test verify/test/workflow-contracts.test.mjs`. Expected: the docs workflow test fails on the count.
- [ ] **Step 3: Edit the workflow.** In `.github/workflows/check-docs.yml`, add `      - run: python3 docs/checks/check_offline.py --root .` as the first `run:` of `agent-files`. Replace the header line "Any workflow edit is a global path, so the nodes that read this file run too." with: the file is selected by `docs` and `tooling` only; the tooling all-workflow boundary owns its release safety; and the offline guard keeps `docs/checks/` free of network and process use. Re-run: the docs workflow test passes.
- [ ] **Step 4: Write the superset test, then lift the constants.**
  - Add the test `the all-workflow release boundary covers the installer boundary`. It calls `assertBoundarySuperset(await readFile(new URL('native-bridge/installer/scripts/test_build_contract.py', root), 'utf8'))`.
  - Add negative cases built from the real source by string replacement, each wrapped in `assert.throws`:
    - an added signing term `|VELES_NEW_`;
    - `--atomic` changed to `--force`;
    - the anchor renamed;
    - the loop body with one literal removed;
    - the loop body with one literal added.
  - In `installerBoundaryPatterns`, find the anchor line `    for name, source in workflows.items():`. Take the following lines until the first non-empty line whose indentation is four spaces or less. Collect `/(?<![\w"])r"((?:[^"\\]|\\.)*)"/g` and require exactly three matches.
  - In `assertBoundarySuperset`:
    - each `|`-split term of `signing` must match `SIGNING_PATTERN`;
    - `environment: android-release` and `environment: release-publish` must match both `new RegExp(environment.replace('(?m)', ''), 'm')` and `RELEASE_ENVIRONMENT_PATTERN`;
    - `PUBLISHING_PATTERN.source.replaceAll('\\/', '/') === publishing`.
  - The two existing boundary tests switch to the three constants.
- [ ] **Step 5: Drop the scanner scopes.**
  - In the scope test, take `.github/workflows/check-docs.yml` out of the must-cover array.
  - Replace the `docs/checks/` assertion with two `assert.ok(!REMOTE_CODE_SCOPES.includes(…))` checks, for `docs/checks/` and for `.github/workflows/check-docs.yml`.
  - Run the test and see it fail. Then delete the two entries and their `// The docs node's …` comment from `verify/check-remote-code.mjs`.
  - Delete the test `flags a docs/checks script that uses npx but skips docs/checks/AGENTS.md` from `verify/test/remote-code.test.mjs`.
- [ ] **Step 6: Run everything.** `node --test verify/test/workflow-contracts.test.mjs verify/test/remote-code.test.mjs`. Expected: all pass.
- [ ] **Step 7: Commit.** `git commit -m "ci(ci): make the tooling contract the owner of the docs workflow boundary"`.

### Task 3: Installer boundary exemption

**Files:**
- Modify: `native-bridge/installer/scripts/test_build_contract.py` (`check_gate_boundary` ~721–820, tests near ~1458–1555)

**Interfaces:**
- Produces: the module constant `TOOLING_OWNED_WORKFLOWS = ("check-docs.yml",)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_tooling_owned_workflows_are_exactly_check_docs`: asserts `TOOLING_OWNED_WORKFLOWS == ("check-docs.yml",)`.
  - `test_tooling_owned_workflow_skipped_others_still_checked`:
    - adds `          KEYSTORE_PASSWORD: x\n` to `workflows()["check-docs.yml"]` and asserts that no error starts with `check-docs.yml `;
    - adds the same line to `pr-title.yml` and asserts that an error starts with `pr-title.yml `.
- [ ] **Step 2: Run them to see them fail.** `python3 -m unittest native-bridge/installer/scripts/test_build_contract.py -k tooling_owned -v`. If discovery by path is refused, use `python3 -m unittest discover -s native-bridge/installer/scripts -p 'test_build_contract.py' -k tooling_owned -v`. Expected: a `NameError` or a failing assertion.
- [ ] **Step 3: Implement.** Add the constant above `check_gate_boundary`. In the loop, change `if name == PUBLISH_WORKFLOW:` to `if name == PUBLISH_WORKFLOW or name in TOOLING_OWNED_WORKFLOWS:`, and add a comment naming `verify/test/workflow-contracts.test.mjs` (the all-workflow boundary and `assertDocsCheckWorkflow`). Keep the three `re.search(r"…", other)` lines and the loop indentation unchanged, and put no double-quoted text in the new comment, because Task 2 extracts the `r"…"` literals from this loop body.
- [ ] **Step 4: Run both suites.** `./gradlew bridgeInstallerScriptsTest`, falling back to the unittest command above if Gradle is unavailable, and `node --test verify/test/workflow-contracts.test.mjs`, because Task 2's superset test reads this file. Expected: both pass.
- [ ] **Step 5: Commit.** `git commit -m "test(installer): exempt the tooling-owned docs workflow from the release boundary loop"`.

### Task 4: Component map and the agent-file pin

**Files:**
- Modify: `.github/components.json`
- Modify: `scripts/tests/test_components.py` (override test ~348–366, new tests after it)
- Modify: `docs/checks/check_agent_files.py:210`
- Modify: `docs/checks/test_check_agent_files.py:153` and `:1597–1604`

- [ ] **Step 1: Write the failing map tests.** The new tests in `test_components.py` take their file lists from `git ls-files`, as `test_each_plugin_file_selects_tooling_docs` does:
  - `test_codeowners_and_check_docs_workflow_overrides`: expects `["docs", "tooling"]` and `owner_nodes == {"docs", "tooling"}`.
  - `test_docs_check_files_select_only_docs`: each file selects exactly `{"docs"}`.
  - `test_verify_test_files_select_only_tooling`: each file selects exactly `{"tooling"}`.
  - `test_verify_files_select_supply_chain`: covers every `git ls-files verify` file except `verify/test/` and `verify/AGENTS.md`.
  - `test_new_verify_file_is_unaccounted`: `unaccounted(["verify/new-check.mjs"], CMAP) == ["verify/new-check.mjs"]`.
  - `test_installer_boundary_suite_selects_tooling`: `affected` of the installer `test_build_contract.py` is exactly `{"installer", "protocol", "supply-chain", "tooling"}`.
- [ ] **Step 2: Run them to see them fail.** `./gradlew scriptsTest`, or `python3 -m unittest discover -s scripts/tests -p 'test_components.py' -v`. Expected: the new and changed tests fail.
- [ ] **Step 3: Edit the map** as design D3 describes:
  - set the override to `["docs", "tooling"]`;
  - delete the `ci-paths` key of `supply-chain`;
  - replace its `verify/` entry with `verify/lib/` and the 14 top-level files that are not yet listed. `verify/package.json` and `verify/package-lock.json` are already listed. Re-check against `git ls-files verify`;
  - append `native-bridge/installer/scripts/test_build_contract.py` to `tooling.ci-paths`.
- [ ] **Step 4: Run the map checks.** `python3 -I scripts/components.py check`, then the Step 2 command. Expected: pass.
- [ ] **Step 5: Update the agent-file pin.**
  - Set `OVERRIDE_TARGETS[".github/workflows/check-docs.yml"] = ("docs", "tooling")`.
  - Set `EXPECTED_OVERRIDE_TARGETS[...] = ["docs", "tooling"]`.
  - In the `test_override_target_sets_exact` case, use the wrong value `["docs"]` with the fragment `"tooling"`.
  - Run `python3 docs/checks/check_agent_files.py --root .` and `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`. Expected: pass.
- [ ] **Step 6: Commit Steps 3 and 5 together.** `git commit -m "ci(ci): stop docs procedure edits from selecting installer and supply-chain"`.

### Task 5: Documentation and selection evidence

**Files:**
- Modify: `docs/ci.md` (~65, ~139–146, ~151–153, ~160–162)
- Modify: `docs/AGENTS.md` (Commands block)
- Modify: `docs/agent-guidance.md` (~27–28)
- Create: `openspec/changes/decouple-docs-ci/apply.md` (evidence)

- [ ] **Step 1: Edit the docs.**
  - `docs/ci.md`:
    - the `docs / agent-files` row lists `check_offline.py --root .` first;
    - the override and workflow paragraphs say `check-docs.yml` selects `docs` and `tooling`, because the tooling all-workflow boundary and `assertDocsCheckWorkflow` own it, and the installer suite exempts it by name;
    - add a sentence that `docs/checks/` is guarded by the docs node's offline guard, not the remote-code scan;
    - add a sentence that `supply-chain` owns `verify/lib/` and the listed top-level `verify/` files, and `tooling` alone owns `verify/test/`.
  - `docs/AGENTS.md` and `docs/agent-guidance.md`: add `python3 docs/checks/check_offline.py --root .` as the first command. In `agent-guidance.md`, add one sentence: the guard allows only standard-library imports and `git` subprocesses in `docs/checks/`.
- [ ] **Step 2: Run the docs checks.** `wc -l docs/AGENTS.md` (40 or fewer), `python3 docs/checks/check_agent_files.py --root .`, `python3 docs/checks/check_site.py --root .` and `python3 docs/checks/check_gate_b_matrix.py`. Expected: exit 0.
- [ ] **Step 3: Commit.** `git commit -m "docs(docs): describe the docs node's offline guard and narrowed selection"`.
- [ ] **Step 4: Gather the selection evidence.**
  - `git switch -c scratch/decouple-evidence`. Make three separate one-line commits, each touching only one file and each on top of the last: a comment in `docs/checks/check_site.py`, a comment in `.github/workflows/check-docs.yml`, and a comment in `verify/test/workflow-contracts.test.mjs`.
  - For each commit `C`, run `python3 -I scripts/components.py changed --base C^ --head C | grep =true`. Expected: `docs`; then `docs` and `tooling`; then `tooling`.
  - Re-run `changed --base f37cc4bc^ --head f37cc4bc` (#174) and `--base 165b2e0f^ --head 165b2e0f` (#175) against the new map (the working-tree `.github/components.json` is the default `--map`). Note that `#174` still selects `native-host`, because of `native-bridge/host/AGENTS.md`, which is out of scope.
  - `git switch chore/decouple-supply-installer-ci && git branch -D scratch/decouple-evidence`.
  - Write the outputs to `apply.md`.

### Task 6: Pre-push checks and CI

- [ ] **Step 1:** `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`. Expected: docs, tooling, supply-chain, installer, protocol, native-host; any extra node is also run.
- [ ] **Step 2:** Run every Linux-runnable command in each selected node's `AGENTS.md` (`tasks.md` 6.1 is the floor). Record each one in `apply.md` as PASS, FAIL (with the output) or NOT RUN (with the reason). The Windows and macOS jobs are NOT RUN locally.
- [ ] **Step 3:** After the push, check the `changes` summary: the map change selects every node, which is the one-time evidence reset. Check that `docs / agent-files` logs the offline guard first. Record both in `apply.md`; the PR note states there is no Android or Chrome behaviour to test on a device.
