# Android split and granular CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CI skip nodes that already passed on identical inputs within a pull request, and make Android an independent `android/` Gradle build, delivered as four pull requests (1, 1b, 2, 3).

**Architecture:** `.github/components.json` + `scripts/components.py` stay the single source of path ownership and gain per-node bases, overrides, retired paths and release-only paths. A new `scripts/last_green.py` reads GitHub Actions run results to find each node's last green PR head. Android moves to `android/` (shim wrapper, root orchestration through `includeBuild`), `rust/` becomes its own Gradle build, and the release planner keeps pre-move attribution through a per-commit layout epoch.

**Tech Stack:** Python 3.8-compatible stdlib (`unittest`), Node 26.8.1 (`node --test`), GitHub Actions, Gradle 9.5.0 / AGP 9.3 / Kotlin 2.2, Rust toolchain (`rust/`), pinned git-cliff 2.14.2.

**Spec:** `proposal.md`, `design.md` (D1-D8), `tasks.md` and the delta specs under `specs/` in this change directory (`openspec/changes/split-android-and-granular-ci/`). Task numbers in brackets, for example `[tasks 2.2]`, point into `tasks.md`.

## Global Constraints

- PR sequence: PR 1 (Tasks 1-7), PR 1b (Task 8), PR 2 (Tasks 9-18), PR 3 (Task 19); each is its own branch off `master` and its own squash-merged PR; later PRs start after the earlier one merged.
- Python is stdlib only and Python 3.8 compatible (no `X | Y` types, no `list[str]` generics); tests run with `python3 -I -m unittest discover -s scripts/tests -v` (also what `./gradlew scriptsTest` runs, with `VELES_GIT_CLIFF` set).
- Contract tests: `node --test verify/test/workflow-contracts.test.mjs` (Node 26.8.1 in CI) and the installer Python suite `native-bridge/installer/scripts/test_build_contract.py`.
- Lookup bounds are named constants: `MAX_CANDIDATES = 20`, `MAX_API_CALLS = 100`.
- A node is green only when every job of its declared `jobs` succeeded in one pinned attempt of one `ci.yml` `pull_request` run for that head, and no extra `<node> / ...` job exists.
- Required `ci-ok` name and semantics do not change; master pushes select every node in `ci.yml`; `release-build.yml` selection is untouched.
- Job display names today: android `lint`, `unit-tests`, `release-hook-guard`, `instrumented-tests`; web-extension `lint`, `unit-tests`, `browser-tests`; installer `lint`, `unit-tests (linux)`, `unit-tests (windows)`, `unit-tests (macos)`, `scripts-tests`; native-host `lint`, `unit-tests (linux)`, `unit-tests (windows)`, `unit-tests (macos)`, `build-contract`, `compile (windows)`, `compile (macos)`; bridge-scaffold `lint`, `unit-tests`; rust `lint`, `unit-tests`, `jni-integration`; protocol `contract-tests`; tooling `unit-tests`; supply-chain `build`.
- Android legacy release paths are exactly `app/`, `build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`, `gradle/libs.versions.toml` with epoch path `android/`; `config/detekt/` is never a legacy path.
- `rust/ndk-version` is the only NDK version source; NDK lookup order `ANDROID_NDK_HOME`, `$ANDROID_SDK_ROOT/ndk/<version>`, `local.properties` `sdk.dir`.
- Rust outputs stay under the repository-root `build/` (`build/rust/target`, `build/rust/host-jni`, `build/rust-tools`); JNI libraries go to `build/rust/jniLibs/<abi>`.
- Signing: no key material is committed. By owner-approved exception, the local, git-ignored fixture assets live at `android/config/android_keys/` and the documented command is `cd android && ./gradlew --init-script config/android_keys/fixture-signing.gradle ...`; release identity stays CI-only (generated only by master CI). APK certificate fingerprints are read from real output only.
- Before every PR and every update push: `./gradlew spotlessCheck detekt lintDebug` (from `android/` after PR 2) and the raw `android.util.Log` guard of `check-android.yml`; validate the future squash message with `python3 -I scripts/commitlint.py --file <path>`.
- Commits are Conventional Commits and end with the attribution trailer the harness requires; PR bodies end with the PR attribution line.
- Frozen evidence (`native-bridge/feasibility/evidence/`), archived OpenSpec changes and dated plans/specs are never rewritten.

## Review Focus

- Fork pull request whose runs list an empty `pull_requests` array: must be accepted by head repository and branch, never by SHA alone (test in Task 5).
- A superseded run cancelled by `cancel-in-progress`: its cancelled jobs must never count as green (Task 5).
- `.github/components.json` edited in the same PR that changes node paths: earlier evidence must be incompatible and the node must run (Task 5).
- Force-push or rebase leaving an older green ancestor: that ancestor is still used; a non-ancestor head is never used (Task 5).
- `.bat` shim on a Windows checkout and a symlink-free shim: `cd android && gradlew.bat --version` must report the root wrapper's Gradle (Task 11, Task 9).
- Deleted files of the moved `app/` appear in the migration PR because rename detection is off: they must be accounted, not "unaccounted" (Task 17).
- A `refactor` squash migration commit must not distort planned versions (Task 17).

---

## Task 1: Component map schema and ownership rules [tasks 1.1, 1.2]

**Files:**
- Modify: `scripts/components.py`
- Test: `scripts/tests/test_components.py`

**Interfaces:**
- Produces (new/changed in `scripts/components.py`):
  - `parse_map(text: str) -> dict` (validation of `load_map`, which becomes `parse_map(open(path).read())`)
  - `owner_nodes(file: str, cmap: dict) -> Optional[Set[str]]`: `None` means global or unaccounted; a set (possibly empty) means accounted
  - `release_paths(node: str, cmap: dict) -> List[str]` = node `paths` + node `release-paths` + `global`, never `ci-paths`, `ci-global`, retired or legacy entries
  - new map keys: node `jobs` (list of str), node `release-paths` (list), node `legacy-release` (`{"paths": [...], "epoch-path": str}`), top-level `global-overrides` (`{exact path: [node, ...]}`) and `retired-paths` (`{prefix: [node, ...]}`); every path list keeps the existing rules (no wildcards, repo-relative, forward slashes), override keys must be exact file names, every node name referenced must exist

- [x] **Step 1: Write the failing tests** in `test_components.py` (use `tmp_map(...)`): `test_override_selects_only_listed_nodes` (path `.github/components.json` with override `["tooling"]` and global `.github/`: `owner_nodes` returns `{"tooling"}`; `affected(["…"], cmap)` is true only for `tooling`), `test_override_still_counts_for_release` (`release_paths("android", cmap)` contains `.github/`), `test_retired_path_is_accounted_and_selects_listed_nodes` (`app/x.kt` under retired `app/` -> `{"android"}`, `unaccounted(...) == []`), `test_release_path_only_is_accounted_but_selects_nothing` (`owner_nodes` returns `set()`), `test_ci_paths_never_in_release_paths`, `test_release_paths_excludes_legacy_and_retired`, `test_wildcard_in_new_lists_rejected` (each new list), `test_override_unknown_node_rejected`, `test_unknown_key_value_types_rejected`.
- [x] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p "test_components.py" -v` from the repo root. Expected: the new tests FAIL (unknown keys or missing behaviour).
- [x] **Step 3: Implement** `parse_map`, the validation of the new keys, and the precedence in `owner_nodes`: `global-overrides` exact match first (returns the listed set), then `global`/`ci-global` (None), then node `paths`/`ci-paths` owners unioned with matching `retired-paths` nodes, then a `release-paths`-only match (empty set), then `unowned` (empty set), else None. Keep `unaccounted` and `affected` working on the new return values.
- [x] **Step 4: Run** the same command. Expected: PASS, and `python3 -I scripts/components.py check` still exits 0 on the real map.
- [x] **Step 5: Commit** `feat(ci): add overrides, retired and release-only paths to the component map`.

## Task 2: Declared jobs and the job-name contract [tasks 1.3, 1.4]

**Files:**
- Modify: `.github/components.json` (add `jobs` to all nine nodes, values from Global Constraints), `verify/test/workflow-contracts.test.mjs`

**Interfaces:**
- Consumes: `callerJobKeys(source)` and the existing workflow parsing helpers in `workflow-contracts.test.mjs`.
- Produces: a test `declared jobs equal the jobs of each ci.yml caller's reusable workflow` and a helper `derivedJobNames(callerName: string): string[]` in the same file (caller job name from `ci.yml`, called workflow from its `uses:`, display name = called job `name:` or job id; matrix jobs and unparseable shapes throw).

- [x] **Step 1: Write the failing test** asserting, for each node in `COMPONENT_MAP.nodes`, `derivedJobNames(node)` deep-equals the node's `jobs` (sorted), plus negative checks that a `strategy.matrix` job and a missing declared job throw or fail (use the file's existing negative-mutation style).
- [x] **Step 2: Run** `node --test verify/test/workflow-contracts.test.mjs`. Expected: FAIL (no `jobs` declared).
- [x] **Step 3: Add `jobs`** to every node in `.github/components.json` and implement `derivedJobNames`; `supply-chain` derives to `["build"]`.
- [x] **Step 4: Run** the same command and `python3 -I scripts/components.py check`. Expected: PASS.
- [x] **Step 5: Commit** `test(ci): declare and verify the jobs each node reports`.

## Task 3: PR 1 map cleanups on the current layout [tasks 1.5, 1.6, 1.7]

**Files:**
- Modify: `.github/components.json`, `scripts/tests/test_components.py`, `scripts/tests/test_release_plan.py`

**Interfaces:**
- Consumes: Task 1 schema.
- Produces: map state - `.editorconfig` removed from `ci-global` and listed in `ci-paths` of `android`, `web-extension`, `bridge-scaffold`; `global-overrides` = `{".github/components.json": ["tooling"]}`; `installer`: `protocol/` and `native-bridge/host/` moved to `release-paths`, `native-bridge/host/rust-toolchain.toml` added to `ci-paths`, `native-bridge/installer/` stays in `paths`.

- [x] **Step 1: Write the failing tests**: `test_components_json_selects_only_tooling`, `test_editorconfig_selects_prettier_and_ktlint_consumers` (selects `android`, `web-extension`, `bridge-scaffold` only), `test_protocol_change_does_not_select_installer` (`affected(["protocol/v1/limits.json"])["installer"]` false, `native-host` true), `test_host_toolchain_selects_installer`, and in `test_release_plan.py` `test_protocol_fix_still_bumps_installer` and `test_host_source_fix_still_bumps_installer` using the git fixture (a `fix` commit touching only `protocol/v1/limits.json`, respectively `native-bridge/host/src/lib.rs`, after an `installer-v` tag plans a patch bump). Also migrate the existing expectations that contradict the new map: `test_ci_global_selects_every_node` (`.editorconfig` is no longer global) and `test_owner_nodes_contract`.
- [x] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p "test_[cr]*.py" -v` with `VELES_GIT_CLIFF="$PWD/build/release-tools/git-cliff/git-cliff"` (an absolute path; run `./gradlew installGitCliff` first and again after any `clean`). Expected: FAIL on the new tests.
- [x] **Step 3: Edit the map** as specified above.
- [x] **Step 4: Run** the same command. Expected: PASS.
- [x] **Step 5: Scratch verification**: `git worktree add` a scratch copy, delete `protocol/` and the host files except `rust-toolchain.toml`, run `./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest bridgeInstallerScriptsTest`; expected PASS. If one fails, keep the failing path in `installer` `ci-paths` and record why in the PR body.
- [x] **Step 6: Commit** `refactor(ci): narrow editorconfig, components.json and installer selection`.

## Task 4: Per-node selection in `components.py` [tasks 2.1, 2.3]

**Files:**
- Modify: `scripts/components.py`
- Test: `scripts/tests/test_components.py`

**Interfaces:**
- Produces:
  - `selection_spec(cmap: dict, node: str) -> dict`: normalised, comparable value holding the node's own `paths`, `ci-paths`, `jobs` (sorted) and its `retired-paths` entries, plus the complete `global`, `ci-global`, `global-overrides`, `retired-paths`, `unowned` sections (sorted lists, mappings with sorted values)
  - `affected_for_node(node: str, files: Optional[List[str]], cmap: dict) -> bool` (`files is None` -> True)
  - CLI `components.py changed --base B [--head H] [--node-head SHA] [--node-bases FILE] [--evidence-notes FILE] [--summary-out FILE] [--all] [--github-output PATH]`: `--head` (default `HEAD`, the tested merge commit) with `--base` defines the full PR diff used for ownership validation and as the fallback; `--node-head` (the PR head, default `--head`) is the right side of each per-node evidence diff; `--node-bases` is JSON `{node: base_sha | null}`; `--evidence-notes` is JSON `{node: "never passed" | "incompatible map"}` containing only nodes that have a note (a missing key means evidence was used); `--summary-out` receives a markdown table with one row per node: `run`/`skip`, the base or `none`, and the reason `never passed`, `incompatible map`, `changed since <sha>`, `unchanged since <sha>` or `fallback`

- [x] **Step 1: Write the failing tests**: `test_selection_spec_ignores_key_order_and_whitespace`, `test_selection_spec_changes_when_override_membership_changes`, `test_selection_spec_changes_when_override_removed`, `test_selection_spec_changes_when_node_paths_expand`, `test_changed_all_selects_every_node_but_still_validates` (git fixture; unaccounted path -> exit 1, all outputs written first), `test_node_bases_skip_unchanged_node` (node with base equal to head-parent where only docs changed -> false), `test_node_without_base_uses_full_diff`, `test_unusable_node_base_falls_back_to_full_diff`, `test_validation_uses_merge_commit_diff_after_master_advanced` (master gains an unrelated commit after the PR branched: no spurious unaccounted or selected path), `test_unaccounted_path_fails_after_outputs_in_every_mode` (first run, node-bases, `--all`, fallback: outputs written, exit 1), `test_summary_rows_for_every_reason` (exact strings for the five reasons plus base and run/skip).
- [x] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p "test_components.py" -v`. Expected: FAIL.
- [x] **Step 3: Implement** the three interfaces. Validation (`unaccounted`) always uses the full diff `--base..--head`; with `--all` every output is true; with `--node-bases` each node uses `changed_files(base_n, node_head)` and falls back to the full diff when that returns `None`; the summary table is written from the same decision.
- [x] **Step 4: Run** the same command. Expected: PASS.
- [x] **Step 5: Commit** `feat(ci): select nodes per base and expose the selection spec`.

## Task 5: `scripts/last_green.py` [tasks 2.2, 2.4, 2.5, 2.6]

**Files:**
- Create: `scripts/last_green.py`, `scripts/tests/test_last_green.py`, `scripts/tests/fixtures/last_green/` (recorded API JSON)

**Interfaces:**
- Consumes: `components.parse_map`, `components.selection_spec`.
- Produces in `scripts/last_green.py`:
  - constants `MAX_CANDIDATES = 20`, `MAX_API_CALLS = 100`; `class LastGreenError(Exception)`
  - `class BudgetExhausted(Exception)` (deliberately not a `LastGreenError`)
  - `class Api(base_url: str, token: str, repo: str, budget: int = MAX_API_CALLS)` with `get(path: str, params: Optional[dict] = None) -> object` and `pages(path: str, params: Optional[dict] = None, key: Optional[str] = None) -> Iterator[dict]` (stdlib `urllib`, counts calls; raises `LastGreenError` on HTTP error (including 403 rate limit and missing permission), bad JSON or an unexpected structure, and `BudgetExhausted` when the call budget runs out)
  - `class NodeEvidence(NamedTuple)` with `base: Optional[str]` and `note: Optional[str]` (`"never passed"`, `"incompatible map"` or `None`)
  - `candidate_heads(commits_oldest_first: List[str], head: str, is_ancestor: Callable[[str, str], bool]) -> List[str]` (newest first, excludes `head`, skips non-ancestors individually, at most `MAX_CANDIDATES`)
  - `run_belongs_to_pr(run: dict, number: int, head_repo: str, head_ref: str) -> bool`
  - `node_is_green(node: str, expected: List[str], jobs: List[dict]) -> bool` (`expected` are job display names without the node prefix; jobs are API job dicts named `"<node> / <name>"`)
  - `find_bases(api: Api, cmap: dict, number: int, head: str, head_repo: str, head_ref: str, show_map: Callable[[str], Optional[str]], is_ancestor: Callable[[str, str], bool]) -> Dict[str, NodeEvidence]` (catches `BudgetExhausted`: nodes not yet resolved get `NodeEvidence(None, "never passed")`; a run whose job pages were not completely read is never green; lets `LastGreenError` propagate)
  - `main(argv: Optional[List[str]] = None) -> int` (reads `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, `GITHUB_API_URL`, `CI_LAST_GREEN`; flags `--pr --head --head-repo --head-ref --out --notes-out`; `--out` receives `{node: sha|null}`, `--notes-out` receives `{node: note}` for nodes with a note only (nodes with usable evidence are omitted); exit 0 on success, on budget exhaustion and under the kill switch, non-zero on `LastGreenError`)

- [x] **Step 1: Write the failing tests** (fake `Api` serving fixtures; each test name is the spec scenario): eligibility - `test_all_jobs_of_one_attempt_green`, `test_one_failed_job_not_green`, `test_failed_jobs_only_rerun_not_green`, `test_results_not_combined_across_runs`, `test_extra_job_not_green`, `test_attempt_mismatch_rejects_run`, `test_run_from_other_pr_ignored`, `test_fork_pr_with_empty_pull_requests_matches_by_repo_and_ref`, `test_cancelled_superseded_run_not_green`, `test_non_success_conclusions_not_green` (parameterised over `failure`, `cancelled`, `skipped`, `neutral`, `timed_out`, `action_required`, `None`), `test_commits_and_jobs_are_paginated`; ancestry/compat - `test_normal_push_uses_previous_head`, `test_force_push_without_green_ancestor_gives_none`, `test_force_push_with_surviving_green_ancestor`, `test_master_movement_only_does_not_select` and `test_master_merged_into_branch_selects_touched_nodes` and `test_failed_node_then_docs_only_push_runs_node` (the last three via the Task 4 CLI in integration tests), `test_equivalent_map_keeps_evidence`, `test_expanded_node_paths_invalidates_evidence`, `test_removed_override_invalidates_all_nodes`, `test_changed_override_membership_invalidates`, `test_renamed_job_invalidates`; outcomes/bounds - `test_never_passed_node_gives_none_without_touching_others`, `test_failed_then_fixed_node_uses_fixed_head`, `test_candidate_bound`, `test_call_bound_leaves_remaining_nodes_without_evidence` (exit 0, unresolved nodes `never passed`), `test_budget_exhausted_during_job_pagination_never_greens_run`, `test_api_error_exits_nonzero`, `test_rate_limit_and_missing_permission_exit_nonzero` (HTTP 403 variants), `test_malformed_structure_exits_nonzero` (valid JSON, missing keys), `test_notes_distinguish_never_passed_from_incompatible_map`, `test_last_green_output_files_drive_selection` (integration: files written by `last_green.main` are consumed unchanged by `components.py changed`), `test_kill_switch_returns_all_none_without_api_calls`.
- [x] **Step 2: Run** `python3 -I -m unittest discover -s scripts/tests -p "test_last_green.py" -v`. Expected: FAIL (module missing).
- [x] **Step 3: Implement** the interfaces. Endpoints: `GET /repos/{repo}/actions/workflows/ci.yml/runs` with `event=pull_request`, `head_sha`, `per_page=100`; jobs via `GET /repos/{repo}/actions/runs/{id}/attempts/{run_attempt}/jobs?per_page=100`. A run counts only when `status == "completed"`; reject it when any job's `run_attempt` differs. Compatibility compares `selection_spec(parse_map(show_map(sha)), node)` with the current spec; `None` map text means incompatible.
- [x] **Step 4: Run** the same command, `test_components.py` and the full scripts suite. Expected: PASS.
- [x] **Step 5: Commit** `feat(ci): find each node's last green pull request head`.

## Task 6: Wire `ci.yml`, contracts and docs [tasks 2.7, 2.8, 2.9]

**Files:**
- Modify: `.github/workflows/ci.yml`, `verify/test/workflow-contracts.test.mjs`, `docs/ci.md`, `CLAUDE.md` (CI notes only)
- Test: `verify/test/workflow-contracts.test.mjs`, `scripts/tests/test_components.py`

**Interfaces:**
- Consumes: Tasks 4 and 5 CLIs.
- Produces: `changes` job permissions `contents: read`, `actions: read`, `pull-requests: read`; env `PR_NUMBER`, `PR_HEAD_SHA`, `PR_HEAD_REPO`, `PR_HEAD_REF`, `CI_LAST_GREEN: ${{ vars.CI_LAST_GREEN }}`, `GITHUB_TOKEN`; step order unchanged (select -> check -> fail); step summary table.

- [x] **Step 1: Write the failing contract tests**: `ci.yml changes job declares exactly the three read permissions`, `ci.yml selects every node on push without calling last_green`, `ci.yml writes outputs before the unaccounted-path failure`, `release-build.yml still selects with components.py changed --base before`, `ci-ok accepts skipped needs and rejects failure and cancelled`.
- [x] **Step 2: Run** `node --test verify/test/workflow-contracts.test.mjs`. Expected: FAIL.
- [x] **Step 3: Update the existing contract assertion** `assertComponentCi` in `verify/test/workflow-contracts.test.mjs` that pins the old selection command, then edit `ci.yml`: for `pull_request`, run `last_green.py --out bases.json --notes-out notes.json` (exit non-zero -> `::warning::` and `--all` with the `fallback` reason while validation still runs on the full diff); then `components.py changed --base <merge-base> --head HEAD --node-head "$PR_HEAD_SHA" --node-bases bases.json --evidence-notes notes.json --summary-out summary.md` (or `--all` for push and fallback) and append `summary.md` to `$GITHUB_STEP_SUMMARY`.
- [x] **Step 4: Update `docs/ci.md`** (selection rules, last-green, kill switch `CI_LAST_GREEN=off`, rollback, new map fields, add the missing `android / release-hook-guard` row) and the `CLAUDE.md` CI notes that changed.
- [x] **Step 5: Run** the contract tests, `python3 -I -m unittest discover -s scripts/tests -v` and `python3 -I scripts/components.py check`. Expected: PASS.
- [x] **Step 6: Commit** `feat(ci): run last-green selection in the changes job`.

## Task 7: Open PR 1 [tasks 2.10, 2.11]

- [x] **Step 1:** Run `./gradlew spotlessCheck detekt lintDebug`, the raw `android.util.Log` guard commands from `check-android.yml`, `./gradlew scriptsTest`, `node --test verify/test/workflow-contracts.test.mjs`. Expected: all succeed (stop and report if any cannot run).
- [x] **Step 2:** Write the squash message to a file and run `python3 -I scripts/commitlint.py --file <file>` (title `feat(ci): skip unchanged nodes using per-node last-green runs`). Expected: exit 0.
- [x] **Step 3:** Push the branch, `gh pr create` with the body listing the map cleanups, the kill switch and rollback (`CI_LAST_GREEN=off` or revert), ending with the PR attribution line; link the PR.
- [x] **Step 4: Scratch PR demonstration**: a scratch PR with an Android-only change, a docs-only follow-up push, then a Rust push; expected summaries `android: unchanged since <sha>` after the docs push and `android: changed since <sha>` after the Rust push; attach them to PR 1.

## Task 8: PR 1b - move the protocol spec [tasks 3.1-3.6]

**Files:**
- Move: `docs/protocol/` -> `protocol/docs/` (14 tracked files)
- Modify: live references listed by the inventory (Kotlin/Rust/TS/`.mjs` comments, `build.gradle.kts`, `protocol/checks/evidence.mjs`, `protocol/v1/manifest.json`, `protocol/v1/binary-layouts.json`, `protocol/README.md`, `native-bridge/host/README.md`, live docs), the fixture generator and `protocol/v1/fixtures/crypto.json`

- [x] **Step 1: Inventory**: `git grep -n "docs/protocol" -- . ':!native-bridge/feasibility/evidence' ':!openspec/changes/archive' ':!docs/superpowers'` saved to the PR body; list excluded historical files.
- [x] **Step 2: Failing check first**: write `protocol/checks/spec-location.test.mjs` asserting `protocol/docs/veles-v1.md` exists, `docs/protocol` does not, and `buildEvidenceInventory` resolves the spec from `protocol/docs`; run `node --test protocol/checks/spec-location.test.mjs`. Expected: FAIL.
- [x] **Step 3:** wire `protocol/checks/spec-location.test.mjs` into the enumerated test list of `protocol/checks/check.mjs` (next to `contract.test.mjs`, `bridge.test.mjs`, `state.test.mjs`, `crypto.test.mjs`); `git mv docs/protocol protocol/docs`; update the references (`evidence.mjs` resolves `join(root, "docs", "protocol")` paths and its fallback branch to `protocol/docs`); update fixture description strings in the generator source and regenerate `crypto.json` so generated and committed fixtures stay equal.
- [x] **Step 4: Verify**: `./gradlew protocolCheck protocolFreshEvidenceCheck rustTest bridgeHostTest`, `npm test` in `web-extension`, `python3 -I scripts/components.py check`, and the inventory command again. Expected: all pass and no live match remains.
- [x] **Step 5: Commit and PR** `refactor(protocol): move the protocol specification under protocol/`; mark the PR body with the revert rollback; run the PR gate of Task 7 steps 1-3.

## Task 9: PR 2 spikes [tasks 4.1-4.4]

**Files:** scratch only (outside the repo); results recorded in the PR body.

- [x] **Step 1: Real-build spike**: copy the repo to a scratch directory, apply the include layout (`includeBuild("android")`, `includeBuild("rust")`, android including `../rust`) on the real app build with AGP 9.3, KSP, Hilt and protobuf; run `./gradlew assembleDebug` from the root and from `android/`. Expected: both build. On failure, adopt the script fallback of design D5 and record it.
- [x] **Step 2: Shim spike**: run `cd android && ./gradlew --version` on Linux and `gradlew.bat --version` on a Windows checkout (use the `homepc` skill for the Windows machine), then a debug build there (`gradlew.bat assembleDebug` from `android/`, or `gradlew.bat help` if that machine has no Android SDK; report which). Expected: both report Gradle 9.5.0.
- [x] **Step 3: Version spike**: `cd android && ./gradlew -q :app:androidGitVersion` must print the same version name and code as the old layout on the same commit.
- [x] **Step 4: git-cliff spike** (pinned 2.14.2) on a scratch repository: 500 repeated `--skip-commit`, `--include-path` and `--context` together. If it fails, plan the planner-side commit filter for Task 17.

## Task 10: Rust build extraction in the old layout [tasks 4.5-4.9]

**Files:**
- Create: `rust/settings.gradle.kts`, `rust/build.gradle.kts`, `rust/ndk-version`
- Modify: root `build.gradle.kts`, root `settings.gradle.kts`, `app/build.gradle.kts`, `gradle/libs.versions.toml`, `rust/scripts/*` (only paths that change)
- Test: `rust/scripts/test-ndk-lookup.sh` (new, shell test of the lookup)

**Interfaces:**
- Produces: included build `rust` exporting tasks `rustToolchainCheck`, `rustInstall`, `rustWasm`, `rustHostJni`, `rustJni`, `rustNdkVersion`, `rustFormat`, `rustLint`, `rustTest`, `rustPackage` (names unchanged); `rustJni` output dir `build/rust/jniLibs/<abi>`; `rustNdkVersion` prints the contents of `rust/ndk-version`; NDK directory resolver with the specified order and the failure message `NDK <version> not found; searched: <locations>`.

- [x] **Step 1: Inventory** every root reference to a Rust task (providers and string names such as `dependsOn("rustHostJni", "rustWasm")`) and every `rootProject`/`:app` reference in `app/build.gradle.kts`; paste the list into the PR body.
- [x] **Step 2: Failing test**: write `rust/scripts/test-ndk-lookup.sh` covering `ANDROID_NDK_HOME` winning, SDK-directory lookup, `local.properties` lookup, the failure message, and `rustNdkVersion` equal to `rust/ndk-version`; run it. Expected: FAIL.
- [x] **Step 3:** Move the Rust tasks verbatim into `rust/build.gradle.kts`, keeping outputs under the repository-root `build/`; implement the NDK lookup; switch `app` `ndkVersion` to read `rust/ndk-version` and remove `ndk` from the catalog; rewire the root build and `app/build.gradle.kts` through `gradle.includedBuild("rust").task(":<name>")`; stage `build/rust/jniLibs` as a `jniLibs` source with `merge*JniLibFolders` depending on `rustJni`; unit tests depend on `rustHostJni` and use `build/rust/host-jni`.
- [x] **Step 3b:** Record with the user that AGP no longer auto-installs the NDK and document the `sdkmanager` step in `BUILD.md`.
- [x] **Step 4: Old-layout checkpoint**: `./gradlew assembleDebug && rust/scripts/verify-apk-jni.sh app/build/outputs/apk/debug/app-debug.apk`, `./gradlew testDebugUnitTest protocolCheck rustTest`, the clean contract (`./gradlew clean && rust/scripts/assert-clean.sh`), and `rust/scripts/test-ndk-lookup.sh`. Expected: all pass; shared Rust tasks run once per root invocation.
- [x] **Step 5: Commit** `refactor(rust): extract the Rust tasks into an included Gradle build`.

## Task 11: Shims and shared hook script in the old layout [tasks 5.1, 5.2]

**Files:**
- Create: `android/gradlew`, `android/gradlew.bat`, `android/.gitattributes`, `.githooks/install-hooks.gradle.kts`
- Modify: `.github/components.json` (provisional `android/` in `android` and `protocol` `paths`), root `build.gradle.kts` (apply the shared script instead of the inline `InstallGitHooksTask`), `verify/test/workflow-contracts.test.mjs`
- Test: `verify/test/workflow-contracts.test.mjs`

**Interfaces:**
- Produces: shim content `#!/bin/sh` + `exec "$(dirname "$0")/../gradlew" "$@"` and `@call "%~dp0..\gradlew.bat" %*`; shared script registering `installGitHooks` with unchanged behaviour (sets `core.hooksPath` to `.githooks` only when unset, warns otherwise, no-op when `CI=true`).

- [x] **Step 1: Failing contract test**: `android shims delegate to the root wrapper, are not symlinks, use LF and the repository has exactly one gradle-wrapper.jar`; run `node --test verify/test/workflow-contracts.test.mjs`. Expected: FAIL.
- [x] **Step 2:** Create the shims (`chmod +x android/gradlew`), `android/.gitattributes` (`gradlew text eol=lf`, `gradlew.bat text eol=lf`), the shared hook script (moved from the root task) and the provisional map entries; run `python3 -I scripts/components.py check`.
- [x] **Step 3: Verify hooks in the old layout**: with a scratch clone, no `core.hooksPath` -> `./gradlew assembleDebug` sets `.githooks`; custom path -> warning and unchanged; `CI=true` -> unchanged. Expected as stated.
- [x] **Step 4: Commit** `build(android): add the wrapper shims and a shared hook installer`.

## Task 12: Layout cut-over (single commit) [task 5.3]

**Files:**
- Move: `app/` -> `android/app/`, `config/detekt/` -> `android/config/detekt/`, `gradle/libs.versions.toml` -> `android/gradle/libs.versions.toml` (Android entries)
- Create: `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/gradle.properties`
- Modify: root `settings.gradle.kts`, root `build.gradle.kts`, root `gradle.properties`, `android/app/build.gradle.kts`

**Interfaces:**
- Produces: `android/settings.gradle.kts` (repositories from the old root settings, `include(":app")`, `includeBuild("../rust")`); `android/build.gradle.kts` (plugin aliases with `apply false`, Spotless Kotlin configuration moved from the root, fixture-path helper resolving `../protocol/v1` and `../protocol/conformance`, `rustNdkVersion` task depending on the included Rust task); root `settings.gradle.kts` with `includeBuild("android")` and `includeBuild("rust")` and no `include(":app")`; root aliases `assembleDebug`, `assembleRelease`, `testDebugUnitTest`, `lintDebug`, `detekt`, `connectedDebugAndroidTest` -> `gradle.includedBuild("android").task(":app:<name>")`, `spotlessCheck`, `spotlessApply` -> `task(":<name>")`, `protocolCheck` and `clean` depending on included-build tasks; loopback classpath at `android/app/build/session-loopback/classpath.txt`.

- [x] **Step 1: Prepare** all edits on one working tree; do not commit a half-moved state.
- [x] **Step 2: Verify at the cut-over** (CI need not be green at this commit; only the PR head must be): `cd android && ./gradlew assembleDebug testDebugUnitTest lintDebug spotlessCheck detekt`; `rust/scripts/verify-apk-jni.sh android/app/build/outputs/apk/debug/app-debug.apk`; from the root `./gradlew assembleDebug protocolCheck`; then `./gradlew clean && rust/scripts/assert-clean.sh`, rebuild with `./gradlew assembleDebug` and verify the APK again. Expected: all succeed.
- [x] **Step 3: Commit** `refactor(android): move the app to an independent android/ Gradle build` (single commit with the root rewiring and hook application).

## Task 13: Editor config and acceptance checks [tasks 5.4, 5.5, 5.6]

**Files:**
- Create: `android/.editorconfig`, `verify/check-android-layout.sh`
- Modify: root `.editorconfig`

- [x] **Step 1: Failing acceptance check** `sh verify/check-android-layout.sh` (one executable command, rerun after Step 2) asserting that `android/.editorconfig` exists with `root = true` and the ktlint overrides and the root `.editorconfig` has no `[*.{kt,kts}]` section, plus: the Android build succeeds with the root `build.gradle.kts`, `settings.gradle.kts` and `gradle.properties` temporarily renamed (scratch copy); `cd rust && ../gradlew help` configures alone; a single root invocation `./gradlew assembleDebug testDebugUnitTest rustHostJni rustJni --console=plain` executes each shared Rust task exactly once (count task executions in the output); `cd android && ./gradlew --version` reports the root wrapper's Gradle; the root aliases resolve with `--dry-run`. Expected: FAIL on the editorconfig ownership assertion only; every other assertion passes.
- [x] **Step 2:** Add `android/.editorconfig` (generic rules plus the `[*.{kt,kts}]` ktlint overrides, `root = true`) and trim the Kotlin section from the root file.
- [x] **Step 3: Rerun** `sh verify/check-android-layout.sh` (expected PASS) and verify hooks from both entry points (cases of Task 11 step 3 from `android/` and from the root alias). Expected as stated.
- [x] **Step 4: Commit** `build(android): give Android its own editorconfig and add layout acceptance checks`.

## Task 14: MAC guard `--scan` [task 5.7]

**Files:**
- Modify: `scripts/check-mac-comparisons.mjs`
- Test: `scripts/check-mac-comparisons.test.mjs` (new)

**Interfaces:**
- Produces: repeatable `--scan DIR`; each directory must exist (exit 2 with `no such directory <path>`); an empty scan set exits 2; `DEFAULT_ROOTS = ['android/app/src/main', 'web-extension/src']`.

- [x] **Step 1: Failing tests** (`node --test scripts/check-mac-comparisons.test.mjs`): `a violation injected into a temporary Kotlin root fails`, `a violation injected into a temporary TypeScript root fails`, `scan of a missing directory exits 2`, `empty scan set exits 2`, `default roots scan android/app/src/main and web-extension/src`, `--root keeps its meaning as base for default roots`.
- [x] **Step 2: Implement** the option in `main()`; keep `--file`.
- [x] **Step 3: Run** the tests and `node scripts/check-mac-comparisons.mjs`. Expected: PASS and exit 0 on the real tree.
- [x] **Step 4: Commit** `fix(ci): scan explicit directories in the MAC comparison guard`.

## Task 15: Workflows and contract migration [tasks 5.8, 5.9, 5.10]

**Files:**
- Modify: `.github/workflows/{check-android,check-web-extension,check-rust,check-protocol,check-tooling,build-android,release-build}.yml`, `verify/test/workflow-contracts.test.mjs`, `native-bridge/installer/scripts/test_build_contract.py`, `.github/components.json`, `scripts/tests/test_components.py`
- Create: `verify/check-root-aliases.sh` (also added to `tooling` `ci-paths` so a script-only edit selects `tooling`; test `test_root_alias_script_selects_tooling` in `test_components.py`)
- Audit: `.github/workflows/publish-release.yml`

**Interfaces:**
- Produces: `check-android.yml` Gradle steps with `working-directory: android` (emulator runner via its `working-directory` input), guards (`check-mac-comparisons.mjs --scan android/app/src/main`, `android.util.Log`, Kotlin pairing-code grep) without `working-directory`; `check-web-extension.yml` runs `--scan web-extension/src` and the `web-extension/src/debug` pairing-code grep; `check-rust.yml` `jni-integration` runs root aliases and verifies `android/app/build/outputs/apk/debug/app-debug.apk`; `check-tooling.yml` steps `sh verify/check-root-aliases.sh` (runs `./gradlew --dry-run assembleDebug assembleRelease testDebugUnitTest lintDebug spotlessCheck detekt connectedDebugAndroidTest protocolCheck`), `sh verify/check-root-aliases.sh --self-test` (executes a scratch dry run whose alias targets a missing task and expects a non-zero exit) and `node --test scripts/check-mac-comparisons.test.mjs`; `build-android.yml` reads `android/app/build/outputs/...`.

- [x] **Step 1: Failing contract tests**: `check-android runs Gradle from android and guards from the root`, `web-extension lint runs the MAC and pairing-code guards`, `tooling resolves every root alias with --dry-run and the inventory distinguishes dry-run from execution`, `build-android reads the relocated APK`, `tooling runs the alias check, its self-test and the MAC guard tests` (mutation: removing a step fails the contract); update the existing assertions that hard-code `app/build` and `:app:assembleDebug` in both contract suites. Run both suites. Expected: FAIL.
- [x] **Step 2: Inventory** root-only commands that `check-android.yml` calls (`rustHostJni`, `rustNdkVersion`): drop the redundant host-JNI prebuild and call the Android-root `rustNdkVersion` from `android/`.
- [x] **Step 3: Edit the workflows** as specified; audit `publish-release.yml` (it downloads the reusable build's artifact and signs it; edit only what changes).
- [x] **Step 4: Run** `node --test verify/test/workflow-contracts.test.mjs` and `python3 -I native-bridge/installer/scripts/test_build_contract.py`. Expected: PASS.
- [x] **Step 5: Commit** `ci(android): run Android checks from android/ and keep guards at the repository root`.

## Task 16: Path audit and documentation [tasks 5.11, 5.12]

**Files:**
- Modify: `rust/scripts/assert-clean.sh`, `.dockerignore`, `.idea/**`, `verify/**`, `CLAUDE.md`, `BUILD.md`, `CONTRIBUTING.md`, `docs/ci.md`, `docs/releasing.md`

- [x] **Step 1: Audit command** (record in the PR): `git grep -nE '(^|[^a-zA-Z_./-])app/' -- . ':!native-bridge/feasibility/evidence' ':!openspec/changes/archive' ':!docs/superpowers' ':!android'` and the `config/detekt` equivalent.
- [x] **Step 2:** Update every live match (`assert-clean.sh` paths to `android/app/build/...`, `.dockerignore` `android/app/build`, docs and `CLAUDE.md` including `cd android && ./gradlew --init-script config/android_keys/fixture-signing.gradle ...`).
- [x] **Step 3: Re-run** the audit and classify each remaining match as operational or intentional historical-layout data (the `legacy-release` and `retired-paths` entries and their planner fixtures added in Task 17). Expected: zero operational matches; list the intentional ones in the PR and re-run the audit after Task 17.
- [x] **Step 4: Commit** `docs(android): point live files at the android/ layout`.

## Task 17: Component map for the move and the legacy release pass [tasks 5.13, 5.14, 5.15]

**Files:**
- Modify: `.github/components.json`, `scripts/components.py` (if needed), `scripts/release_plan.py`
- Test: `scripts/tests/test_components.py`, `scripts/tests/test_release_plan.py`, `scripts/tests/gitfixture.py`

**Interfaces:**
- Consumes: Task 1 schema; existing `make_plan`, `_cliff`, `_include_args`, `_reasons`, `_expected_bump`.
- Produces: `legacy_commits(repo: Path, tag: Optional[str], epoch_path: str) -> List[str]` (old-epoch commit ids in `<tag>..HEAD`, those whose tree lacks `epoch_path`); `legacy_skip_args(repo: Path, tag: Optional[str], epoch_path: str) -> List[str]` (`--skip-commit <sha>` for every new-epoch commit, or a planner-side filter if Task 9 step 4 failed); `make_plan` merges the main and legacy passes by commit id, takes the highest bump for tagged components, keeps `initial-version` for untagged ones, and keeps the `--bumped-version` cross-check when the legacy pass contributes no commit.
- Map state: `android` `paths` `android/`, `rust/`, `protocol/`; `ci-paths` hook script (and no `.editorconfig`); `build.gradle.kts`, `settings.gradle.kts`, `gradle.properties` removed from `global` and added to the `paths` of every non-Android node; `protocol` lists `android/`; `retired-paths` `{"app/": ["android"], "config/detekt/": ["android"]}`; `legacy-release` as in Global Constraints.

- [x] **Step 1: Failing tests** in `test_components.py`: `test_root_build_file_selects_every_node_but_android` (migrating the existing root-global expectations and `test_editorconfig_selects_prettier_and_ktlint_consumers`, which after the move expects `web-extension` and `bridge-scaffold` only because `.editorconfig` leaves `android` `ci-paths`), `test_wrapper_selects_every_node`, `test_android_detekt_selects_android_and_protocol`, `test_migration_pr_deleted_app_files_are_accounted` (files `app/build.gradle.kts` and `config/detekt/detekt.yml` plus `android/…`: `unaccounted == []`, android selected), `test_protocol_spec_edit_selects_protocol_consumers_not_installer`, `test_android_check_workflow_override` is deferred to Task 19. In `test_release_plan.py` (git fixture with an epoch commit that adds `android/`): `test_unreleased_feat_app_before_move_still_bumps_android`, `test_root_only_feat_after_move_does_not_bump_android_but_bumps_web_extension_and_installer`, `test_squash_migration_counts_through_normal_pass`, `test_revert_and_reapply_keeps_old_epoch_attribution`, `test_commit_matching_both_passes_counted_once`, `test_legacy_pass_inert_after_new_tag`, `test_untagged_component_keeps_initial_version_with_legacy_commits`, `test_config_detekt_gains_no_attribution`, `test_refactor_migration_leaves_earlier_attribution_unchanged`, `test_bumped_version_crosscheck_kept_without_legacy_commits`.
- [x] **Step 2: Run** `VELES_GIT_CLIFF="$PWD/build/release-tools/git-cliff/git-cliff" python3 -I -m unittest discover -s scripts/tests -p "test_[cr]*.py" -v`. Expected: FAIL.
- [x] **Step 3: Implement** the map edits and the planner functions above.
- [x] **Step 4: Run** the same command and `python3 -I scripts/components.py check`. Expected: PASS.
- [x] **Step 5: Commit** `feat(release): keep release attribution across the android/ move`.

## Task 18: Verify identity, open PR 2 and gate it [tasks 5.16-5.19]

- [x] **Step 1: Identity** in two scratch clones (never the working repository): check out the merge base in one and the PR head in the other, create the same local tag `android-v1.2.3` on each checked-out commit so the git-derived version inputs are identical, and build debug and release APKs with the same approved signing configuration (`VELES_KEYSTORE_*`, or the default debug keystore for the debug APK). Compare `versionName` and `versionCode` (`aapt dump badging`; expected `1.2.3` and `10203` in both) and certificate fingerprints (`apksigner verify --print-certs`) from real output; expected identical. If the approved identity is unavailable, stop and report; never regenerate, substitute or move keys.
- [x] **Step 2:** `./gradlew clean && rust/scripts/assert-clean.sh`; then run `./gradlew installGitCliff` in each of the two scratch clones (clean removes release tooling) and, without tags, `python3 -I scripts/release_plan.py --git-cliff "$PWD/build/release-tools/git-cliff/git-cliff" plan --releases-json releases.json --manifest-json manifest.json --out plan.json --notes notes.md --today 2026-11-13` with `releases.json` containing `[]` and `manifest.json` containing `{}`; expected: identical attribution of every pre-existing commit in `notes.md` for the merge-base and PR-head runs, and no new Android bump caused by the migration commit.
- [x] **Step 3: Local gate**: from `android/` run `./gradlew spotlessCheck detekt lintDebug`, the raw Log guard, and from the root the checks that remain; validate the squash message with commitlint; write the revert rollback into the PR body.
- [x] **Step 4:** Push, `gh pr create` titled `refactor(android): move the app to an independent android/ Gradle build`, link the PR.
- [x] **Step 5: Owner confirmation**: ask the owner to confirm the local `config/android_keys/fixture-signing.gradle` works from `android/` and record it in the PR.
- [x] **Step 6: Merge gate**: all CI nodes green (including `rust / jni-integration`, `protocol / contract-tests`, `tooling / unit-tests`) and step 5 recorded before merging.

## Task 19: PR 3 - node-owned Android workflows [tasks 6.1-6.3]

**Files:**
- Modify: `.github/components.json`, `scripts/tests/test_components.py`, `docs/ci.md`

**Interfaces:**
- Produces: `global-overrides` entries `.github/workflows/check-android.yml` and `.github/workflows/build-android.yml`, each `["android", "tooling", "installer"]`.

- [x] **Step 1: Failing tests** (also migrate the check-android.yml assertion of `test_global_selects_all`, which assumes a global workflow, and rerun the whole components suite after each map change): `test_check_android_workflow_selects_android_tooling_installer`, `test_build_android_workflow_selects_android_tooling_installer`, `test_other_workflows_stay_global`; run `python3 -I -m unittest discover -s scripts/tests -p "test_components.py" -v`. Expected: FAIL.
- [x] **Step 2:** Add the two overrides; update `docs/ci.md`.
- [x] **Step 3: Verify** the tooling workflow contracts and the installer `scripts-tests` still run for those edits (`node --test verify/test/workflow-contracts.test.mjs`, `python3 -I native-bridge/installer/scripts/test_build_contract.py`). Expected: PASS.
- [x] **Step 4: Gate and PR** as Task 7 steps 1-3, title `ci: select only the owning nodes for the Android workflows`, revert rollback in the body.

## Task 20: Wrap-up [tasks 7.1, 7.2]

- [ ] **Step 1:** After PRs 1, 1b, 2 and 3 merge, run `/opsx:verify` and then `/opsx:archive` for this change.
- [ ] **Step 2 (follow-up, not a prerequisite):** when no in-flight PR needs them and the next Android release tag contains the migration, open a cleanup change that removes the inert `legacy-release` and `retired-paths` entries.
