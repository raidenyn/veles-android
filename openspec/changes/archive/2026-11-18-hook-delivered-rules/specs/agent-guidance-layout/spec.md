## MODIFIED Requirements

### Requirement: Root guidance is one minimal AGENTS.md
The repository SHALL provide a root `AGENTS.md` that states what Veles is, lists a node map telling agents to read `<dir>/AGENTS.md` before changing files under `<dir>/`, and points at the repo-wide rules. It MUST also carry the generated hook-delivered rules pointer block that the `hooked-rule-delivery` capability defines. It MUST NOT exceed 120 lines, SHOULD stay at or below 60, and MUST NOT contain Android, macOS or Windows specific content.

#### Scenario: Line cap
- **WHEN** the root `AGENTS.md` has 121 lines
- **THEN** the agent-file check fails; at 61 to 120 lines it passes with a warning

#### Scenario: No platform content at the root
- **WHEN** the root `AGENTS.md` names a Gradle task, a keystore, a code-signing identity or a Windows installer step
- **THEN** a reviewer or the check rejects it and the fact belongs in the owning node file

#### Scenario: Node map
- **WHEN** an agent works in a tool that does not load nested guidance files (Antigravity)
- **THEN** the root node map still tells it which `AGENTS.md` to read before editing that folder

#### Scenario: Pointer block present
- **WHEN** `.agents/hooked-rules/` holds a rule and the root `AGENTS.md` has no pointer block, or a stale one
- **THEN** the agent-file check fails

### Requirement: Rules live in .agents/rules, scoped by path
The repository SHALL keep every always-on and path-scoped rule as one concern per file in a single flat directory `.agents/rules/` (no subdirectories), named `<scope>-<topic>.md` where `<scope>` is `repo` or a node or platform id, each at most 24,000 bytes and each `repo-*` rule at most 4,096 bytes (the always-on context budget; the target is 2 KB), with frontmatter keys limited to `trigger`, `globs` and `paths`. A `repo-*` rule MUST have frontmatter `trigger: always_on` and MUST NOT have `globs` or `paths`. A node rule MUST have `trigger: glob`, `globs` and `paths` as identical, non-empty YAML lists of patterns under the scope's folder (`android/**`, `web-extension/**`, `rust/**`, `protocol/**`, `native-bridge/**`, `native-bridge/host/**`, `native-bridge/installer/**`, `native-bridge/installer/macos/**`, `native-bridge/installer/windows/**`, `verify/**`, `scripts/**`, `docs/**` for the scopes of the same names; no brace expansion and no `..` path segment), so that an agent loads it only after reading or editing a file in a listed folder. A rule consumed by several sibling nodes MUST be named for their narrowest common parent scope and list only the exact consuming patterns (for example `native-bridge-<topic>.md` with `native-bridge/installer/macos/**` and `native-bridge/host/**`), so an unrelated sibling such as `native-bridge/installer/windows/**` does not load it. Hook-delivered rules are not rules of this directory: they live in `.agents/hooked-rules/` as the `hooked-rule-delivery` capability defines, and the size, key and frontmatter requirements of this requirement apply only to `.agents/rules/`. The initial `repo-*` rules MUST be: no time-based unit tests (no sleeps or wall-clock dependence, inject a clock); before pushing, run the checks listed in the `AGENTS.md` of every node you touched, without platform specifics; and never regenerate, replace or commit signing keys, with details left to the node files. The `repo-*` rules MUST also include four rules for the superspec workflow, one concern each: the reviewer roster (`repo-review-models.md`), the review gate and apply stop (`repo-superspec-review-gate.md`), tracker intake and board status (`repo-tracker-intake.md`) and the closeout (`repo-superspec-closeout.md`). Each of those MUST state only non-negotiable steps and, where it has procedure detail, MUST open with an instruction to read the named section of `docs/superspec-workflow.md` before acting, and the root `AGENTS.md` MUST list all eight `repo-*` rules. An eighth `repo-*` rule, `repo-real-device-testing.md`, requires real-environment testing (capability `real-device-testing`). The initial node rule MUST be `android-lint-before-push.md` carrying the Android PR lint gate. Each node `AGENTS.md` MUST list its rule files. `.claude/rules` MUST be a symlink to `.agents/rules`.

#### Scenario: Repo-wide rule frontmatter
- **WHEN** a `repo-*` rule in `.agents/rules/` lacks `trigger: always_on` or has `globs` or `paths`, or any rule in `.agents/rules/` is over 24,000 bytes, a `repo-*` rule there is over 4,096 bytes, or any rule is in a subdirectory of `.agents/rules/`
- **THEN** the agent-file check fails

#### Scenario: Repo rule size budget
- **WHEN** a `repo-*` rule in `.agents/rules/` is exactly 4,096 bytes
- **THEN** the agent-file check passes, and at 4,097 bytes it fails and names the file

#### Scenario: Node rule size
- **WHEN** a node rule in `.agents/rules/` is exactly 24,000 bytes (well over the `repo-*` cap)
- **THEN** the agent-file check passes, and at 24,001 bytes it fails and names the file

#### Scenario: One size finding
- **WHEN** a `repo-*` rule in `.agents/rules/` is 24,001 bytes
- **THEN** the agent-file check reports one size finding for it, not two

#### Scenario: Workflow rules listed
- **WHEN** the root `AGENTS.md` omits any of the four superspec workflow rules
- **THEN** the agent-file check fails and names the unlisted rule

#### Scenario: Procedure pointer
- **WHEN** one of the three workflow rules other than the roster does not open with the instruction to read its section of `docs/superspec-workflow.md`
- **THEN** the plan's verification (a grep for that opening sentence) fails

#### Scenario: Node rule frontmatter
- **WHEN** `android-lint-before-push.md` has `globs` and `paths` that differ, include a brace expansion, include a `..` segment, or list a pattern outside the scope's folder
- **THEN** the agent-file check fails and names the file

#### Scenario: Unknown frontmatter key
- **WHEN** a rule in `.agents/rules/` has a key other than `trigger`, `globs` or `paths` (for example `keywords`)
- **THEN** the agent-file check fails

#### Scenario: Parent scope applies in children
- **WHEN** a `native-bridge-*` rule lists `native-bridge/**`
- **THEN** it also loads inside `native-bridge/host/` and `native-bridge/installer/`, and the rule's text is written for that

#### Scenario: Multi-node rule patterns
- **WHEN** a `native-bridge-*` rule lists `native-bridge/installer/macos/**` and `native-bridge/host/**`
- **THEN** the agent-file check accepts it, and reading a file under `native-bridge/installer/windows/` does not load it

#### Scenario: Rule listed by its node
- **WHEN** a rule file exists that its scope's `AGENTS.md` does not list, or a listed rule file does not exist
- **THEN** the agent-file check fails and names it

#### Scenario: Scope matches the name
- **WHEN** a rule named `rust-*.md` is scoped to `android/**`
- **THEN** the agent-file check fails

#### Scenario: Android rule only for Android
- **WHEN** an agent has read only files under `rust/` in a session
- **THEN** `android-lint-before-push.md` is not in its context, and after it reads a file under `android/` it is

#### Scenario: Repo rules always present
- **WHEN** an agent starts a session in the repository and has read no file
- **THEN** every `repo-*` rule in `.agents/rules/` is in its context, and no file under `.agents/hooked-rules/` is

#### Scenario: Rules reach each tool
- **WHEN** each of Claude Code, OpenCode and Antigravity runs the scoping scenarios above
- **THEN** Claude Code loads rules through `.claude/rules` and `paths`, OpenCode through the pinned `opencode-rules` plugin configured in `opencode.json`, and Antigravity through `.agents/rules` with `trigger: glob`, and the verification record states how each was observed

#### Scenario: Pre-push rule has no platform detail
- **WHEN** the `repo-` pre-push rule is read
- **THEN** it names no Gradle, Cargo or npm command, tells the agent to find the touched nodes with `scripts/components.py changed` (including nodes selected by shared inputs) and to run the checks listed in each node's `AGENTS.md`

#### Scenario: Real-device rule listed
- **WHEN** the root `AGENTS.md` omits `.agents/rules/repo-real-device-testing.md`
- **THEN** the agent-file check fails and names the unlisted rule

#### Scenario: Conventional Commits rule moved
- **WHEN** `.agents/rules/repo-conventional-commits.md` exists, or the root `AGENTS.md` lists it among the `repo-*` rules
- **THEN** the agent-file check fails, because the commit-type rule lives at `.agents/hooked-rules/repo-commit-types.md`

### Requirement: OpenCode scoped rules use a range-bounded, allow-listed plugin
`opencode.json` SHALL contain only the keys `$schema` and `plugins`, and `plugins` SHALL contain exactly two entries: the local hooked-rules plugin in `scripts/opencode-hooked-rules/` (declared with the local-entry syntax that the `hooked-rule-delivery` verification records), and the package `opencode-rules` constrained to the supported range `>=2.0.0 <3` (a caret or inequality range that admits no version below 2.0.0 or at 3.0.0 and above), never `latest`, `*`, an exact pin, or any range crossing the major bound, with `additionalRuleDirectories` containing `.agents/rules`. The root `AGENTS.md`, `CONTRIBUTING.md` and `docs/AGENTS.md` MUST state that OpenCode users need the `opencode-rules` plugin at the range pinned in `opencode.json` for scoped rules to be delivered, without repeating the range, and the qualified plugin range and OpenCode version MUST be recorded where the system-level qualification keeps them; the plugin's installation, package-integrity and transitive-dependency policy and per-version regression tests are outside this change. A `.github/CODEOWNERS` entry MUST name an owner for `opencode.json`, `.claude/`, `.mcp.json` when present, `.agents/`, `**/AGENTS.md`, `/docs/superspec-workflow.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` (the instruction files agents follow and the code their tools run automatically; the entries for `/docs/superspec-workflow.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` MUST name the same owner as the existing agent-guidance entries, which review checks and the agent-file check does not), and contributor guidance MUST tell reviewers not to launch agents in an untrusted PR checkout, or to disable project configuration, before reading those files. If the plugin cannot be used, the root `AGENTS.md` and the node files MUST still make the rule files discoverable.

#### Scenario: Plugin requirement documented
- **WHEN** the root `AGENTS.md` or `CONTRIBUTING.md` does not mention `opencode-rules` as an OpenCode requirement, or `docs/AGENTS.md` lacks the install, update and untrusted-checkout guidance
- **THEN** the agent-file check or review fails, and no document repeats the pinned range

#### Scenario: Supported range accepted
- **WHEN** `opencode.json` lists `opencode-rules@>=2.0.0 <3` or `opencode-rules@^2.0.0` and the local hooked-rules plugin, and nothing else
- **THEN** the agent-file check passes

#### Scenario: Unpinned or out-of-range plugin
- **WHEN** `opencode.json` lists `opencode-rules@latest`, `opencode-rules@*`, an exact pin, or a range that admits a version below 2.0.0 or at 3.0.0 and above
- **THEN** the agent-file check fails

#### Scenario: Additional plugin or key
- **WHEN** `opencode.json` adds a plugin other than `opencode-rules` and the local hooked-rules plugin, an MCP server, a command or any key other than `$schema` and `plugins`
- **THEN** the agent-file check fails

#### Scenario: Rule directory configured
- **WHEN** `opencode.json` omits `.agents/rules` from `additionalRuleDirectories`
- **THEN** the agent-file check fails

#### Scenario: Code-owner coverage
- **WHEN** `.github/CODEOWNERS` lacks an entry for `opencode.json`, `.claude/`, `.agents/`, `**/AGENTS.md`, `/docs/superspec-workflow.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` or `/scripts/opencode-hooked-rules/`
- **THEN** the agent-file check fails

#### Scenario: Plugin unavailable
- **WHEN** OpenCode starts with the plugin unavailable (offline with an empty plugin cache, an install failure or the plugin disabled)
- **THEN** OpenCode still starts, scoped rules are not delivered, and an agent can still find them through the root `AGENTS.md` and the node files' read-the-rule instructions, which the verification record shows

### Requirement: The agent-file check enforces the layout
`docs/checks/check_agent_files.py` SHALL be a standard-library Python script, run by the `docs` node, that fails on each machine-enforceable invariant of this capability and of `openspec-skills-delivery`: the line cap; the node-file and rule-file lists; the legacy files and directories; rule naming, size (24,000 bytes, and 4,096 bytes for `repo-*` rules), key set, per-kind frontmatter and scope patterns; the symlink and skill-parity checks; the `opencode.json` allow-list; the `.agents/hooked-rules/` file format (through the parser imported from `scripts/agent_rules.py`), body size and regex validity; pointer-block freshness; the Claude Code hook wiring in `.claude/settings.json` (each of the four hooks with its exact matcher, the exact engine command and the 5-second timeout; that the engine never emits an `allow` decision is tested through the engine's own output and the host verification, not through this check); the `opencode-rules` requirement mention in the root `AGENTS.md` and `CONTRIBUTING.md`; CODEOWNERS coverage; the start-at-root notice in every `AGENTS.md`; and that every tracked nested `AGENTS.md` that the `docs` node does not already own exclusively (all except `docs/AGENTS.md` and `native-bridge/AGENTS.md`) has its `global-overrides` entry in `.github/components.json`. It MUST NOT be expected to verify behaviour in the three tools, content preservation, or generated-skill content, which verification and review cover. Every enforced invariant MUST have a positive and a negative test under the same node. The size tests MUST share one fixture builder and cover each cap at its boundary (the cap passes, the cap plus one byte fails), using a node-scoped rule for the 24,000-byte cap, and the fixture MUST also list the rule in its node's `AGENTS.md` so the tests assert a single finding. The check MUST scan tracked and untracked-but-not-ignored files so that a nested `CLAUDE.md` is caught while ignored build and vendor trees are not.

#### Scenario: Negative tests
- **WHEN** the check's tests run
- **THEN** there is a failing case for each enforced invariant, including: a nested `CLAUDE.md`, a reappearing `.claude/commands/`, a missing node file, an unlisted nested `AGENTS.md`, an over-long root file, a rule in a subdirectory, an unknown frontmatter key, a `repo-*` rule with `globs`, mismatched `globs` and `paths`, a rule not listed by its node, an unpinned or extra plugin in `opencode.json`, a `repo-*` rule over 4,096 bytes, missing CODEOWNERS coverage (including for each of the procedure doc, `openspec/config.yaml` and `openspec/schemas/`), a missing start-at-root notice, a nested `AGENTS.md` without a `global-overrides` entry, a wrong symlink, a skill without its link, an invalid hooked rule (unknown key, duplicate key, `[]` list, bad regex, no trigger, a body over 4,096 bytes), a stale pointer block, a missing Claude Code hook, a wrong hook matcher or command, a missing timeout, a missing local OpenCode plugin entry, and missing CODEOWNERS coverage of `/scripts/agent_rules.py` or `/scripts/opencode-hooked-rules/`

#### Scenario: Clean tree
- **WHEN** the check runs on the migrated tree
- **THEN** it passes with the line-count warning only if the root file exceeds 60 lines
