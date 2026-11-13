## Design Summary

**Goal.** Make the repository structure more separated and CI faster, in four deliverables
(each its own PR under this change; 1b is a small prerequisite):

1. **PR 1: last-green tracking for `ci.yml`.** On pull requests, only the first run diffs against the merge
   base with `master`. Every later run decides, per node, against the node's own last green PR head, so a
   commit that does not touch a node's inputs does not re-run that node. Master pushes always run every node
   in `ci.yml`. Map cleanups that need no layout change ride along (see Key Decisions).
   - **PR 1b: move the protocol spec under `protocol/`.** `docs/protocol/` (`veles-v1.md`, `otp-03-api.md`,
     `reviews/`) moves inside the protocol node so spec edits select the protocol checks that read it, and
     live references are updated. Done before PR 2 so the Android move does not carry unrelated comment edits.
2. **PR 2: `android/` move.** All Android code and builds move to `android/` as an independent Gradle build.
3. **PR 3: node-owned Android workflows.** `check-android.yml` and `build-android.yml` stop being `global`.

**What exists today (verified in the repo).**
- CI is already a component tree: `.github/components.json` + `scripts/components.py` select nodes, `ci.yml`
  calls one reusable `check-<node>.yml` per node, and `ci-ok` is the only required aggregate.
- `docs/`, `openspec/`, `.claude/`, `.opencode/`, `site/` and an **explicit list** of root files (`README.md`,
  `BUILD.md`, `CONTRIBUTING.md`, `CONTRIBUTION.md`, `LICENSE`, `CLAUDE.md`, ...) are `unowned`, so docs-only
  changes already select no node. `components.py check` fails for any tracked file that is not owned, global
  or listed as unowned, so a new top-level markdown file needs an entry. Markdown inside a node folder already
  selects that node.
- The root `build.gradle.kts` (about 113 KB, 84 tasks) owns Rust, WASM, protocol, native-bridge, installers,
  supply-chain and release tooling and also reaches into `:app`: `rustJni` takes the NDK from
  `project(":app")` and writes to the app's build directory; `sessionLoopbackCheck` and `protocolCheck` depend on
  `:app:sessionLoopbackClasspath` and `:app:protocolJvmTest`; root `clean` depends on `:app:clean`. In the other
  direction `app/build.gradle.kts` uses `rootProject` for the `protocol/` fixtures, `rustHostJni`, `rustJni` and
  `installGitHooks`.
- `global` is `.github/`, `build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`, `gradle/`, the wrapper
  and `.java-version`. Global entries select every node **and** count as release paths for every release node
  (`release_paths` = node paths + global; only `ci-paths` and `ci-global` are CI-only).
- A PR's base is always `merge-base(master, HEAD)`, so every push re-runs every node the PR has touched since
  it branched.
- `release-build.yml` also calls `components.py changed` on master pushes, deliberately building only the
  release components that changed.

**Target Gradle layout.** Dependencies point one way: `root -> android -> rust`.

- `android/` is an independent build: own `settings.gradle.kts`, `build.gradle.kts`, `gradle.properties`,
  `gradle/libs.versions.toml`, `config/detekt`, Spotless Kotlin config, version plugin (`android-v*`) and its
  own `.editorconfig`. `android/gradlew` and `android/gradlew.bat` are shims to the single root wrapper, so
  `cd android && ./gradlew assembleDebug` builds Android alone.
- `rust/` becomes its own Gradle build (`rustJni`, `rustHostJni`, `rustWasm`, `rustTest`, ...), included by both
  the root build and `android/`. It needs an explicit interface (see Open Questions).
- The root build orchestrates everything (`includeBuild(android)`, `includeBuild(rust)`) with aggregate tasks for
  local runs and cross-component contracts. It keeps protocol, native-bridge, installers, verify and release tooling.
- Each CI node runs its own component's build (Android jobs run from `android/`), so a root-build edit does not
  select Android jobs.

**Node selection after the change (changes from today only).**

| Changed path | Change vs today |
|---|---|
| `android/` | selects `android` and `protocol` (unchanged: the protocol node's JVM consumer and session loopback need the Android classpath) |
| root `build.gradle.kts`, `settings.gradle.kts` | stop being `global`; owned by the non-Android nodes, so they no longer select `android` or cut an Android release |
| `gradlew`, `gradlew.bat`, `gradle/wrapper/`, `.java-version` | unchanged: `global` (select every node, count for every release) |
| `gradle.properties` | split: root keeps its part, Android properties move to `android/gradle.properties` |
| `rust/` | unchanged: selects `android`, `web-extension`, `rust`, `protocol` (+ `supply-chain` for manifests/locks) |
| `protocol/` | no longer selects `installer` in CI (the installer's checks never read it); stays a release path for `installer` |
| `native-bridge/host/` | `installer` only for `rust-toolchain.toml` in CI; the rest becomes a release-only path (to verify) |
| `.github/components.json` | selects `tooling` only (was every node) |
| root `.editorconfig` | selects `web-extension` and `bridge-scaffold` only; Kotlin rules move to `android/.editorconfig` |
| `ci.yml` and workflows no node owns | unchanged: every node |
| `check-android.yml`, `build-android.yml` (PR 3) | select `android` **plus** the nodes whose contract tests read them (`tooling`; `installer`'s scripts-tests for `build-android.yml`) |
| explicitly unowned root files and unowned directories (`README.md` and the other listed root markdown, `docs/`, `openspec/`, `.opencode/`, `site/`) | selects nothing, as today (a new top-level file needs an `unowned` entry or `components.py check` fails); the protocol spec moves under `protocol/` (PR 1b) and then selects `protocol` like the rest of `protocol/` |

**Selection-model changes needed in `components.py` / `components.json`** (to specify in the design):
- Replace the blanket `.github/` global with explicit ownership; narrower node ownership must win over broad
  global matching (today `global` is checked first and wins).
- Add a **release-only** path list (mirror of `ci-paths`) and keep `release_paths`, the release planner and its
  tests consistent; global/CI/release semantics for each moved path are listed per node.
- **Release attribution must survive the move.** The release planner applies the *current* release paths to the
  whole range from the last tag to `HEAD`, so removing `app/` or former global paths would drop unreleased
  `feat`/`fix` commits from a component's bump. PR 2 and PR 3 therefore keep **transitional legacy release
  paths** (for example `app/` for Android) until the next release tag of each affected component, with
  migration tests that contain unreleased commits made before the ownership change and assert the same
  planned versions before and after, while excluding future root-only edits. The legacy attribution must be
  **bounded to pre-migration history** (a migration cutoff commit or equivalent history-aware attribution),
  because a static legacy include path would also bump on future root-only `feat`/`fix` commits; the exact
  mechanism is left to the design.
- Last-green selection and "master always full" apply to **`ci.yml` only**. `release-build.yml` keeps its
  changed-only master semantics through the existing command or a separate mode, with a PR 1 acceptance test.
- `components.py check` keeps validating every tracked file in every PR, including after the move.

**How last-green is decided (PR runs after the first).** Three different SHAs are involved and must not be mixed:
- *evidence SHA*: a previous PR head SHA whose `ci.yml` run is inspected;
- *tested merge SHA*: `github.sha`, the PR merge commit the workflow actually checked out;
- *selection head SHA*: `github.event.pull_request.head.sha`, used for the diff.

Rules:
1. The `changes` job lists the PR's head SHAs newest first (from the PR's commits and the `ci.yml` runs for
   event `pull_request`), with pagination, and inspects, for each, the `ci.yml` run and **its jobs of the latest
   attempt** through the Actions API (read permissions only; works for fork PRs).
2. A node is green on a head SHA only when, **within one `ci.yml` run attempt for that PR**, every expected job
   named `<node> / <job>` completed `success`. Skipped, neutral, cancelled, failed, missing and in-progress jobs
   never count, and results from different runs are never combined. Matrix names (for example
   `native-host / unit-tests (windows)`) are part of the expected set.
3. The diff is `git diff <evidence head> <selection head>`. It excludes master-only movement only when the
   evidence head is an **ancestor** of the selection head (a normal push). After a rebase or force-push, or when
   the evidence head is not reachable, the node falls back to the merge-base diff (conservative re-run). A
   merge of `master` into the PR branch re-selects nodes touched by that merge (conservative).
4. **Per node:** if no green head is found for a node within a cap (about 20 heads), that node is treated as
   never passed and falls back to the merge-base diff. It still runs only if its inputs changed against the
   base, so an Android-only first run does not force unrelated nodes to run on the next push.
   **Run-wide:** any API error, rate limit or unexpected response selects every node (conservative fallback).
5. A node is green only when **all** its expected jobs succeeded in the latest attempt of one run. A
   "re-run failed jobs" attempt that does not re-execute the previously green sibling jobs therefore leaves the
   node ineligible for skipping (a safe, accepted limitation: it just runs again on the next relevant push);
   "re-run all jobs" gives complete evidence.
6. `ci-ok` accepts a skipped node and fails on any failed or cancelled job, as today; its name and required
   status are unchanged.
7. The required job list per node lives in `components.json` and is checked against the real workflows by the
   contract tests.
8. **Historical compatibility default:** if the evidence run's job set does not exactly match the node's current
   expected set (jobs added, removed or renamed since), or compatibility with the current map cannot be
   established, the node is not green and runs. The precise mechanism is left to the design.

## Alternatives Considered

### Alternative A: Adopt moonrepo/moon for affected detection and caching
- **Approach**: Replace `components.json` / `components.py` selection with moon projects (`dependsOn`, task
  `inputs` with negated globs) and `moon ci`; use its content-hash task cache.
- **Pros**: dependency graph and affected detection for free; negated input globs; content-hash skipping covers
  master movement without extra rules.
- **Cons**: no per-project base and no "last green" notion (single `--base` per run); native cross-run caching
  needs a Bazel-REAPI remote cache (self-hosted `bazel-remote` or a paid cloud cache); there is no GitHub
  Actions cache integration, and manually persisting `.moon/cache` is possible but documented as needing
  careful invalidation; fork PRs get no secrets for a remote cache; cache hits skip by default, which
  conflicts with "master always re-runs"; Gradle is not first-class (only wrapped system commands); the
  per-job check names the last-green lookup relies on would need a multi-job topology that moon does not
  provide by itself; the component map is also read by `release_plan.py`, which would need a second source of
  truth or a rewrite; another pinned binary and tool manager under the supply-chain policy. Some facts
  (moonbase status, relation defaults in `moon ci`, stdout replay, Gradle subfolder examples) could not be
  confirmed from the docs.
- **Why not chosen**: it does not provide the agreed last-green semantics out of the box, needs new
  infrastructure or care for caching, and would replace tooling that already works. Revisit if the node
  count grows a lot or content-hash caching with a remote cache becomes desirable.

### Alternative B: Move only the `app` module to `android/`, keep one shared root Gradle build
- **Approach**: `git mv app android/app`; the root build and wrapper stay, `settings.gradle.kts` includes
  `android/app`.
- **Pros**: smallest change; root orchestration unchanged; no wrapper or composite-build work.
- **Cons**: the root `build.gradle.kts` (Rust, bridge, release tasks) stays `global`, so any root-build edit
  still selects every node and "only `android/` changed means Android runs" stays false; the structure is only
  cosmetically separated.
- **Why not chosen**: it does not deliver the isolation that motivates the move.

### Alternative C: Fingerprint-based skip (hash of a node's input trees) with a pass marker
- **Approach**: hash the git trees of everything a node depends on and skip when a green run with the same
  hash exists.
- **Pros**: independent of commit order and of merge commits; naturally covers master movement.
- **Cons**: needs a marker store (`actions/cache`, commit statuses or an artifact) with eviction or retention
  limits, or the same check-run lookup anyway.
- **Why not chosen**: the agreed semantics are explicitly "diff against the node's last green PR commit; first
  run against the base branch; master always full". A hash is a different mechanism and does not change what
  runs on master; it was not chosen because it differs from the requested model, not because hashes cannot
  support full master runs.

### Where last-green is recorded (decision within the chosen approach)
1. **GitHub Actions run/job results (chosen).** No extra writer; "green" is exactly what GitHub recorded for a
   specific `ci.yml` run; visible in the PR UI; a missing result just means a re-run. Needs the `<node> / <job>`
   naming pinned by contract tests and a few API calls.
2. Workflow artifact with `{node: last-green commit}`: explicit, easy carry-forward, but only exists if the run
   got far enough to write it, needs cancelled-run handling and expires.
3. `actions/cache` marker keyed per PR, node and commit: saved inside the node job on success, but caches can be
   evicted and are only readable inside the same PR.
Git notes and PR comments were rejected: they write to the repo or add PR noise.

## Agreed Approach

**Alternative: in-repo plan, delivered in four PRs (1, 1b, 2, 3)** (not A, B or C above).

- Keep `components.json` / `components.py` as the single source of truth and extend it with last-green
  selection for `ci.yml` read from GitHub Actions results (PR 1).
- Make Android genuinely independent: `android/` as its own Gradle build with a shim wrapper, `rust/` extracted
  into its own Gradle build, root build as orchestrator with one-way dependencies (PR 2).
- Move the protocol spec under `protocol/` so it belongs to a node (PR 1b).
- Move Android's workflows out of `global`, keeping the contract-test consumers selected (PR 3).

Why it wins: it implements the agreed semantics (first PR run against the base, later runs against the node's
last green PR head, master always full) on tooling the repo already owns and tests, adds no infrastructure or
new supply-chain dependency, and the PR 1 benefit does not depend on the larger PR 2 move.

## Key Decisions

Decided by the user:
- Android becomes a **standalone Gradle build** in `android/`.
- The dependency is **one-way** (`android` consumes `rust/` and `protocol/`); the root build **orchestrates
  everything, including Android**, while `android/` stays independently buildable.
- Android keeps a **shim wrapper** (Option A) to the single root wrapper, rather than symlinks or a second copy.
- **Master always runs every check.** On PRs, the first run diffs against the merge base with `master`; later
  runs diff **per node against its last completed green run**. A node is the unit: any change to its inputs
  re-runs all of its jobs.
- **Master is assumed green:** master movement alone never re-runs a node whose inputs the PR did not touch.
- An **unresolvable last-green commit counts as never passed** (re-run).
- **Last-green is recorded in GitHub's own run results**, nothing stored in the repo.
- **Markdown**: markdown inside a node folder selects that node (as today); markdown outside node folders
  (root, `.opencode/`, `docs/`, ...) selects nothing.
- **Move the protocol spec under `protocol/`** (not an exception to the markdown rule) and update related
  references. Live references (code comments, `build.gradle.kts`, protocol checks, fixtures, READMEs) are
  updated; historical records (archived OpenSpec changes, dated plans/specs, frozen evidence) are left as
  written.
- Deliver in **separate PRs**: last-green first, then the protocol-spec move (1b), then the `android/` move, then
  workflow ownership.
- **Park moon**; continue with the in-repo plan.

Proposed by the assistant in answer to the user's questions; no objection but **not explicitly confirmed**:
- `components.json` selects `tooling` only; `ci.yml` and unowned workflows still select every node.
- Root `.editorconfig` selects the Prettier consumers (`web-extension`, `bridge-scaffold`); Android gets its own.
- Split release inputs from CI inputs: `installer` keeps `protocol/` and most of `native-bridge/host/` as
  release-only paths (a new field mirroring `ci-paths`).

Assistant calls (not yet confirmed):
- Extract `rust/` into its own Gradle build included from both root and `android/`.
- The `protocol` node keeps `android/` in its paths (unchanged): its JVM fixture consumer and session
  loopback need the Android classpath, so they stay cross-component contracts owned by the protocol node and
  run through root orchestration.
- Android CI jobs run from `android/` (not through root aliases); a cheap root-configuration smoke check in
  `tooling` covers the root aliases.
- The wrapper and `.java-version` stay `global` (unchanged).
- Walk-back cap of about 20 commits; the required job list per node lives in `components.json`.
- Last-green and full-master apply to `ci.yml` only; `release-build.yml` keeps its semantics.

Constraints to carry into the design:
- Frozen evidence logs under `native-bridge/feasibility/evidence/` are not rewritten.
- `config/android_keys/` (git-ignored fixture keystore and init script) must not be moved or deleted; only
  references change, and the init script must be verified to work from the new layout. The signing certificate
  and the computed version of the built APK must match the old layout.
- Release tooling (`release_plan.py`, `cliff.toml`, `publish-release.yml`, `release-build.yml`,
  `check-android.yml`, workflow contract tests, docs, `CLAUDE.md`) must follow the new paths.
- Path-sensitive consumers to audit and update: `scripts/check-mac-comparisons.mjs` (default root
  `app/src/main`), `.dockerignore` (`app/build`), `.idea`, the Android lint guards in `check-android.yml`
  (the pairing-code guard also greps `web-extension/src/debug`, so it is not Android-only today), the
  `rust / jni-integration` job that builds the APK, build output and cache paths, root `clean` aliases.

## Open Questions

- **Protocol spec move details (PR 1b).** Target directory (assistant default: `protocol/docs/` holding
  `veles-v1.md`, `otp-03-api.md` and `reviews/`, 14 tracked files). Live references outside frozen evidence and
  historical records (build the reproducible inventory in the design): Kotlin, Rust, TypeScript and `.mjs` comments, `build.gradle.kts` (input of the protocol
  check), `protocol/checks/evidence.mjs` (resolves `../../docs/protocol`), `protocol/v1/manifest.json` and
  `binary-layouts.json` (`document` fields), `protocol/README.md`, host README. Risk: text edits inside
  `protocol/v1/fixtures/*.json` descriptions change files whose digests are recorded in the fixtures'
  provenance/manifest; decide whether to edit them (and refresh digests under the provenance rules) or leave
  those strings and add a stable pointer. Also decide whether `docs/protocol/` keeps a stub pointer.
- **Rust build interface (PR 2).** Today `rustJni` takes the NDK directory from `project(":app")` and writes
  to the app's build directory; the app wires `rustHostJni`, `rustJni` and `installGitHooks` from the root and
  reads `protocol/` fixtures through `rootProject`. The design must define: NDK/toolchain inputs without
  `:app`, Rust-owned output directories, how Android stages JNI libs and depends on Rust tasks, fixture paths
  from `android/` (`../protocol/...`), and where `installGitHooks` runs. The script fallback (if nested
  `includeBuild` fails) must preserve the same interface.
- **Cross-component checks.** Assign owners and inputs to `sessionLoopbackCheck`, `protocolCheck` (Kotlin
  consumer plus TypeScript), the `rust / jni-integration` clean/rebuild contract and the Android lint guard
  that reads `web-extension/src/debug`; decide how a root-orchestration edit executes the affected
  contracts without restoring blanket Android selection.
- **Contract tests that read workflows.** `native-bridge/installer/scripts/test_build_contract.py` reads every
  workflow (including `build-android.yml`) and `verify/test/workflow-contracts.test.mjs` inventories Android
  commands; after PR 3 these consumers must stay selected (`tooling`, `installer`) or the contracts move.
- **Gradle spike (PR 2):** can one `rust/` build be included from both the root build and `android/` (nested
  composite)? Does `cd android && ./gradlew` work through the shim on Linux and on a Windows checkout? Does the
  `android-v*` version still compute from the `android/` subfolder, and does the signed APK keep its
  certificate?
- **Installer paths:** confirm the installer's Python build-contract suites do not read `protocol/` or the host
  before moving them to release-only paths.
- **Other `protocol/` edges:** audit each consumer (`web-extension`, `bridge-scaffold`, `rust`, `native-host`)
  for real file reads before finalising which nodes `protocol/` selects.
- **Job inventory:** exact job names per node for the green check (matrix jobs), and how to treat a node whose
  workflow gains or loses a job.
- **Release effect of ownership changes:** root build files and `.github/` stop being universal release paths and
  `app/` becomes `android/`; define the transitional legacy release paths (see "Selection-model changes"), how
  long they stay, and the before/after planner tests using unreleased commits.
- **Later:** whether other nodes' workflows should become node-owned like Android's (out of scope here), and a
  markdown or docs-format check, which does not exist today.
