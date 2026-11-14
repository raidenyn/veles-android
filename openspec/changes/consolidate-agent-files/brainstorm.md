## Design Summary

### Intent and why

Agent guidance is scattered and tool-specific: a 336-line `CLAUDE.md` (Android architecture plus
build commands for every node, all loaded for every task), a duplicate `CONTRIBUTION.md` stub, and
two OpenSpec-generated trees (`.claude/`, `.opencode/`) holding duplicate skills and command files
whose text differs only in command spelling. Goal: one tool-neutral source of agent guidance that
works in OpenCode, Claude Code and Antigravity, with a tiny root file and node-specific details
that load only when an agent works in that node.

### Goals

- Delete `CONTRIBUTION.md` (keep `CONTRIBUTING.md`) and `CLAUDE.md`; fix every reference.
- Root `AGENTS.md`: as small as possible (target ≤ 60 lines, hard cap 120), no platform specifics.
- One `AGENTS.md` per node/platform folder holding only that scope's facts, commands, constraints
  and rules (Android only under `android/`, macOS only under the macOS folder, Windows only under
  the Windows folder).
- `.agents/` is the canonical home for repo-wide rules and skills.
- Upgrade OpenSpec 1.x → 2.0 as part of this change (see below); this removes command files and the
  `.opencode/` tree and makes OpenSpec create the Claude Code skill links itself.
- Repo-wide rules, initial set (approved as a framework example; more to be added later):
  Conventional Commits; no time-based unit tests (no sleeps or wall-clock dependence, inject a
  clock); before pushing, run the checks listed in the `AGENTS.md` of every node you touched;
  never regenerate, replace or commit signing keys (details live in the node files).
- Node-level rules (e.g. Android: run linters locally before pushing) live in a `## Rules` section
  of that node's `AGENTS.md`.
- Compatibility with OpenCode, Claude Code and Antigravity, proven rather than assumed.
- A new CI node `docs` that owns these files and a repo-owned check that keeps layout and symlinks
  from drifting.

### Non-goals / anti-goals

- No third-party sync tool (rulesync / ruler) now. Anti-goal: committed duplicates of the same
  content in several tool folders.
- No behavior change to builds, release gating, or app code. CI gates that read `CLAUDE.md` are
  repointed, not weakened.
- Not a re-litigation of guidance content: facts move, with only duplication-with-code or
  `docs/` dropped.
- Anti-goal: a layout that works in one tool but silently loads nothing in another.
- Not in scope: extending the rule set beyond the initial framework example.

### Verified facts (recon and spikes, 2026-11-14)

| | Claude Code | OpenCode | Antigravity |
|---|---|---|---|
| Root instructions | `AGENTS.md` read directly when no `CLAUDE.md`/`CLAUDE.local.md`/`.claude/CLAUDE.md` exists in the working dir **or any ancestor** (needs ≥ 2.1.277; ≥ 2.1.281 for Bedrock/telemetry-off sessions). Verified on 2.1.323. | `AGENTS.md` (docs differ by version on `CLAUDE.md` fallback; irrelevant once `CLAUDE.md` is gone). Installed: 2.2.7 | `AGENTS.md`, `GEMINI.md` |
| Nested `AGENTS.md` | Loaded on demand when it reads a file in that dir (verified; no `CLAUDE.md` in that dir) | Version-dependent; not relied on | Not loaded (workspace + parents only) |
| Skills dir | `.claude/skills` only (`.agents/` not read) | `.agents/skills`, `.claude/skills`, `.opencode/skills` | `.agents/skills` |
| Rules | `.claude/rules/` (dir symlink verified) | `opencode.json` `instructions` globs | `.agents/rules/*.md`, needs `trigger` frontmatter, 24 KB/file |
| Commands | skills are slash commands (`/openspec-propose`, verified in `slash_commands`) | skills loaded by name | skills are slash commands |

- Claude Code **does not read `.agents/`**, so Claude needs links into `.agents/skills` and
  `.agents/rules`. Directory and per-skill symlinks were both proven to be followed.
- OpenSpec 2.0.0 (`latest`, published 2026-11-11), tested on a copy of this repo's OpenSpec setup:
  `openspec update` writes one copy of skills to `.agents/skills/`, creates per-skill symlinks
  `.claude/skills/<name>` → `../../.agents/skills/<name>`, removes all 20 command files and the
  entire `.opencode/` skill/command tree, and a second run changes nothing. The custom
  `superspec` schema still works (`new change`, `status`, `instructions`). Command files and the
  `delivery` setting are retired; every workflow is a skill.
- OpenSpec 1.17 `update`, with command symlinks in place, corrupted the layout: it created
  duplicate `.opencode/skills`, wrote `opsx/*.md` through the symlink into `.agents/workflows/`,
  and rewrote skills. This is why command files are dropped, not bridged.
- Windows PC (Win 11, Developer Mode on, OpenSpec 1.16.1): with `core.symlinks=false` a committed
  symlink checks out as a text file, so Claude Code would see no skills. With symlinks on they work
  without elevation, but `openspec update` rewrote all skill files and created `.claude/commands/`,
  alternating between runs. Not reproducible on Linux (same version, with or without CRLF), so the
  cause is unknown; must be re-tested on 2.0.

## Alternatives Considered

### Alternative A: `.agents/` as source + minimal symlinks + repo-owned check (chosen)
- **Approach**: `.agents/{rules,skills}` canonical; the few links Claude Code needs; repo-owned
  stdlib-Python check in the new `docs` node; node `AGENTS.md` files.
- **Pros**: matches the request; no new dependency under the strict supply-chain policy; with
  OpenSpec 2.0 the skill links are created by OpenSpec itself, so hand-made bridges shrink to one
  directory link (`.claude/rules`).
- **Cons**: symlinks need `core.symlinks=true` (Developer Mode) on Windows; the check must
  recognise OpenSpec-created links.
- **Why not chosen**: chosen.

### Alternative B: rulesync
- **Approach**: `.rulesync/` source generating AGENTS.md, CLAUDE.md, `.claude/`, `.opencode/`,
  `.agents/`; `generate --check` in CI.
- **Pros**: per-tool naming handled; built-in drift check; commands/hooks/MCP support.
- **Cons**: `.agents/` becomes a generated copy; duplicates everything; competes with
  `openspec update` over the same files; new npm dependency in the supply-chain verifier; docs show
  inconsistent versioning.
- **Why not chosen**: solves a problem we no longer have (OpenSpec 2.0 is skills-only). Remains
  the fallback if we later need hooks, MCP or per-tool permissions.

### Alternative C: ruler
- **Approach**: `.ruler/` concatenated into per-agent files.
- **Pros**: simple, MIT.
- **Cons**: beta; no commands; writes legacy `.agent/` for Antigravity; nested mode experimental;
  gitignores outputs (breaks worktrees).
- **Why not chosen**: behind rulesync and A.

### Sub-alternatives rejected
- Keep a one-line `CLAUDE.md` containing `@AGENTS.md` or a `CLAUDE.md` ↔ `AGENTS.md` symlink:
  unnecessary on Claude Code ≥ 2.1.277; a symlink either way adds a Windows failure mode.
- Keep command files with a bridging `.agents/workflows`: proven to be clobbered by
  `openspec update` on 1.17.

## Agreed Approach

Alternative A, skills-only, on OpenSpec 2.0. Target layout (see the approved review diagram):

```
AGENTS.md                           root, ≤ 60 lines: what Veles is, node map, pointers
opencode.json                       instructions: [".agents/rules/*.md"]
.agents/rules/*.md                  repo-wide rules, frontmatter `trigger: always_on`
.agents/skills/openspec-*/          single copy, generated by OpenSpec 2
.claude/settings.json               kept (Claude-only config)
.claude/rules  -> ../.agents/rules                      SYMLINK (directory)
.claude/skills/openspec-* -> ../../.agents/skills/…     SYMLINKS (created by `openspec update`)
android/AGENTS.md  web-extension/AGENTS.md  rust/AGENTS.md  protocol/AGENTS.md
native-bridge/AGENTS.md  native-bridge/host/AGENTS.md  native-bridge/installer/AGENTS.md
native-bridge/installer/macos/AGENTS.md  native-bridge/installer/windows/AGENTS.md
verify/AGENTS.md  scripts/AGENTS.md
docs/AGENTS.md                      the `docs` node: check/test commands, how to add rules and nodes
docs/checks/check_agent_files.py (+ tests)   check of the new `docs` node
removed: CLAUDE.md, CONTRIBUTION.md, .opencode/, .claude/commands/
```

Design notes:
- **Node `AGENTS.md` loading.** Root `AGENTS.md` contains a node map ("before changing files under
  `<dir>/`, read `<dir>/AGENTS.md`"). This works in all three tools, including Antigravity and
  OpenCode that don't auto-load nested files. Claude Code also loads them on demand. There are no
  `CLAUDE.md` files anywhere (they would suppress `AGENTS.md`). The root file carries no
  Android/macOS/Windows content: platform facts are visible only when the agent works there.
- **Cross-node impact notes**: `rust/AGENTS.md` and `protocol/AGENTS.md` state that Android CI also
  runs for their changes and point at `android/AGENTS.md` (these dirs are in the android node's
  `paths`).
- **Rule frontmatter**: the intersection all three tools accept (Antigravity needs
  `trigger: always_on`; Claude Code ignored it in the spike). Root rules: one concern per file.
- **Moving `CLAUDE.md` content**: Android architecture, package table, test screen, sensitive
  notifications, Android signing identities, the PR lint gate (`./gradlew spotlessCheck detekt
  lintDebug` plus the raw `android.util.Log` guard from `check-android.yml`) → `android/AGENTS.md`;
  per-node command blocks → their node file; macOS signing identity and TCC rules → `native-bridge/installer/macos/AGENTS.md`
  (the one canonical macOS destination; `native-bridge/host/AGENTS.md` keeps only a one-line
  pointer plus the CI artifact names the build-contract gate requires, which are not signing
  instructions); Windows facts → `native-bridge/installer/windows/AGENTS.md`; `host/scripts/macos-test-bundle.sh`
  cites the macOS file; release and
  Conventional Commits → `scripts/AGENTS.md` pointing at `docs/releasing.md`; the runner and clean
  contract and the workflow ownership table → pointers to `docs/ci.md` (not duplicated). Anything
  the code or `docs/` already records is dropped.
- **OpenSpec 2.0 upgrade** (part of this change): `openspec update --dry-run`, then `update`;
  commit; verify idempotence. Edits made inside `openspec-*` skills are overwritten, so none may
  exist (the current ones are generated). Node ≥ 22.12 required for the npm package. Upgrade the
  `superspec` schema if needed (verified working). `.github/` and docs that mention
  `/opsx:name` commands (including `openspec/schemas/superspec/` instructions and templates) are
  updated to the skill names (`/openspec-propose`, …); archived evidence is untouched.
- **New CI node `docs`** (non-release): owns `docs/`, `openspec/`, `AGENTS.md`, `.agents/`,
  `.claude/`, `opencode.json`, `README.md`, `BUILD.md`, `CONTRIBUTING.md`; edits to any
  `*/AGENTS.md` also select it. Adds `check-docs.yml` called from `ci.yml`, included in `ci-ok` and
  last-green selection, documented in `docs/ci.md` and the `ci-pipeline` spec, with selection tests
  in `scripts/tests/test_components.py`. `native-bridge/AGENTS.md` (matches no scaffold path
  today) gets an explicit owner. Node `AGENTS.md` edits should not trigger that node's full CI if
  `components.py` can exclude them without a schema change; otherwise accept the extra CI.
- **Check script** (`docs/checks/check_agent_files.py`, stdlib Python, run by `check-docs`):
  fails when `CLAUDE.md`, `CONTRIBUTION.md` or `.opencode/` reappear; the root file exceeds its
  line cap; a node dir expected to have `AGENTS.md` lacks one; a rule has invalid frontmatter;
  `.claude/rules` or a `.claude/skills/*` entry is not the expected symlink (regular-file stand-in
  on Linux/macOS is an error; on Windows an explicit warning that Claude Code bridges are
  inactive); `.agents/skills` and `.claude/skills` disagree.
- **Existing CI gates that read `CLAUDE.md`**: `native-bridge/host/scripts/test_build_contract.py`
  (`check_docs`) requires host Gradle task names, `native-host-windows`, `native-host-macos` and
  `native-host.cdx.json`; repoint it to `native-bridge/host/AGENTS.md`, keep those strings there.
  Also update the citations in `native-bridge/host/scripts/macos-test-bundle.sh` and the comment in
  `verify/check-android-layout.sh`. Gate semantics stay identical.
- **Deleted paths must stay accounted for** *(from review 3, verified)*: `scripts/components.py`
  validates deleted paths too, so removing `CLAUDE.md`, `CONTRIBUTION.md` and `.opencode/*` from
  `unowned` would fail the `changes` job and block `ci-ok` on this very PR. Keep the entries
  until the migration lands, or add `retired-paths` entries that select `docs`; the check
  script also forbids their return. Tests cover the complete migration diff, including deletions.
- **Pin the OpenSpec CLI** *(from review 3; superseded later: relaxed to `>= 2.0.0, < 3` with a structural check and no CLI in CI, see design D6/D8a)*: `openspec update` regenerates with the *installed*
  CLI (1.17.0 here, the version that corrupted the layout). The procedure is: install exactly
  `@fission-ai/openspec@2.0.0`, assert `openspec --version`, then `update --dry-run`, `update`,
  commit, re-run `update` to prove idempotence. Record the required version in contributor
  guidance (root `AGENTS.md` pointer / `docs/AGENTS.md`), and have the check script (and CI) assert
  `openspec --version` equals the pinned version. Note: `.agents/skills/.openspec-target` is a
  list of tool IDs, not version metadata (installed CLI `shared-skill-target.js`); the check script
  excludes it when comparing skill directories.
  **Status (verified 2026-11-14):** the CLI is already installed globally at exactly 2.0.0
  (`openspec --version` → `2.0.0`, `npm ls -g` → `@fission-ai/openspec@2.0.0`); this change's
  `list`/`status` work under it with the `superspec` schema. The repo itself is **not yet**
  migrated: `.claude/commands`, `.claude/skills`, `.opencode/` are still the 1.x copies and
  `.agents/` does not exist. `openspec update --dry-run` on the real repo reports **61 files would
  change**: delete 10 `.claude/commands/opsx/*`, 10 `.opencode/commands/*`, 10 `.opencode/skills/*`
  and the 10 real `.claude/skills/*` directories; add 10 `.agents/skills/*` and 10 `.claude/skills/*`
  symlinks; add the `.agents/skills/.openspec-target` marker (40 deletions + 20 additions + 1; the
  reviewer re-ran it and confirmed 61). The global `~/.config/openspec/config.json` was already
  migrated by the CLI install and keeps `profile: custom` without the `update` workflow (harmless).
  This matches the target layout, so the implementation's first task is `openspec update` (commit
  the planning artifacts first).
- **Supply-chain scanner coverage** *(from review 3)*: `verify/check-remote-code.mjs` scans fixed
  scopes, so `docs/checks/` and `check-docs.yml` would escape it. Add both to the scanner scopes,
  select `supply-chain` for checker changes via `ci-paths`, and extend the workflow-contract
  suite (`verify/test/workflow-contracts.test.mjs`, per the `ci-pipeline` spec) with positive and
  negative tests for the new workflow and check invocation.
- **Verification standard**: Claude Code and OpenCode exercised empirically; Antigravity verified
  against official docs and real `openspec init` output (and `homepc agy` if available);
  `verify.md` states which were run and which were doc-verified. Windows: re-run the symlink and
  `openspec update` spike on the PC with 2.0.
- **Commits**: Conventional Commits, type `docs`/`chore` (no release bump); PR title and body too.

## Key Decisions

1. Alternative A over rulesync/ruler (user). Fallback to rulesync only if skills-only stops being
   enough.
2. Committed symlinks are acceptable (user); minimise them: one directory link plus
   OpenSpec-created per-skill links.
3. No `CLAUDE.md` at all; root `AGENTS.md` only (user raised; verified on Claude Code 2.1.323).
4. Skills-only OpenSpec (no command files) via upgrade to 2.0 (user raised; verified).
5. New `docs` CI node owns the files and the check (user).
6. Platform/node specifics only in the node's folder (user): Android, macOS, Windows.
7. Initial rule set approved as a framework example (user).
8. *(My call)* node-level rules live in node `AGENTS.md`; repo rules in `.agents/rules/`.
9. *(My call)* root node map instead of `instructions` globs, because it works in all three tools.
10. *(My call)* gates reading `CLAUDE.md` are repointed to `native-bridge/host/AGENTS.md`.
11. *(My call)* no `GEMINI.md`.
12. *(My call, review 3)* retired paths stay accounted for in `components.json` (or `retired-paths`
    selecting `docs`) so the deletion PR passes `changes`/`ci-ok`.
13. *(My call, review 3)* OpenSpec is pinned to exactly 2.0.0 and its version asserted.
14. *(My call, review 3)* the new check and workflow join the supply-chain scanner and the
    workflow-contract tests.
15. *(My call, review 4)* the `docs` node gets its own `docs/AGENTS.md`, and the check requires it.
16. *(My call, review 4)* one canonical destination per platform: macOS signing only in
    `installer/macos/AGENTS.md`, Windows only in `installer/windows/AGENTS.md`; cross-platform
    nodes (e.g. `host/`) point to them instead of copying.

## Open Questions

- Does OpenCode list each skill twice because it scans both `.claude/skills` and `.agents/skills`?
  (Not testable via its CLI so far; spike during implementation.)
- Does `openspec update` 2.0 behave on Windows (symlinks on and off)? Spike on the PC.
- Can `scripts/components.py` express "`*/AGENTS.md` selects `docs` but not the node's CI" without a
  schema change?
- Does Antigravity pick up `.agents/rules/*.md` and the root node map as expected? (Docs only.)
- Claude Code below 2.1.277, or a `CLAUDE.md`/`CLAUDE.local.md` in a parent folder, hides
  `AGENTS.md`: document in the root file; consider the `claude-md-and-agents-md` setting.
