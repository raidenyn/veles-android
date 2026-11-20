## 1. Docs offline guard (test first)

- [x] 1.1 Create `docs/checks/test_check_offline.py`. It builds fixtures in temporary git repositories (`git init` and `git add`, with `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, no clock), imports the guard module and calls `scan(root)` in-process. Write the failing tests: every rejected and accepted form listed in design D1, the real `docs/checks/` tree passing, and the exit-code contract (0 clean, 1 violation or unparsable file, 2 usage or I/O) (spec: *Docs check scripts stay offline*)
- [x] 1.2 Implement `docs/checks/check_offline.py`: Python 3.8 standard library only, `--root`, `git ls-files docs/checks`, the D1 rules with `STDLIB_ALLOWED`, sibling modules, the constant `subprocess` attributes, `IMPORTLIB_EXEMPT = (("check_agent_files.py", "_load_engine"), ("test_check_agent_files.py", "_load_engine"))` and the `git` global-option and subcommand rules, and `error <path>:<line>: <message>` output. Iterate until 1.1 passes
- [x] 1.3 `git add docs/checks/check_offline.py docs/checks/test_check_offline.py`, because the guard lists files with `git ls-files` and must scan its own files. Then run `python3 docs/checks/check_offline.py --root .` on the real tree (exit 0), then `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` with the local Python and with Python 3.8 (`uv run --python 3.8 python -m unittest discover -s docs/checks -p 'test_*.py' -v`)

## 2. Docs workflow and the tooling contract

- [x] 2.1 In `verify/test/workflow-contracts.test.mjs`, write the failing changes first:
  - `assertDocsCheckWorkflow` expects four runs, with `python3 docs/checks/check_offline.py --root .` as the first `run:` of `agent-files` (an explicit index-order assertion before the agent-file check), and has the new count message;
  - it rejects any `environment:` and any `git push`;
  - the existing mutation tests follow the new message, and dropped-guard, reordered-guard, changed-guard-arguments (like the site guard's `guardArgs` mutation), `environment:` and `git push` mutations are added (design D2)
- [x] 2.2 Add `python3 docs/checks/check_offline.py --root .` as the first `run:` of the `agent-files` job in `.github/workflows/check-docs.yml`, and rewrite the header comment: the file selects `docs` and `tooling`, and it runs the offline guard (design D4). Run `node --test verify/test/workflow-contracts.test.mjs` until it passes
- [x] 2.3 Lift the signing, environment and publishing regexes of the two existing all-workflow boundary tests to module-level constants. Add the test "the all-workflow release boundary covers the installer boundary": it reads `native-bridge/installer/scripts/test_build_contract.py`, finds the loop body after `    for name, source in workflows.items():` up to the first non-empty line indented four spaces or less, and requires exactly three `r"…"` literals. Each signing term must match the tooling signing regex; both environment lines must match both environment regexes; the publishing sources must be equal once `\/` becomes `/`. Missing extraction fails. Write the comparison as a pure function over the installer source text, and give it negative tests (design D2; spec: *Installer boundary gains a term*):
  - a source with an extra signing term the tooling regex does not match;
  - a different publishing regex;
  - a missing anchor;
  - a loop body with two or four literals.

  Each must fail
- [x] 2.4 In the remote-code scope test (around lines 1355–1358), take `.github/workflows/check-docs.yml` out of the must-cover loop and assert that `docs/checks/` and `.github/workflows/check-docs.yml` are both absent from `REMOTE_CODE_SCOPES`. Then drop both entries and their comment line from `verify/check-remote-code.mjs`
- [x] 2.5 Delete the test "flags a docs/checks script …" from `verify/test/remote-code.test.mjs`, and run `node --test verify/test/remote-code.test.mjs`

## 3. Installer boundary exemption

- [x] 3.1 In `native-bridge/installer/scripts/test_build_contract.py`, write the failing tests:
  - only `TOOLING_OWNED_WORKFLOWS == ("check-docs.yml",)` is skipped;
  - a signing credential added to `check-docs.yml` is not reported by `check_gate_boundary`, while the same credential added to `pr-title.yml` still is (spec: *Docs workflow outside the installer boundary*)
- [x] 3.2 Add `TOOLING_OWNED_WORKFLOWS` and skip those names in the all-workflows loop, with a comment that names the tooling all-workflow tests as the owner. Keep the loop's three `re.search(r"…", other)` literals and its indentation unchanged, because 2.3 extracts them. Run `./gradlew bridgeInstallerScriptsTest` (or `python3 -m unittest` on that file if Gradle is unavailable)

## 4. Component map

- [x] 4.1 In `scripts/tests/test_components.py`, write the failing tests (design D3):
  - the `check-docs.yml` override is `["docs", "tooling"]` and selects exactly those nodes;
  - every `git ls-files docs/checks` file selects exactly `{"docs"}`;
  - every `git ls-files verify/test` file selects exactly `{"tooling"}`;
  - every `git ls-files verify` file except `verify/test/**` and `verify/AGENTS.md` selects `supply-chain`;
  - a hypothetical `verify/new-check.mjs` is unaccounted;
  - `native-bridge/installer/scripts/test_build_contract.py` selects exactly `installer`, `protocol`, `supply-chain` and `tooling`
- [x] 4.2 Edit `.github/components.json`:
  - set the override to `["docs", "tooling"]`;
  - remove supply-chain's `ci-paths` key;
  - replace supply-chain's `verify/` entry with `verify/lib/` and the exact top-level files from design D3, re-checked against `git ls-files verify` and without duplicating `verify/package*.json`;
  - add `native-bridge/installer/scripts/test_build_contract.py` to `tooling.ci-paths`.

  Commit 4.2 together with 4.3: between them `docs / agent-files` is red, because `OVERRIDE_TARGETS` no longer matches the map. Run `python3 -I scripts/components.py check` and `./gradlew scriptsTest`
- [x] 4.3 Update the agent-file check: `OVERRIDE_TARGETS[".github/workflows/check-docs.yml"]` becomes `("docs", "tooling")` in `docs/checks/check_agent_files.py`, and `EXPECTED_OVERRIDE_TARGETS` changes in `docs/checks/test_check_agent_files.py`. In `test_override_target_sets_exact`, the `check-docs.yml` case uses the wrong value `["docs"]` with the fragment `"tooling"`. Run `python3 docs/checks/check_agent_files.py --root .` and the docs unit tests

## 5. Documentation and selection evidence

- [ ] 5.1 Update `docs/ci.md`:
  - the `docs / agent-files` row of the command table;
  - the override paragraph (around lines 139–146) and the workflow paragraph (around line 162): `check-docs.yml` selects `docs` and `tooling`, and why;
  - a sentence that `docs/checks/` is guarded by the docs node, not the remote-code scan;
  - supply-chain's `verify/` ownership, with `verify/test/` belonging to `tooling`
- [ ] 5.2 Add the guard command to the Commands block of `docs/AGENTS.md` (keep it at 40 lines or fewer) and to the command list in `docs/agent-guidance.md`, with one sentence on what the guard enforces. Run `python3 docs/checks/check_agent_files.py --root .` and `python3 docs/checks/check_site.py --root .`
- [ ] 5.3 Record the selection evidence. Run `python3 -I scripts/components.py changed` against the merge base for synthetic commits on a scratch branch that is deleted afterwards:
  - only `docs/checks/check_site.py`;
  - only `.github/workflows/check-docs.yml`;
  - only `verify/test/workflow-contracts.test.mjs`.

  Expect `docs`, then `docs` and `tooling`, then `tooling`. Also re-run the two historical commits from the brainstorm (#174, #175) against the new map, and record the outputs in `apply.md`

## 6. Pre-push checks and CI

- [ ] 6.1 Find the touched nodes with `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` (expected: docs, tooling, supply-chain, installer, protocol, native-host). Run **every** command in the `AGENTS.md` of each selected node that can run on Linux; the lists below are a floor, not the whole set:
  - docs: the offline guard, the agent-file check, `check_gate_b_matrix.py`, `check_site.py` and the unit tests;
  - tooling: everything `scripts/AGENTS.md` lists, including `python3 -I scripts/components.py check` and its `node --test` suites, plus `node --test verify/test/workflow-contracts.test.mjs` and `./gradlew scriptsTest`;
  - supply-chain: `node --test verify/test/*.test.mjs` and `verify/verify-supply-chain.sh`, after `npm ci --ignore-scripts --prefix verify` and `./gradlew installCargoDeny`;
  - installer: `./gradlew bridgeInstallerScriptsTest` and the Linux checks;
  - native-host and protocol: their listed Linux checks.

  Report each check that cannot run here (Windows or macOS jobs) as NOT RUN, never as passed
- [ ] 6.2 After the PR is pushed, confirm in the `changes` job summary that the map change selected every node (the expected one-time evidence reset), and that `docs / agent-files` ran the offline guard first. State in the PR note what ran and where; there is no Android or Chrome behaviour to test on a device
