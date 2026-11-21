## MODIFIED Requirements

### Requirement: One AGENTS.md per node and platform folder
Each of `android/`, `web-extension/`, `rust/`, `protocol/`, `native-bridge/`, `native-bridge/host/`, `native-bridge/installer/`, `native-bridge/installer/macos/`, `native-bridge/installer/windows/`, `verify/`, `scripts/` and `docs/` SHALL contain an `AGENTS.md` holding that scope's purpose, commands, constraints, links to the `docs/` page(s) that hold its architecture and procedures, its `## Rules` section, the start-at-root notice this capability requires, any content another capability pins to that file (the `(schema version v5)` text and the short hook-delivered-rules section in `docs/AGENTS.md`), and the tokens a CI gate requires (for example the host task and artifact names in `native-bridge/host/AGENTS.md`). It MUST NOT hold other narrative, and MUST NOT exceed 40 lines. Its commands MUST be only those the verifier does not run as a `check` or `test` (build, release, device, platform, producer and targeted lines); a command the verifier runs MUST NOT be repeated there or in a rule it lists (capability `affected-node-verification`, The table cannot drift from the docs). `rust/AGENTS.md` and `protocol/AGENTS.md` MUST state that Android CI also runs for their changes. `native-bridge/AGENTS.md` MUST hold only scaffold content and state that Tauri is not the approved production path.

#### Scenario: Android-only content
- **WHEN** an agent reads the guidance for `web-extension/`
- **THEN** it contains no Android architecture, signing or Gradle lint instruction

#### Scenario: Android lint rule
- **WHEN** an agent works under `android/`
- **THEN** `android/AGENTS.md` lists no lint rule and documents neither `./gradlew spotlessCheck detekt lintDebug` nor the source-guard scripts; those remain `check` commands of the `android` node in `scripts/verify-commands.json`, so both gates run them, and `.agents/rules/android-lint-before-push.md` does not exist

#### Scenario: Raw Log guard keeps its behaviour
- **WHEN** `android/scripts/check-raw-log.sh` runs from the repository root
- **THEN** it scans the Kotlin sources of both `android/app/src/main` and `android/app/src/debug`, ignores `LogSink.kt`, prints the same `::error::` lines, exits 0 on a clean scan and exits 1 on a fully-qualified `android.util.Log` reference, a wildcard `android.util` import or a bare `Log.d/w/e/i/v` call, and `verify/test/workflow-contracts.test.mjs` pins the workflow's invocation, its repository-root working directory and that scan scope

#### Scenario: Shared Rust change
- **WHEN** an agent edits `rust/` or `protocol/`
- **THEN** their `AGENTS.md` tells it that Android CI also runs for the change, names no Android lint rule, and the verifier selects the `android` node for it

#### Scenario: Node map and files agree
- **WHEN** a tracked nested `AGENTS.md` is missing from the root node map, or the map lists a file that does not exist
- **THEN** the agent-file check fails and names it

#### Scenario: Nested line cap
- **WHEN** a tracked nested `AGENTS.md` has 41 lines
- **THEN** the agent-file check fails and names it; exactly 40 lines passes

### Requirement: Rules live in .agents/rules, scoped by path
The repository SHALL keep every always-on and path-scoped rule as one concern per file in a single flat directory `.agents/rules/` (no subdirectories), named `<scope>-<topic>.md` where `<scope>` is `repo` or a node or platform id, each at most 24,000 bytes and each `repo-*` rule at most 4,096 bytes (the always-on context budget; the target is 2 KB), with frontmatter keys limited to `trigger`, `globs` and `paths`. A `repo-*` rule MUST have frontmatter `trigger: always_on` and MUST NOT have `globs` or `paths`. A node rule MUST have `trigger: glob`, `globs` and `paths` as identical, non-empty YAML lists of patterns under the scope's folder (`android/**`, `web-extension/**`, `rust/**`, `protocol/**`, `native-bridge/**`, `native-bridge/host/**`, `native-bridge/installer/**`, `native-bridge/installer/macos/**`, `native-bridge/installer/windows/**`, `verify/**`, `scripts/**`, `docs/**` for the scopes of the same names; no brace expansion and no `..` path segment), so that an agent loads it only after reading or editing a file in a listed folder. A rule consumed by several sibling nodes MUST be named for their narrowest common parent scope and list only the exact consuming patterns (for example `native-bridge-<topic>.md` with `native-bridge/installer/macos/**` and `native-bridge/host/**`), so an unrelated sibling such as `native-bridge/installer/windows/**` does not load it. Hook-delivered rules are not rules of this directory: they live in `.agents/hooked-rules/` as the `hooked-rule-delivery` capability defines, and the size, key and frontmatter requirements of this requirement apply only to `.agents/rules/`. The always-on `repo-*` rules in `.agents/rules/` MUST be exactly three: no time-based unit tests (no sleeps or wall-clock dependence, inject a clock) (`repo-no-time-based-tests.md`); tracker intake and board status (`repo-tracker-intake.md`); and never regenerate, replace or commit signing keys, with details left to the node files (`repo-signing-keys.md`). The root `AGENTS.md` MUST list all three. Five `repo-*` rules are hook-delivered rules in `.agents/hooked-rules/` (capability `hooked-rule-delivery`), next to `repo-commit-types.md`: before pushing, a complete `python3 -I scripts/verify.py --branch` run for HEAD, which the push gate requires, without platform specifics (`repo-pre-push-checks.md`); real-environment testing (`repo-real-device-testing.md`, capability `real-device-testing`); and three rules for the superspec workflow, one concern each: the reviewer roster (`repo-review-models.md`), the review gate and apply stop (`repo-superspec-review-gate.md`) and the closeout (`repo-superspec-closeout.md`). The four superspec workflow rules (those three and tracker intake) MUST each state only non-negotiable steps and, where they have procedure detail, MUST open with an instruction to read the named section of `docs/superspec-workflow.md` before acting; the closeout rule MAY put its apply-to-finalize lead paragraph (capability `superspec-closeout`) before that instruction. A node rule MUST NOT repeat a command the verifier runs, and `.agents/rules/android-lint-before-push.md` MUST NOT exist. `scripts-verify-commands.md` MUST carry the guidance for editing `scripts/verify-commands.json`, scoped to that file only, and point to `docs/verify.md`. Each node `AGENTS.md` MUST list its rule files. `.claude/rules` MUST be a symlink to `.agents/rules`.

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
- **THEN** it names no Gradle, Cargo or npm command, tells the agent to commit first and run `python3 -I scripts/verify.py --branch` in the background and wait for it, and holds no by-hand fallback to node checks

#### Scenario: Real-device rule listed
- **WHEN** `.agents/hooked-rules/repo-real-device-testing.md` exists and the root pointer block has no line for it
- **THEN** the agent-file check fails as a stale pointer block

#### Scenario: Conventional Commits rule moved
- **WHEN** `.agents/rules/repo-conventional-commits.md` exists, or the root `AGENTS.md` lists it among the `repo-*` rules
- **THEN** the agent-file check fails, because the commit-type rule lives at `.agents/hooked-rules/repo-commit-types.md`

#### Scenario: Command-table rule
- **WHEN** an agent reads or edits `scripts/verify-commands.json`
- **THEN** `.agents/rules/scripts-verify-commands.md` is in its context, describes the one-way duplicate check, `scripts/AGENTS.md` lists it, and it points to `docs/verify.md`; reading other files under `scripts/` does not load it

### Requirement: The agent-file check enforces the layout
`docs/checks/check_agent_files.py` SHALL be a standard-library Python script, run by the `docs` node, that fails on each machine-enforceable invariant of this capability and of `openspec-skills-delivery`: the 40-line cap on the root and every tracked nested `AGENTS.md`; the node-file and rule-file lists; the legacy files and directories; rule naming, size (24,000 bytes, and 4,096 bytes for `repo-*` rules), key set, per-kind frontmatter and scope patterns; the symlink and skill-parity checks, reading the expected skill list from `docs/openspec-skills.md`; the `opencode.json` allow-list; the `.agents/hooked-rules/` file format (through the parser imported from `scripts/agent_rules.py`), body size, regex validity, stop-predicate and proof names, and the `requires`/`exempt` constraints; pointer-block freshness; the delivery budget of the `hooked-rule-delivery` capability; the three always-on `repo-*` rules; the Claude Code hook wiring in `.claude/settings.json` (each of the five hooks with its exact matcher, no `SubagentStop` hook, the exact engine command and the 5-second timeout, and in `Stop` only the verify command after it with the 1,800-second timeout; that the engine never emits an `allow` decision is tested through the engine's own output and the host verification, not through this check); the `opencode-rules` requirement mention in the root `AGENTS.md` and `CONTRIBUTING.md`; CODEOWNERS coverage; the start-at-root notice in every `AGENTS.md`; and that every tracked nested `AGENTS.md` that the `docs` node does not already own exclusively (all except `docs/AGENTS.md` and `native-bridge/AGENTS.md`) has its `global-overrides` entry in `.github/components.json`; that `scripts/verify-commands.json` has its `global-overrides` entry selecting exactly `docs` and `tooling`; and the command-table duplicate check of the `affected-node-verification` capability, in the sibling module `docs/checks/verify_table.py`. It MUST NOT be expected to verify behaviour in the three tools, content preservation, or generated-skill content, which verification and review cover. Every enforced invariant MUST have a positive and a negative test under the same node. The line-cap tests MUST cover the root and a nested file at 40 and 41 lines, and the size tests MUST share one fixture builder and cover each cap at its boundary (the cap passes, the cap plus one byte fails), using a node-scoped rule for the 24,000-byte cap, and the fixture MUST also list the rule in its node's `AGENTS.md` so the tests assert a single finding. The check MUST scan tracked and untracked-but-not-ignored files so that a nested `CLAUDE.md` is caught while ignored build and vendor trees are not.

#### Scenario: Negative tests
- **WHEN** the check's tests run
- **THEN** there is a failing case for each enforced invariant, including: a nested `CLAUDE.md`, a reappearing `.claude/commands/`, a missing node file, an unlisted nested `AGENTS.md`, an over-long root file, an over-long nested file, a rule in a subdirectory, an unknown frontmatter key, a `repo-*` rule with `globs`, mismatched `globs` and `paths`, a rule not listed by its node, an unpinned or extra plugin in `opencode.json`, a `repo-*` rule over 4,096 bytes, missing CODEOWNERS coverage (including for each of the procedure doc, `docs/agent-guidance.md`, `docs/openspec-workflow.md`, `docs/opencode-rules.md`, `openspec/config.yaml` and `openspec/schemas/`), a missing start-at-root notice, a nested `AGENTS.md` without a `global-overrides` entry, a wrong symlink, a skill without its link, an invalid hooked rule (unknown key, duplicate key, `[]` list, bad regex, no trigger, a body over 4,096 bytes), a stale pointer block, a missing Claude Code hook (the `Stop` hook included), a wired `SubagentStop` hook, a wrong hook matcher or command, a missing timeout, an unknown stop predicate, a trigger over the delivery budget, a moved rule left in `.agents/rules/`, a missing local OpenCode plugin entry, a plugin range repeated in `docs/AGENTS.md`, `docs/agent-guidance.md` or `docs/opencode-rules.md`, missing CODEOWNERS coverage of `/scripts/agent_rules.py` or `/scripts/opencode-hooked-rules/`, a `Stop` entry without the verify command or with a third command, a missing or wrong `scripts/verify-commands.json` override, each duplicate-check finding, an unknown proof name, `requires` without `gates`, `exempt` without `requires`, and a leftover `doc` or `excluded` key in the table

#### Scenario: Clean tree
- **WHEN** the check runs on the migrated tree
- **THEN** it passes with no line-count finding
