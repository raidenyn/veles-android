## ADDED Requirements

### Requirement: Documentation node
The component map SHALL declare a non-release CI node `docs` that owns `docs/`, `openspec/`, `AGENTS.md`, `.agents/`, `.claude/`, `opencode.json`, `.gitattributes`, `.github/CODEOWNERS` (through `global-overrides`), `README.md`, `BUILD.md`, `CONTRIBUTING.md` and `native-bridge/AGENTS.md`, enumerated without wildcards. Every other nested `AGENTS.md` MUST be listed in `global-overrides` mapped to `docs` (`native-bridge/host/AGENTS.md` to `docs` and `native-host`), so that editing agent guidance never selects a code node. The node MUST have no release tag and the planner MUST NOT version it.

#### Scenario: Documentation ownership
- **WHEN** the map check runs on a tree containing `AGENTS.md`, `.agents/rules/` and `native-bridge/AGENTS.md`
- **THEN** each path is owned by `docs` and none is unaccounted

#### Scenario: Not a release component
- **WHEN** a `feat(docs): …` commit changes only files owned by `docs`
- **THEN** no release component is bumped

#### Scenario: Override does not change release attribution
- **WHEN** a `fix(android): …` commit changes only `android/AGENTS.md`
- **THEN** `android` still gets a patch bump because `global-overrides` change CI selection only, which is why guidance edits are typed `docs:`

#### Scenario: A new nested guidance file
- **WHEN** a tracked `AGENTS.md` exists in a node directory but is not listed in `global-overrides`
- **THEN** the docs check fails and names the file

## MODIFIED Requirements

### Requirement: Component map is the single source of path ownership
The repository SHALL define every release component and CI check node in `.github/components.json`. Paths MUST be repository-relative directory prefixes ending in `/` or exact file names, without wildcards. Release planning and CI change selection MUST read this file and no other path list. A node MAY declare `ci-paths` (inputs that select it in CI and never count toward its release), `release-paths` (inputs that count toward its release and never select it in CI) and the expected check names `jobs`; the map MAY declare `global-overrides` (an exact path mapped to the nodes it selects in CI instead of every node, or, for a path some node owns, instead of the nodes that own it, without changing release attribution), and `basename-owners` (a file basename mapped to nodes that are additionally selected in CI whenever a file with that basename changes, in any directory, without changing release attribution).

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
- **WHEN** a `fix(ci)` commit changes only `.github/components.json`, which `global-overrides` maps to `tooling` and `docs`
- **THEN** only `tooling` and `docs` are selected in CI, while the path still counts as a global release path

#### Scenario: Basename owner
- **WHEN** a PR adds `rust/new-dir/AGENTS.md` or `android/CLAUDE.md`, whose basenames `basename-owners` maps to `docs`
- **THEN** `docs` is selected in addition to the nodes whose paths contain the file, so the docs check can reject a missing override or a forbidden `CLAUDE.md` on the pull request itself

#### Scenario: Override of an owned file
- **WHEN** `protocol/AGENTS.md`, which the `protocol` node's `protocol/` prefix owns, is mapped by `global-overrides` to `docs`
- **THEN** a `docs:` commit changing only that file selects only `docs` in CI and does not bump any component

### Requirement: Global and unowned paths
The component map SHALL list `global` paths (workflows, the Gradle wrapper, `gradle/` wrapper files and `.java-version`) that belong to every node, and `unowned` paths that belong to none. Root build files (`build.gradle.kts`, `settings.gradle.kts`, `gradle.properties`) MUST NOT be global; they MUST be paths of every non-Android node except `docs`, whose checks are plain Python and do not use Gradle. Every tracked top-level directory and file MUST be owned by a node, global, or unowned.

#### Scenario: Root build file belongs to every component
- **WHEN** a `fix(build): …` commit changes only `gradle/wrapper/gradle-wrapper.properties`
- **THEN** every CI node is selected and every release component gets a patch bump

#### Scenario: Root build file belongs to every non-Android component
- **WHEN** a `fix(build): …` commit changes only `build.gradle.kts`
- **THEN** every CI node except `android` and `docs` is selected and `web-extension` and `installer` get a patch bump while `android` does not

#### Scenario: Docs node ignores root build files
- **WHEN** a PR changes only `gradle.properties` or `settings.gradle.kts`
- **THEN** the `docs` node is not selected

#### Scenario: Unaccounted path
- **WHEN** a new top-level directory is added that is not owned, global or unowned
- **THEN** the map check run by the CI `changes` job fails and names the directory

#### Scenario: Cross-directory import
- **WHEN** a file under `verify/lib/` changes
- **THEN** the `web-extension` node is selected, because `web-extension/scripts/package.mjs` imports it

### Requirement: Retired paths are accounted for
The component map SHALL support `retired-paths`, mapping a path prefix or exact file name that no longer exists after a move or removal to the nodes it selects. A changed path under a retired prefix or equal to a retired file MUST count as accounted for ownership validation and MUST select the listed nodes in CI. A `retired-paths` entry MUST NOT by itself make a path a release input; release attribution stays determined by `paths`, `release-paths`, `global` and `legacy-release`.

#### Scenario: Migration pull request
- **WHEN** the pull request that moves `app/` to `android/` deletes files under `app/` and `config/detekt/`
- **THEN** those paths are accounted and `android` is selected

#### Scenario: Retired CI-only prefix
- **WHEN** a commit touches only `config/detekt/`, which `retired-paths` lists and which was never a release input
- **THEN** it does not count toward any component's next version

#### Scenario: Retired prefix that is also a legacy path
- **WHEN** an old-layout `feat` commit touches only `app/`, which is both retired and a legacy release path of `android`
- **THEN** it is attributed to `android` through `legacy-release`, not through `retired-paths`

#### Scenario: Removed agent files
- **WHEN** the pull request that adopts `AGENTS.md` deletes `CLAUDE.md`, `CONTRIBUTION.md` and the files under `.opencode/`
- **THEN** each deleted path is accounted for through a `retired-paths` entry that selects `docs`, and none of them is a release input
