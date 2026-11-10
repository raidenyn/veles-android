## Why

Only the Android app has a version today; the web extension and installers are frozen at 0.1.0 and
are never released, releases are APK-only, and CI runs every job on every PR. Commit history is not
machine-readable, so versions and release notes cannot be derived from it. Now that the extension and
native installers exist, each component needs its own version that moves only when it really
changed, one owner-controlled release that lists every component, and CI that runs only what a change
touched.

## What Changes

**Versioning**
- From: one Android version from plain `X.Y.Z` tags; other components frozen at `0.1.0`.
- To: independent SemVer per release component (`android`, `web-extension`, `installer`) computed by
  git-cliff from Conventional Commits that touched the component's paths, including shared `rust/`
  and `protocol/`; state kept only in `<component>-v<semver>` tags; versions injected at build time.
- Reason: bump only on real changes, no release PR, no bot commits.
- Impact: non-breaking for users; Android restarts at `0.1.0` (`versionCode` 100 > 3, upgrades in place).

**Releasing**
- From: `release.yml` on a pushed tag (APK only, repo-level keystore secrets), a manual prerelease in
  `release-build.yml`, and `release-native-installers.yml` producing unpublished evidence.
- To: one owner-only (`raidenyn`) `publish-release` workflow that builds and signs only changed
  components, carries unchanged components' assets byte-for-byte, and publishes one combined GitHub
  Release tagged `YYYY.MM.DD` with a component version table. The three old paths are retired.
- Impact: breaking for the release process (owner only); keystore secrets move to a protected
  environment.

**Commit conventions**
- Added: `scripts/commitlint.py`, a `.githooks/commit-msg` hook installed automatically by Android
  Gradle builds or explicitly with `./gradlew installGitHooks` (no root `package.json`, no npm
  lifecycle scripts), a required `pr-title` check that re-runs on title edits,
  squash commits titled from the PR title, a `master` ruleset, and a post-merge commit guard.

**CI and builds**
- From: a flat `ci.yml` running everything; label `release-build` builds the full graph; installer
  labels `windows-installer-build` / `mac-installer-build`.
- To: a component tree (`<component> / <check>`) of reusable check workflows gated by a changes job
  over `.github/components.json`, with a single `ci-ok` gate; PR builds by labels `build-android`,
  `build-web-extension`, `build-windows-installer`, `build-macos-installer` (`build-watch` reserved);
  master pushes build only changed components and publish a release preview.
- Impact: PRs without a build label no longer upload an extension zip.

## Capabilities

### New Capabilities
- `component-versioning`: component map, tag-driven SemVer per component from Conventional Commits
  (shared paths included), build-time version injection.
- `release-publishing`: owner-only publish workflow, change planning, carry-forward of unchanged
  assets, combined date-tagged GitHub Release, signing boundaries, release preview.
- `commit-conventions`: commit message validator, local hook installation, PR title check, squash
  merge policy, post-merge guard.
- `ci-pipeline`: component-tree CI with change-based selection and aggregate gate, label-driven PR
  builds, changed-only master builds.

### Modified Capabilities
- None (no existing specs in `openspec/specs/`).

## Impact

- Workflows: `ci.yml` (rewritten as orchestrator), new `check-*.yml`, `pr-title.yml`,
  `publish-release.yml`; `release-build.yml` reshaped; `build-pr-installers.yml` labels renamed;
  `release.yml` and `release-native-installers.yml` removed; `build-native-*.yml` gain a `version`
  input; `build-native-host.yml` split into the `native-host` and `installer` nodes.
- Contract tests: `verify/test/workflow-contracts.test.mjs` rewritten for the new graph.
- Code: `app/build.gradle.kts` (`androidGitVersion` prefix), `web-extension/src/manifest.ts`,
  `native-bridge/host/src/host.rs`, installer producers / `assemble_unsigned.py` (descriptor version
  injection), root `build.gradle.kts` (hook installer), `web-extension/scripts/package.mjs` and tests (zip
  name), `build-android.yml` (unsigned only).
- New files: `.github/components.json`, `cliff.toml`, `scripts/commitlint.py`,
  `scripts/components.py`, `scripts/release_plan.py` (+ tests), `.githooks/commit-msg`,
  `docs/releasing.md`, `docs/ci.md`.
- New tool dependency: pinned git-cliff binary (CI and publish only).
- GitHub settings (owner, manual): squash title/body, `master` ruleset, environments
  `android-release` and `release-publish`, label renames.
- Docs: `CLAUDE.md` versioning, release and workflow sections.
