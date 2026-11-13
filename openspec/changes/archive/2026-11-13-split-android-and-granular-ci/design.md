## Context

`brainstorm.md` fixes the requirements and decisions; this document says how to build them. Verified facts the
design relies on:

- Node selection lives in `.github/components.json` + `scripts/components.py`. `ci.yml`'s `changes` job runs
  `components.py changed --base <merge-base|before>`; each node calls one reusable `check-<node>.yml`; `ci-ok`
  is the only required aggregate. `release_plan.py` feeds git-cliff `--include-path` from
  `components.release_paths(node)` = node `paths` + every `global` entry, over `<tag>..HEAD`.
- Reusable-workflow jobs are reported as `<node> / <job name>` (for example `native-host / unit-tests (windows)`).
  The job inventory today: android `lint`, `unit-tests`, `release-hook-guard`, `instrumented-tests`;
  web-extension `lint`, `unit-tests`, `browser-tests`; installer `lint`, `unit-tests (linux|windows|macos)`,
  `scripts-tests`; native-host `lint`, `unit-tests (linux|windows|macos)`, `build-contract`,
  `compile (windows|macos)`; bridge-scaffold `lint`, `unit-tests`; rust `lint`, `unit-tests`, `jni-integration`;
  protocol `contract-tests`; tooling `unit-tests`; supply-chain `build`.
- The root `build.gradle.kts` owns Rust, protocol, bridge, installer, verify and release tasks and reaches into
  `:app` (`rustJni` takes the NDK from AGP of `:app` and writes into the app's build directory;
  `protocolCheck`/`sessionLoopbackCheck` need `:app:protocolJvmTest` and `:app:sessionLoopbackClasspath`; root
  `clean` needs `:app:clean`). `app/build.gradle.kts` reads `rootProject` for `protocol/` fixtures, `rustHostJni`,
  `rustJni`, `installGitHooks`, and `detekt` config. The version catalog's `ndk` is used by both sides.
- The installer's checks read workflow files and `native-bridge/host/rust-toolchain.toml` (`rustup show
  active-toolchain`), not `protocol/` and not other host files. `native-host`, `bridge-scaffold`, `rust` and
  `web-extension` do read `protocol/v1/` fixtures.
- Fixture digests (`fixture_sha256`) are canonical hashes of `{id, input, expected}` only, so description text in
  fixtures can change without breaking digests.
- Toy spike (Gradle 9.5.0, repo wrapper, plain tasks): `cd android && ./gradlew check` works through a
  `exec "$(dirname "$0")/../gradlew" "$@"` shim; a root build including both `android` and `rust`, with `android`
  also including `rust`, runs `androidCheck` and `rustTest` correctly. AGP and Kotlin plugins were not exercised.

## Goals / Non-Goals

**Goals:**
- Per-node last-green skipping on PR runs of `ci.yml`; master pushes run every node.
- An independent `android/` Gradle build; one-way dependencies root -> android -> rust.
- Tighter node inputs (`components.json`, `.editorconfig`, installer, Android workflows, root build files) with
  release attribution preserved.
- Protocol spec inside the protocol node.

**Non-Goals:**
- New CI tooling (moon), a docs-format check, owning other nodes' workflows, changing which checks exist, release
  signing/tag/publish behaviour, rewriting frozen evidence or historical docs.

## Decisions

### D1. Last-green selection (PR 1)

New stdlib-only `scripts/last_green.py` (+ tests with recorded API fixtures); `components.py` gains per-node bases.

`changes` job, `pull_request` events (the existing step order is kept: selection writes its outputs first, then
`components.py check`, then the failure step, so selected nodes still run when validation fails):
1. Full-history checkout. The full PR diff against `merge-base(origin/<base>, HEAD)` (HEAD = merge commit, so this
   is the PR's net diff) is computed as today. **Ownership validation (`unaccounted`) always runs on this full
   changed-path set**, whatever the selection mode (last-green, `--all` on push, or API fallback).
2. `last_green.py --repo --pr <n> --head $PR_HEAD_SHA --pr-head-repo <owner/name> --pr-head-ref <ref>` prints
   `{node: base_sha | null}`:
   - lists the PR's commits (`GET /repos/{r}/pulls/{n}/commits`, paginated) and treats them newest first as candidate
     evidence heads, skipping any that is not an ancestor of `$PR_HEAD_SHA` (`git merge-base --is-ancestor`; covers
     force-pushes and unfetched commits);
   - for each candidate, lists runs (`GET /repos/{r}/actions/workflows/ci.yml/runs?event=pull_request&head_sha=<sha>`)
     and accepts a run only if it is a completed `ci.yml` `pull_request` run for that head **and belongs to this PR**:
     its `pull_requests` array contains `<n>`, or, when that array is empty (fork PRs), its `head_repository` and
     `head_branch` equal the PR's head repository and ref;
   - for an accepted run, reads the jobs of **one pinned attempt** (`GET
     /repos/{r}/actions/runs/{id}/attempts/{run_attempt}/jobs`, paginated, `run_attempt` taken from the run), and
     rejects the run if any job reports a different `run_attempt` (a rerun started during the lookup);
   - a node is green on that head iff, within that single run attempt, every job in the node's expected set (D2) has
     `conclusion == success` and no extra `<node> / ...` job exists. Jobs of different runs or attempts are never
     combined;
   - **map compatibility**: `components.py` loads the component map as of the evidence head (`git show
     <sha>:.github/components.json`) and compares a normalised selection spec with the current one: the node's own
     `paths`, `ci-paths`, `retired-paths` entries and `jobs`, plus the **whole** shared sections `global`,
     `ci-global`, `global-overrides`, `retired-paths` and `unowned` (not only entries that select this node, so an
     override that is removed, or whose node list changes, also invalidates evidence of the nodes it used to exclude).
     Any difference makes the evidence incompatible (not green). A shared-section edit is rare, so invalidating every
     node's evidence is an accepted cost; tests cover an expanded input set, a removed override and a changed
     override membership;
   - stops at about 20 candidates or about 100 API calls. `base_sha` is the newest green head, else `null`.
3. `components.py changed --node-bases bases.json --head $PR_HEAD_SHA`: for each node, `files_n = diff(base_n, head)`
   when `base_n` is set, else the full PR diff; the node is selected when any file in `files_n` is owned by it (D2).
   Master movement is ignored by construction (ancestor diff).
4. Any API error, rate limit, malformed response or missing permission: `last_green.py` exits non-zero, selection falls
   back to every node, and a `::warning::` is emitted. Validation of step 1 still runs.
5. A step summary lists each node, the base used and the reason (`never passed`, `incompatible map`, `changed since
   <sha>`, `unchanged since <sha>`, `fallback`).

For `push` (master): selection is `--all` (no API calls); ownership validation still runs on `before..after`.
`release-build.yml` keeps its own `changes` job and `components.py changed --base <before>` unchanged; a regression
test pins that.

Permissions: `ci.yml` stays `contents: read` at the top; the `changes` job declares all of `contents: read`,
`actions: read` and `pull-requests: read` (a job-level mapping replaces the inherited one). Fork PRs get read tokens,
which is enough. No secrets are involved.

Kill switch: repository variable `CI_LAST_GREEN=off` makes `last_green.py` return all `null` (merge-base behaviour).
`ci-ok` keeps its name; skipped nodes remain acceptable.

Alternatives: artifact or cache markers (more writers, eviction/expiry); a content hash of node inputs (a different
semantics than agreed); both were rejected in the brainstorm.

Accepted limitations: a "re-run failed jobs" attempt leaves the node ineligible until a full run succeeds; a merge of
`master` into the PR re-selects nodes it touched.

### D2. Component map schema and ownership model (PR 1, extended in PR 2/3)

`components.json` changes (validated by `components.py`):
- per node `jobs`: the expected job display names without the `<node> / ` prefix. The contract test in
  `verify/test/workflow-contracts.test.mjs` derives the real names by following each `ci.yml` caller's `uses:` to its
  reusable workflow: display name = caller job name + ` / ` + the called job's `name:` (or its job id when unnamed, as
  for `supply-chain / build`). Matrix (`strategy.matrix`) or otherwise unsupported shapes are rejected by the test;
  conditionally skipped jobs make a node ineligible, as agreed. A difference between derived and declared names is a
  test failure.
- per node `release-paths`: release-only inputs (mirror of `ci-paths`, never select a node in CI).
- top-level `global-overrides`: exact path -> list of nodes. A path in this map selects exactly those nodes in CI instead
  of every node. It does not change release attribution (the path still counts through `global`). Initial entry:
  `.github/components.json -> [tooling]`.
- top-level `retired-paths` (PR 2): path prefix -> nodes, for paths that no longer exist after a move. They are
  *accounted* for changed-path validation (deleted files such as `app/**` and `config/detekt/**` appear in the PR that
  moves them, because rename detection is off) and select the listed nodes in CI. They are never release inputs and are
  removed once no in-flight PR can touch them.
- `release_paths(node)` = `paths` + `release-paths` + `global`, unchanged. **Legacy release attribution is not part of
  `release_paths`**; it is a separate, bounded structure (D6).

Rationale: an explicit override map is easier to review and test than "longest prefix wins", and it does not turn
`.github/workflows/` into a tooling-only directory.

PR 1 map cleanups on the current layout:
- `.editorconfig` leaves `ci-global`; it becomes `ci-paths` of `android`, `web-extension` and `bridge-scaffold`
  (ktlint and Prettier read it). PR 2 gives Android its own file.
- `installer`: `protocol/` and `native-bridge/host/` (except `rust-toolchain.toml`) move from `paths` to
  `release-paths`; `native-bridge/host/rust-toolchain.toml` becomes an `installer` `ci-path`. Acceptance: with
  `protocol/` and the other host files removed from a scratch worktree, `bridgeInstallerFormat bridgeInstallerLint
  bridgeInstallerTest bridgeInstallerScriptsTest` still pass.
- `.github/components.json` override -> `tooling`.

### D3. Protocol spec move (PR 1b)

`git mv docs/protocol protocol/docs` (14 tracked files). Updates, driven by a reproducible inventory
(`git grep -n "docs/protocol"` excluding `native-bridge/feasibility/evidence/`, `openspec/changes/archive/`,
`docs/superpowers/` and other dated records, which stay as written): Kotlin/Rust/TypeScript/`.mjs` comments,
`build.gradle.kts` input, `protocol/checks/evidence.mjs` (path resolution, including its fallback branch),
`protocol/v1/manifest.json`/`binary-layouts.json` document references, fixture description strings,
`protocol/README.md`, the host README, live docs. No stub is left at `docs/protocol/` (a stub would sit in
unowned `docs/` and hide stale links); links in `docs/` that point to it are updated. Fixture description strings: the per-fixture digest
covers only `{id, input, expected}`, but `crypto.mjs` also defines a whole-array digest and `crypto.test.mjs`
requires the generated candidate to equal the committed fixtures, so strings in generator-owned files
(`protocol/v1/fixtures/crypto.json`) are updated in the generator source and regenerated, and fresh reports are
produced without rewriting frozen evidence; `protocolCheck` and `protocolFreshEvidenceCheck` prove it. Because the edited files include app, rust, web-extension and host sources,
the PR selects every node once, which is accepted.

### D4. Gradle layout (PR 2)

```
settings.gradle.kts          includeBuild("android"), includeBuild("rust")  (no include(":app"))
build.gradle.kts             protocol*, bridge*, installer*, verify, release tasks + root aliases
gradlew, gradlew.bat, gradle/wrapper/   the only wrapper (global)
android/settings.gradle.kts  rootProject.name, include(":app"), includeBuild("../rust"), repositories
android/build.gradle.kts     plugin aliases (apply false), Spotless, helper for ../protocol paths
android/gradle/libs.versions.toml, android/gradle.properties, android/config/detekt/, android/.editorconfig
android/app/                 the former app/ module
android/gradlew, android/gradlew.bat   shims to ../gradlew(.bat)
rust/settings.gradle.kts, rust/build.gradle.kts   the rust* tasks moved out of the root build
```

- Shims: `android/gradlew` is `#!/bin/sh` + `exec "$(dirname "$0")/../gradlew" "$@"` (LF endings, executable bit,
  `.gitattributes` entry if absent); `android/gradlew.bat` is `@call "%~dp0..\gradlew.bat" %*`. A contract test
  checks their content and that no second `gradle-wrapper.jar` exists.
- Root aliases keep documented commands working from the root, in four kinds: **app aliases** (`assembleDebug`,
  `assembleRelease`, `testDebugUnitTest`, `lintDebug`, `detekt`, `connectedDebugAndroidTest`) depend on
  `gradle.includedBuild("android").task(":app:<task>")`; **Android-root aliases** (`spotlessCheck`, `spotlessApply`)
  depend on `...task(":<task>")` because Spotless is applied at the Android build root; **retained root aggregates**
  (`protocolCheck`, `bridge*`, `rustTest`, ...) stay root tasks and depend on included-build tasks of `android` and
  `rust`; **Rust exports** are the tasks listed in D5. Root `clean` depends on the Android and Rust cleans. CI jobs of the Android node call the Android build directly (`working-directory:
  android`); jobs of other nodes call root tasks. `CLAUDE.md`, `BUILD.md`, `CONTRIBUTING.md` and `docs/ci.md`
  document both forms.
- Root Spotless (Kotlin) moves to Android; root no longer needs the version catalog. The root plugin aliases for
  Android plugins disappear.
- Version plugin: `androidGitVersion { prefix = "android-v" }` stays in `android/app/build.gradle.kts`. Acceptance:
  `cd android && ./gradlew -q :app:androidGitVersion` and the built APK's `versionName/versionCode` match the old
  layout on the same commit; the signing certificate of the debug and release APKs is unchanged.
- `installGitHooks` keeps its behaviour (existing `commit-conventions` requirement: set `core.hooksPath` only when
  unset, never under `CI=true`, run on every Android build). Its task lives in one shared script
  `.githooks/install-hooks.gradle.kts` applied by both the root build and `android/build.gradle.kts`; `:app:preBuild`
  depends on the Android copy, so `cd android && ./gradlew assembleDebug` and the root alias both install hooks. The
  script is one of the permitted accesses outside `android/` listed in the `android-standalone-build` spec (alongside
  `rust/`, `protocol/`, the wrapper, `.java-version`, repository-root `build/` outputs, Git metadata and signing
  inputs) and is an Android `ci-path`.

### D5. Rust build interface (PR 2)

`rust/` is a Gradle build (tasks moved verbatim: `rustToolchainCheck`, `rustInstall`, `rustWasm`, `rustHostJni`,
`rustJni`, `rustLint*`, `rustFormat`, `rustTest`, `rustPackage`, `rustNdkVersion`, ...), included by the root and by
`android/`. The root build refers to many of them by provider or string name (for example `dependsOn("rustHostJni",
"rustWasm")`, `rustWasm` in the protocol and extension tasks, `rustInstall`, the verify-tool installers); PR 2 starts
with a reproducible inventory of every such reference and rewires each through `gradle.includedBuild("rust").task(...)`,
including the verification-tool installers that the Rust tasks depend on. Interface:
- **Output locations are preserved**: the build writes into the repository-root `build/` (`build/rust/target`,
  `build/rust/host-jni`, `build/rust-tools`, ...) so CI cache paths, scripts (`assert-clean.sh`,
  `verify-apk-jni.sh`) and docs keep working. JNI libraries go to `build/rust/jniLibs/<abi>` (Rust-owned) instead
  of the app's build directory.
- **Android staging**: `android.sourceSets["main"].jniLibs.srcDir(<that dir>)` and `merge*JniLibFolders` depends
  on the included build's `:rustJni`; unit tests depend on `:rustHostJni` and use `../build/rust/host-jni` as
  `java.library.path`.
- **NDK**: single source of truth becomes `rust/ndk-version` (one line). `rustJni`/`rustNdkVersion` resolve the NDK
  from `ANDROID_NDK_HOME` or `$ANDROID_SDK_ROOT/ndk/<version>` (or `local.properties` `sdk.dir`) and fail with a
  clear message otherwise; Android sets `ndkVersion` from `../rust/ndk-version`. Direction stays android -> rust.
- **Fixtures**: Android test code resolves `protocol/v1` and `protocol/conformance` through
  `layout.settingsDirectory.dir("../protocol/...")`.
- **Fallback** if a real AGP/Kotlin build shows classloader or configuration problems with the nested include: the
  Android build runs the Rust tasks through `Exec` on `rust/scripts/*` with the same output directories and
  versions; every other part of this design is unchanged.

### D6. Release attribution through the move (PR 2 and PR 3)

Per node, optional `legacy-release`: `{ "paths": [...], "epoch-path": "android/" }`. For `android`, `paths` is the old
*effective release* set that stops applying: `app/`, `build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`,
`gradle/libs.versions.toml` (not `config/detekt/`, which is CI-only today and must not gain release attribution).

Bounding is by **layout epoch per commit**, not by a stored SHA: a commit is in the old-layout epoch iff its tree has no
`android/` (`git cat-file -e <sha>:android/` fails). In `release_plan.py`, for a node with `legacy-release`:
1. main pass: unchanged (`release_paths(node)`, `<tag>..HEAD`);
2. legacy pass: git-cliff with the legacy include paths over `<tag>..HEAD`, passing `--skip-commit` for every commit in
   the range whose tree contains `android/`, so only old-epoch commits are considered;
3. commits are deduplicated by id across both passes (root, protocol and Rust changes can match both); reasons and notes
   are merged; the next version is `_expected_bump(current, union of reasons)`. The existing cross-check against
   git-cliff's own `--bumped-version` is kept when the legacy pass contributes no commit and is replaced by the tests
   below when it does.

Consequences: a `feat` touching `app/` before the move still bumps Android; a root-only `feat` after the move has
`android/` in its tree and is skipped by the legacy pass; the squash migration commit itself touches `android/` and
counts through the main pass; if PR 2 is reverted, commits made while `android/` is absent are old-epoch again and are
attributed through legacy, and they stay so after a reapply; after the next Android tag the legacy pass finds no
old-epoch commit in range and is inert (the entry is removed in a later cleanup). `web-extension`, `installer` and the
other nodes keep the root build files in their own `paths`, so their attribution does not change. Tests extend
`scripts/tests/test_release_plan.py` with fixture repos: squash migration, unreleased `feat(app)` before the move,
root-only `feat` after, revert with intervening `app/` changes then reapply, a commit matching both passes, and
inertness after a new tag, each asserting the planned versions before and after the migration.

### D7. Node inputs after the move (PR 2 and PR 3)

- `android`: `paths` = `android/`, `rust/`, `protocol/`; `jobs` as in Context; `ci-paths` = `.githooks/install-hooks.gradle.kts` (the wrapper
  and `.java-version` remain `global`).
- Root build files (`build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`) leave `global` and become
  `paths` of every non-Android node (web-extension, installer, rust, protocol, native-host, bridge-scaffold,
  supply-chain, tooling), which keeps their release behaviour; they no longer select or release Android.
- `protocol` node: `app/` -> `android/` (unchanged selection semantics).
- `retired-paths` (D2) for the move: `app/` -> `[android]` and `config/detekt/` -> `[android]`, so deleted files in the
  migration PR are accounted and select Android; `components.py check` still validates every tracked file.
- PR 3 `global-overrides`: `.github/workflows/check-android.yml` and `.github/workflows/build-android.yml` ->
  `[android, tooling, installer]` (tooling's workflow contract suite and the installer's scripts-tests read them).
  Other workflows stay global.
- Root-orchestration coverage without selecting Android: root build edits select `rust` (its `jni-integration`
  job runs the root aliases `assembleDebug`, `clean` and the rust tasks), `protocol` (`protocolCheck`) and
  `tooling`, whose `unit-tests` gets one extra step `./gradlew --dry-run assembleDebug assembleRelease
  testDebugUnitTest lintDebug spotlessCheck detekt connectedDebugAndroidTest protocolCheck` to resolve every alias.

### D8. Cross-component checks and guards (PR 2)

- `sessionLoopbackCheck` and the Kotlin JVM fixture consumer stay owned by the `protocol` node (its `paths` keep
  `android/`, `web-extension/`, `rust/`, `protocol/`, host and installer paths) and run through `./gradlew
  protocolCheck`, whose Android parts are included-build tasks. The classpath file moves to
  `android/app/build/session-loopback/classpath.txt`.
- `rust / jni-integration` stays in the `rust` node and uses the root aliases; no new edge (today `app/` also does
  not select `rust`). `assert-clean.sh` and `verify-apk-jni.sh` paths are updated to `android/app/build/...`.
- The pairing-code guard: its Kotlin half stays in `android / lint`; its `web-extension/src/debug` half moves to
  `web-extension / lint`, closing an existing gap. `scripts/check-mac-comparisons.mjs`'s `--root` is only the base directory that the
  built-in default roots are joined to (and missing roots are skipped silently), so it cannot select a scan
  directory. The script gains a repeatable `--scan DIR` option (must exist; an empty scan set is an error, exit 2)
  and its default roots become `android/app/src/main` and `web-extension/src`. Android lint runs `--scan
  android/app/src/main`, web-extension lint `--scan web-extension/src`; tests inject a violation into each root.
  Repository guards (this script, the `android.util.Log` and pairing-code greps) run **from the repository root**
  (no `working-directory`), while Android Gradle commands run with `working-directory: android`; the contract tests
  pin both.
- Path-sensitive consumers updated in PR 2 from a reproducible audit (`git grep` for `app/` references excluding
  frozen evidence and archived changes): `.dockerignore`, `.idea`, `verify/` scripts, `publish-release.yml` and
  `build-android.yml` output paths (`android/app/build/outputs/...`), `release-build.yml`, `docs/*`, `CLAUDE.md`,
  `BUILD.md`, `CONTRIBUTING.md`, `docs/ci.md` job table (including the missing `release-hook-guard` row).
- Signing: no key material is committed. By owner-approved exception, the local, git-ignored fixture assets
  live at `android/config/android_keys/` after the move, and the tracked documentation uses
  `cd android && ./gradlew --init-script config/android_keys/fixture-signing.gradle ...`. The local init
  script is not in the repository, so its compatibility with the new layout is verified by the owner on
  the machine that holds it before PR 2 merges; certificate fingerprints are compared before any install,
  per `CLAUDE.md`. Release identity stays CI-only (generated only by master CI).

## Risks / Trade-offs

- [Last-green skips a node that should have run (map or job inventory drift)] -> `jobs` is contract-tested against the
  workflows; exact job-set match required; conservative fallback on any mismatch or API error; master always runs
  everything; kill switch variable.
- [A stale pass after a rebase or merge of master into the PR] -> ancestor rule; non-ancestors fall back to the
  merge-base diff; merged-master changes re-select touched nodes.
- [Actions API/permission/rate-limit failures slow or disable the optimisation] -> bounded calls, all-nodes
  fallback, warning in the log; `ci-ok` semantics unchanged.
- [Nested `includeBuild` or AGP/Kotlin plugin behaviour differs from the toy spike] -> early PR 2 spike on the
  real build; the documented `Exec` fallback keeps the same interface.
- [Release attribution lost or inflated by the move, or by a revert/reapply] -> epoch-based `legacy-release` (per-commit
  layout epoch, deduplicated passes, self-expiring) with fixture-based planner tests before merging PR 2.
- [Evidence from an earlier map definition suppresses a node] -> the node's normalised CI selection spec at the evidence
  head must equal the current one; otherwise the node runs.
- [Two ways to run Android (root alias vs `android/`) drift] -> aliases are thin; the `tooling` dry-run step resolves
  them on every root-build edit; CI of the Android node never depends on the aliases.
- [The shared hook script couples Android to `.githooks/`] -> one small script, listed as an Android `ci-path`, covered
  by the existing `commit-conventions` scenarios run from both entry points.
- [PR 1b touches many nodes' files] -> comment-only edits except for build/check path constants, verified by
  `protocolCheck`; it merges before PR 2 so the Android move stays reviewable.
- [Frozen/historical docs keep the old path] -> intentional; recorded in the PR body.
- [Windows checkout of the shim] -> `.bat` shim plus a Windows checkout verification in PR 2; symlinks avoided.

## Migration Plan

**PR 1 (last-green, current layout).** Implement `scripts/last_green.py`, `components.py` per-node bases and
`--all`, `jobs`/`release-paths`/`global-overrides` schema and checks, the `.editorconfig`, installer and
`components.json` cleanups, `ci.yml` `changes` changes and permissions, step summary, kill switch, workflow-contract
tests (job inventory, release-build untouched), `docs/ci.md`. Verify: unit tests with recorded API fixtures covering
first run, normal push, docs-only push, rebase/force-push, failed-then-fixed node, rerun-failed-only, job-set drift,
API error, never-passed node, a map edit that expands a node's inputs, fork-PR run association, attempt mismatch,
deleted/moved paths in the ownership validation; a scratch PR demonstrating Android-only change then docs-only push skips Android.
Rollback: set `CI_LAST_GREEN=off` or revert.

**PR 1b (protocol spec).** `git mv`, reference updates from the inventory, no stub; run `protocolCheck`,
`protocolFreshEvidenceCheck`, host/rust/extension test suites. Rollback: revert.

**PR 2 (`android/`).** Order inside the PR: (1) real-build spike for nested `includeBuild` and the shim on Linux
and Windows, (2) extract `rust/` build with the preserved output locations, (3) move `app/` and split root
settings/catalog/properties/detekt/Spotless/`.editorconfig`, (4) root aliases and included builds, (5) update
workflows, scripts, docs and path audit, (6) `components.json` (`retired-paths`, node paths, `legacy-release`) and planner tests. Verify: all
nodes green; APK versionName/versionCode and signing certificate identical to a build from the previous layout of
the same commit; `assert-clean.sh`; `release-build`/publish dry runs against a fork or the planner CLI. Rollback:
revert the squash commit; the legacy entry is inert.

**PR 3 (Android workflows).** Add the two `global-overrides`, keep tooling and installer selected, update
`docs/ci.md`. Rollback: revert.

## Open Questions

- Real-build spike outcomes (PR 2 step 1): nested `includeBuild` with AGP 9.3/KSP/Hilt, the `androidGitVersion`
  plugin from `android/`, shim on a Windows checkout. A negative result switches D5 to its `Exec` fallback.
- Whether `ANDROID_NDK_HOME`-first NDK resolution is acceptable for developers who rely on AGP auto-installing the
  NDK (they would install it once via sdkmanager).
- Exact content of the local signing init script (owner to confirm before PR 2 merges).
- Whether the pinned git-cliff (2.14.2) handles a long `--skip-commit` list and `--context` output as D6 needs; if not,
  the legacy pass filters commits through the planner instead of git-cliff.
- Whether to remove the `legacy-release` and `retired-paths` entries in a follow-up commit or leave them inert.
- Whether other nodes' workflows should later use `global-overrides` too (out of scope).
