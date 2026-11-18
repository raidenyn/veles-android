## MODIFIED Requirements

### Requirement: Documentation node
The component map SHALL declare a non-release CI node `docs` that owns `docs/`, `openspec/`, `AGENTS.md`, `.agents/`, `.claude/`, `opencode.json`, `.gitattributes`, `.github/CODEOWNERS` (through `global-overrides`), `README.md`, `BUILD.md`, `CONTRIBUTING.md`, `docs.json`, `.mintignore` and `native-bridge/AGENTS.md`, enumerated without wildcards. Every other nested `AGENTS.md` MUST be listed in `global-overrides` mapped to `docs` (`native-bridge/host/AGENTS.md` to `docs` and `native-host`), so that editing agent guidance never selects a code node. The node MUST have no release tag and the planner MUST NOT version it.

#### Scenario: Documentation ownership
- **WHEN** the map check runs on a tree containing `AGENTS.md`, `.agents/rules/` and `native-bridge/AGENTS.md`
- **THEN** each path is owned by `docs` and none is unaccounted

#### Scenario: Documentation site files
- **WHEN** a PR changes only `docs.json` or `.mintignore`
- **THEN** only the `docs` node is selected

#### Scenario: Not a release component
- **WHEN** a `feat(docs): …` commit changes only files owned by `docs`
- **THEN** no release component is bumped

#### Scenario: Override does not change release attribution
- **WHEN** a `fix(android): …` commit changes only `android/AGENTS.md`
- **THEN** `android` still gets a patch bump because `global-overrides` change CI selection only, which is why guidance edits are typed `docs:`

#### Scenario: A new nested guidance file
- **WHEN** a tracked `AGENTS.md` exists in a node directory but is not listed in `global-overrides`
- **THEN** the docs check fails and names the file
