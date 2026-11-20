## ADDED Requirements

### Requirement: Docs check scripts stay offline
The `docs` node SHALL run `python3 docs/checks/check_offline.py --root .` as the first `run:` step of `docs / agent-files`, and the guard MUST fail when a tracked file in `docs/checks/` is neither a `*.py` file nor `AGENTS.md`, when a `*.py` file there does not parse, imports a module that is neither on the guard's standard-library allow-list nor a sibling module in `docs/checks/`, starts a process other than `git`, uses `shell=` with any value other than `False`, passes a forbidden global option or network subcommand to `git`, references an `os` process function, or uses `importlib` outside the guard's named exemptions. The guard MUST use only the Python 3.8 standard library, and its tests MUST run it in-process under the existing `docs / unit-tests` step.

#### Scenario: Real tree passes
- **WHEN** the guard runs on the repository's `docs/checks/`
- **THEN** it exits 0

#### Scenario: Network import
- **WHEN** a file in `docs/checks/` imports `urllib.request`
- **THEN** the guard reports the file and line and exits 1

#### Scenario: Non-git process
- **WHEN** a file in `docs/checks/` calls `subprocess.run(["curl", url])`, or `subprocess.run(cmd, shell=True)`
- **THEN** the guard exits 1

#### Scenario: Forbidden git option or subcommand
- **WHEN** a file in `docs/checks/` runs `["git", "-c", "core.sshCommand=x", "status"]` or `_git(root, "clone", url)`
- **THEN** the guard exits 1, while `_git(root, "ls-files", "-z", "-c", "-i")` passes because `-c` follows the subcommand

#### Scenario: Shell script in docs checks
- **WHEN** a tracked `docs/checks/run.sh` is added
- **THEN** the guard exits 1

## MODIFIED Requirements

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
- **THEN** it reports `docs / agent-files` (the offline guard `python3 docs/checks/check_offline.py --root .`, then the agent-file layout check, then the documentation site guard `python3 docs/checks/check_site.py --root .`) and `docs / unit-tests` (the tests of all three checks), and it reports no release or build job

#### Scenario: Site guard step in the workflow contract
- **WHEN** the `agent-files` job of `check-docs.yml` drops the site guard step or runs it with other arguments
- **THEN** the workflow contract suite fails

#### Scenario: Offline guard step in the workflow contract
- **WHEN** the `agent-files` job of `check-docs.yml` drops the offline guard step, runs it with other arguments, or runs it after the agent-file layout check
- **THEN** the workflow contract suite fails

#### Scenario: Declared checks drift
- **WHEN** a check workflow adds, removes or renames a job without updating the node's declared check names
- **THEN** the workflow contract suite fails

### Requirement: Workflow contract suites move with the workflows
Every change that adds, moves, renames or deletes a workflow SHALL update, in the same commit, `verify/test/workflow-contracts.test.mjs`, both `test_build_contract.py` suites and the workflow scopes of `verify/check-remote-code.mjs`, preserving their negative tests. The exception is a workflow listed in the installer suite's `TOOLING_OWNED_WORKFLOWS` tuple: today only `check-docs.yml`, a workflow that `workflow-contracts.test.mjs` pins to a closed command allow-list. Selection by `tooling` alone, as for `project-flow.yml`, does not put a workflow on that list. It MUST be covered by `workflow-contracts.test.mjs` and its all-workflow release boundary, and it MUST be exempt by exact name from the installer suite's all-workflows release boundary and absent from the scanner's scopes. The tooling all-workflow release boundary MUST reject every signing name, release environment and publishing command that the installer suite's all-workflows boundary rejects, and a tooling test MUST read those terms from the installer suite's source, failing when it cannot extract them.

#### Scenario: Retired workflow name
- **WHEN** `build-native-host.yml` is deleted
- **THEN** no suite or scanner scope refers to it and the equivalent checks target `check-native-host.yml` and `check-installer.yml`

#### Scenario: New docs workflow
- **WHEN** `check-docs.yml` is added or changed
- **THEN** `verify/test/workflow-contracts.test.mjs` covers it, with negative tests that fail when the workflow installs any package from a registry at run time (including the OpenSpec CLI), declares any `environment:`, or runs `git push`, and neither `check-docs.yml` nor `docs/checks/` is a scope of `verify/check-remote-code.mjs`

#### Scenario: Docs workflow outside the installer boundary
- **WHEN** the installer suite's all-workflows release boundary runs
- **THEN** it skips `check-docs.yml` by exact name and still rejects a signing credential added to any other workflow except `publish-release.yml`

#### Scenario: Installer boundary gains a term
- **WHEN** the installer suite's all-workflows boundary gains a signing name that the tooling signing check does not reject, or its publishing pattern stops matching the tooling one
- **THEN** the tooling suite fails

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
- **THEN** exactly `docs` and `tooling` run, and `installer` and `supply-chain` do not, because the tooling contract owns the workflow's release boundary and the scanner does not scan it

#### Scenario: Docs check script edit
- **WHEN** a PR changes only files under `docs/checks/`
- **THEN** only `docs` runs, because the docs offline guard, not the remote-code scan, checks those scripts

#### Scenario: Workflow contract test edit
- **WHEN** a PR changes only `verify/test/workflow-contracts.test.mjs`, or any other file under `verify/test/` that is not a guidance file such as `AGENTS.md`
- **THEN** only `tooling` runs, because `supply-chain` owns only `verify/lib/` and the exact top-level `verify/` files the component map lists

#### Scenario: Verifier file selects supply-chain
- **WHEN** a PR changes a tracked `verify/` file outside `verify/test/`, other than `verify/AGENTS.md`
- **THEN** `supply-chain` runs

#### Scenario: Installer boundary edit selects tooling too
- **WHEN** a PR changes `native-bridge/installer/scripts/test_build_contract.py`
- **THEN** exactly `installer`, `protocol`, `supply-chain` and `tooling` run: the first three own the file through their paths, and `tooling` runs because its boundary test reads the file

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
