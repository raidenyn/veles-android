## Design Context

The repository's 13 `AGENTS.md` files carry architecture, release, tooling and supply-chain prose that
belongs in documentation. Nine of them exceed 40 lines, **357 lines over the cap** in total:
`docs/AGENTS.md` 177, `android/AGENTS.md` 137, `scripts/AGENTS.md` 102, `verify/AGENTS.md` 60, root
`AGENTS.md` 56, `native-bridge/installer/macos/AGENTS.md` 49, `native-bridge/host/AGENTS.md` 48,
`rust/AGENTS.md` 45 and `native-bridge/AGENTS.md` 43. The check in `docs/checks/check_agent_files.py`
only warns above 60 lines and fails above 120, and only for the root.

## Design Summary

This change makes **40 lines a hard cap on every `AGENTS.md`** — root and every nested tracked file — and
fails the check at 41 with `has <n> lines; the AGENTS.md cap is 40 lines`. Each `AGENTS.md` becomes a
short contract for its folder: what it is, the commands to run, the constraints an agent must obey, its
`## Rules` list and links to the detail. Narrative detail moves to `docs/` pages; `.agents/rules/` stays
reserved for enforceable constraints, not prose.

The per-file budgets below were prototyped against the real content; every one fits at or under 40
including the title, notice, headings and blank lines:

| `AGENTS.md` | Now | Target | What stays | Where the detail goes |
|---|---|---|---|---|
| root | 56 | 39 | purpose, notice, 12-entry node map, 8 repo-rule paths, one `opencode-rules` line, `/init` and parent-`CLAUDE.md` caveat, hook pointer block | `CONTRIBUTING.md` (tool versions, checkout, OpenSpec range) and `docs/agent-guidance.md` |
| `android/` | 137 | 33 | purpose, short signing block (including the fixture keystore names), Build & Test commands, protocol pointer, rules | new `docs/android.md` (core flow, bank-config DB, test screen, sensitive notifications, packages, navigation, testing, adding a handler, fixture build line) |
| `docs/` | 177 | ~27 | purpose and links, check commands, exit semantics, short hook-rule section, rules | new `docs/agent-guidance.md` (check explanation, adding a rule or node), `docs/openspec-workflow.md` (update procedure, fork divergences), `docs/opencode-rules.md` (plugin install, update, untrusted-checkout warning), `docs/openspec-skills.md` (workflow list and the 11 skill names the check parses); hook detail already in `docs/hook-delivered-rules.md` |
| `scripts/` | 102 | 34 | purpose and links, commands, project-flow exit codes, the required guidance-only-`docs` sentence | `docs/ci.md` (workflow ownership, git hooks), `docs/releasing.md` (types and bumps, versions, tags, env injection) |
| `verify/` | 60 | 28 | purpose and link, commands, 0/1/2 exit contract | `docs/reproducible-builds.md` (tool pins, prerequisites, policy checks) |
| `native-bridge/installer/macos/` | 49 | 39 | purpose, canonical-identity statement, compressed signing/TCC block under its existing heading, commands, rules | `docs/otp-25-installers.md`, `docs/desktop-ble-poc.md` |
| `native-bridge/host/` | 48 | 33 | purpose, commands, toolchain and target-dir facts, CI artifact and SBOM names, platform pointers | `docs/ci.md`, `docs/reproducible-builds.md` |
| `rust/` | 45 | 30 | purpose, commands, output notes, Android-CI rule pointer | no new page; prose compressed |
| `native-bridge/` | 43 | 30 | purpose and Tauri warning, links, commands, pin facts, rules | `docs/otp-23-requirements-matrix.md` (D-01 rationale) |

**Headroom under the hard cap.** Moving the 11-bullet skill list out of `docs/AGENTS.md` into
`docs/openspec-skills.md` gives that file several lines of headroom (the minimal prototype is 27 lines; a
fuller draft with all six hook-detail items is about 35), so a newer OpenSpec 2.x that adds a skill edits
the data page, not an always-read `AGENTS.md`. The root and the macOS file sit one or two lines under the
cap. The root is the tightest file because the check requires the node map and all eight `repo-*` paths
there. Any change that adds a node, a `repo-*` rule, a root line **or a hooked rule** must keep the file at
or under 40 by compressing it or moving a fact to docs: each hooked rule adds one line to the root pointer
block when `pointers --write` runs. That is the policy the user asked for. The new error makes the
constraint visible in the adding-a-node procedure in `docs/agent-guidance.md`, and the hooked-rule
procedure in `docs/AGENTS.md` and `docs/hook-delivered-rules.md` states the root cap and the compression
step.

## Alternatives Considered

### Alternative A: docs for prose, hard 40-line cap on every AGENTS.md (chosen)

- **Approach**: add `AGENTS_LINE_MAX = 40` to the check, replace the root 60/120 split with one error
  above 40, apply the same cap to nested files, and move the excess prose into docs pages. Keep
  `.agents/rules/` for constraints only.
- **Pros**: matches the request; the agent's entry file stays short and scannable; docs pages are already
  the repository's home for narrative; scoped rules keep their narrow, enforceable purpose; the check is
  one constant and one boundary test per file kind.
- **Cons**: the same fact now has a navigation hop from the node file to a docs page; four new docs files
  plus one skills data page need owners and links; some duplication with existing docs must be removed
  rather than copied.
- **Why chosen**: it is the only option that satisfies a hard 40-line cap without turning every read in a
  folder into a large context load.

### Alternative B: move everything into `.agents/rules/`

- **Approach**: put the displaced architecture and tooling prose into new `<scope>-architecture.md` rules
  so the plugin delivers them whenever a file in the folder is read; `AGENTS.md` keeps only commands.
- **Pros**: no navigation hop; the detail arrives automatically with the files it describes.
- **Cons**: node rules load on every read in the folder and `repo-*` rules load always, so the same prose
  returns to the context the cap is meant to protect; the 24,000-byte node cap and 4,096-byte repo cap
  bound the move. Architecture is not an enforceable constraint, which is what the rules directory is
  for.
- **Why not chosen**: it works against the reason for the cap and overloads the rule mechanism.

### Alternative C: cap only new or edited files

- **Approach**: enforce 40 lines for an `AGENTS.md` that a change touches, grandfather the existing 357
  excess lines, and shrink files opportunistically.
- **Pros**: no large content migration in this change.
- **Cons**: the policy is not true on the tree that ships it; the existing big files remain the examples
  agents copy; a later change can silently grow a file it did not fully rewrite.
- **Why not chosen**: the user asked for a hard policy, and a check that passes on the current tree is
  the point.

### Alternative D: check only, no content move

- **Approach**: add the 40-line cap and leave the files alone.
- **Pros**: smallest diff.
- **Cons**: CI fails on the files the change is about; the policy cannot be adopted.
- **Why not chosen**: it cannot pass.

## Agreed Approach

Use Alternative A.

- **One cap, one code path**: `AGENTS_LINE_MAX = 40` in `docs/checks/check_agent_files.py`. A shared helper
  counts `text.splitlines()` after the existing CRLF normalization and returns one error finding when the
  count is above 40, with the message `has <n> lines; the AGENTS.md cap is 40 lines`. `_check_root` and
  `_check_nested_files` both use it; `ROOT_LINE_WARN` and `ROOT_LINE_MAX` are removed. A file at exactly 40
  lines passes; 41 fails. There is no warning band.
- **What stays in an `AGENTS.md`**: purpose in two or three lines; the commands and their one-line
  rationale; the constraints that must be obeyed in that folder; the `## Rules` section; the fixed
  start-at-root notice; links to the docs page(s) that hold the detail. No architecture narratives, no
  workflow tables, no policy essays. Root constraints that an agent must obey stay at the root in one
  line: do not run `/init`, and a parent `CLAUDE.md`/`CLAUDE.local.md` hides `AGENTS.md`; the Claude Code
  version minimum, `core.symlinks=true` and the OpenSpec CLI range live in `CONTRIBUTING.md`, which the
  root links.
- **Section destinations are exhaustive.** Every section of the nine files has a stated home:
  - `docs/AGENTS.md`: the intro, check commands and exit semantics, the Gate B validator command, the
    short hook-rule section and `## Rules` stay. The check explanation and "adding a rule or node" go to
    `docs/agent-guidance.md`; the OpenSpec update procedure and the full superspec-fork divergence list go
    to `docs/openspec-workflow.md`; the required workflow list and the 11 skill names go to
    `docs/openspec-skills.md`; the OpenCode plugin install/update/caveat section and the untrusted-checkout
    warning go to `docs/opencode-rules.md`. The `(schema version v5)` text stays in `docs/AGENTS.md`.
  - `android/AGENTS.md`: all eight architecture sections and "Adding a New Bank Handler" go to
    `docs/android.md`; the signing block stays, including the `otp23-fixture.keystore` and
    `fixture-signing.gradle` names and the `## Signing identities (Android)` heading that
    `docs/otp-11-debug-controls.md` cites; the Build & Test commands, the NDK/rustup prerequisite and the
    `protocolJvmTest` pointer stay. The fixture build line and backup detail go to `docs/android.md`.
  - `scripts/AGENTS.md`: the Build & Test commands, the `project_flow.py` exit codes and the
    guidance-only-`docs` sentence (required by `commit-conventions`) stay; the Conventional Commits
    explanation, the versions/tags paragraph and the `VELES_*_VERSION` injection facts go to
    `docs/releasing.md`; the git-hooks paragraph and the workflow-ownership table go to `docs/ci.md`.
  - `verify/AGENTS.md`: the commands and 0/1/2 exit contract stay; the tool pins, prerequisites and policy
    checks go to `docs/reproducible-builds.md`.
  - `native-bridge/installer/macos/AGENTS.md`: the canonical-identity statement, the
    `## Signing identity and TCC (macOS)` heading (cited by
    `native-bridge/host/scripts/macos-test-bundle.sh` and `docs/otp-23-lifecycle-scenarios.md`) and the TCC
    rule pointer stay; the rest is compressed, with detail already in `docs/otp-25-installers.md` and
    `docs/desktop-ble-poc.md`.
  - `native-bridge/host/AGENTS.md`: the commands, toolchain pin, target directory, the
    `native-host-windows` / `native-host-macos` / `native-host.cdx.json` names and the platform pointers
    stay (the build-contract gate asserts them); the CI prose is compressed.
  - `rust/AGENTS.md` and `native-bridge/AGENTS.md`: prose is compressed; commands and pins stay; the
    native-bridge rationale points at `docs/otp-23-requirements-matrix.md`.
  - root: the intro, notice, node map, rule paths and hook block stay; tool and checkout versions move to
    `CONTRIBUTING.md`; the `/init` and parent-`CLAUDE.md` constraints stay.
- **Links replace copies**: each `AGENTS.md` that loses a section links its new home, and moved text is
  removed from the source rather than duplicated. The full cross-reference sweep is part of verification:
  `git grep -n` for every moved heading and old `docs/AGENTS.md` pointer, covering at least root
  `AGENTS.md`, `CONTRIBUTING.md` (three references), `docs/ci.md`, `docs/otp-11-debug-controls.md`,
  `docs/otp-23-lifecycle-scenarios.md`, `native-bridge/host/scripts/macos-test-bundle.sh`,
  `native-bridge/installer/macos/AGENTS.md` and `scripts/AGENTS.md`. It also covers the two check messages
  that hard-code "in docs/AGENTS.md" (`check_agent_files.py` in `check_links`) and the fork divergence text
  that says "the current-version mentions in `README.md`, `INTEGRATION.md` and this file": the version
  mention stays in `docs/AGENTS.md`, so the reinstall checklist in `docs/openspec-workflow.md` names that
  file explicitly rather than "this file". Both cited signing headings keep their names, so the docs that
  cite them stay correct.
- **The new instruction docs stay code-owned.** `docs/agent-guidance.md`, `docs/openspec-workflow.md` and
  `docs/opencode-rules.md` carry install/update instructions, so `.github/CODEOWNERS` gains one owner line
  for each, `CODEOWNER_PATTERNS` and `CODEOWNER_SAMPLE_PATHS` in the check list them, their test copy is
  updated, and `PLUGIN_RANGE_DOCS` lists `docs/AGENTS.md` explicitly (separate from `DOCUMENTED_SKILLS`,
  which now points at `docs/openspec-skills.md`) plus `docs/opencode-rules.md` and
  `docs/agent-guidance.md`, so a copied plugin range is caught. The `docs/AGENTS.md` case in
  `test_plugin_range_not_repeated_in_docs` is kept and the two new docs are added to it. `docs/android.md`
  and `docs/openspec-skills.md` stay under the `docs` node without a new owner line: the text moved into
  them is documentation, while the executable pieces (`.githooks/`, `.agents/skills/`, the verifier
  scripts) remain owned where they live; this is a recorded deliberate decision, not an omission.
- **Checker contracts are preserved**: the root node map, the eight `repo-*` listings, the
  `opencode-rules` mention in the root and `CONTRIBUTING.md`, the hook pointer block, the
  `## OpenSpec skills` list and the `global-overrides` entries keep their meaning. The skill-list source
  moves: `DOCUMENTED_SKILLS` becomes `docs/openspec-skills.md`, and the fixture writes the list there.
- **Spec deltas**: modify four capabilities and record one deliberate no-delta.
  - `agent-guidance-layout`: the root and node requirements state the 40-line cap and the shorter file
    contract; the platform-canonical-folder requirement allows narrative to live in the node's linked
    docs page while the canonical constraints stay in the node file; the "Existing content is preserved"
    requirement accepts the linked docs page for the fixture build detail (the keystore names stay in the
    node file); the OpenCode requirement moves the full install/update/caveat guidance and the
    untrusted-checkout warning to `docs/opencode-rules.md`, keeps the one-sentence statement that OpenCode
    users need the plugin pinned in `opencode.json` in `docs/AGENTS.md` with a link to that page, and adds
    the three new docs to its CODEOWNERS list; its "Plugin requirement documented" scenario is updated to
    read the statement in `docs/AGENTS.md` and the guidance in `docs/opencode-rules.md`; the check
    requirement says every `AGENTS.md`, names the boundary tests and replaces the 60-line clean-tree
    scenario with the 40-line boundary.
  - `superspec-review-gate`: the `(schema version v5)` text stays in `docs/AGENTS.md`, and the full
    divergence list moves to `docs/openspec-workflow.md`, which `docs/AGENTS.md` links.
  - `openspec-skills-delivery`: the schema fork is recorded in `docs/openspec-workflow.md`; the required
    workflow list and the resulting skill names move to `docs/openspec-skills.md`, and the agent-file
    check reads that file.
  - `hooked-rule-delivery`: the short hook section stays in `docs/AGENTS.md`; the untrusted-checkout
    warning moves to `docs/opencode-rules.md`, which `docs/AGENTS.md` links.
  - `commit-conventions`: unchanged, because the guidance-only-`docs` sentence stays in
    `scripts/AGENTS.md`.
- **Tests**: update `test_constants_are_exact`; replace the root 60/120 test with four boundary cases (root
  40 passes, root 41 fails, nested 40 passes, nested 41 fails); assert one finding per over-long file and
  the exact message fragment; point the skill-list fixtures and the two "in docs/AGENTS.md" messages at
  `docs/openspec-skills.md`; slim the clean fixture root (the repo-rule bullets are emitted two per line)
  so it sits near 35 lines and tests that append one or two lines still stay under 40. Update the
  CODEOWNERS constants and the plugin-range docs expectations, keeping the `docs/AGENTS.md` case in
  `test_plugin_range_not_repeated_in_docs`. The clean fixture writes `docs/opencode-rules.md`,
  `docs/agent-guidance.md` and `docs/openspec-skills.md`, and `codeowners_text()` gains the three new owner
  lines, so the tests' one-finding assertions still hold.
- **Verification**: `python3 docs/checks/check_agent_files.py --root .`,
  `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`,
  `git ls-files '*AGENTS.md' | xargs wc -l` (the same scope the check uses, so ignored and vendor trees
  are excluded), `python3 -I scripts/components.py check` for the new tracked docs,
  `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'` for the host
  file, and the `git grep` cross-reference sweep above.

## Key Decisions

- **The cap is 40 lines for every `AGENTS.md`**, including the root, and it is a hard error, not a
  warning. This replaces the root's 60-line warning and 120-line error; nested files gain the cap.
- **A line is a line**: the count is `len(text.splitlines())` after CRLF normalization, so blank lines,
  the title, headings, the notice and the pointer block all count. Exactly 40 passes; 41 fails with
  `the AGENTS.md cap is 40 lines`.
- **`AGENTS.md` is a contract, not a manual**: purpose, commands, constraints, rules, links. Architecture
  and procedures are documentation.
- **Docs are the home for narrative; rules remain the home for constraints.** No node or repo rule is
  added for prose. The existing `android-lint-before-push.md`, `native-bridge-test-app-tcc.md` and
  `repo-signing-keys.md` keep their jobs.
- **The parsed skill list moves out of `AGENTS.md`**: `docs/openspec-skills.md` becomes the check's
  `DOCUMENTED_SKILLS` source. This is the one place where a routinely growing list would otherwise make
  the 40-line cap unstable; the root node map and `repo-*` listing stay in the root because the check and
  the start-at-root behavior require them there.
- **The moved instruction pages are code-owned**: the three new agent-tooling docs get CODEOWNERS lines
  and checker entries; the plugin range guard covers them.
- **Four capabilities change**: `agent-guidance-layout` (cap, file contract, OpenCode/CODEOWNERS),
  `superspec-review-gate` and `openspec-skills-delivery` (divergence and skill-list locations), and
  `hooked-rule-delivery` (untrusted-warning location). `commit-conventions` needs no delta because the
  guidance-only sentence stays in `scripts/AGENTS.md`.
- **The checker's other invariants do not change**: node-map parity, rule listings and frontmatter, the
  plugin range and mentions, hooks, symlinks, CODEOWNERS coverage, pointer-block freshness and the host
  build-contract strings keep their current behaviour.
- **`docs/AGENTS.md` keeps `(schema version v5)`**, because the spec requires it there; the 11-bullet skill
  list and the workflow list move to `docs/openspec-skills.md`.
- **Displaced content keeps one home.** A fact is either moved to a docs page and linked from its former
  file, or deliberately dropped with a reason in the verification record. Nothing is copied into two
  places.

## Open Questions

None. The user chose the docs-for-prose split and the Opus reviewer preset.
