## MODIFIED Requirements

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
- **WHEN** a PR changes only `.agents/hooked-rules/repo-commit-types.md`, the root `AGENTS.md` or `opencode.json`
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

#### Scenario: Rule engine edit selects docs too
- **WHEN** a PR changes only `scripts/agent_rules.py`, or, separately, only one file under `scripts/opencode-hooked-rules/`
- **THEN** in each case exactly `tooling` and `docs` run and no other node starts, because `global-overrides` maps each of those files to both nodes and the agent-file check imports the engine's parser
