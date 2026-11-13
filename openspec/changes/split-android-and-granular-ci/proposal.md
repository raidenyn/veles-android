## Why

Every push to a pull request re-runs every CI node the PR has touched since it branched, and any edit to the
root Gradle build, `.github/` or `.editorconfig` selects all nine nodes. Android code, its Gradle build and the
shared root build are entangled, so Android cannot be built, checked or released in isolation. Separating the
structure and re-running only what changed since a node last passed makes CI faster and the repo easier to
reason about.

## What Changes

Delivered as four separate PRs (1, 1b, 2, 3) under this change.

**PR 1: last-green node selection for `ci.yml`**
- From: a PR run always diffs against `merge-base(master, HEAD)`.
- To: the first PR run keeps that diff; later runs diff each node against its last green PR head, read from
  GitHub Actions run and job results. A node is green only when all its expected jobs succeeded in the latest
  attempt of one run and that job set matches the node's current expected set; when compatibility with the
  current map cannot be established, the node runs. No usable evidence, an unreachable commit, a rebase or
  force-push falls back to the merge-base diff for that node; an API error runs every node. Master pushes
  always run every node in `ci.yml`.
- Reason: stop re-running unchanged nodes on every push.
- Impact: non-breaking; `ci-ok` keeps its name and meaning; `release-build.yml` keeps its changed-only master
  semantics.
- Map cleanups on the current layout: `.github/components.json` selects only `tooling`; root `.editorconfig`
  selects only its Prettier consumers; `installer` stops being selected in CI by `protocol/` and by
  `native-bridge/host/` paths its checks do not read, through a new release-only path list (they stay release
  inputs; applied only to inputs confirmed unused by the installer checks); a required job list per node is
  checked against the real workflows.

**PR 1b: move the protocol spec under `protocol/`**
- From: `docs/protocol/` (unowned) is read by the protocol check, so spec edits run no protocol check.
- To: it lives inside the protocol node (assistant default `protocol/docs/`); live references (code comments,
  `build.gradle.kts`, protocol checks, manifests, READMEs) are updated; historical records and frozen evidence
  are left as written.

**PR 2: `android/` as an independent Gradle build**
- From: `app/` is a module of one root Gradle build that also owns Rust, protocol, bridge, installers, verify and
  release tooling, and the root build reaches into `:app`.
- To: `android/` holds the app, its own settings, version catalog, detekt/Spotless config, `.editorconfig` and a
  `gradlew` shim to the single root wrapper; `rust/` becomes its own Gradle build included by both the root build
  and `android/`; the root build orchestrates everything through included builds. Dependencies point one way:
  root -> android -> rust. Android CI jobs run from `android/`.
- Reason: Android checks should run only when Android, or something it depends on, changes. Android changes
  still select the protocol node's cross-component contracts (JVM fixture consumer, session loopback), and
  root-orchestration edits stay covered without blanket Android selection; their wiring is left to the design.
- Impact: root `build.gradle.kts` and `settings.gradle.kts` stop selecting Android and stop cutting Android
  releases; release attribution of unreleased commits is preserved through bounded legacy release paths.

**PR 3: node-owned Android workflows**
- From: all of `.github/` is `global`.
- To: `check-android.yml` and `build-android.yml` are owned by Android plus the nodes whose contract tests read
  them (`tooling`, and `installer` for `build-android.yml`); narrower ownership wins over the broad global match.

**Out of scope**: moonrepo/moon or any new CI tool; owning other nodes' workflows; a docs-format check; changing
which checks exist; changing release signing, tags or publishing behaviour.

## Capabilities

### New Capabilities
- `pr-last-green-selection`: per-node skipping on pull requests based on the node's last green PR head, with the
  first-run, master, fallback and evidence rules.
- `android-standalone-build`: the independent `android/` Gradle build, shim wrapper, included `rust/` build, root
  orchestration and one-way dependency rules.

### Modified Capabilities
- `ci-pipeline`: change-based node selection (first versus later PR runs, master always full, node-owned
  workflows, `components.json` and `.editorconfig` selection, installer CI inputs) and the aggregate gate
  accepting nodes skipped by last-green evidence.
- `component-versioning`: component map ownership (CI-only, release-only and global paths, root build files no
  longer global, protocol spec inside the protocol node, transitional release attribution) and the Android
  version computed from the `android/` subfolder.

## Impact

- **CI tooling**: `.github/components.json`, `scripts/components.py` and tests, `scripts/release_plan.py`,
  `.github/workflows/{ci,check-android,build-android,release-build,check-rust}.yml`,
  `verify/test/workflow-contracts.test.mjs`, `native-bridge/installer/scripts/test_build_contract.py`,
  `docs/ci.md`. To audit, edited only if their interfaces change: `publish-release.yml` (its Android build
  delegates to `build-android.yml`).
- **Build**: root `build.gradle.kts` split into root, `rust/` and `android/` builds; `settings.gradle.kts`,
  `gradle.properties`, `gradle/libs.versions.toml`, `config/detekt`, version plugin and Spotless move or split;
  `android/gradlew` shims on Linux and Windows.
- **Code and paths**: `app/` -> `android/app/`; live references to the protocol spec path (code comments, build
  files, protocol checks and manifests, READMEs; inventory in the design); path-sensitive consumers (`scripts/check-mac-comparisons.mjs`, `.dockerignore`, `.idea`, Android lint
  guards, `CLAUDE.md`, docs).
- **Constraints**: frozen evidence logs are not rewritten; `config/android_keys/` is not moved or deleted and the
  APK signing certificate and computed version must match the old layout; unreleased commits keep their release
  attribution.
- **Risks** (tracked as design spikes): nested `includeBuild` of `rust/` from root and `android/`; shim wrapper
  on Windows; the Android version plugin from a subfolder; last-green correctness under rebases, reruns and job
  set changes.
