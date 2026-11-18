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
- **THEN** it reports `docs / agent-files` (the agent-file layout check, then the documentation site guard `python3 docs/checks/check_site.py --root .`) and `docs / unit-tests` (the tests of both checks), and it reports no release or build job

#### Scenario: Site guard step in the workflow contract
- **WHEN** the `agent-files` job of `check-docs.yml` drops the site guard step or runs it with other arguments
- **THEN** the workflow contract suite fails

#### Scenario: Declared checks drift
- **WHEN** a check workflow adds, removes or renames a job without updating the node's declared check names
- **THEN** the workflow contract suite fails
