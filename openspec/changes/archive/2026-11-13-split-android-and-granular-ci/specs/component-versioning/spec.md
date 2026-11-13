## ADDED Requirements

### Requirement: Retired paths are accounted for
The component map SHALL support `retired-paths`, mapping a path prefix that no longer exists after a move to the nodes it selects. A changed path under a retired prefix MUST count as accounted for ownership validation and MUST select the listed nodes in CI. A `retired-paths` entry MUST NOT by itself make a path a release input; release attribution stays determined by `paths`, `release-paths`, `global` and `legacy-release`.

#### Scenario: Migration pull request
- **WHEN** the pull request that moves `app/` to `android/` deletes files under `app/` and `config/detekt/`
- **THEN** those paths are accounted and `android` is selected

#### Scenario: Retired CI-only prefix
- **WHEN** a commit touches only `config/detekt/`, which `retired-paths` lists and which was never a release input
- **THEN** it does not count toward any component's next version

#### Scenario: Retired prefix that is also a legacy path
- **WHEN** an old-layout `feat` commit touches only `app/`, which is both retired and a legacy release path of `android`
- **THEN** it is attributed to `android` through `legacy-release`, not through `retired-paths`

### Requirement: Legacy release attribution is bounded by layout epoch
A release component MAY declare `legacy-release` with legacy paths and an epoch path. The planner SHALL attribute a commit to the component through the legacy paths only when that commit's tree does not contain the epoch path. It MUST deduplicate commits matched by both the normal and the legacy pass by commit id and merge their reasons and notes. For a component with a tag, the next version MUST be computed from the highest bump implied by the merged reasons. Legacy paths MUST NOT be part of `release_paths`.

#### Scenario: Unreleased feature before the move
- **WHEN** a `feat(app)` commit touching only `app/` precedes the migration and follows `android-v0.4.0`
- **THEN** the next Android version is 0.5.0

#### Scenario: Root-only feature after the move
- **WHEN** a `feat` commit made after the migration touches only the root `build.gradle.kts`
- **THEN** Android is not bumped by it, while `web-extension` and `installer` are

#### Scenario: Squash migration commit
- **WHEN** the single squash commit that adds `android/` is planned
- **THEN** it counts through the normal pass because it touches `android/`

#### Scenario: Revert and reapply
- **WHEN** the migration is reverted, an `app/` feature is committed while `android/` is absent, and the migration is reapplied
- **THEN** that feature is still attributed to Android through the legacy paths

#### Scenario: Commit matched twice
- **WHEN** one commit touches both `rust/` and a legacy path
- **THEN** it is counted once in the merged reasons and notes

#### Scenario: Legacy becomes inert
- **WHEN** the next Android tag contains the migration
- **THEN** the legacy pass finds no old-layout commit in range and changes nothing

#### Scenario: Untagged component keeps its initial version
- **WHEN** no `android-v*` tag exists and an old-layout `feat` commit touches `app/`
- **THEN** the planned Android version is the `initial-version` and the notes cover commits after the onboarding commit

### Requirement: Android legacy paths are exactly the former release inputs
For `android`, the legacy paths SHALL be only the former effective release inputs that stop applying after the move (`app/`, `build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`, `gradle/libs.versions.toml`) and MUST NOT include CI-only inputs such as `config/detekt/`.

#### Scenario: CI-only input gains no attribution
- **WHEN** an old-layout `fix` commit touches only `config/detekt/detekt.yml`
- **THEN** Android is not bumped by it

### Requirement: The normative protocol specification is owned by the protocol node
The normative protocol specification, its API document and its review records SHALL live under `protocol/docs/`, inside the `protocol` node's paths, so edits to them select the protocol checks that read them. No stub MUST remain at `docs/protocol/`, live references (code comments, build files, protocol checks, manifests and fixture descriptions, READMEs, live documents) MUST point to the new location, and archived changes, dated plans and specs, and frozen evidence MUST be left as written.

#### Scenario: Specification path
- **WHEN** `protocol/checks/evidence.mjs` resolves the specification
- **THEN** it reads `protocol/docs/veles-v1.md`

#### Scenario: Fixture digests unchanged
- **WHEN** fixture description strings are updated for the new path
- **THEN** generated and committed fixtures stay equal, per-fixture and whole-array digests are unchanged where they cover only data, and `protocolCheck` and `protocolFreshEvidenceCheck` pass

#### Scenario: No stale live reference
- **WHEN** the reference audit searches live files for `docs/protocol`
- **THEN** no match remains outside archived changes, dated plans and specs, and frozen evidence

## MODIFIED Requirements

### Requirement: Component map is the single source of path ownership
The repository SHALL define every release component and CI check node in `.github/components.json`. Paths MUST be repository-relative directory prefixes ending in `/` or exact file names, without wildcards. Release planning and CI change selection MUST read this file and no other path list. A node MAY declare `ci-paths` (inputs that select it in CI and never count toward its release), `release-paths` (inputs that count toward its release and never select it in CI) and the expected check names `jobs`; the map MAY declare `global-overrides` (an exact path mapped to the nodes it selects in CI instead of every node, without changing release attribution).

#### Scenario: Release and CI read the same paths
- **WHEN** a path prefix is added to the `android` node's `paths`
- **THEN** both the release planner and the CI changes job treat files under that prefix as belonging to `android` without any other file being edited

#### Scenario: Wildcards are rejected
- **WHEN** a node lists a path containing `*`, `?` or `[`
- **THEN** the map check fails and names the node and path

#### Scenario: CI-only input
- **WHEN** a `fix` commit changes only a path listed in a node's `ci-paths`
- **THEN** that node is selected in CI and the commit does not bump the component

#### Scenario: Release-only input
- **WHEN** a `fix` commit changes only a path listed in `installer`'s `release-paths`, such as `protocol/v1/limits.json`
- **THEN** the `installer` component gets a patch bump and the `installer` CI node is not selected by that commit

#### Scenario: Override affects only CI
- **WHEN** a `fix(ci)` commit changes only `.github/components.json`, which `global-overrides` maps to `tooling`
- **THEN** only `tooling` is selected in CI, while the path still counts as a global release path

### Requirement: Global and unowned paths
The component map SHALL list `global` paths (workflows, the Gradle wrapper, `gradle/` wrapper files and `.java-version`) that belong to every node, and `unowned` paths that belong to none. Root build files (`build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`) MUST NOT be global; they MUST be paths of every non-Android node. Every tracked top-level directory and file MUST be owned by a node, global, or unowned.

#### Scenario: Root build file belongs to every component
- **WHEN** a `fix(build): …` commit changes only `gradle/wrapper/gradle-wrapper.properties`
- **THEN** every CI node is selected and every release component gets a patch bump

#### Scenario: Root build file belongs to every non-Android component
- **WHEN** a `fix(build): …` commit changes only `build.gradle.kts`
- **THEN** every CI node except `android` is selected and `web-extension` and `installer` get a patch bump while `android` does not

#### Scenario: Unaccounted path
- **WHEN** a new top-level directory is added that is not owned, global or unowned
- **THEN** the map check run by the CI `changes` job fails and names the directory

#### Scenario: Cross-directory import
- **WHEN** a file under `verify/lib/` changes
- **THEN** the `web-extension` node is selected, because `web-extension/scripts/package.mjs` imports it

### Requirement: Versions are derived from commits on owned paths
The next version of a release component SHALL be computed from the commits reachable from the target commit but not from the component's latest tag, counting only commits that touched the component's release inputs: its `paths`, its `release-paths`, a global path, or, within the bounded layout epoch, its `legacy-release` paths. `ci-paths`, `global-overrides` and `retired-paths` MUST NOT make a commit count. Each component's previous tag MUST be resolved independently of other components' tags.

#### Scenario: Shared code bumps every consumer
- **WHEN** the only commit since the last releases is `fix(crypto): reject short keys` touching only `rust/veles-crypto/`
- **THEN** `android` and `web-extension` each get a patch bump and `installer` is unchanged

#### Scenario: Untouched component does not bump
- **WHEN** commits since the last releases touch only `web-extension/`
- **THEN** `android` and `installer` keep their versions

#### Scenario: Independent previous tags
- **WHEN** `android-v0.4.0` is older than `web-extension-v0.2.0` and a `feat` commit touching `android/` lies between them
- **THEN** that commit counts toward the next Android version only

#### Scenario: Migration does not change attribution of earlier commits
- **WHEN** the planner runs on the same history before and after a non-bumping migration commit (type `refactor`) that adds `android/`
- **THEN** every earlier commit keeps the same attribution and every component's bump reasons from those commits are identical

### Requirement: Bump rules
`feat` SHALL bump the minor version; `fix` and `perf` MUST bump the patch version; a `!` header, `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer MUST bump the major version, also below 1.0.0. Every other type and every non-conventional commit MUST NOT bump.

#### Scenario: Feature and fix
- **WHEN** the commits on `web-extension/` since `web-extension-v0.2.3` are one `fix:` and one `feat:`
- **THEN** the next web-extension version is 0.3.0

#### Scenario: Performance fix
- **WHEN** the only commit on `android/` since `android-v0.4.0` is `perf(android): cache regexes`
- **THEN** the next Android version is 0.4.1

#### Scenario: Breaking change below 1.0
- **WHEN** a commit `feat(protocol)!: v2 frames` touches `protocol/` after `installer-v0.3.2`
- **THEN** the next installer version is 1.0.0

#### Scenario: Non-bumping types
- **WHEN** the only commits on `android/` since `android-v0.4.0` have types `docs`, `chore`, `refactor`, `test`, `build`, `ci`, `style` or `revert`, or are non-conventional
- **THEN** the planner reports no change for Android

### Requirement: Android version from tags
Android SHALL derive `versionName` and `versionCode` (`MMNNPP`) from the `android-v` tag, whether it is built from the repository root or from `android/`.

#### Scenario: Android build reads the tag
- **WHEN** the commit being built carries `android-v1.2.3`
- **THEN** the APK has `versionName` 1.2.3 and `versionCode` 10203

#### Scenario: Built from the Android folder
- **WHEN** `cd android && ./gradlew assembleRelease` builds the commit carrying `android-v1.2.3`
- **THEN** the APK has `versionName` 1.2.3 and `versionCode` 10203
