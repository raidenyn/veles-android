# Consolidate Agent Files Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `CLAUDE.md` with a tiny root `AGENTS.md`, per-node `AGENTS.md` files and scoped rule files under `.agents/`, migrate OpenSpec to 2.x skills-only, and add a `docs` CI node with a repo-owned check, working in Claude Code, OpenCode and Antigravity.

**Architecture:** `.agents/` is canonical (rules, skills). Claude Code reaches it through the symlinks `.claude/rules` and `.claude/skills/*`; OpenCode through the pinned `opencode-rules` plugin configured in `opencode.json`; Antigravity natively. A stdlib-Python check (`docs/checks/check_agent_files.py`) run by the new non-release `docs` CI node enforces the layout; nested `AGENTS.md` edits select only `docs` through `global-overrides`.

**Tech Stack:** Python 3.8-compatible stdlib (`unittest`), JSON component map (`scripts/components.py`), GitHub Actions reusable workflows, Node `node:test` workflow-contract suite, OpenSpec CLI 2.x, git symlinks.

**Spec:** `openspec/changes/consolidate-agent-files/` (`proposal.md`, `design.md` D1–D12 with D5a–D5d, `specs/*/spec.md`, `tasks.md`). The plan groups the 66 checklist items of `tasks.md` into 14 tasks; the numbers in parentheses name the `tasks.md` items each plan task fulfils.

## Global Constraints

- Root `AGENTS.md`: hard cap 120 lines (error above), target ≤ 60 (warning above); no Android/macOS/Windows content.
- Start-at-root notice, root: `> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there.`; nested: the same sentence followed by `; if you started in this folder, read this folder's rule files listed below by hand.` (exact strings live in one constant, `NOTICE_ROOT` / `NOTICE_NESTED`, in the check).
- Rule files: flat `.agents/rules/<scope>-<topic>.md`, ≤ 24,000 bytes, frontmatter keys only `trigger`, `globs`, `paths`. `repo-*`: `trigger: always_on`, no `globs`/`paths`. Node rules: `trigger: glob` and YAML-list `globs` and `paths` identical to the scope pattern, no braces. Scope map (D5a): `android`→`android/**`, `web-extension`→`web-extension/**`, `rust`→`rust/**`, `protocol`→`protocol/**`, `native-bridge`→`native-bridge/**`, `host`→`native-bridge/host/**`, `installer`→`native-bridge/installer/**`, `macos`→`native-bridge/installer/macos/**`, `windows`→`native-bridge/installer/windows/**`, `verify`→`verify/**`, `scripts`→`scripts/**`, `docs`→`docs/**`.
- Initial rules: `repo-conventional-commits.md`, `repo-no-time-based-tests.md`, `repo-pre-push-checks.md`, `repo-signing-keys.md`, `android-lint-before-push.md`.
- Symlinks: only `.claude/rules` → `../.agents/rules` and `.claude/skills/<name>` → `../../.agents/skills/<name>`; `.claude/settings.json` stays a regular file.
- `opencode.json`: only `$schema` and `plugins`; exactly one `opencode-rules` entry constrained to the supported range `>=2.0.0 <3` (never `latest`, `*`, or a range admitting a version below 2.0.0 or 3.0.0 and above) with `additionalRuleDirectories` containing `.agents/rules`. The range comes from the separate system-level qualification (owner decision: `>=2.0.0 <3`, verified with plugin 2.0.0 on OpenCode 2.3.0); no document repeats the range.
- OpenSpec CLI `>= 2.0.0, < 3` (2.0.0 last verified; 1.x unsupported); no OpenSpec CLI or any registry install in CI.
- Claude Code ≥ 2.1.277 (≥ 2.1.281 for Bedrock/telemetry-off sessions); a `CLAUDE.md`/`CLAUDE.local.md` in any parent folder hides `AGENTS.md`.
- No `CLAUDE.md`, `CLAUDE.local.md`, `.claude/CLAUDE.md` anywhere; no `CONTRIBUTION.md`, `.opencode/`, `.claude/commands/`.
- Overrides: 10 nested `AGENTS.md` → `["docs"]` (android, web-extension, rust, protocol, native-bridge/host, native-bridge/installer, installer/macos, installer/windows, verify, scripts) except `native-bridge/host/AGENTS.md` → `["docs","native-host"]`; `.github/CODEOWNERS` → `["docs"]`; `.github/components.json` → `["tooling","docs"]`; `.github/workflows/check-docs.yml` → `["docs","tooling","installer","supply-chain"]`. `docs/AGENTS.md` and `native-bridge/AGENTS.md` are owned by `docs`, no override.
- `basename-owners`: `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md` → `["docs"]` (so a new nested guidance file, a stray `CLAUDE.md` or a dropped override selects `docs` on the PR itself).
- `docs/checks/*.py` must not mention scanner tokens (`npx`, `curl … | sh`, …) outside string literals: the remote-code scanner strips `//` and `/* */` comments but not Python `#` comments.
- Commits: Conventional Commits, type `docs`/`chore` (no release bump); WIP commits on the disposable branch use `chore(docs): wip …` so the commit-msg hook accepts them; never `feat`/`fix` on an owned nested `AGENTS.md`. Check names: `docs / agent-files`, `docs / unit-tests`.

## Review Focus

- A `globs` written as a plain string (Antigravity's documented form) in a node rule: the OpenCode plugin would discard it and make the rule unconditional → the check rejects it (Task 7).
- A rule file or `AGENTS.md` checked out with CRLF on Windows → frontmatter still parses and the check does not report false violations (Task 7).
- An OpenSpec skill present in `.agents/skills/` without its `.claude/skills/` link, or a different skill set from another contributor's workflow list → the check names the skill (Task 8).
- A docs-only edit of a file that a gate reads (`docs/reproducible-builds.md` for native-host, `docs/otp-25-installers.md` for installer) passing PR CI and failing on master → `ci-paths` entries and tests (Task 9).
- A new nested `AGENTS.md` without an override, a `CLAUDE.md` from `/init`, or an edit dropping an override passing PR CI because `docs` was not selected → `basename-owners` and the `.github/components.json` override (Task 9).
- An agent started inside `android/`: no rule file arrives; the notice and the node file's rule list must let it read them → verified per tool (Task 13).

---

## Task 1: Preconditions and blocking spikes (tasks 1.1–1.5)

**Files:**
- Create: `openspec/changes/consolidate-agent-files/traceability.md`
- No repository files change.

**Interfaces:**
- Produces: the qualified plugin range (`PLUGIN_RANGE` = `>=2.0.0 <3`), the Antigravity spike verdict, the `CLAUDE.md` traceability table (rows: `CLAUDE.md` section → destination file or "dropped: reason").

- [ ] **Step 1:** Commit the planning artifacts: `git add openspec/changes/consolidate-agent-files && git commit -m "docs(openspec): plan consolidate-agent-files"`. Work from here in a worktree outside the repo for verification runs.
- [ ] **Step 2:** Antigravity spike through `homepc agy` (`homepc ensure` first): ask it to create a scratch workspace rule with `trigger: glob` and `globs: ["android/**"]` (YAML list) plus an extra `paths` key, then report whether it is applied after reading `android/x.txt` and whether the extra key causes an error. Expected: accepted. If rejected, STOP and return to the owner with the D5a fallbacks; do not continue.
- [ ] **Step 3:** Get the qualified plugin range and OpenCode version from the system-level task. Expected: `opencode-rules` constrained to `>=2.0.0 <3` and an OpenCode version. If missing, STOP and ask the owner.
- [ ] **Step 4:** Run `openspec --version`; expected a version in `>= 2.0.0, < 3`. Run `git diff --stat -- .claude/skills .opencode/skills`; expected empty (no hand edits).
- [ ] **Step 5:** Write `traceability.md`: one table row for each of the 13 headings of the 336-line `CLAUDE.md` (five level-two sections: Mandatory Local Checks, Stable Signing Identities, Build & Test Commands, Architecture Overview, Versioning & Releases; plus the 8 Architecture subsections) with a destination from design D4, plus rows for facts found nowhere else (OTP-23 fixture keystore, `codeFormat`, `core.hooksPath`, `bridgeInstallerSupplyChain`, retired `build-toolchain-manifest.yml`, class names `SeedCallback`/`TestResultFlow`). Commit with `docs(openspec): add traceability draft`.

## Task 2: OpenSpec 2.x skills-only migration (tasks 2.1–2.5)

**Files:**
- Modify: `.github/components.json` (`unowned`), `openspec/schemas/superspec/` (schema and templates)
- Generated: `.agents/skills/**`, `.claude/skills/*` (symlinks), removed `.claude/commands/`, `.opencode/`

**Interfaces:**
- Produces: the recorded user-wide workflow list and resulting `openspec-*` skill names (used by `EXPECTED_OPENSPEC_SKILLS` in Task 8 and documented in `docs/AGENTS.md` in Task 5).

- [ ] **Step 1:** `openspec update --dry-run`; expected ≈61–63 changes (see design). Record the workflow list from `~/.config/openspec/config.json` and the skill names it implies.
- [ ] **Step 2:** In `.github/components.json` append `unowned` entries `.agents/`, `.gitattributes`, `opencode.json`, `AGENTS.md`, `native-bridge/AGENTS.md` (keep `.claude/` and `.opencode/`). Run `python3 -I scripts/components.py check`; expected exit 0.
- [ ] **Step 3:** `openspec update`; then replace every `/opsx:<name>` in `openspec/schemas/superspec/` instructions and templates with the matching skill name (`/openspec-propose`, `/openspec-apply-change`, …); `grep -rn '/opsx:' openspec/schemas` expected empty.
- [ ] **Step 4:** Run `openspec update` again; expected "nothing to update" and `git status --short` unchanged. Confirm `.claude/skills/*` are symlinks (`find .claude/skills -maxdepth 1 -type l | wc -l` equals the skill count), no `.claude/commands`, no `.opencode`.
- [ ] **Step 5:** Run `python3 -I scripts/components.py check && ./gradlew scriptsTest`; expected pass. Commit: `chore(openspec): migrate to OpenSpec 2 skills-only`.

## Task 3: Repo-wide rules, symlink, `opencode.json`, `.gitattributes` (tasks 3.1–3.8)

**Files:**
- Create: `.gitattributes`, `.agents/rules/repo-conventional-commits.md`, `repo-no-time-based-tests.md`, `repo-pre-push-checks.md`, `repo-signing-keys.md`, `android-lint-before-push.md`, `opencode.json`, symlink `.claude/rules`
- Draft (not committed here): `.github/CODEOWNERS`

**Interfaces:**
- Produces: the rule files and `opencode.json` whose exact shapes Task 7 and Task 8 validate; the CODEOWNERS draft committed in Task 11.

- [ ] **Step 1:** Write `.gitattributes`: `.agents/** text eol=lf`, `**/AGENTS.md text eol=lf`, `opencode.json text eol=lf`.
- [ ] **Step 2:** Write the four `repo-*` rules, frontmatter exactly `---\ntrigger: always_on\n---`. `repo-pre-push-checks.md` tells the agent to find touched nodes with `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and run each node's checks listed in its `AGENTS.md`, including nodes selected by shared inputs; it names no Gradle, Cargo or npm command. `repo-signing-keys.md`: never regenerate, replace or commit signing keys; node files hold details.
- [ ] **Step 3:** Write `android-lint-before-push.md` with frontmatter `trigger: glob`, `globs:` list `- "android/**"` and `paths:` list `- "android/**"`; body: run `./gradlew spotlessCheck detekt lintDebug` and the raw `android.util.Log` guard copied from the `android / lint` job in `.github/workflows/check-android.yml`.
- [ ] **Step 4:** `ln -s ../.agents/rules .claude/rules`. Write `opencode.json` with `$schema` and `"plugins": [{"package": "opencode-rules@<PLUGIN_RANGE>", "options": {"additionalRuleDirectories": [".agents/rules"]}}]` (here `opencode-rules@>=2.0.0 <3`).
- [ ] **Step 5:** Draft `.github/CODEOWNERS` content (covering `opencode.json`, `.claude/`, `.mcp.json`, `.agents/`, `**/AGENTS.md`) in the scratch directory only.
- [ ] **Step 6:** Smoke test at the repo root with Claude Code and OpenCode (`opencode run -m openai/gpt-6.1-sol`): ask for tokens placed temporarily in two rule bodies; expected: `repo-*` present from start, the Android rule only after a read under `android/`. Remove the temporary tokens. Run `python3 -I scripts/components.py check`; expected exit 0. Commit: `chore(agents): add repo-wide rules and opencode config`.

## Task 4: Root, Android, Rust, Protocol and web-extension guidance (tasks 4.1–4.4)

**Files:**
- Create: `AGENTS.md`, `android/AGENTS.md`, `rust/AGENTS.md`, `protocol/AGENTS.md`, `web-extension/AGENTS.md`
- Source of facts: `CLAUDE.md` (still present) and `traceability.md`

**Interfaces:**
- Consumes: Task 3 rule file names. Produces: node files that list their rules (`android/AGENTS.md` lists `android-lint-before-push.md`; `rust/` and `protocol/` instruct reading it) and carry `NOTICE_NESTED`; the root carries `NOTICE_ROOT`.

- [ ] **Step 1:** Write the root `AGENTS.md` (≤ 60 lines): one-paragraph "what Veles is", the node map ("before changing files under `<dir>/`, read `<dir>/AGENTS.md`") listing the 12 folders, the `.agents/rules/` pointer that lists all four repo-rule paths individually (`repo-conventional-commits.md`, `repo-no-time-based-tests.md`, `repo-pre-push-checks.md`, `repo-signing-keys.md`), the OpenCode requirement line (`opencode-rules` at the version pinned in `opencode.json`, see `docs/AGENTS.md`), OpenSpec range, Claude Code prerequisites, `core.symlinks=true`, "do not run `/init`", `NOTICE_ROOT`. Check `wc -l AGENTS.md` ≤ 60.
- [ ] **Step 2:** Write `android/AGENTS.md` from `CLAUDE.md` sections: Architecture Overview and all subsections, package table, Android signing identities and the OTP-23 fixture keystore, Android build/test commands, a literal `## Rules` section listing `.agents/rules/android-lint-before-push.md`, `NOTICE_NESTED`.
- [ ] **Step 3:** Write `rust/AGENTS.md` and `protocol/AGENTS.md` (their commands from the Build & Test section) each with a literal `## Rules` section (`None.` where no rule exists) containing "Android CI also runs for your changes — read `.agents/rules/android-lint-before-push.md`" and `NOTICE_NESTED`; write `web-extension/AGENTS.md` (extension commands, `rustWasm` prerequisite) with a `## Rules` section (`None.`) and `NOTICE_NESTED`.
- [ ] **Step 4:** Tick the destination column of the corresponding `traceability.md` rows. Commit: `docs(agents): add root and node guidance for android, rust, protocol, web-extension`.

## Task 5: Native-bridge, verify, scripts, docs guidance; gate repoint; delete legacy files (tasks 4.5–4.13)

**Files:**
- Create: `native-bridge/AGENTS.md`, `native-bridge/host/AGENTS.md`, `native-bridge/installer/AGENTS.md`, `native-bridge/installer/macos/AGENTS.md`, `native-bridge/installer/windows/AGENTS.md`, `verify/AGENTS.md`, `scripts/AGENTS.md`, `docs/AGENTS.md`
- Modify: `native-bridge/host/scripts/test_build_contract.py` (`check_docs` caller, line ~632), `native-bridge/host/scripts/macos-test-bundle.sh` (signing citation), `verify/check-android-layout.sh` (comment), `CONTRIBUTING.md`, `openspec/changes/consolidate-agent-files/traceability.md`
- Delete: `CLAUDE.md`, `CONTRIBUTION.md`

**Interfaces:**
- Consumes: `check_docs(claude_md, repro_md, readme)` in `test_build_contract.py` (parameter names unchanged; the first argument's source file changes). Produces: `native-bridge/host/AGENTS.md` containing every string `check_docs` requires: the Gradle task names in `TASKS`, `native-host-windows`, `native-host-macos`, `native-host.cdx.json`.

- [ ] **Step 0:** Scanner prerequisite (the scanner already scans `verify/`, `scripts/` and `native-bridge/installer/windows/` as code, so prose there would trip it): in `verify/test/remote-code.test.mjs` add a failing test that `check-remote-code` ignores a file named `AGENTS.md` but still flags a script file with the same text; implement the exclusion in `verify/check-remote-code.mjs`; run `node --test verify/test/remote-code.test.mjs`; expected pass.
- [ ] **Step 1:** Write `native-bridge/AGENTS.md` (scaffold only; "Tauri is not the approved production path"), `native-bridge/host/AGENTS.md` (host build commands from Build & Test, the strings above, one-line pointers to the platform files), `installer/AGENTS.md`, `installer/macos/AGENTS.md` (the single canonical macOS signing identity and TCC rules from "Stable Signing Identities"), `installer/windows/AGENTS.md`; every file carries `NOTICE_NESTED` and a literal `## Rules` section (`None.` while no rule exists for that scope).
- [ ] **Step 2:** Write `verify/AGENTS.md` (supply-chain verifier, exact Node 26.8.1/npm 11.19.0 pin, install-script and remote-code policy), `scripts/AGENTS.md` (release tooling, Conventional Commits, `commitlint`, `installGitHooks`/`core.hooksPath`, pointer to `docs/releasing.md`), `docs/AGENTS.md` (how to run the check and its tests, add a rule or a node, OpenSpec range and update procedure, a machine-readable `## OpenSpec skills` section (heading, then one bullet per skill name in backticks, from the Task 2 record) that the check parses as the authoritative skill list, the documented workflow list, the recorded fork of the third-party `superspec` schema, the OpenCode plugin: what it is, install at the version in `opencode.json`, update, disabling it or project config for untrusted checkouts, plugin-unavailable behaviour) — none repeats the plugin version.
- [ ] **Step 3:** Add an "AI agent tooling" section to `CONTRIBUTING.md` with the per-tool requirements (OpenCode plugin, Claude Code ≥ 2.1.277/2.1.281 and parent-`CLAUDE.md` caveat, Antigravity notes, `core.symlinks=true`, OpenSpec `>= 2.0.0, < 3` on Linux/macOS, start at the repo root, no version number for the plugin).
- [ ] **Step 4:** In `native-bridge/host/scripts/test_build_contract.py` change `read("CLAUDE.md")` to `read("native-bridge/host/AGENTS.md")` in `Documentation.test_docs_distinguish_production_host_from_scaffold` and the `"CLAUDE.md"` label in `check_docs` to `"native-bridge/host/AGENTS.md"`; assertions unchanged. Update the signing citation in `macos-test-bundle.sh` to `native-bridge/installer/macos/AGENTS.md` and the `CLAUDE.md` mention in `verify/check-android-layout.sh`.
- [ ] **Step 5:** Run `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py' -v` (the command `check-native-host.yml` runs); expected: all pass, including `Documentation`. `bridgeHostToolchainCheck` is not a prerequisite for this static suite.
- [ ] **Step 6:** `git rm CLAUDE.md CONTRIBUTION.md`; leave `CLAUDE.md`, `CONTRIBUTION.md` and `.opencode/` in the map (`unowned`/retired) until Task 9. Complete every `traceability.md` row: each destination exists, or the drop reason is written.
- [ ] **Step 7:** `python3 -I scripts/components.py check`; expected exit 0. Commit: `docs(agents): move CLAUDE.md content into node guidance`.
- [ ] **Step 8:** Record the feature branch (`FEATURE_BRANCH=$(git branch --show-current)`) and create the disposable branch for Tasks 6–10: `git switch -c wip/docs-node`.

## Task 6: The check, part 1: scaffold, legacy files, root and node files (tasks 6.1–6.3)

**Files:**
- Create: `docs/checks/check_agent_files.py`, `docs/checks/test_check_agent_files.py`
- Work happens in a temporary branch `wip/docs-node` (Tasks 6–10 are committed as one commit in Task 11).

**Interfaces:**
- Produces (used by Tasks 7–8 and Task 10):
  - `Finding = collections.namedtuple("Finding", "severity path message")` (`severity` is `"error"` or `"warning"`).
  - `list_files(root: str) -> list` — tracked plus untracked-not-ignored paths via `git ls-files -z -co --exclude-standard` (NUL-separated so Windows paths are not quoted); drop entries ending in `/` (nested git worktrees); link kind is read from the index mode (`git ls-files -s`, 120000) for tracked paths and from `os.path.islink` for untracked ones, so an untracked new skill with its link is not a false positive.
  - `check_repo(root: str, platform: str = sys.platform) -> list` — runs all checks, returns findings.
  - `main(argv=None) -> int` — prints `severity path: message`, returns 1 when any error, else 0; options `--root`, `--platform`.
  - Constants `NOTICE_ROOT`, `NOTICE_NESTED`, `NODE_FILES` (the 12 expected `AGENTS.md` paths including `docs/AGENTS.md`), `NESTED_EXCLUSIVE_DOCS = ("docs/AGENTS.md", "native-bridge/AGENTS.md")`.
  - Test helper in the test file: `make_repo(tmp: str, files: dict, symlinks: dict = None) -> str` that `git init`s and adds files itself (no import of `scripts/tests/gitfixture.py`) with isolated git configuration (`GIT_CONFIG_GLOBAL` pointing at an empty file, `GIT_CONFIG_NOSYSTEM=1`, fixed author/committer environment variables) so a developer's `core.autocrlf` or `core.symlinks` cannot change results.

- [ ] **Step 1: Write failing tests** (`unittest.TestCase`, Python 3.8): `test_clean_fixture_passes`; `test_nested_claude_md_fails` (`android/CLAUDE.md`); `test_claude_local_and_dot_claude_md_fail`; `test_contribution_md_fails`; `test_opencode_dir_and_claude_commands_fail`; `test_root_line_boundaries` (60 lines passes clean, 61 warns, 120 warns, 121 errors); `test_nested_missing_rules_section_fails`; `test_crlf_agents_md_notice_still_matches`; each failing test asserts the finding's severity, path and a distinctive message fragment, not merely that some finding exists; `test_missing_notice_fails` (root and one nested); `test_missing_node_file_fails`; `test_unlisted_nested_agents_md_fails` and `test_listed_missing_file_fails` (node map parity both directions); `test_nested_without_override_fails` (reads `.github/components.json` in the fixture; exempt `docs/AGENTS.md` and `native-bridge/AGENTS.md`).
- [ ] **Step 2:** Run `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`; expected FAIL (module missing).
- [ ] **Step 3: Implement** `list_files`, the legacy-path checks (a `CLAUDE.md`/`CLAUDE.local.md` basename anywhere, `.claude/CLAUDE.md`, `CONTRIBUTION.md`, any path under `.opencode/` or `.claude/commands/`), the line cap and notices, `NODE_FILES` presence, and node-map parity (parse the root file's node map: lines of the form ``- `<dir>/` — …`` and compare with tracked nested `AGENTS.md`). Use `json` to read `.github/components.json` `global-overrides` for the override check.
- [ ] **Step 4:** Re-run the unittest command; expected PASS. Run `python3 docs/checks/check_agent_files.py --root .` on the real tree; expected: only findings that later tasks fix (rules and symlink checks are not yet implemented), no errors for legacy files or notices.
- [ ] **Step 5:** Commit on `wip/docs-node`: `chore(docs): wip agent-file check part 1` (not pushed).

## Task 7: The check, part 2: rule invariants (task 6.4)

**Files:**
- Modify: `docs/checks/check_agent_files.py`, `docs/checks/test_check_agent_files.py`

**Interfaces:**
- Consumes: `Finding`, `list_files`, `make_repo` from Task 6.
- Produces: `parse_frontmatter(text: str) -> dict` (tolerant of CRLF; values: str or list of str; raises `ValueError` on malformed), `SCOPES: dict` (scope → pattern, `None` for `repo`), `check_rules(root, files) -> list`.

- [ ] **Step 1: Write failing tests:** `test_rule_in_subdirectory_fails`; `test_unknown_scope_name_fails`; `test_rule_over_24000_bytes_fails`; `test_unknown_frontmatter_key_fails` (`keywords`); `test_repo_rule_with_globs_fails`; `test_repo_rule_must_be_always_on`; `test_node_rule_globs_must_be_list` (string `globs: "android/**"` fails); `test_node_rule_globs_paths_must_match`; `test_node_rule_pattern_must_equal_scope` (`rust-x.md` scoped to `android/**` fails); `test_brace_expansion_fails`; `test_rule_size_boundaries` (24,000 bytes passes, 24,001 errors); `test_node_rule_requires_trigger_glob` (missing or `always_on` fails); `test_node_rule_scalar_paths_fails`; `test_listed_rule_file_missing_fails`; `test_frontmatter_rejects_unsupported_yaml` (anchors, nested maps, duplicate keys, unquoted lists, empty block list) and `test_frontmatter_strips_bom`; `test_scope_resolved_by_longest_known_prefix` (`web-extension-x.md`, `native-bridge-x.md` and `host-x.md` resolve to the right scope); `test_rule_not_listed_by_scope_agents_md_fails` and `test_repo_rules_listed_by_root`; `test_crlf_rule_parses`.
- [ ] **Step 2:** Run the unittest command; expected FAIL on the new tests.
- [ ] **Step 3: Implement** `parse_frontmatter` with a deliberately restricted grammar: delimited by `---` lines; one `key: value` per line (plain scalars), `key: ["a", "b"]` flow lists of double-quoted strings, or a `key:` line followed by `  - "a"` block items; strip `\r`; strip a leading UTF-8 BOM; reject everything else (anchors, nested maps, duplicate keys, unquoted lists, tabs, a `key:` block list with zero items) with `ValueError` instead of misparsing; resolve the scope of `<scope>-<topic>.md` by the longest known scope that is a prefix followed by `-` (never `split("-", 1)`) and `check_rules` against `SCOPES` and the listing rule (node rules listed in the `AGENTS.md` of the scope's folder: scope→file map `android`→`android/AGENTS.md`, … `docs`→`docs/AGENTS.md`, `repo`→root).
- [ ] **Step 4:** Re-run; expected PASS. Run the check on the real tree; expected: all five rule files valid.
- [ ] **Step 5:** Commit on `wip/docs-node`: `chore(docs): wip agent-file check part 2`.

## Task 8: The check, part 3: symlinks, skills, `opencode.json`, CODEOWNERS, plugin mention (tasks 6.5–6.8)

**Files:**
- Modify: `docs/checks/check_agent_files.py`, `docs/checks/test_check_agent_files.py`
- Create and `git add`: `.github/CODEOWNERS` (from the Task 3 draft; it must be staged before the wip commit so the later squash carries it)

**Interfaces:**
- Consumes: Task 6–7 interfaces. Produces: `check_links(root, files, platform) -> list`, `check_opencode(root) -> list`, `check_codeowners(root) -> list`, `check_plugin_mentions(root) -> list`, `parse_documented_skills(text: str) -> tuple` (reads the bullet names under the `## OpenSpec skills` heading of `docs/AGENTS.md`; that document is the authoritative list, no hard-coded constant).

- [ ] **Step 1: Write failing tests:** `test_missing_rules_symlink_fails`; `test_rules_regular_file_fails_on_linux_warns_on_windows` (index mode via `git ls-files -s` mode 120000; platform parameter `"linux"` vs `"win32"`); `test_wrong_symlink_target_fails`; `test_skill_without_link_fails`; `test_untracked_skill_with_link_is_not_a_false_positive`; `test_orphan_claude_skill_link_fails`; `test_wrong_skill_link_target_fails`; `test_skill_set_must_equal_documented_list` (an added or missing skill fails; updating `docs/AGENTS.md` together with the skills passes); `test_settings_json_symlink_fails`; `test_override_target_sets_exact` (host lacking `native-host`, a nested file mapped to a code node, `check-docs.yml` missing `installer`/`supply-chain`, `.github/components.json` missing `docs` each fail); and `test_codeowners_missing_entry_fails` asserts the missing pattern in the message; `test_openspec_target_marker_excluded`; `test_opencode_extra_key_fails`; `test_opencode_second_plugin_fails`; `test_opencode_unpinned_fails` (`@latest`, `@*`, `@^1.0.0`, `@>=1.0.0 <3`); `test_opencode_range_accepted` (`opencode-rules@>=2.0.0 <3` and `opencode-rules@^2.0.0` pass); `test_opencode_missing_rules_dir_fails`; `test_codeowners_missing_entry_fails`; `test_plugin_mention_required_in_root_and_contributing`; `test_plugin_range_not_repeated_in_docs` (no `opencode-rules@` version spec may appear in `AGENTS.md`/`CONTRIBUTING.md`/`docs/AGENTS.md`).
- [ ] **Step 2:** Run the unittest command; expected FAIL.
- [ ] **Step 3: Implement** `check_links`, `check_opencode`, `check_codeowners`, `check_plugin_mentions` and the override-target check (compare each `global-overrides` value with the exact set from Global Constraints); link kind comes from the git index (`git ls-files -s`), the worktree stand-in decision from `platform` (error on `linux`/`darwin`, warning on `win32` naming `core.symlinks=true` and Developer Mode); wire all into `check_repo`.
- [ ] **Step 4:** Re-run; expected PASS. Run `python3 docs/checks/check_agent_files.py --root .` on the real tree; expected exactly the ten missing nested-`AGENTS.md` override findings (overrides arrive in Task 9) and nothing else; exit 0 is required after Task 9 Step 4.
- [ ] **Step 5:** `git add .github/CODEOWNERS docs/checks` and commit on `wip/docs-node`: `chore(docs): wip agent-file check part 3`.

## Task 9: `docs` node map, overrides, fixtures and tests (tasks 5.1–5.5)

**Files:**
- Modify: `.github/components.json`, `scripts/tests/test_components.py`, `scripts/tests/test_last_green.py`, `docs/ci.md`
- Create: `.github/CODEOWNERS` (already in the tree from Task 8)

**Interfaces:**
- Consumes: `parse_map`, `affected`, `owner_nodes`, `unaccounted`, `load_map` from `scripts/components.py` (no code change expected; change only if a failing test proves it necessary).
- Produces: node `docs` (`release: false`, `jobs: ["agent-files","unit-tests"]`, `paths`: `docs/`, `openspec/`, `AGENTS.md`, `.agents/`, `.claude/`, `opencode.json`, `.gitattributes`, `README.md`, `BUILD.md`, `CONTRIBUTING.md`, `native-bridge/AGENTS.md`), `retired-paths` entries `CLAUDE.md`, `CONTRIBUTION.md`, `.opencode/` → `["docs"]`, 12 new `global-overrides`, and `ci-paths` entries for gate-read docs.

- [ ] **Step 1: Write failing tests** in `test_components.py`: `test_docs_only_change_selects_only_docs`; `test_unowned_selects_nothing` (rewrite `test_unowned` to use `LICENSE`/`site/index.html`); `test_root_build_file_selects_every_node_but_android_and_docs` (rename and extend `test_root_build_file_selects_every_node_but_android`); `test_nested_agents_md_selects_only_docs` (10 files, exact target sets; `native-bridge/host/AGENTS.md` → `{docs, native-host}`); `test_codeowners_and_check_docs_workflow_overrides`; `test_agent_files_with_code_select_both`; `test_retired_agent_files_accounted`; `test_override_does_not_change_release_paths`; `test_new_nested_agent_or_claude_file_selects_docs` (`rust/new-dir/AGENTS.md`, `site/AGENTS.md`, `android/CLAUDE.md` each include `docs`) and `test_components_json_edit_selects_tooling_and_docs`; update the existing tests that now fail with the new map: `test_migration_pr_deleted_app_files_are_accounted` (lines ~265–267 assert the exact `retired-paths` dict: extend it with `CLAUDE.md`, `CONTRIBUTION.md`, `.opencode/` → `["docs"]`), and also the three that still treat documentation as selecting nothing or as a root-build consumer: `test_owner_nodes_contract` (expected set becomes `NODES - {"android", "docs"}`), `test_node_bases_skip_unchanged_node` and `test_evidence_note_uses_full_diff_selection` (replace the `docs/ci.md` fixtures with `LICENSE`, or expect `docs`); `test_gate_read_docs_select_reading_node` (`docs/reproducible-builds.md` selects `docs` and `native-host`; `docs/otp-25-installers.md` selects `docs` and `installer`); `test_no_unowned_entry_is_shadowed` (map check: no `unowned` entry shadowed by a node path or retired path). In `test_last_green.py` the three `docs/ci.md` "docs only" tests (lines ~788, 818, 867) still pass on the new map; optionally switch them to `LICENSE` where they mean "no node" for clarity, and add one test where `docs/ci.md` selects only `docs`.
- [ ] **Step 2:** Run `python3 -m unittest discover -s scripts/tests -v` (with the pinned git-cliff that `./gradlew scriptsTest` installs); expected the new and updated `test_components.py` tests FAIL.
- [ ] **Step 3: Implement** `basename-owners` in `scripts/components.py` (union into `owner_nodes`, include it in `selection_spec`, validate its node names) and the map changes in `.github/components.json` (including the `basename-owners` entries and `.github/components.json` → `["tooling","docs"]`); remove every `unowned` entry now owned by `docs` or retired (`docs/`, `openspec/`, `README.md`, `BUILD.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `CONTRIBUTION.md`, `.claude/`, `.opencode/`) and the temporary Task 2 entries; keep `site/`, `LICENSE`, `.gitignore`, `.dockerignore`, `.idea/`, `.superpowers/`, `native-bridge/feasibility/`; exclude `docs` from the root build files' consumers (do not add `build.gradle.kts`, `settings.gradle.kts`, `gradle.properties` to its `paths`). Audit the files read by `native-bridge/host/scripts/test_build_contract.py`, `native-bridge/installer/scripts/test_build_contract.py` and `verify/` for docs-owned paths and add each to the reading node's `ci-paths`. Implement the shadowed-entry map check in `scripts/components.py` only if the test requires it.
- [ ] **Step 4:** Re-run; expected PASS. Run `python3 -I scripts/components.py check` and `python3 docs/checks/check_agent_files.py --root .`; expected exit 0 for both.
- [ ] **Step 5:** Update `docs/ci.md` (docs node and checks, overrides on owned files, `basename-owners`, retired paths may be exact files not only prefixes, unowned-fixture example). Commit on `wip/docs-node`: `chore(docs): wip docs node map and tests`.

## Task 10: `check-docs.yml`, `ci.yml`, scanner and workflow contracts (tasks 5.6–5.7, 7.1–7.4)

**Files:**
- Create: `.github/workflows/check-docs.yml`
- Modify: `.github/workflows/ci.yml` (outputs near line 45, caller job modelled on the `tooling` job at lines 163–170, `ci-ok.needs` at line 204), `verify/test/workflow-contracts.test.mjs` (`CHECK_WORKFLOWS` at line 667 plus new tests), `verify/check-remote-code.mjs` (scopes and `AGENTS.md` exclusion), `.github/components.json` (`supply-chain` `ci-paths` for `docs/checks/`)

**Interfaces:**
- Consumes: `check-tooling.yml` as the model (inputs `commit-sha`, pinned `actions/checkout` SHA, `permissions: contents: read`).
- Produces: reusable workflow `check-docs.yml` with jobs `agent-files` (`python3 docs/checks/check_agent_files.py --root .`) and `unit-tests` (`python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`); no registry installs.

- [ ] **Step 1: Write failing tests** in `workflow-contracts.test.mjs`: `docs` in `CHECK_WORKFLOWS` (so the reusable/`contents: read`/no-secrets assertions run), declared checks `agent-files`/`unit-tests` match the map, `ci.yml` has a `docs` caller and `ci-ok` needs it, and a reusable assertion that every `run:` step of `check-docs.yml` starts with `python3 docs/checks/` or `python3 -m unittest` (an allow-list), with negative cases mutating the real workflow with `npm ci`, `npm install`, `npx`, `pip install`, `python3 -m pip install`, `curl` and `wget`, each rejected. Add scanner tests in `verify/test/remote-code.test.mjs`: `check-remote-code` flags a script file under `docs/checks/` that uses `npx`, and does not flag the same text inside a file named `AGENTS.md`.
- [ ] **Step 2:** Run `node --test verify/test/workflow-contracts.test.mjs verify/test/remote-code.test.mjs`; expected FAIL on the new tests.
- [ ] **Step 3: Implement** `check-docs.yml` (copy `check-tooling.yml` header, drop the Node/Java/Gradle steps), the `ci.yml` output, caller job and `ci-ok.needs` entry, the `CHECK_WORKFLOWS` addition, scanner scopes for `docs/checks/` and `check-docs.yml`, the `AGENTS.md` exclusion, and the `supply-chain` `ci-paths` entry.
- [ ] **Step 4:** Re-run; expected PASS. Run `python3 -I scripts/components.py check`, `./gradlew scriptsTest`, `python3 -m unittest discover -s docs/checks -v`, `node --test verify/test/remote-code.test.mjs`, the host build-contract suite (`python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'`) and the installer script suites (`./gradlew bridgeInstallerScriptsTest`, not on Windows) and, if its prerequisites exist, `verify/verify-supply-chain.sh`; expected all pass.
- [ ] **Step 5:** Commit on `wip/docs-node`: `chore(docs): wip docs workflow and contracts`.

## Task 11: Atomic activation commit (task 7.5)

**Files:**
- Branch operation only.

- [ ] **Step 1:** Return to the recorded feature branch (`git switch "$FEATURE_BRANCH"`) and run `git merge --squash wip/docs-node`; `git status --short` should list the check, tests, map, workflow, scanner, CODEOWNERS and docs changes.
- [ ] **Step 2:** Re-run every command from Task 10 Step 4 on the squashed tree; expected all pass.
- [ ] **Step 3:** Commit: `chore(docs): add docs CI node and agent-file check` (body: no release bump). Delete the disposable branch with `git branch -D wip/docs-node` (a squash leaves it unmerged, so `-d` refuses; its individual WIP commits are intentionally discarded). Confirm no disposable rule token remains (`git grep -n TOKEN -- .agents`).

## Task 12: Live reference migration (tasks 8.1–8.3)

**Files:**
- Modify: every live file that mentions `CLAUDE.md`, `CONTRIBUTION.md`, `.opencode`, `.claude/commands` or `/opsx:` (found by search), including `docs/otp-23-lifecycle-scenarios.md`.

- [ ] **Step 1:** `grep -rnE 'CLAUDE\.md|CONTRIBUTION\.md|\.opencode|\.claude/commands|/opsx:' --exclude-dir=build --exclude-dir=node_modules --exclude-dir=.git .`; classify each hit as live or frozen (`docs/superpowers/plans|specs`, `docs/otp-23-contract-alignment.md`, `protocol/docs/reviews/`, `native-bridge/feasibility/evidence/`, archived changes are decided explicitly).
- [ ] **Step 2:** Update each live hit to its new destination or skill name; leave frozen evidence untouched and list the exclusions in `traceability.md`.
- [ ] **Step 3:** Confirm no `/opsx:` remains in `openspec/schemas/superspec/` and the fork is recorded in `docs/AGENTS.md`. Run the agent-file check and `python3 -I scripts/components.py check`; expected pass. Commit: `docs: migrate live references to AGENTS.md and skill names`.

## Task 13: Verification (tasks 9.1–9.7)

**Files:**
- Create: notes for `openspec/changes/consolidate-agent-files/verify.md` (written in the verify step of the workflow)

- [ ] **Step 1:** From a worktree outside the repo and for each tool, record the exact version and run the scoping matrix with disposable fixture rules: `repo-*` present from the first turn; a rule scoped to `rust/**` absent after an `android/` read; a `native-bridge/**` fixture rule loaded after reads in `native-bridge/host/` and `native-bridge/installer/`; extra `paths`/`trigger` keys cause no error; a second read does not duplicate; the result when started inside `android/` (expected: no rule file delivered, `AGENTS.md` chain and notice present).
- [ ] **Step 2:** Claude Code: `AGENTS.md` without `CLAUDE.md`; nested file on read; skill links. OpenCode (with the plugin installed by following the written steps on a clean profile): nested `AGENTS.md`; whether skills are listed twice; the plugin-unavailable state (offline, empty cache or disabled) and manual discovery through the root file. Antigravity through `homepc agy`: `AGENTS.md`, `.agents/rules` including the scoped rule, `.agents/skills`, the node map. Label documentation-only evidence.
- [ ] **Step 3:** Windows PC: symlinks with `core.symlinks` true and false, the check's warning, `openspec update` on 2.x, native rule scoping in the tools that run there.
- [ ] **Step 4:** Finish the traceability table; store it with the change; assemble the verification notes (versions, observed vs documentation-only, idempotence result from Task 2, node-folder limitation per tool).

## Task 14: Pre-PR checks, pull request, finalize and archive (tasks 10.1–10.5)

**Files:**
- Create: `openspec/changes/consolidate-agent-files/verify.md`, `finalize.md` (through the workflow)

- [ ] **Step 1:** Run the mandatory lint gate for the submitted changes: `./gradlew spotlessCheck detekt lintDebug` and the raw `android.util.Log` guard from `.github/workflows/check-android.yml`; if a check cannot run, report the blocker instead of claiming readiness.
- [ ] **Step 2:** Run the checks of every touched node: `python3 -I scripts/components.py check`, `./gradlew scriptsTest`, `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -v`, both build-contract suites, `node --test verify/test/workflow-contracts.test.mjs`.
- [ ] **Step 3:** Validate the PR title/body and each local commit with `python3 -I scripts/commitlint.py --message "<message>"`; titles use `docs`/`chore`; none touching an owned nested `AGENTS.md` is `feat` or `fix`.
- [ ] **Step 4:** Push, create the PR (title `docs: consolidate agent guidance into AGENTS.md and .agents`), link it, watch CI until `ci-ok` is green; address findings.
- [ ] **Step 5:** After merge, write `finalize.md` and archive the change in a follow-up PR (`docs(openspec): archive consolidate-agent-files`), syncing deltas so `agent-guidance-layout` and `openspec-skills-delivery` are created and `ci-pipeline`, `component-versioning` and `pr-last-green-selection` updated.
