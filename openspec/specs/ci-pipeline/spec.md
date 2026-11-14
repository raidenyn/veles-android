# ci-pipeline Specification

## Purpose
TBD - created by archiving change component-releases-and-conventional-commits. Update Purpose after archive.

## Requirements

### Requirement: Component-tree CI
`ci.yml` SHALL call one reusable check workflow per CI node of `.github/components.json`, so that checks are reported as `<node> / <check>`. The component map MUST declare the check names each node reports, and the workflow contract suites MUST fail when a workflow and the declaration differ.

#### Scenario: Android node
- **WHEN** a PR changes `android/app/src/main/`
- **THEN** the PR shows `android / lint`, `android / unit-tests`, `android / release-hook-guard` and `android / instrumented-tests`

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

#### Scenario: Docs node
- **WHEN** the `docs` node runs
- **THEN** it reports `docs / agent-files` (the agent-file layout check) and `docs / unit-tests` (the tests of that check), and it reports no release or build job

#### Scenario: Declared checks drift
- **WHEN** a check workflow adds, removes or renames a job without updating the node's declared check names
- **THEN** the workflow contract suite fails
### Requirement: No existing check is lost
Every command run by `ci.yml` and `build-native-host.yml` before this change SHALL still run in exactly one CI node, and the clean contract MUST keep its order: build JNI and extension outputs, clean, assert clean, rebuild and re-verify the APK, in one job.

#### Scenario: No check lost
- **WHEN** the contract suites compare the check workflows with the pre-change command inventory
- **THEN** every pre-change command appears in exactly one node

#### Scenario: Clean contract order
- **WHEN** `rust / jni-integration` runs
- **THEN** `assembleDebug` and the extension build precede `./gradlew clean`, which precedes `assert-clean.sh`, which precedes the second `assembleDebug` and JNI verification
### Requirement: Workflow contract suites move with the workflows
Every change that adds, moves, renames or deletes a workflow SHALL update, in the same commit, `verify/test/workflow-contracts.test.mjs`, both `test_build_contract.py` suites and the workflow scopes of `verify/check-remote-code.mjs`, preserving their negative tests.

#### Scenario: Retired workflow name
- **WHEN** `build-native-host.yml` is deleted
- **THEN** no suite or scanner scope refers to it and the equivalent checks target `check-native-host.yml` and `check-installer.yml`

#### Scenario: New docs workflow
- **WHEN** `check-docs.yml` is added
- **THEN** `verify/test/workflow-contracts.test.mjs` and the workflow scopes of `verify/check-remote-code.mjs` cover it, with a negative test that fails when the workflow installs any package from a registry at run time (including the OpenSpec CLI), and the checker script under `docs/checks/` is inside the scanner's scopes
### Requirement: Change-based node selection
A `changes` job SHALL run the component map check on every run and compute affected nodes with `scripts/components.py`, without rename detection. For the first run of a pull request it MUST diff against the merge base with the base branch; for later runs of a pull request it MUST select each node as the `pr-last-green-selection` capability defines; for a push to `master` it MUST select every node. Ownership validation of the full changed-path set MUST run in every mode, and the node outputs MUST be written before a validation failure fails the job. Each node MUST run only when selected. A changed path MUST select, in CI: every node whose `paths` or `ci-paths` contain it; every node when it is `global` or `ci-global`, unless `global-overrides` lists the nodes it selects instead; and, for an exact file name listed in `global-overrides` whether or not it is also global, exactly the listed nodes, taking precedence over every node whose `paths` contain it; plus, for every node that a `basename-owners` entry lists for the changed file's basename, that node in addition to all of the above; the nodes its `retired-paths` entry lists; and no node when it is `unowned`. A changed `AGENTS.md` that `global-overrides` lists MUST select exactly the nodes the entry names: `docs` for every nested `AGENTS.md`, and `docs` plus `native-host` for `native-bridge/host/AGENTS.md`, whose content the `native-host / build-contract` check reads. Every node MUST be selected when a global path without an override changed, when a changed path is not accounted for, or when the base is missing or all zeros. The path scenarios below state the nodes a path selects on the first run of a pull request, except the two that name a push; later pull request runs additionally skip nodes that have green evidence, as `pr-last-green-selection` defines, and a push to `master` selects every node whatever the paths.

#### Scenario: Extension-only PR
- **WHEN** a PR changes only `web-extension/src/`
- **THEN** `web-extension` runs and `android`, `rust`, `native-host` and `installer` are skipped

#### Scenario: Shared Rust change
- **WHEN** a PR changes only `rust/veles-crypto/src/lib.rs`
- **THEN** `rust`, `android`, `web-extension` and `protocol` run

#### Scenario: Global path
- **WHEN** a PR changes `.github/workflows/ci.yml`, a workflow that no node owns, or `gradlew`
- **THEN** every node runs

#### Scenario: Wrapper or Java version
- **WHEN** a PR changes `gradlew`, `gradle/wrapper/gradle-wrapper.properties` or `.java-version`
- **THEN** every node runs, including `android`

#### Scenario: Root build file
- **WHEN** a PR changes only the root `build.gradle.kts`
- **THEN** every node runs except `android` and `docs`

#### Scenario: Lint configuration change
- **WHEN** a PR changes only `android/config/detekt/detekt.yml`
- **THEN** the `android` node runs, together with `protocol`, whose paths list `android/`

#### Scenario: Moved file
- **WHEN** a PR moves a file from `android/` to `web-extension/`
- **THEN** both `android` and `web-extension` run

#### Scenario: Component map edit
- **WHEN** a PR changes only `.github/components.json`
- **THEN** `tooling` and `docs` run, because dropping an override must fail the docs check on the pull request

#### Scenario: Root editor configuration
- **WHEN** a PR changes only the root `.editorconfig`
- **THEN** `web-extension` and `bridge-scaffold` run and `android` does not, because `android/.editorconfig` governs Kotlin

#### Scenario: Protocol input does not select the installer
- **WHEN** a PR changes only `protocol/v1/limits.json`
- **THEN** `android`, `web-extension`, `rust`, `protocol`, `native-host` and `bridge-scaffold` run and `installer` does not

#### Scenario: Host toolchain selects the installer
- **WHEN** a PR changes only `native-bridge/host/rust-toolchain.toml`
- **THEN** `installer` runs

#### Scenario: Android check workflow edit
- **WHEN** a PR changes only `.github/workflows/check-android.yml`
- **THEN** `android`, `tooling` and `installer` run and the other nodes do not

#### Scenario: Android build workflow edit
- **WHEN** a PR changes only `.github/workflows/build-android.yml`
- **THEN** `android`, `tooling` and `installer` run and the other nodes do not

#### Scenario: Documentation only
- **WHEN** the first run of a PR changes only `README.md`, `docs/ci.md` or files under `openspec/`
- **THEN** only `docs` runs and `ci-ok` passes once it is green

#### Scenario: Path that no node selects
- **WHEN** the first run of a PR changes only `LICENSE` or files under `site/`
- **THEN** no node runs and `ci-ok` passes

#### Scenario: Agent guidance edit
- **WHEN** a PR changes only `.agents/rules/repo-conventional-commits.md`, the root `AGENTS.md` or `opencode.json`
- **THEN** only `docs` runs

#### Scenario: Nested AGENTS.md selects only docs
- **WHEN** a PR changes only one of `android/AGENTS.md`, `web-extension/AGENTS.md`, `rust/AGENTS.md`, `protocol/AGENTS.md`, `native-bridge/AGENTS.md`, `native-bridge/installer/AGENTS.md`, `native-bridge/installer/macos/AGENTS.md`, `native-bridge/installer/windows/AGENTS.md`, `verify/AGENTS.md` or `scripts/AGENTS.md`
- **THEN** only `docs` runs and no `android`, `web-extension`, `rust`, `protocol`, `native-host`, `installer`, `bridge-scaffold`, `supply-chain` or `tooling` job starts, although a node's `paths` contain the file, because `global-overrides` lists each nested `AGENTS.md`

#### Scenario: Host AGENTS.md also selects native-host
- **WHEN** a PR changes only `native-bridge/host/AGENTS.md`
- **THEN** `docs` and `native-host` run and the other nodes do not, so a removed string that `native-host / build-contract` requires fails in the PR

#### Scenario: New guidance or CLAUDE.md anywhere
- **WHEN** a PR adds `rust/new-dir/AGENTS.md`, `site/AGENTS.md` or `android/CLAUDE.md`
- **THEN** `docs` runs in addition to any node whose `paths` contain the file, because `basename-owners` maps those basenames to `docs`

#### Scenario: AGENTS.md together with code
- **WHEN** a PR changes `android/AGENTS.md` and `android/app/src/main/Foo.kt`
- **THEN** `android` runs because of the code change and `docs` runs because of the guidance change

#### Scenario: AGENTS.md edit after green evidence
- **WHEN** a later PR run changes only `rust/AGENTS.md` and `android` has green evidence
- **THEN** `android` is skipped and `docs` runs

#### Scenario: Docs workflow edit
- **WHEN** a PR changes only `.github/workflows/check-docs.yml`
- **THEN** `docs`, `tooling`, `installer` and `supply-chain` run and the other nodes do not, because the installer contract suite reads every workflow and the scanner scans it

#### Scenario: CODEOWNERS edit
- **WHEN** a PR changes only `.github/CODEOWNERS`
- **THEN** only `docs` runs, because `global-overrides` maps it to `docs`

#### Scenario: Documentation read by a gate
- **WHEN** a PR changes only `docs/reproducible-builds.md`, which the host build-contract suite reads
- **THEN** `docs` and `native-host` run

#### Scenario: Legacy agent file removed
- **WHEN** a PR deletes `CLAUDE.md`, `CONTRIBUTION.md` or files under `.opencode/`
- **THEN** the paths are accounted for through `retired-paths` and `docs` runs

#### Scenario: Markdown inside a node folder
- **WHEN** a PR changes only `native-bridge/installer/README.md`
- **THEN** the `installer` node runs, together with `protocol`, whose paths list `native-bridge/installer/`

#### Scenario: Protocol specification edit
- **WHEN** a PR changes only `protocol/docs/veles-v1.md`
- **THEN** every node whose paths list `protocol/` runs (`android`, `web-extension`, `rust`, `protocol`, `native-host`, `bridge-scaffold`) and `installer` does not

#### Scenario: Master push
- **WHEN** a push to `master` changes only `docs/ci.md`
- **THEN** every node runs

#### Scenario: New branch push
- **WHEN** a push event has `before` equal to forty zeros
- **THEN** every node runs

#### Scenario: Unaccounted path
- **WHEN** a PR adds a top-level file that no node, global, retired or unowned entry accounts for
- **THEN** every node is selected and `changes` fails after writing its outputs

#### Scenario: Moved-away path
- **WHEN** the PR that moves `app/` to `android/` deletes `app/build.gradle.kts`
- **THEN** validation succeeds through the `retired-paths` entry and `android` runs
### Requirement: Single aggregate gate
`ci.yml` SHALL end with `ci-ok`, which depends on every node, runs with `always()`, and fails when `changes` did not succeed or any needed job's result is `failure` or `cancelled`. `ci-ok` MUST be the only required check from `ci.yml`. A node skipped because the selection excluded it, including because of last-green evidence, MUST NOT fail `ci-ok`.

#### Scenario: Child failure
- **WHEN** `android / unit-tests` fails
- **THEN** `ci-ok` fails

#### Scenario: Skipped nodes
- **WHEN** all affected nodes pass and the others are skipped
- **THEN** `ci-ok` passes

#### Scenario: Skipped by evidence
- **WHEN** `android` is skipped because it was green on an earlier pull request head and nothing it owns changed
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
On a push to `master`, `release-build.yml` SHALL build unsigned artifacts only for affected release components, plus the Rust JNI/WASM package when `rust` is affected, and MUST NOT build installers or unaffected components. Its change selection MUST NOT be altered by `ci.yml` selecting every node on `master` pushes.

#### Scenario: Android-only merge
- **WHEN** a merged PR changed only `android/`
- **THEN** the master run builds the APK only

#### Scenario: Docs-only merge
- **WHEN** a merged PR changed only `docs/`
- **THEN** the master run builds nothing

#### Scenario: CI runs everything, builds stay changed-only
- **WHEN** a merged PR changed only `web-extension/`
- **THEN** `ci.yml` runs every node on the master push and `release-build.yml` builds the extension only
### Requirement: Missing lint coverage is tracked
Nodes without lint or format checks SHALL be listed as future work in `docs/ci.md`; adding them MUST NOT be part of this change.

#### Scenario: Gap list
- **WHEN** a reader opens `docs/ci.md`
- **THEN** it lists every node's checks and marks protocol lint, Swift/NSIS lint, actionlint, shellcheck, ruff and coverage as future work
