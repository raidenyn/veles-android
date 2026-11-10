# ci-pipeline Specification

## Purpose
TBD - created by archiving change component-releases-and-conventional-commits. Update Purpose after archive.

## Requirements
### Requirement: Component-tree CI
`ci.yml` SHALL call one reusable check workflow per CI node of `.github/components.json`, so that checks are reported as `<node> / <check>`.

#### Scenario: Android node
- **WHEN** a PR changes `app/src/main/`
- **THEN** the PR shows `android / lint`, `android / unit-tests` and `android / instrumented-tests`

#### Scenario: Web extension node
- **WHEN** the `web-extension` node runs
- **THEN** it reports `web-extension / lint`, `web-extension / unit-tests` and `web-extension / browser-tests`

#### Scenario: Rust and protocol nodes
- **WHEN** the `rust` and `protocol` nodes run
- **THEN** they report `rust / lint`, `rust / unit-tests`, `rust / jni-integration` and `protocol / contract-tests`

#### Scenario: Native host node
- **WHEN** the `native-host` node runs
- **THEN** it reports `lint`, `unit-tests` on Linux, Windows and macOS, `build-contract`, and `compile` on Windows and macOS

#### Scenario: Installer node
- **WHEN** the `installer` node runs
- **THEN** it reports `lint`, `unit-tests` on Linux, Windows and macOS, and `scripts-tests`

#### Scenario: Remaining nodes
- **WHEN** the `bridge-scaffold`, `supply-chain` and `tooling` nodes run
- **THEN** they report `bridge-scaffold / lint`, `bridge-scaffold / unit-tests`, the supply-chain policy check and `tooling / unit-tests`

### Requirement: No existing check is lost
Every command run by `ci.yml` and `build-native-host.yml` before this change SHALL still run in exactly one CI node, and the clean contract MUST keep its order: build JNI and extension outputs, clean, assert clean, rebuild and re-verify the APK, in one job.

#### Scenario: No check lost
- **WHEN** the contract suites compare the check workflows with the pre-change command inventory
- **THEN** every pre-change command appears in exactly one node

#### Scenario: Clean contract order
- **WHEN** `rust / jni-integration` runs
- **THEN** `assembleDebug` and the extension build precede `./gradlew clean`, which precedes `assert-clean.sh`, which precedes the second `assembleDebug` and JNI verification

### Requirement: Workflow contract suites move with the workflows
Every change that moves, renames or deletes a workflow SHALL update, in the same commit, `verify/test/workflow-contracts.test.mjs`, both `test_build_contract.py` suites and the workflow scopes of `verify/check-remote-code.mjs`, preserving their negative tests.

#### Scenario: Retired workflow name
- **WHEN** `build-native-host.yml` is deleted
- **THEN** no suite or scanner scope refers to it and the equivalent checks target `check-native-host.yml` and `check-installer.yml`

### Requirement: Change-based node selection
A `changes` job SHALL run the component map check on every run and compute affected nodes with `scripts/components.py`. For pull requests it MUST diff against the merge base with the base branch; for pushes against `github.event.before`, without rename detection. Each node MUST run only when affected. Every node MUST be selected when a global path changed, a changed path is not accounted for, or the base is missing or all zeros.

#### Scenario: Extension-only PR
- **WHEN** a PR changes only `web-extension/src/`
- **THEN** `web-extension` runs and `android`, `rust`, `native-host` and `installer` are skipped

#### Scenario: Shared Rust change
- **WHEN** a PR changes only `rust/veles-crypto/src/lib.rs`
- **THEN** `rust`, `android`, `web-extension` and `protocol` run

#### Scenario: Global path
- **WHEN** a PR changes `.github/workflows/check-android.yml` or `build.gradle.kts`
- **THEN** every node runs

#### Scenario: Lint configuration change
- **WHEN** a PR changes only `config/detekt/detekt.yml`
- **THEN** the `android` node runs

#### Scenario: Moved file
- **WHEN** a PR moves a file from `app/` to `web-extension/`
- **THEN** both `android` and `web-extension` run

#### Scenario: New branch push
- **WHEN** a push event has `before` equal to forty zeros
- **THEN** every node runs

### Requirement: Single aggregate gate
`ci.yml` SHALL end with `ci-ok`, which depends on every node, runs with `always()`, and fails when `changes` did not succeed or any needed job's result is `failure` or `cancelled`. `ci-ok` MUST be the only required check from `ci.yml`.

#### Scenario: Child failure
- **WHEN** `android / unit-tests` fails
- **THEN** `ci-ok` fails

#### Scenario: Skipped nodes
- **WHEN** all affected nodes pass and the others are skipped
- **THEN** `ci-ok` passes

#### Scenario: Changes job fails
- **WHEN** `changes` fails so every node is skipped
- **THEN** `ci-ok` fails

### Requirement: Label-driven PR builds
PR builds SHALL be selected only by labels, re-evaluated on `opened`, `reopened`, `synchronize`, `labeled` and `unlabeled`: `build-android` builds the unsigned release APK, `build-web-extension` the extension zip, and `build-windows-installer` / `build-macos-installer` the debug installers including the native host behind the `native-installer-debug` approval. A PR without a build label MUST NOT build or upload any component artifact.

#### Scenario: Android label
- **WHEN** `build-android` is added to a PR
- **THEN** an unsigned release APK built from the PR head is uploaded

#### Scenario: No label
- **WHEN** a PR changing `web-extension/` has no build label
- **THEN** no extension zip is uploaded, while the `web-extension` checks still run

#### Scenario: Installer label
- **WHEN** `build-windows-installer` is added to a non-draft PR and the owner approves `native-installer-debug`
- **THEN** a debug Windows installer containing the host built from the PR head is uploaded

### Requirement: Legacy build labels retired
The labels `release-build`, `windows-installer-build` and `mac-installer-build` SHALL NOT trigger any workflow. `build-watch` MUST be documented as reserved.

#### Scenario: Old label
- **WHEN** `release-build` is added to a PR
- **THEN** no build starts

### Requirement: Changed-only master builds
On a push to `master`, `release-build.yml` SHALL build unsigned artifacts only for affected release components, plus the Rust JNI/WASM package when `rust` is affected, and MUST NOT build installers or unaffected components.

#### Scenario: Android-only merge
- **WHEN** a merged PR changed only `app/`
- **THEN** the master run builds the APK only

#### Scenario: Docs-only merge
- **WHEN** a merged PR changed only `docs/`
- **THEN** the master run builds nothing

### Requirement: Missing lint coverage is tracked
Nodes without lint or format checks SHALL be listed as future work in `docs/ci.md`; adding them MUST NOT be part of this change.

#### Scenario: Gap list
- **WHEN** a reader opens `docs/ci.md`
- **THEN** it lists every node's checks and marks protocol lint, Swift/NSIS lint, actionlint, shellcheck, ruff and coverage as future work
