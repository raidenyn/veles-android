## MODIFIED Requirements

### Requirement: Platform facts live in one canonical folder
macOS signing identity and TCC rules SHALL live only in the macOS node: `native-bridge/installer/macos/AGENTS.md` owns the canonical identity facts, and the macOS TCC rule it points at is `.agents/rules/native-bridge-test-app-tcc.md`. Windows facts SHALL live only in `native-bridge/installer/windows/AGENTS.md`, and Android signing and architecture only in `android/AGENTS.md`. Cross-platform nodes such as `native-bridge/host/` MUST point to the platform file instead of copying it, and MAY keep the CI artifact names the build-contract gate requires. A macOS TCC rule consumed by both the macOS node and another node under `native-bridge/` MUST be named for their narrowest common parent (`native-bridge-<topic>.md`), MUST list only the exact consuming patterns under that parent (`native-bridge/installer/macos/**` and `native-bridge/host/**`) so an unrelated sibling such as `native-bridge/installer/windows/**` does not load it, and MUST be listed in `native-bridge/AGENTS.md`; a macOS-only rule stays `macos-<topic>.md`, scoped to `native-bridge/installer/macos/**` and listed by that node. The macOS node's `AGENTS.md` MUST still point at the rule.

#### Scenario: Host pointer
- **WHEN** `native-bridge/host/AGENTS.md` mentions macOS signing
- **THEN** it is a one-line pointer to `native-bridge/installer/macos/AGENTS.md` and names `.agents/rules/native-bridge-test-app-tcc.md`, which loads under `native-bridge/host/` through its `native-bridge/host/**` pattern without a by-hand read instruction; the rule text is not copied

#### Scenario: Script citation
- **WHEN** `native-bridge/host/scripts/macos-test-bundle.sh` cites its signing rules
- **THEN** it cites `native-bridge/installer/macos/AGENTS.md`

#### Scenario: macOS rule lives in the node
- **WHEN** a macOS TCC rule file is added or rescoped
- **THEN** it is named for the narrowest common parent and its `globs`/`paths` list only the consuming patterns under that parent (`macos-<topic>.md` with `native-bridge/installer/macos/**` when only that node consumes it; `native-bridge-<topic>.md` with `native-bridge/installer/macos/**` and `native-bridge/host/**` when the host also consumes it), listed by that scope's `AGENTS.md`, and the root rules gain no macOS content

### Requirement: Rules live in .agents/rules, scoped by path
The repository SHALL keep all rules as one concern per file in a single flat directory `.agents/rules/` (no subdirectories), named `<scope>-<topic>.md` where `<scope>` is `repo` or a node or platform id, each at most 24,000 bytes and each `repo-*` rule at most 4,096 bytes (the always-on context budget; the target is 2 KB), with frontmatter keys limited to `trigger`, `globs` and `paths`. A `repo-*` rule MUST have frontmatter `trigger: always_on` and MUST NOT have `globs` or `paths`. A node rule MUST have `trigger: glob`, `globs` and `paths` as identical, non-empty YAML lists of patterns under the scope's folder (`android/**`, `web-extension/**`, `rust/**`, `protocol/**`, `native-bridge/**`, `native-bridge/host/**`, `native-bridge/installer/**`, `native-bridge/installer/macos/**`, `native-bridge/installer/windows/**`, `verify/**`, `scripts/**`, `docs/**` for the scopes of the same names; no brace expansion and no `..` path segment), so that an agent loads it only after reading or editing a file in a listed folder. A rule consumed by several sibling nodes MUST be named for their narrowest common parent scope and list only the exact consuming patterns (for example `native-bridge-<topic>.md` with `native-bridge/installer/macos/**` and `native-bridge/host/**`), so an unrelated sibling such as `native-bridge/installer/windows/**` does not load it. The initial `repo-*` rules MUST be: Conventional Commits; no time-based unit tests (no sleeps or wall-clock dependence, inject a clock); before pushing, run the checks listed in the `AGENTS.md` of every node you touched, without platform specifics; and never regenerate, replace or commit signing keys, with details left to the node files. The `repo-*` rules MUST also include four rules for the superspec workflow, one concern each: the reviewer roster (`repo-review-models.md`), the review gate and apply stop (`repo-superspec-review-gate.md`), tracker intake and board status (`repo-tracker-intake.md`) and the closeout (`repo-superspec-closeout.md`). Each of those MUST state only non-negotiable steps and, where it has procedure detail, MUST open with an instruction to read the named section of `docs/superspec-workflow.md` before acting, and the root `AGENTS.md` MUST list all nine `repo-*` rules. A ninth `repo-*` rule, `repo-real-device-testing.md`, requires real-environment testing (capability `real-device-testing`). The initial node rule MUST be `android-lint-before-push.md` carrying the Android PR lint gate. Each node `AGENTS.md` MUST list its rule files. `.claude/rules` MUST be a symlink to `.agents/rules`.

#### Scenario: Repo-wide rule frontmatter
- **WHEN** a `repo-*` rule lacks `trigger: always_on` or has `globs` or `paths`, or any rule is over 24,000 bytes, a `repo-*` rule is over 4,096 bytes, or any rule is in a subdirectory
- **THEN** the agent-file check fails

#### Scenario: Repo rule size budget
- **WHEN** a `repo-*` rule is exactly 4,096 bytes
- **THEN** the agent-file check passes, and at 4,097 bytes it fails and names the file

#### Scenario: Node rule size
- **WHEN** a node rule is exactly 24,000 bytes (well over the `repo-*` cap)
- **THEN** the agent-file check passes, and at 24,001 bytes it fails and names the file

#### Scenario: One size finding
- **WHEN** a `repo-*` rule is 24,001 bytes
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
- **WHEN** a rule has a key other than `trigger`, `globs` or `paths` (for example `keywords`)
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
- **THEN** every `repo-*` rule is in its context

#### Scenario: Rules reach each tool
- **WHEN** each of Claude Code, OpenCode and Antigravity runs the scoping scenarios above
- **THEN** Claude Code loads rules through `.claude/rules` and `paths`, OpenCode through the pinned `opencode-rules` plugin configured in `opencode.json`, and Antigravity through `.agents/rules` with `trigger: glob`, and the verification record states how each was observed

#### Scenario: Pre-push rule has no platform detail
- **WHEN** the `repo-` pre-push rule is read
- **THEN** it names no Gradle, Cargo or npm command, tells the agent to find the touched nodes with `scripts/components.py changed` (including nodes selected by shared inputs) and to run the checks listed in each node's `AGENTS.md`

#### Scenario: Real-device rule listed
- **WHEN** the root `AGENTS.md` omits `.agents/rules/repo-real-device-testing.md`
- **THEN** the agent-file check fails and names the unlisted rule
