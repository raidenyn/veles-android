## Context

`AGENTS.md` is the first file an agent reads in this repository. The root file carries the node map and
the `repo-*` rule list; each node file carries that folder's commands and constraints. Over time the node
files also absorbed architecture narratives, workflow tables and policy prose: nine of the 13 files are
over 40 lines, 357 lines over in total, with `docs/AGENTS.md` at 177 and `android/AGENTS.md` at 137.

`docs/checks/check_agent_files.py` enforces the layout but only checks the root's length (`ROOT_LINE_WARN
= 60`, `ROOT_LINE_MAX = 120`). Nested files, where most of the growth is, have no cap. The check also
parses the OpenSpec skill list from `docs/AGENTS.md` (`DOCUMENTED_SKILLS`), requires the root node map and
all `repo-*` listings there, and protects `**/AGENTS.md` and a few instruction files with CODEOWNERS.
`native-bridge/host/scripts/test_build_contract.py` reads `native-bridge/host/AGENTS.md` for Gradle task
and artifact names.

The user asked for a hard 40-line policy on every agent file, with the displaced information in docs or
rules, and a check that enforces it. This design says how to migrate the content and the check without
losing a fact or breaking a spec.

## Goals / Non-Goals

**Goals:**

- Every tracked `AGENTS.md` (root and nested) is at most 40 lines, enforced as a hard error at 41.
- Each `AGENTS.md` keeps only purpose, commands, folder constraints, the `## Rules` list, the fixed
  start-at-root notice and links to detail.
- Every displaced fact has exactly one home — a docs page, an existing rule, or a recorded deliberate drop
  in the change's `traceability.md` — and is linked from the file that used to hold it; nothing is
  duplicated.
- The check, its tests, the CODEOWNERS protection and the four affected main specs agree with the new
  layout.
- The move keeps the host build-contract assertions, the node-map parity, the repo-rule listings, the
  plugin-range guard, the skill-set check and the hook pointer block working.

**Non-Goals:**

- No change to product code, workflow files, dependencies, toolchains, release behavior or runtime. The
  doc and checker edits still select the `installer`, `native-host` and `supply-chain` nodes through their
  ci-paths (`docs/otp-25-installers.md`, `docs/reproducible-builds.md` and `docs/checks/`), so their checks
  run under the pre-push rule; that is a consequence, not a workflow change.
- No new `.agents/rules/` file: architecture and procedures are not enforceable constraints.
- No change to the rule-size caps, rule frontmatter, symlink rules, hook wiring or the review workflow.
- No rewrite of generated `openspec-*` skills or `openspec/schemas/` content beyond re-applying the
  existing fork divergences after the move.
- No new line cap on `docs/` pages; only `AGENTS.md` files are capped.

## Decisions

### D1. One hard cap, one code path

Add `AGENTS_LINE_MAX = 40`; delete `ROOT_LINE_WARN` and `ROOT_LINE_MAX`. A helper
`_check_line_cap(path, text)` counts `len(text.splitlines())` and returns one error finding above the cap:
`has <n> lines; the AGENTS.md cap is 40 lines`. `_check_root` and `_check_nested_files` call it; the count
runs on the already CRLF-normalized `_read_text` result, so a Windows checkout behaves the same. No warning
band: 40 passes, 41 fails.

*Alternative rejected:* a warning at 40 and an error at 50 leaves the exact growth the user wants stopped;
a per-file limit table multiplies maintenance for no benefit.

### D2. `docs/openspec-skills.md` is the skill-list source

`DOCUMENTED_SKILLS` changes from `docs/AGENTS.md` to `docs/openspec-skills.md`; the parser, the docstring,
the two check messages and the test comment change to name the new file. The page holds the user-wide
workflow list and the 11 `openspec-*` names, under the same `## OpenSpec skills` heading the parser expects,
so a newer OpenSpec 2.x that adds a skill edits a data page instead of an always-read `AGENTS.md`.

*Alternative rejected:* keeping the 11 bullets in `docs/AGENTS.md` consumes a third of its budget and makes
the 40-line cap fail on a routine CLI update.

### D3. Explicit plugin-range guard membership

`PLUGIN_RANGE_DOCS` currently reaches `docs/AGENTS.md` only through `DOCUMENTED_SKILLS`. After D2 it lists
`docs/AGENTS.md` explicitly, plus `docs/opencode-rules.md` and `docs/agent-guidance.md`.
`test_plugin_range_not_repeated_in_docs` keeps its `docs/AGENTS.md` case and adds the two new pages;
`clean_files()` must therefore write both pages, and `codeowners_text()` must carry their owner lines.

### D4. Docs ownership for moved instructions

`docs/agent-guidance.md`, `docs/openspec-workflow.md` and `docs/opencode-rules.md` are agent instructions
(the first tells how to add rules, the second how to run `openspec update` and re-apply the fork, the third
how to install a plugin and why an untrusted checkout is dangerous). They get one CODEOWNERS line each,
plus entries in `CODEOWNER_PATTERNS` and `CODEOWNER_SAMPLE_PATHS` and their test copies.
`docs/android.md` and `docs/openspec-skills.md` stay under the `docs` node: they hold product documentation
and a data list, and the executable pieces (`.githooks/`, `.agents/skills/`, the verifier scripts) remain
owned where they live.

### D5. Content map

The nine files are rewritten to the targets in `brainstorm.md` (root 39, android 33, docs ~27, scripts 34,
verify 28, macos 39, host 33, rust 30, native-bridge 30). The section-by-section destinations are:

| Source | Destination |
|---|---|
| `docs/AGENTS.md` check explanation and adding a rule/node | new `docs/agent-guidance.md` |
| `docs/AGENTS.md` OpenSpec update and fork divergences | new `docs/openspec-workflow.md` |
| `docs/AGENTS.md` workflow list and skill names | new `docs/openspec-skills.md` |
| `docs/AGENTS.md` OpenCode plugin and untrusted-checkout warning | new `docs/opencode-rules.md` |
| `android/AGENTS.md` eight architecture sections and adding a handler | new `docs/android.md` |
| `android/AGENTS.md` signing build line and backup detail | `docs/android.md` (the keystore and init-script names stay in the node file) |
| `scripts/AGENTS.md` Conventional Commits, versions, tags, env injection | `docs/releasing.md` |
| `android/AGENTS.md` `androidGitVersion` `prefix`/`codeFormat` | `docs/releasing.md` (single home; `android/AGENTS.md` and `scripts/AGENTS.md` link it) |
| `scripts/AGENTS.md` git hooks and workflow ownership | `docs/ci.md` |
| `verify/AGENTS.md` tool pins, prerequisites, policy checks | `docs/reproducible-builds.md` |
| `macos/AGENTS.md` signing/TCC prose | compressed in place; history already in `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md` (D6); a missing detail goes to `docs/desktop-ble-poc.md` |
| `host/AGENTS.md` CI and SBOM prose | `docs/ci.md`, `docs/reproducible-builds.md` (the required artifact names stay) |
| `native-bridge/AGENTS.md` Tauri rationale | `docs/otp-23-requirements-matrix.md` (D-01) |
| `rust/AGENTS.md` prose | compressed in place |

Root keeps the node map, the eight `repo-*` paths, one line for the plugin requirement (the one-sentence
statement that OpenCode users need the pinned `opencode-rules` plugin), one line for the `/init` and
parent-`CLAUDE.md` constraints and the hook block; versions, checkout and the OpenSpec CLI
range live in `CONTRIBUTING.md`. `docs/AGENTS.md` keeps `(schema version v5)`, the one-sentence OpenCode
plugin requirement with a link, the check commands, the Gate B command, the exit semantics, the short hook
section and links to the new pages. `docs/openspec-workflow.md` links `CONTRIBUTING.md` for the CLI range
rather than repeating it; that range has one home in `CONTRIBUTING.md`.

### D6. Documentation edits that are not new pages

- `docs/ci.md`: a short "Git hooks" section (`./gradlew installGitHooks` sets `core.hooksPath=.githooks`,
  runs from `:app:preBuild`, acts only when unset and is skipped under `CI=true`) and any workflow
  ownership fact not already in its tree/table.
- `docs/releasing.md`: the commit-type-to-bump table is already there; add the build-time version
  injection facts (`VELES_EXTENSION_VERSION`, `VELES_INSTALLER_VERSION`, `VELES_HOST_VERSION`) and the
  `androidGitVersion` `prefix = "android-v"` / `codeFormat = "MMNNPP"` facts. That file is their single
  home; `android/AGENTS.md` and `scripts/AGENTS.md` link it.
- `docs/reproducible-builds.md`: the exact Node/npm pin, the `verify/package.json` and
  `verify/cargo-tools.toml` pins, the 0/1/2 contract, the install-script and remote-code policy details
  and the six-SBOM list, where they are not already present.
- `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md` already hold the macOS identity and TCC
  history; this change adds nothing canonical there. `macos/AGENTS.md` keeps the canonical identity
  statement and the rules under its cited `## Signing identity and TCC (macOS)` heading: no ad-hoc
  substitution or key export, the exact test-bundle identifier, no fallback identity and signing only in
  `publish-release.yml`. Only the surrounding prose is compressed. If a needed detail is missing from
  both docs, add it to `docs/desktop-ble-poc.md` and cite it from the node file — never move a canonical
  rule out of the node.
- `docs/AGENTS.md` states the root 40-line cap and the one-pointer-line rule (the `Fail open`
  requirement); `docs/hook-delivered-rules.md` and the adding-a-node note in `docs/agent-guidance.md` link
  that section, so the policy has one home.
- `CONTRIBUTING.md`: repoint the three `docs/AGENTS.md` references to the new agent-guidance,
  OpenCode and OpenSpec pages, and say the node files link their detail in `docs/`. It remains the single
  home of the OpenSpec CLI range.
- **Cross-reference sweep**: root `AGENTS.md` ("Setup and troubleshooting: `docs/AGENTS.md`" → the
  OpenCode page), `CONTRIBUTING.md` (three pointers), `docs/ci.md`, `docs/otp-11-debug-controls.md`,
  `docs/otp-23-lifecycle-scenarios.md`, `native-bridge/host/scripts/macos-test-bundle.sh`,
  `native-bridge/installer/macos/AGENTS.md`, `scripts/AGENTS.md`, the checker docstrings and messages that
  name `docs/AGENTS.md` (including the `check_links` messages) and the test comment that calls it the
  authoritative source.

### D7. Specification deltas

Four deltas, each with the full modified requirement text:

1. `agent-guidance-layout`: 40-line root and node requirements; the shorter file contract; the
   platform-canonical-folder requirement allows narrative in the linked docs page; "Existing content is
   preserved" accepts the linked page for the fixture build detail; the OpenCode requirement moves the
   guidance and untrusted warning, keeps the one-sentence statement in `docs/AGENTS.md` and extends the
   CODEOWNERS list; the warning on `docs/opencode-rules.md` is tool-neutral and cross-linked from
   `CONTRIBUTING.md`, because it covers `.claude/settings.json` as well as `opencode.json`; the check
   requirement covers every `AGENTS.md` and its boundary tests.
2. `superspec-review-gate`: the fork divergence list moves to `docs/openspec-workflow.md`; the
   `(schema version v5)` text stays in `docs/AGENTS.md`. The requirement's list of workflow-instruction
   files that must not name a model gains `docs/openspec-workflow.md`, `docs/agent-guidance.md` and
   `docs/opencode-rules.md`.
   Divergence item 5's "this file" refers to the version mention, which stays in `docs/AGENTS.md`, so the
   reinstall checklist in `docs/openspec-workflow.md` names that file explicitly instead of "this file".
3. `openspec-skills-delivery`: the workflow list and skill names move to `docs/openspec-skills.md`;
   the check reads it there; the fork recording moves to `docs/openspec-workflow.md`. The OpenSpec CLI
   range stays single-homed in `CONTRIBUTING.md`, and `docs/openspec-workflow.md` links it.
4. `hooked-rule-delivery`: the untrusted-checkout warning moves to `docs/opencode-rules.md`; the short
   hook section stays in `docs/AGENTS.md`.

`commit-conventions` gets no delta: the guidance-only-`docs` sentence stays in `scripts/AGENTS.md`.

### D8. Test changes

- `test_constants_are_exact`: replace the root cap constants with `AGENTS_LINE_MAX == 40`.
- Replace `test_root_line_boundaries` with root 40/41 and nested 40/41 cases; assert one finding, the
  error severity and the `the AGENTS.md cap is 40 lines` fragment. `root_text(target_lines=...)` already
  supports the root; `nested_text()` gains the same `target_lines` pad (or the two nested cases build the
  text directly), because it currently has no way to reach an exact boundary.
- Slim the clean fixture root to about 35 lines by emitting the eight repo-rule bullets two per line, so
  the tests that append a rule or a pointer line stay under 40.
- Skill fixtures move to `docs/openspec-skills.md`; `nested_text("docs")` no longer appends the list;
  `test_documented_skill_section_parses` parses the new page; the two "in docs/AGENTS.md" messages become
  "in docs/openspec-skills.md".
- `EXPECTED_CODEOWNER_PATTERNS` and the fixture CODEOWNERS gain the three owner lines; `PLUGIN_RANGE_DOCS`
  expectations add the two new pages; `clean_files()` writes the new pages.
- Keep every existing negative test passing; an over-long nested file and an over-long root each get a
  dedicated case. The verification record lists the final `git ls-files '*AGENTS.md' | xargs wc -l` output,
  proving every file is at or under 40.

## Risks / Trade-offs

- **A fact is lost or weakened in the move** → the destination table names a home for every section; the
  change's `traceability.md` records the section-by-section destinations and every deliberate drop, and
  `git grep` finds stale pointers.
- **A docs page becomes the new dumping ground** → the new pages are topic-scoped and linked from the
  files that used to hold the text; only `AGENTS.md` is capped, but the destination pages are reviewable
  and owner-protected where they carry instructions.
- **The root sits at 39–40 and a new node/rule/hook pushes it over** → the policy is deliberate: the change
  adding content must compress or link; the new error and the procedure text in
  `docs/agent-guidance.md` / `docs/hook-delivered-rules.md` make that explicit. The skill list, the one
  routinely growing item, moved out.
- **`docs/AGENTS.md` loses the skill list from the place an agent reads first** → the root and docs pages
  link `docs/openspec-skills.md`; the check still enforces parity with `.agents/skills/`.
- **CODEOWNERS last-match semantics** → the three new lines are added without a later catch-all `docs/`
  rule; `check_codeowners` resolves sample paths with GitHub's last-match rule and fails if an owner is
  lost.
- **The host build-contract gate** → `native-bridge/host/AGENTS.md` keeps the task names,
  `native-host-windows`, `native-host-macos` and `native-host.cdx.json`; the suite runs in verification.
- **The hook pointer block is generated** → after any root edit, run
  `python3 -I scripts/agent_rules.py pointers --write` and the check; the block is one line per hooked rule.
- **CRLF checkouts** → the count uses the normalized text, so Windows and Linux agree; the `.gitattributes`
  LF policy is unchanged.

## Migration Plan

1. Add the five new docs pages and move the detail into the existing docs pages listed in D5/D6.
2. Rewrite the nine `AGENTS.md` files to their targets and add the links; run
   `python3 -I scripts/agent_rules.py pointers --write` for the root block.
3. Change the checker (`AGENTS_LINE_MAX`, D2, D3) and `.github/CODEOWNERS`; update the tests and fixtures.
4. Update `CONTRIBUTING.md` and the cross-references.
5. Write and apply the four spec deltas.
6. Verify: the check, its unit tests, the `git ls-files` sweep, `components.py check`,
   `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and the checks
   of every node it selects (at least `docs`, `installer`, `native-host` and `supply-chain`; `.github/` is
   global), the host build-contract suite and the `git grep` sweep. Record the final per-file line counts
   and the section-by-section destinations in `traceability.md`; the verify phase links it into
   `verify.md`, which is regenerated on each run.

Rollback is the reverse diff; no data or runtime state is involved.

## Open Questions

None. The user chose the docs-for-prose split and the Opus reviewer preset; the line budgets were
prototyped before writing.
