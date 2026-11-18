## Why

The repository's 13 `AGENTS.md` files have grown into manuals: `docs/AGENTS.md` is 177 lines,
`android/AGENTS.md` 137 and `scripts/AGENTS.md` 102, and nine files together are 357 lines over a 40-line
budget. Agents read these files first, so the bulk costs context before any task starts and hides the
commands and rules that matter. The existing check only warns above 60 lines and fails above 120, and only
for the root, so nothing stops the growth.

## What Changes

**Length policy**
- From: a warning above 60 lines and an error above 120, root only; nested files have no line check.
- To: a hard error above 40 lines for the root and every tracked nested `AGENTS.md`, with the message
  `has <n> lines; the AGENTS.md cap is 40 lines`; exactly 40 passes.
- Reason: a single, predictable budget, enforced where agents read.
- Impact: non-breaking for product code; the guidance tree must be reorganized in the same change.
- Consequence: the root sits at 39 lines, so a change that adds a node, a `repo-*` rule or a hooked rule
  (one pointer line each) must compress the root or move a fact to docs; the new error makes that
  requirement visible at review time.

**What an `AGENTS.md` holds**
- From: purpose plus architecture narratives, workflow tables, policy essays and command blocks.
- To: purpose, commands with a one-line rationale, the constraints that must be obeyed in that folder, the
  `## Rules` section, the start-at-root notice and links to the detail. Architecture and procedures live
  in `docs/`.
- Reason: the file is an entry point, not a manual.

**Content migration**
- From: nine files over the cap.
- To: every file at or under 40 lines. New pages hold the displaced detail:
  `docs/android.md` (Android architecture and flows), `docs/agent-guidance.md` (the check, adding a rule or
  node, the 40-line procedure), `docs/openspec-workflow.md` (OpenSpec update and the superspec-fork
  divergences), `docs/opencode-rules.md` (plugin install/update, untrusted-checkout warning) and
  `docs/openspec-skills.md` (the workflow list and the 11 skill names the check parses). Existing
  `docs/ci.md`, `docs/releasing.md`, `docs/reproducible-builds.md`, `docs/otp-25-installers.md`,
  `docs/desktop-ble-poc.md` and `docs/hook-delivered-rules.md` take the rest.
- Reason: one home per fact; the AGENTS.md files link rather than duplicate. The verification record
  carries a destination for every moved section and a reason for anything deliberately dropped.

**Checker and ownership**
- From: the skill list parsed from `docs/AGENTS.md`; the plugin-range guard reaching `docs/AGENTS.md` only
  through that alias; the plugin, skill and OpenSpec instructions living in `docs/AGENTS.md`, which the
  `**/AGENTS.md` CODEOWNERS rule already protects.
- To: `DOCUMENTED_SKILLS = "docs/openspec-skills.md"`; `PLUGIN_RANGE_DOCS` lists `docs/AGENTS.md`
  explicitly plus `docs/opencode-rules.md` and `docs/agent-guidance.md`; `.github/CODEOWNERS` and the
  checker's CODEOWNERS constants gain the three new instruction pages, so moving the text does not drop
  the protection it had inside `docs/AGENTS.md`.
- Reason: the skill set changes on its own cadence and must not destabilize the 40-line file; the moved
  instructions keep owner review.

**Specifications**
- From: specs that pin the divergence list, the skill list and the untrusted warning to `docs/AGENTS.md`,
  and a root cap of 60/120.
- To: deltas that move those facts to their docs pages and make the 40-line cap normative for every
  `AGENTS.md`.
- Reason: the guidance layout is a checked contract; the change must update the contract it changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `agent-guidance-layout`: the root and node requirements carry the 40-line cap and the shorter file
  contract; the platform-canonical-folder requirement allows narrative in the node's linked docs
  page(s) — `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md` for macOS — while the canonical
  constraints stay in the node file (the macOS identity statement, the cited
  `## Signing identity and TCC (macOS)` and `## Signing identities (Android)` headings and the Android
  keystore/init-script names); the "Existing content is preserved or explicitly dropped" requirement
  accepts the linked docs page for the fixture build line (`docs/android.md`) and the `core.hooksPath`
  paragraph (`docs/ci.md`); the check requirement covers every `AGENTS.md` with boundary tests; the OpenCode requirement moves the
  install/update/untrusted guidance to `docs/opencode-rules.md` and extends its CODEOWNERS list.
- `superspec-review-gate`: the fork divergence list moves from `docs/AGENTS.md` to
  `docs/openspec-workflow.md`; the `(schema version v5)` text stays.
- `openspec-skills-delivery`: the workflow list and skill names move to `docs/openspec-skills.md`, which the
  agent-file check reads; the fork recording moves to `docs/openspec-workflow.md`.
- `hooked-rule-delivery`: the untrusted-checkout warning moves to `docs/opencode-rules.md`; the short
  hook-delivered-rules section stays in `docs/AGENTS.md`.

## Impact

- **Edited guidance**: root `AGENTS.md`; `android/AGENTS.md`; `docs/AGENTS.md`; `scripts/AGENTS.md`;
  `verify/AGENTS.md`; `native-bridge/AGENTS.md`; `native-bridge/host/AGENTS.md`;
  `native-bridge/installer/macos/AGENTS.md`; `rust/AGENTS.md`.
- **New docs**: `docs/android.md`, `docs/agent-guidance.md`, `docs/openspec-workflow.md`,
  `docs/opencode-rules.md`, `docs/openspec-skills.md`.
- **Edited docs**: `docs/ci.md`, `docs/releasing.md`, `docs/reproducible-builds.md`,
  `docs/otp-25-installers.md`, `docs/desktop-ble-poc.md`, `docs/hook-delivered-rules.md`, `CONTRIBUTING.md`.
- **Checker**: `docs/checks/check_agent_files.py`, `docs/checks/test_check_agent_files.py`.
- **Ownership**: `.github/CODEOWNERS`.
- **Specs**: deltas for the four capabilities above. No product code, workflow, dependency or runtime
  change. The host build-contract strings and the CODEOWNERS/opencode invariants stay unchanged.
- **Checks**: the agent-file check, its unit tests, `git ls-files '*AGENTS.md' | xargs wc -l`,
  `python3 -I scripts/components.py check`, `python3 -I scripts/components.py changed --base
  $(git merge-base origin/master HEAD)` and the checks of every node it selects (the edited docs are
  ci-paths of the `installer` and `native-host` nodes, `docs/checks/` is a `supply-chain` path, and
  `.github/` is a global path), the host build-contract suite and a `git grep` sweep for stale
  cross-references.
