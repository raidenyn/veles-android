## MODIFIED Requirements

### Requirement: Rules live in .agents/rules, scoped by path
The repository SHALL keep every always-on and path-scoped rule as one concern per file in a single flat directory `.agents/rules/` (no subdirectories), named `<scope>-<topic>.md` where `<scope>` is `repo` or a node or platform id, each at most 24,000 bytes and each `repo-*` rule at most 4,096 bytes (the always-on context budget; the target is 2 KB), with frontmatter keys limited to `trigger`, `globs` and `paths`. A `repo-*` rule MUST have frontmatter `trigger: always_on` and MUST NOT have `globs` or `paths`. A node rule MUST have `trigger: glob`, `globs` and `paths` as identical, non-empty YAML lists of patterns under the scope's folder (`android/**`, `web-extension/**`, `rust/**`, `protocol/**`, `native-bridge/**`, `native-bridge/host/**`, `native-bridge/installer/**`, `native-bridge/installer/macos/**`, `native-bridge/installer/windows/**`, `verify/**`, `scripts/**`, `docs/**` for the scopes of the same names; no brace expansion and no `..` path segment), so that an agent loads it only after reading or editing a file in a listed folder. A rule consumed by several sibling nodes MUST be named for their narrowest common parent scope and list only the exact consuming patterns (for example `native-bridge-<topic>.md` with `native-bridge/installer/macos/**` and `native-bridge/host/**`), so an unrelated sibling such as `native-bridge/installer/windows/**` does not load it. Hook-delivered rules are not rules of this directory: they live in `.agents/hooked-rules/` as the `hooked-rule-delivery` capability defines, and the size, key and frontmatter requirements of this requirement apply only to `.agents/rules/`. The always-on `repo-*` rules in `.agents/rules/` MUST be exactly three: no time-based unit tests (no sleeps or wall-clock dependence, inject a clock) (`repo-no-time-based-tests.md`); tracker intake and board status (`repo-tracker-intake.md`); and never regenerate, replace or commit signing keys, with details left to the node files (`repo-signing-keys.md`). The root `AGENTS.md` MUST list all three. Five `repo-*` rules are hook-delivered rules in `.agents/hooked-rules/` (capability `hooked-rule-delivery`), next to `repo-commit-types.md`: before pushing, a complete `python3 -I scripts/verify.py` (task gate) run for HEAD, which the push gate requires, without platform specifics (`repo-pre-push-checks.md`); real-environment testing (`repo-real-device-testing.md`, capability `real-device-testing`); and three rules for the superspec workflow, one concern each: the reviewer roster (`repo-review-models.md`), the review gate and apply stop (`repo-superspec-review-gate.md`) and the closeout (`repo-superspec-closeout.md`). The four superspec workflow rules (those three and tracker intake) MUST each state only non-negotiable steps and, where they have procedure detail, MUST open with an instruction to read the named section of `docs/superspec-workflow.md` before acting; the closeout rule MAY put its apply-to-finalize lead paragraph (capability `superspec-closeout`) before that instruction. A node rule MUST NOT repeat a command the verifier runs, and `.agents/rules/android-lint-before-push.md` MUST NOT exist. `scripts-verify-commands.md` MUST carry the guidance for editing `scripts/verify-commands.json`, scoped to that file only, and point to `docs/verify.md`. Each node `AGENTS.md` MUST list its rule files. `.claude/rules` MUST be a symlink to `.agents/rules`.

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
- **WHEN** the root `AGENTS.md` omits the always-on tracker-intake rule or another of the three always-on `repo-*` rules, the pointer block lacks a line for one of the three hooked workflow rules, or `.agents/rules/` holds a `repo-*` rule other than those three
- **THEN** the agent-file check fails and names the rule

#### Scenario: Moved rule left behind
- **WHEN** `.agents/rules/repo-superspec-closeout.md`, `repo-superspec-review-gate.md`, `repo-review-models.md`, `repo-real-device-testing.md` or `repo-pre-push-checks.md` exists
- **THEN** the agent-file check fails, because the rule lives in `.agents/hooked-rules/`

#### Scenario: Procedure pointer
- **WHEN** one of the three workflow rules other than the roster (closeout and review gate in `.agents/hooked-rules/`, tracker intake in `.agents/rules/`) lacks the instruction to read its section of `docs/superspec-workflow.md` at its opening (for the closeout, right after its lead paragraph)
- **THEN** the plan's verification (a grep for that opening sentence) fails

#### Scenario: Node rule frontmatter
- **WHEN** `native-bridge-test-app-tcc.md` has `globs` and `paths` that differ, include a brace expansion, include a `..` segment, or list a pattern outside the scope's folder
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
- **WHEN** an agent has read only files under `rust/` in a session, and then reads a file under `native-bridge/host/`
- **THEN** no node rule is in its context before the second read, `native-bridge-test-app-tcc.md` is after it, and no Android rule exists to load under `android/`

#### Scenario: Repo rules always present
- **WHEN** an agent starts a session in the repository and has read no file
- **THEN** the three `repo-*` rules in `.agents/rules/` are in its context, and no file under `.agents/hooked-rules/` is

#### Scenario: Rules reach each tool
- **WHEN** each of Claude Code, OpenCode and Antigravity runs the scoping scenarios above
- **THEN** Claude Code loads rules through `.claude/rules` and `paths`, OpenCode through the pinned `opencode-rules` plugin configured in `opencode.json`, and Antigravity through `.agents/rules` with `trigger: glob`, and the verification record states how each was observed

#### Scenario: Pre-push rule has no platform detail
- **WHEN** `.agents/hooked-rules/repo-pre-push-checks.md` is read
- **THEN** it names no Gradle, Cargo or npm command, tells the agent to commit first and run `python3 -I scripts/verify.py` in the background and wait for it, and holds no by-hand fallback to node checks

#### Scenario: Real-device rule listed
- **WHEN** `.agents/hooked-rules/repo-real-device-testing.md` exists and the root pointer block has no line for it
- **THEN** the agent-file check fails as a stale pointer block

#### Scenario: Conventional Commits rule moved
- **WHEN** `.agents/rules/repo-conventional-commits.md` exists, or the root `AGENTS.md` lists it among the `repo-*` rules
- **THEN** the agent-file check fails, because the commit-type rule lives at `.agents/hooked-rules/repo-commit-types.md`

#### Scenario: Command-table rule
- **WHEN** an agent reads or edits `scripts/verify-commands.json`
- **THEN** `.agents/rules/scripts-verify-commands.md` is in its context, describes the one-way duplicate check, `scripts/AGENTS.md` lists it, and it points to `docs/verify.md`; reading other files under `scripts/` does not load it
