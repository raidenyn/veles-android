## ADDED Requirements

### Requirement: Component map is the single source of path ownership
The repository SHALL define every release component and CI check node in `.github/components.json`. Paths MUST be repository-relative directory prefixes ending in `/` or exact file names, without wildcards. Release planning and CI change selection MUST read this file and no other path list.

#### Scenario: Release and CI read the same paths
- **WHEN** a path prefix is added to the `android` node
- **THEN** both the release planner and the CI changes job treat files under that prefix as belonging to `android` without any other file being edited

#### Scenario: Wildcards are rejected
- **WHEN** a node lists a path containing `*`, `?` or `[`
- **THEN** the map check fails and names the node and path

### Requirement: Global and unowned paths
The component map SHALL list `global` paths (workflows and root build files) that belong to every node, and `unowned` paths that belong to none. Every tracked top-level directory and file MUST be owned by a node, global, or unowned.

#### Scenario: Root build file belongs to every component
- **WHEN** a `fix(build): …` commit changes only `build.gradle.kts`
- **THEN** every CI node is selected and every release component gets a patch bump

#### Scenario: Unaccounted path
- **WHEN** a new top-level directory is added that is not owned, global or unowned
- **THEN** the map check run by the CI `changes` job fails and names the directory

#### Scenario: Cross-directory import
- **WHEN** a file under `verify/lib/` changes
- **THEN** the `web-extension` node is selected, because `web-extension/scripts/package.mjs` imports it

### Requirement: Release components and their tags
The release components SHALL be `android`, `web-extension` and `installer`, each versioned independently with SemVer and tagged `<component>-v<MAJOR>.<MINOR>.<PATCH>`. Windows and macOS installers MUST share the `installer` version. No other node MUST have a release version.

#### Scenario: Tag format
- **WHEN** the web extension is released at 0.3.1
- **THEN** the tag `web-extension-v0.3.1` points at the released commit

#### Scenario: Old plain tags are ignored
- **WHEN** the planner computes the current Android version in a repository with tags `0.0.1`..`0.0.3`
- **THEN** those tags are not treated as Android releases

### Requirement: Versions are derived from commits on owned paths
The next version of a release component SHALL be computed from the commits reachable from the target commit but not from the component's latest tag, counting only commits that touched the component's paths or a global path. Each component's previous tag MUST be resolved independently of other components' tags.

#### Scenario: Shared code bumps every consumer
- **WHEN** the only commit since the last releases is `fix(crypto): reject short keys` touching only `rust/veles-crypto/`
- **THEN** `android` and `web-extension` each get a patch bump and `installer` is unchanged

#### Scenario: Untouched component does not bump
- **WHEN** commits since the last releases touch only `web-extension/`
- **THEN** `android` and `installer` keep their versions

#### Scenario: Independent previous tags
- **WHEN** `android-v0.4.0` is older than `web-extension-v0.2.0` and a `feat` commit touching `app/` lies between them
- **THEN** that commit counts toward the next Android version only

### Requirement: Bump rules
`feat` SHALL bump the minor version; `fix` and `perf` MUST bump the patch version; a `!` header, `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer MUST bump the major version, also below 1.0.0. Every other type and every non-conventional commit MUST NOT bump.

#### Scenario: Feature and fix
- **WHEN** the commits on `web-extension/` since `web-extension-v0.2.3` are one `fix:` and one `feat:`
- **THEN** the next web-extension version is 0.3.0

#### Scenario: Performance fix
- **WHEN** the only commit on `app/` since `android-v0.4.0` is `perf(android): cache regexes`
- **THEN** the next Android version is 0.4.1

#### Scenario: Breaking change below 1.0
- **WHEN** a commit `feat(protocol)!: v2 frames` touches `protocol/` after `installer-v0.3.2`
- **THEN** the next installer version is 1.0.0

#### Scenario: Non-bumping types
- **WHEN** the only commits on `app/` since `android-v0.4.0` have types `docs`, `chore`, `refactor`, `test`, `build`, `ci`, `style` or `revert`, or are non-conventional
- **THEN** the planner reports no change for Android

### Requirement: Initial versions without a tag
A release component without a matching tag SHALL use its `initial-version` and MUST limit its first notes to commits after the onboarding commit. The initial Android version MUST exceed 0.0.3.

#### Scenario: First Android version
- **WHEN** no `android-v*` tag exists
- **THEN** the planned Android version is 0.1.0, which produces `versionCode` 100

### Requirement: Versions are injected at build time
No committed file SHALL be the authoritative version of a release component; every build MUST obtain its version from the component's tag or from a value injected by the publish workflow.

#### Scenario: No version commit
- **WHEN** a component is released
- **THEN** no commit is created on `master` to record its version

### Requirement: Android version from tags
Android SHALL derive `versionName` and `versionCode` (`MMNNPP`) from the `android-v` tag.

#### Scenario: Android build reads the tag
- **WHEN** the commit being built carries `android-v1.2.3`
- **THEN** the APK has `versionName` 1.2.3 and `versionCode` 10203

### Requirement: Web extension version
The web extension SHALL take its manifest `version` from an injected value when present, otherwise from the latest `web-extension-v` tag, otherwise `0.0.1`. Every value MUST satisfy Chrome's rules (one to four dot-separated integers, each 0..65535, no leading zeros, not all zero). The packaged zip MUST be named from the emitted manifest version.

#### Scenario: Injected version
- **WHEN** the extension is built with injected version 0.4.0
- **THEN** `manifest.json` has `"version": "0.4.0"` and the package is `veles-extension-0.4.0.zip`

#### Scenario: Development build
- **WHEN** the extension is built three commits after `web-extension-v0.2.0` without injection
- **THEN** `version` is 0.2.0 and `version_name` identifies the commits since the tag

#### Scenario: No tag
- **WHEN** no `web-extension-v*` tag is reachable and nothing is injected
- **THEN** `version` is 0.0.1

#### Scenario: Invalid versions rejected
- **WHEN** the injected version is `0.0.0`, `01.2.3`, `1.2` with a non-numeric part, or has a part above 65535
- **THEN** the build fails and names the rule

### Requirement: Installer and host version
The production installer build SHALL use one effective version — the injected installer version, else the tracked config value — for the packaged descriptor, the macOS `VERSION` file, the Windows uninstaller and the native host user agent. That version MUST be a declared input of every build task that embeds it. `Cargo.toml` and `Cargo.lock` MUST NOT be modified at build time.

#### Scenario: Every embedded version agrees
- **WHEN** release installers are built with version 0.2.0
- **THEN** `descriptor.json`, the macOS `VERSION` file and the Windows uninstaller all carry 0.2.0, the host reports `veles-native-host/0.2.0`, and the descriptor digest is the one checked by `release_gate.py`

#### Scenario: Version-only rebuild
- **WHEN** two consecutive production builds differ only in the injected version
- **THEN** the second build re-runs the host, uninstaller and assembly tasks and embeds the new version

#### Scenario: Locked Cargo builds still pass
- **WHEN** the installer is built with an injected version
- **THEN** `cargo --locked` succeeds because no Cargo manifest or lockfile changed
