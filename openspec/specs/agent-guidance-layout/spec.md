# agent-guidance-layout Specification

## Purpose
This capability defines where agent instructions, rules and skills live for this repository: a minimal root `AGENTS.md`, one `AGENTS.md` per node and platform folder, path-scoped rules in `.agents/rules/`, the small set of allowed symlinks, and the machine check that keeps that layout from drifting. It also records what each of Claude Code, OpenCode and Antigravity reads, so a layout that silently loads nothing in one tool cannot ship.

## Requirements

### Requirement: Root guidance is one minimal AGENTS.md
The repository SHALL provide a root `AGENTS.md` that states what Veles is, lists a node map telling agents to read `<dir>/AGENTS.md` before changing files under `<dir>/`, and points at the repo-wide rules. It MUST also carry the generated hook-delivered rules pointer block that the `hooked-rule-delivery` capability defines. It MUST NOT exceed 40 lines, and MUST NOT contain Android, macOS or Windows specific content. Tool versions, checkout setup and the OpenSpec CLI range MUST live in `CONTRIBUTING.md` and the docs pages the root links; the root MAY keep the one-sentence `opencode-rules` requirement and the `/init` / parent-`CLAUDE.md` warning.

#### Scenario: Line cap
- **WHEN** the root `AGENTS.md` has 41 lines
- **THEN** the agent-file check fails; exactly 40 lines passes with no line-count finding

#### Scenario: No platform content at the root
- **WHEN** the root `AGENTS.md` names a Gradle task, a keystore, a code-signing identity or a Windows installer step
- **THEN** a reviewer or the check rejects it and the fact belongs in the owning node file or the docs page it links

#### Scenario: Node map
- **WHEN** an agent works in a tool that does not load nested guidance files (Antigravity)
- **THEN** the root node map still tells it which `AGENTS.md` to read before editing that folder

#### Scenario: Pointer block present
- **WHEN** `.agents/hooked-rules/` holds a rule and the root `AGENTS.md` has no pointer block, or a stale one
- **THEN** the agent-file check fails

### Requirement: One AGENTS.md per node and platform folder
Each of `android/`, `web-extension/`, `rust/`, `protocol/`, `native-bridge/`, `native-bridge/host/`, `native-bridge/installer/`, `native-bridge/installer/macos/`, `native-bridge/installer/windows/`, `verify/`, `scripts/` and `docs/` SHALL contain an `AGENTS.md` holding that scope's purpose, commands, constraints, links to the `docs/` page(s) that hold its architecture and procedures, its `## Rules` section, the start-at-root notice this capability requires, any content another capability pins to that file (the `(schema version v5)` text and the short hook-delivered-rules section in `docs/AGENTS.md`), and the tokens a CI gate requires (for example the host task and artifact names in `native-bridge/host/AGENTS.md`). It MUST NOT hold other narrative, and MUST NOT exceed 40 lines. `rust/AGENTS.md` and `protocol/AGENTS.md` MUST state that Android CI also runs for their changes and instruct the agent to read `.agents/rules/android-lint-before-push.md`. `native-bridge/AGENTS.md` MUST hold only scaffold content and state that Tauri is not the approved production path.

#### Scenario: Android-only content
- **WHEN** an agent reads the guidance for `web-extension/`
- **THEN** it contains no Android architecture, signing or Gradle lint instruction

#### Scenario: Android lint rule
- **WHEN** an agent works under `android/`
- **THEN** `android/AGENTS.md` lists `android-lint-before-push.md`, whose scoped rule tells it to run `./gradlew spotlessCheck detekt lintDebug` and the raw `android.util.Log` guard from `check-android.yml` before pushing

#### Scenario: Shared Rust change
- **WHEN** an agent edits `rust/` or `protocol/`
- **THEN** their `AGENTS.md` tells it that the Android checks also apply and instructs it to read `.agents/rules/android-lint-before-push.md`, not merely to see the Android file

#### Scenario: Node map and files agree
- **WHEN** a tracked nested `AGENTS.md` is missing from the root node map, or the map lists a file that does not exist
- **THEN** the agent-file check fails and names it

#### Scenario: Nested line cap
- **WHEN** a tracked nested `AGENTS.md` has 41 lines
- **THEN** the agent-file check fails and names it; exactly 40 lines passes

### Requirement: Platform facts live in one canonical folder
macOS signing identity and TCC rules SHALL live only in the macOS node: `native-bridge/installer/macos/AGENTS.md` owns the canonical identity statement and the rules it applies, and the macOS TCC rule it points at is `.agents/rules/native-bridge-test-app-tcc.md`. Narrative history MAY live in `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md`, which the node links; the identity rules (no ad-hoc substitution, no key export, no TCC reset), the exact test-bundle identifier, the no-fallback rule, signing and notarization only in `publish-release.yml`, and the `## Signing identity and TCC (macOS)` heading MUST stay in the node. Windows facts SHALL live only in `native-bridge/installer/windows/AGENTS.md`. Android signing constraints SHALL live only in `android/AGENTS.md`: the fixture build steps live in the linked `docs/android.md`, while every signing constraint and the fixture keystore and init-script names stay in the node, under the `## Signing identities (Android)` heading. Cross-platform nodes such as `native-bridge/host/` MUST point to the platform file instead of copying it, and MAY keep the CI artifact names the build-contract gate requires. A macOS TCC rule consumed by both the macOS node and another node under `native-bridge/` MUST be named for their narrowest common parent (`native-bridge-<topic>.md`), MUST list only the exact consuming patterns under that parent (`native-bridge/installer/macos/**` and `native-bridge/host/**`) so an unrelated sibling such as `native-bridge/installer/windows/**` does not load it, and MUST be listed in `native-bridge/AGENTS.md`; a macOS-only rule stays `macos-<topic>.md`, scoped to `native-bridge/installer/macos/**` and listed by that node. The macOS node's `AGENTS.md` MUST still point at the rule.

#### Scenario: Host pointer
- **WHEN** `native-bridge/host/AGENTS.md` mentions macOS signing
- **THEN** it is a one-line pointer to `native-bridge/installer/macos/AGENTS.md` and names `.agents/rules/native-bridge-test-app-tcc.md`, which loads under `native-bridge/host/` through its `native-bridge/host/**` pattern without a by-hand read instruction; the rule text is not copied

#### Scenario: Script citation
- **WHEN** `native-bridge/host/scripts/macos-test-bundle.sh` cites its signing rules
- **THEN** it cites `native-bridge/installer/macos/AGENTS.md`

#### Scenario: macOS narrative in docs
- **WHEN** an agent reads `native-bridge/installer/macos/AGENTS.md`
- **THEN** it finds the canonical identity and TCC rules in the node under the cited heading, and the node links `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md` for history; this change adds no new copy of the rules to those pages

#### Scenario: macOS rule lives in the node
- **WHEN** a macOS TCC rule file is added or rescoped
- **THEN** it is named for the narrowest common parent and its `globs`/`paths` list only the consuming patterns under that parent (`macos-<topic>.md` with `native-bridge/installer/macos/**` when only that node consumes it; `native-bridge-<topic>.md` with `native-bridge/installer/macos/**` and `native-bridge/host/**` when the host also consumes it), listed by that scope's `AGENTS.md`, and the root rules gain no macOS content

### Requirement: Legacy agent files do not exist
The repository SHALL NOT contain `CONTRIBUTION.md`, `.opencode/` or `.claude/commands/`, and MUST NOT contain a `CLAUDE.md`, `CLAUDE.local.md` or `.claude/CLAUDE.md` anywhere, because any of them hides `AGENTS.md` from Claude Code. `CONTRIBUTING.md` MUST remain the single contribution guide.

#### Scenario: Nested CLAUDE.md
- **WHEN** a tool such as `/init` creates `android/CLAUDE.md`
- **THEN** the agent-file check fails and names the file

#### Scenario: Legacy directory returns
- **WHEN** `.claude/commands/` or `.opencode/` is tracked or present untracked and not ignored
- **THEN** the agent-file check fails

#### Scenario: Contribution guide
- **WHEN** a reader looks for contribution guidance
- **THEN** only `CONTRIBUTING.md` exists

### Requirement: Rules live in .agents/rules, scoped by path
The repository SHALL keep every always-on and path-scoped rule as one concern per file in a single flat directory `.agents/rules/` (no subdirectories), named `<scope>-<topic>.md` where `<scope>` is `repo` or a node or platform id, each at most 24,000 bytes and each `repo-*` rule at most 4,096 bytes (the always-on context budget; the target is 2 KB), with frontmatter keys limited to `trigger`, `globs` and `paths`. A `repo-*` rule MUST have frontmatter `trigger: always_on` and MUST NOT have `globs` or `paths`. A node rule MUST have `trigger: glob`, `globs` and `paths` as identical, non-empty YAML lists of patterns under the scope's folder (`android/**`, `web-extension/**`, `rust/**`, `protocol/**`, `native-bridge/**`, `native-bridge/host/**`, `native-bridge/installer/**`, `native-bridge/installer/macos/**`, `native-bridge/installer/windows/**`, `verify/**`, `scripts/**`, `docs/**` for the scopes of the same names; no brace expansion and no `..` path segment), so that an agent loads it only after reading or editing a file in a listed folder. A rule consumed by several sibling nodes MUST be named for their narrowest common parent scope and list only the exact consuming patterns (for example `native-bridge-<topic>.md` with `native-bridge/installer/macos/**` and `native-bridge/host/**`), so an unrelated sibling such as `native-bridge/installer/windows/**` does not load it. Hook-delivered rules are not rules of this directory: they live in `.agents/hooked-rules/` as the `hooked-rule-delivery` capability defines, and the size, key and frontmatter requirements of this requirement apply only to `.agents/rules/`. The always-on `repo-*` rules in `.agents/rules/` MUST be exactly three: no time-based unit tests (no sleeps or wall-clock dependence, inject a clock) (`repo-no-time-based-tests.md`); tracker intake and board status (`repo-tracker-intake.md`); and never regenerate, replace or commit signing keys, with details left to the node files (`repo-signing-keys.md`). The root `AGENTS.md` MUST list all three. Five `repo-*` rules are hook-delivered rules in `.agents/hooked-rules/` (capability `hooked-rule-delivery`), next to `repo-commit-types.md`: before pushing, run the checks listed in the `AGENTS.md` of every node you touched, without platform specifics (`repo-pre-push-checks.md`); real-environment testing (`repo-real-device-testing.md`, capability `real-device-testing`); and three rules for the superspec workflow, one concern each: the reviewer roster (`repo-review-models.md`), the review gate and apply stop (`repo-superspec-review-gate.md`) and the closeout (`repo-superspec-closeout.md`). The four superspec workflow rules (those three and tracker intake) MUST each state only non-negotiable steps and, where they have procedure detail, MUST open with an instruction to read the named section of `docs/superspec-workflow.md` before acting; the closeout rule MAY put its apply-to-finalize lead paragraph (capability `superspec-closeout`) before that instruction. The initial node rule MUST be `android-lint-before-push.md` carrying the Android PR lint gate. Each node `AGENTS.md` MUST list its rule files. `.claude/rules` MUST be a symlink to `.agents/rules`.

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
- **THEN** the three `repo-*` rules in `.agents/rules/` are in its context, and no file under `.agents/hooked-rules/` is

#### Scenario: Rules reach each tool
- **WHEN** each of Claude Code, OpenCode and Antigravity runs the scoping scenarios above
- **THEN** Claude Code loads rules through `.claude/rules` and `paths`, OpenCode through the pinned `opencode-rules` plugin configured in `opencode.json`, and Antigravity through `.agents/rules` with `trigger: glob`, and the verification record states how each was observed

#### Scenario: Pre-push rule has no platform detail
- **WHEN** `.agents/hooked-rules/repo-pre-push-checks.md` is read
- **THEN** it names no Gradle, Cargo or npm command, tells the agent to find the touched nodes with `scripts/components.py changed` (including nodes selected by shared inputs) and to run the checks listed in each node's `AGENTS.md`

#### Scenario: Real-device rule listed
- **WHEN** `.agents/hooked-rules/repo-real-device-testing.md` exists and the root pointer block has no line for it
- **THEN** the agent-file check fails as a stale pointer block

#### Scenario: Conventional Commits rule moved
- **WHEN** `.agents/rules/repo-conventional-commits.md` exists, or the root `AGENTS.md` lists it among the `repo-*` rules
- **THEN** the agent-file check fails, because the commit-type rule lives at `.agents/hooked-rules/repo-commit-types.md`

### Requirement: OpenCode scoped rules use a range-bounded, allow-listed plugin
`opencode.json` SHALL contain only the keys `$schema` and `plugins`, and `plugins` SHALL contain exactly two entries: the local hooked-rules plugin in `scripts/opencode-hooked-rules/` (declared with the local-entry syntax that the `hooked-rule-delivery` verification records), and the package `opencode-rules` constrained to the supported range `>=2.0.0 <3` (a caret or inequality range that admits no version below 2.0.0 or at 3.0.0 and above), never `latest`, `*`, an exact pin, or any range crossing the major bound, with `additionalRuleDirectories` containing `.agents/rules`. The root `AGENTS.md` and `CONTRIBUTING.md` MUST state that OpenCode users need the `opencode-rules` plugin at the range pinned in `opencode.json` for scoped rules to be delivered, without repeating the range. `docs/AGENTS.md` MUST state the same requirement in one sentence and link `docs/opencode-rules.md`, which MUST hold the install, update and untrusted-checkout guidance without repeating the range. The qualified plugin range and OpenCode version MUST be recorded where the system-level qualification keeps them; the plugin's installation, package-integrity and transitive-dependency policy and per-version regression tests are outside this change. A `.github/CODEOWNERS` entry MUST name an owner for `opencode.json`, `.claude/`, `.mcp.json` when present, `.agents/`, `**/AGENTS.md`, `/docs/superspec-workflow.md`, `/docs/agent-guidance.md`, `/docs/openspec-workflow.md`, `/docs/opencode-rules.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` (the instruction files agents follow and the code their tools run automatically; the entries for `/docs/superspec-workflow.md`, `/docs/agent-guidance.md`, `/docs/openspec-workflow.md`, `/docs/opencode-rules.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/` MUST name the same owner as the existing agent-guidance entries, which review checks and the agent-file check does not), and contributor guidance MUST tell reviewers not to launch agents in an untrusted PR checkout, or to disable project configuration, before reading those files. If the plugin cannot be used, the root `AGENTS.md` and the node files MUST still make the rule files discoverable.

#### Scenario: Plugin requirement documented
- **WHEN** the root `AGENTS.md` or `CONTRIBUTING.md` does not mention `opencode-rules` as an OpenCode requirement, or `docs/AGENTS.md` lacks the one-sentence statement and link, or `docs/opencode-rules.md` lacks the install, update and untrusted-checkout guidance
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
- **WHEN** `.github/CODEOWNERS` lacks an entry for `opencode.json`, `.claude/`, `.agents/`, `**/AGENTS.md`, `/docs/superspec-workflow.md`, `/docs/agent-guidance.md`, `/docs/openspec-workflow.md`, `/docs/opencode-rules.md`, `/openspec/config.yaml`, `/openspec/schemas/`, `/scripts/agent_rules.py` or `/scripts/opencode-hooked-rules/`
- **THEN** the agent-file check fails

#### Scenario: Plugin unavailable
- **WHEN** OpenCode starts with the plugin unavailable (offline with an empty plugin cache, an install failure or the plugin disabled)
- **THEN** OpenCode still starts, scoped rules are not delivered, and an agent can still find them through the root `AGENTS.md` and the node files' read-the-rule instructions, which the verification record shows

### Requirement: Every AGENTS.md tells the agent where to start
Scoped rule delivery works only when the agent is started at the repository root, so the root `AGENTS.md` and every nested `AGENTS.md` SHALL carry a short fixed notice. The root notice MUST read `> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there.` and a nested notice MUST read `> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.` followed by that folder's rule file list.

#### Scenario: Notice present
- **WHEN** a tracked `AGENTS.md` lacks its notice
- **THEN** the agent-file check fails and names the file

#### Scenario: Started inside a node folder
- **WHEN** Claude Code or OpenCode is started inside `android/`
- **THEN** no rule file is delivered automatically in either tool (observed limitation); Claude Code loads the ancestor `AGENTS.md` chain (2/2 observed runs) while OpenCode loads only the node's own `AGENTS.md` (3/3 observed runs), and in both tools the notice plus the listed rule files let the agent read them by hand, which the verification record shows for each tool

### Requirement: Symlinks are minimal and exact
The only committed symlinks used for agent guidance SHALL be the directory link `.claude/rules` → `../.agents/rules` and one link per skill `.claude/skills/<name>` → `../../.agents/skills/<name>`, created by `openspec update` for OpenSpec skills and by hand for any other skill kept in `.agents/skills/`. `.claude/settings.json` MUST remain a regular file.

#### Scenario: Wrong link target
- **WHEN** `.claude/rules` is missing, is a regular file or points elsewhere on Linux or macOS
- **THEN** the agent-file check fails

#### Scenario: Windows without symlinks
- **WHEN** the check runs on Windows and a required link is a text file
- **THEN** it reports an explicit warning that Claude Code rules and skills are inactive, naming `core.symlinks=true` and Developer Mode, and does not pass silently

#### Scenario: Skills agree
- **WHEN** a skill exists in `.agents/skills/` without its `.claude/skills/` link, or the reverse
- **THEN** the agent-file check fails and names the skill

### Requirement: Guidance files keep LF line endings
The repository SHALL include a `.gitattributes` entry forcing `eol=lf` for `.agents/**`, `**/AGENTS.md` and `opencode.json`.

#### Scenario: Windows checkout
- **WHEN** a Windows checkout has `core.autocrlf=true`
- **THEN** guidance files, rule frontmatter and generated skills keep LF endings

### Requirement: Existing content is preserved or explicitly dropped
Every section of a guidance file that this change moves, including every section of the pre-change `CLAUDE.md`, SHALL have a documented destination in a node `AGENTS.md`, a `docs/` file that actually holds the fact, or an explicit decision to drop it as obsolete or already recorded in code. A pointer instead of a copy MUST be used only where the target document holds the fact. The change MUST ship a section-by-section traceability table, and the assertions of every CI gate that read the old guidance files MUST be preserved unchanged.

#### Scenario: Build-contract gate
- **WHEN** `native-host / build-contract` runs
- **THEN** its docs check reads `native-bridge/host/AGENTS.md` and still requires the host Gradle task names, `native-host-windows`, `native-host-macos` and `native-host.cdx.json`

#### Scenario: Fact without a documentation home
- **WHEN** the pre-change `CLAUDE.md` records the OTP-23 fixture keystore or the `core.hooksPath` hook behaviour that no `docs/` file holds
- **THEN** it moves into the owning node's `AGENTS.md`, the docs page that node links (`docs/android.md` for the fixture build line, `docs/ci.md` for `core.hooksPath`), or is dropped with a recorded reason

#### Scenario: Traceability
- **WHEN** a reviewer opens the verification record
- **THEN** every former `CLAUDE.md` section maps to a destination or a recorded drop

### Requirement: The three target tools are verified
The layout SHALL be verified in Claude Code, OpenCode and Antigravity, each exercised empirically when the tool is available; a tool that cannot be run MUST be reported as a blocker and not silently downgraded to documentation, and implementation MUST not proceed past that blocker without an explicit owner acceptance of it. The verification record MUST state the exact tool versions, which evidence was observed and which came only from documentation, and MUST run from a worktree outside the repository so no parent `CLAUDE.md` hides `AGENTS.md`. It MUST include whether OpenCode lists skills twice, the Windows behaviour of the symlinks and of `openspec update`, native Windows scoping of rules in the tools that run there, the plugin-unavailable state of OpenCode, and what each tool delivers when started inside a node folder. Before scoped rules are adopted, an empirical Antigravity run, when Antigravity is available, MUST show that it accepts `globs` as a YAML list (its documentation shows only a comma-separated string) and matches the patterns relative to the workspace root; if it does not, the rule scheme MUST be revisited with the owner before implementation continues. When Antigravity is unavailable, its verification MUST be recorded as a blocker and proceeds only under that explicit owner acceptance; this change records an inconclusive Antigravity driver run as the blocker (design D5a "Result (apply)" and `verification-notes.md`) and does not claim the empirical run.

#### Scenario: Evidence labelled
- **WHEN** a claim in the verification record rests only on documentation
- **THEN** it is labelled documentation-only

#### Scenario: Antigravity run
- **WHEN** Antigravity is available on the PC through `homepc agy`
- **THEN** its handling of `AGENTS.md`, `.agents/rules` and `.agents/skills` is exercised and recorded

#### Scenario: Claude Code prerequisites
- **WHEN** a contributor uses Claude Code older than 2.1.277, Bedrock or telemetry-off sessions older than 2.1.281, or has a `CLAUDE.md` or `CLAUDE.local.md` in the working directory or a parent folder
- **THEN** contributor guidance explains that `AGENTS.md` is not read and how to fix it

### Requirement: The agent-file check enforces the layout
`docs/checks/check_agent_files.py` SHALL be a standard-library Python script, run by the `docs` node, that fails on each machine-enforceable invariant of this capability and of `openspec-skills-delivery`: the 40-line cap on the root and every tracked nested `AGENTS.md`; the node-file and rule-file lists; the legacy files and directories; rule naming, size (24,000 bytes, and 4,096 bytes for `repo-*` rules), key set, per-kind frontmatter and scope patterns; the symlink and skill-parity checks, reading the expected skill list from `docs/openspec-skills.md`; the `opencode.json` allow-list; the `.agents/hooked-rules/` file format (through the parser imported from `scripts/agent_rules.py`), body size, regex validity and stop-predicate names; pointer-block freshness; the delivery budget of the `hooked-rule-delivery` capability; the three always-on `repo-*` rules; the Claude Code hook wiring in `.claude/settings.json` (each of the five hooks with its exact matcher, no `SubagentStop` hook, the exact engine command and the 5-second timeout; that the engine never emits an `allow` decision is tested through the engine's own output and the host verification, not through this check); the `opencode-rules` requirement mention in the root `AGENTS.md` and `CONTRIBUTING.md`; CODEOWNERS coverage; the start-at-root notice in every `AGENTS.md`; and that every tracked nested `AGENTS.md` that the `docs` node does not already own exclusively (all except `docs/AGENTS.md` and `native-bridge/AGENTS.md`) has its `global-overrides` entry in `.github/components.json`. It MUST NOT be expected to verify behaviour in the three tools, content preservation, or generated-skill content, which verification and review cover. Every enforced invariant MUST have a positive and a negative test under the same node. The line-cap tests MUST cover the root and a nested file at 40 and 41 lines, and the size tests MUST share one fixture builder and cover each cap at its boundary (the cap passes, the cap plus one byte fails), using a node-scoped rule for the 24,000-byte cap, and the fixture MUST also list the rule in its node's `AGENTS.md` so the tests assert a single finding. The check MUST scan tracked and untracked-but-not-ignored files so that a nested `CLAUDE.md` is caught while ignored build and vendor trees are not.

#### Scenario: Negative tests
- **WHEN** the check's tests run
- **THEN** there is a failing case for each enforced invariant, including: a nested `CLAUDE.md`, a reappearing `.claude/commands/`, a missing node file, an unlisted nested `AGENTS.md`, an over-long root file, an over-long nested file, a rule in a subdirectory, an unknown frontmatter key, a `repo-*` rule with `globs`, mismatched `globs` and `paths`, a rule not listed by its node, an unpinned or extra plugin in `opencode.json`, a `repo-*` rule over 4,096 bytes, missing CODEOWNERS coverage (including for each of the procedure doc, `docs/agent-guidance.md`, `docs/openspec-workflow.md`, `docs/opencode-rules.md`, `openspec/config.yaml` and `openspec/schemas/`), a missing start-at-root notice, a nested `AGENTS.md` without a `global-overrides` entry, a wrong symlink, a skill without its link, an invalid hooked rule (unknown key, duplicate key, `[]` list, bad regex, no trigger, a body over 4,096 bytes), a stale pointer block, a missing Claude Code hook (the `Stop` hook included), a wired `SubagentStop` hook, a wrong hook matcher or command, a missing timeout, an unknown stop predicate, a trigger over the delivery budget, a moved rule left in `.agents/rules/`, a missing local OpenCode plugin entry, a plugin range repeated in `docs/AGENTS.md`, `docs/agent-guidance.md` or `docs/opencode-rules.md`, and missing CODEOWNERS coverage of `/scripts/agent_rules.py` or `/scripts/opencode-hooked-rules/`

#### Scenario: Clean tree
- **WHEN** the check runs on the migrated tree
- **THEN** it passes with no line-count finding

### Requirement: The procedure doc exists and the rules cite real sections
`docs/superspec-workflow.md` SHALL exist and have the headings *Reviewer brief*, *Review passes and caps*, *Revision binding*, *Escalation*, *Next task*, *tracker.md*, *Closeout* and *Monitoring*. It MUST name no model and no concrete project, and its opening paragraph MUST name each workflow rule by its current path. The rules MUST be read from `.agents/hooked-rules/` for the review gate and the closeout and from `.agents/rules/` for tracker intake. The review-gate rule MUST cite the sections *Reviewer brief*, *Review passes and caps*, *Revision binding* and *Escalation*, the tracker-intake rule *Next task* and *tracker.md*, and the closeout rule *Closeout* and *Monitoring*; every section a rule cites MUST exist.

#### Scenario: Cited section missing
- **WHEN** a rule cites a section that `docs/superspec-workflow.md` does not have
- **THEN** the plan's verification (a grep over the rules and the doc's headings) fails

#### Scenario: Doc names a model
- **WHEN** `docs/superspec-workflow.md` names a model or a concrete project
- **THEN** the plan's verification fails
