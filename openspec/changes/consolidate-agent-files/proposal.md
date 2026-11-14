## Why

Agent guidance is scattered and tool-specific: a 336-line `CLAUDE.md` that loads Android detail for every task, a duplicate `CONTRIBUTION.md`, and two OpenSpec-generated trees (`.claude/`, `.opencode/`) holding duplicate skills and command files. Guidance drifts and wastes context. Claude Code now reads `AGENTS.md` and OpenSpec 2.0 writes one `.agents/skills` copy, so a single neutral source can serve OpenCode, Claude Code and Antigravity.

## What Changes

**Agent instruction files**
- From: `CLAUDE.md` (336 lines, all nodes) and `CONTRIBUTION.md` (3-line pointer).
- To: a root `AGENTS.md` (≤ 60 lines, hard cap 120, no platform specifics) plus one `AGENTS.md` per node and platform folder (android, web-extension, rust, protocol, native-bridge, host, installer, installer/macos, installer/windows, verify, scripts, docs). `CLAUDE.md` and `CONTRIBUTION.md` are deleted; no `CLAUDE.md` exists anywhere.
- The root `AGENTS.md` carries a mandatory node map ("before changing files under `<dir>/`, read `<dir>/AGENTS.md`"), because Antigravity does not load nested files (Claude Code and OpenCode 2.x load them on demand natively, verified); `rust/` and `protocol/` guidance points at the Android checks because Android CI also runs for their changes.
- Every `AGENTS.md` (root and nested) carries a short fixed notice that scoped rules load only when the agent is started at the repository root (observed: started inside a node folder neither Claude Code nor the OpenCode plugin delivers rule files; Claude Code still loads the `AGENTS.md` ancestor chain, while OpenCode loads only that folder's file), plus, in nested files, that folder's rule file list.
- Content moves, it is not rewritten: facts go to the node that owns them, and only material already recorded in code or `docs/` is dropped. Each platform has one canonical destination (macOS signing/TCC only under `installer/macos`, Windows only under `installer/windows`); cross-platform nodes such as `host/` point to them instead of copying. Existing gate assertions are preserved unchanged.
- Reason: platform facts should load only when an agent works in that folder; Claude Code reads `AGENTS.md` natively when no `CLAUDE.md` or `CLAUDE.local.md` exists in the working directory or any ancestor (Claude Code ≥ 2.1.277, ≥ 2.1.281 for Bedrock and telemetry-off sessions); this prerequisite is documented for contributors.
- Impact: breaking for anything that reads `CLAUDE.md` (the `native-host / build-contract` gate and two script comments are repointed). Not a release-breaking change: commits are `docs`/`chore` and trigger no version bump.

**Rules**
- All rules live in `.agents/rules/` as flat files named `<scope>-<topic>.md`. Repo-wide rules (`repo-*`: Conventional Commits, no time-based unit tests, run the checks of every node you touched, never regenerate/replace/commit signing keys) are always on; node rules (e.g. `android-lint-before-push.md`, `rust-*.md`) are scoped to that node's folder by path, so an agent sees them only when it touches that folder. Each node `AGENTS.md` lists its rule files.
- Scoped rules carry three frontmatter keys with identical patterns: `paths` (Claude Code), `trigger: glob` + `globs` (Antigravity and OpenCode). `.claude/rules` is a symlink to `.agents/rules` for Claude Code; OpenCode loads them through the third-party plugin `opencode-rules` constrained to the supported range `>=2.0.0 <3` in `opencode.json`, and the root `AGENTS.md`, `CONTRIBUTING.md` and `docs/AGENTS.md` state that OpenCode users need it (without repeating the range) (installing and qualifying the plugin is a separate, system-level task; this change assumes it works and tests the repo's rules against it).

**OpenSpec 2.0**
- From: OpenSpec 1.x with command files and per-tool skill copies in `.claude/` and `.opencode/`.
- To: OpenSpec 2.0 or a newer 2.x (`>= 2.0.0, < 3`, last verified 2.0.0), skills only: one copy in `.agents/skills/`, per-skill symlinks `.claude/skills/*` created by `openspec update`, `.claude/commands/` and `.opencode/` removed. References to `/opsx:<name>` commands become the skill names (`/openspec-propose`, …).

**New `docs` CI node**
- A non-release node owning `docs/`, `openspec/`, `AGENTS.md`, `.agents/`, `.claude/`, `opencode.json`, `README.md`, `BUILD.md` and `CONTRIBUTING.md` (enumerated; the component map forbids wildcards), plus every nested `AGENTS.md` (each selecting only `docs` through `global-overrides`, with `native-bridge/AGENTS.md` given an explicit owner), with a `check-docs` workflow and a stdlib-Python check (`docs/checks/check_agent_files.py`) that enforces the layout, symlinks, line cap, rule frontmatter and the absence of legacy OpenSpec 1.x output (structural check; the OpenSpec CLI is not installed in CI).

**Verification**
- Claude Code, OpenCode and Antigravity each exercised empirically (a tool that cannot be run is reported as a blocker, not downgraded to documentation); the Windows symlink and `openspec update` behaviour re-tested on 2.0. `verify.md` must distinguish empirical from documentation-only evidence. A blocking prerequisite for the scoped-rule design: Antigravity documents `globs` as a comma-separated string while the OpenCode plugin accepts only YAML arrays, so Antigravity's acceptance of the array form must be proven early; the fallback decision is described in the design. Still open at this stage: whether OpenCode lists skills twice (it also scans `.claude/skills`), whether Antigravity picks up `.agents/rules` and the root node map, and that scoped rules arrive in OpenCode through the plugin only for the matching folder.

## Capabilities

### New Capabilities
- `agent-guidance-layout`: where agent instructions, rules and skills live (root and node `AGENTS.md`, `.agents/`, the allowed symlinks, removed legacy files), what each tool reads, and the check that keeps it from drifting.
- `openspec-skills-delivery`: OpenSpec 2.x (`>= 2.0.0, < 3`) in skills-only mode, with an idempotent `openspec update`, no command files, and a structural (CLI-free) CI check.

### Modified Capabilities
- `ci-pipeline`: adds the `docs` node (`check-docs` workflow), includes it in the required `ci-ok` aggregate and in the workflow-contract suites, extends supply-chain scanner coverage to the new check and workflow, and replaces the "documentation only → no node runs" scenario (documentation and agent-file edits now select `docs`).
- `pr-last-green-selection`: scenarios that use a documentation-only change as the "no node runs" example switch to another unowned path, since `docs/` now selects the `docs` node.
- `component-versioning`: the component map gains the `docs` node and its ownership of the agent files, reconciled with the requirement that root build files select every non-Android node; deleted legacy paths (`CLAUDE.md`, `CONTRIBUTION.md`, `.opencode/`) are added as retired-path entries using the existing mechanism (no new machinery).

## Impact

- **Files:** new `AGENTS.md` files, `.agents/`, `opencode.json`, `.github/CODEOWNERS`, `docs/checks/`, `.github/workflows/check-docs.yml`; removed `CLAUDE.md`, `CONTRIBUTION.md`, `.opencode/`, `.claude/commands/`; regenerated `.claude/skills/` (symlinks).
- **References and live documentation:** every live reference to removed files or `/opsx:` command names is migrated, including live docs (e.g. `docs/otp-23-lifecycle-scenarios.md` cites the signing guidance), `openspec/schemas/superspec/` instructions and templates, and `.github/components.json`; archived and frozen evidence under `docs/`, `protocol/docs/reviews/`, `native-bridge/feasibility/evidence/` and archived changes is deliberately left untouched. `CONTRIBUTING.md` remains the single contribution guide.
- **CI and tooling:** `.github/components.json`, `scripts/components.py` tests, `ci.yml`, `docs/ci.md`, `verify/check-remote-code.mjs` and the workflow-contract suite, `native-bridge/host/scripts/test_build_contract.py`, `macos-test-bundle.sh`, `verify/check-android-layout.sh`.
- **Dependencies:** OpenSpec CLI `>= 2.0.0, < 3` for contributors (2.0.0 installed and verified; not needed in CI); Node ≥ 22.12. No app, protocol or release behaviour changes.
- **Risks:** symlinks need `core.symlinks=true` on Windows; `openspec update` on Windows is unproven on 2.0 and is re-tested in the implementation.
- **Out of scope:** extending the rule set beyond the initial framework example; adopting rulesync (kept as a fallback).
