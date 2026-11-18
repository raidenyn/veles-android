# Review record: brainstorm — reviewer Opus (Opus-5.5, medium)

Preset: opus (first Opus).
Upstream inputs: none.
Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (0 critical, 3 high, 5 medium, 3 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Other specs that the move would break are left unchanged: `superspec-review-gate` requires the divergence list in `docs/AGENTS.md`, `openspec-skills-delivery` records the fork there, `hooked-rule-delivery` requires the short hook section there, and `commit-conventions` requires the guidance-only-`docs` sentence in `scripts/AGENTS.md`. | **Fixed.** The Agreed Approach now modifies `agent-guidance-layout`, `superspec-review-gate` and `openspec-skills-delivery` (divergence list moves to `docs/openspec-workflow.md`, skill list stays), records `hooked-rule-delivery` as a deliberate no-delta because its short section stays, and keeps the guidance-only sentence in `scripts/AGENTS.md`, so `commit-conventions` needs no delta. |
| 2 | high | Moved agent instructions lose code-owner protection; `docs/opencode-rules.md`, `docs/openspec-workflow.md` and `docs/agent-guidance.md` would be plain `docs/` files, and the plugin-range guard would not cover the new plugin page. | **Fixed.** `.github/CODEOWNERS` gains one owner line per page, `CODEOWNER_PATTERNS`/`CODEOWNER_SAMPLE_PATHS` and the test copy are updated, and `PLUGIN_RANGE_DOCS` gains `docs/opencode-rules.md` and `docs/agent-guidance.md`. `docs/android.md` stays under the `docs` node. |
| 3 | high | The 40-line target is not shown to be reachable for the files that stay large; root, android and scripts each looked over budget before title, rules and notice. | **Fixed.** The Design Summary now carries a per-file budget table with a prototype target for all nine files (root 40, android 33, docs 40, scripts 34, verify 28, macos 40, host 33, rust 30, native-bridge 30) and an explicit "what stays" column; the drafts were counted against the real content. |
| 4 | medium | Root constraints (the `/init` ban, the Claude Code minimum, the parent-`CLAUDE.md` caveat) were moved to docs, against the "constraints stay" principle. | **Fixed.** The root keeps one line with the `/init` ban and the parent-`CLAUDE.md` caveat; version numbers, `core.symlinks=true` and the OpenSpec range live in the linked `CONTRIBUTING.md`. |
| 5 | medium | The spec delta misses conflicting scenarios: "Existing content is preserved" puts the OTP-23 fixture keystore in the node `AGENTS.md`, and the clean-tree scenario still mentions the 60-line warning. | **Fixed.** The delta list now names the "Existing content is preserved" requirement (linked docs page accepted; the keystore names also stay in `android/AGENTS.md`) and the check requirement's clean-tree scenario (40-line boundary). |
| 6 | medium | Some sections have no destination: Gate B validator, check semantics, scripts' Conventional Commits/project-flow/commands, android's adding-a-handler and prerequisites. | **Fixed.** The Agreed Approach now has an exhaustive "Section destinations" list covering all nine files, including Gate B, exit semantics, commands, project-flow codes, the guidance-only sentence, prerequisites and adding-a-handler. |
| 7 | medium | Cross-reference updates are given only as two examples. | **Fixed.** The Agreed Approach names the required `git grep` sweep and the known references (root, `CONTRIBUTING.md` ×3, `docs/ci.md`, `docs/otp-11-debug-controls.md`, `docs/otp-23-lifecycle-scenarios.md`, `native-bridge/installer/macos/AGENTS.md`, `scripts/AGENTS.md`) and keeps the cited `## Signing identities (Android)` heading name. |
| 8 | medium | The clean fixture root is already 39 lines; tests that append lines may cross 40 and get an extra length finding. | **Fixed.** The Tests bullet slims the fixture root by emitting the repo-rule bullets two per line (about 35 lines), leaving headroom for the one- and two-line additions. |
| 9 | low | "357 lines in total" is ambiguous. | **Fixed.** It now reads "357 lines over the cap" in both places. |
| 10 | low | The new error message text is not given. | **Fixed.** The exact message is `has <n> lines; the AGENTS.md cap is 40 lines`, and the tests assert that fragment. |
| 11 | low | The verification sweep may include ignored or vendor files. | **Fixed.** Verification uses `git ls-files '*AGENTS.md' | xargs wc -l`, the same scope as the check. |

Notes:
- `PLUGIN_RANGE_DOCS` currently guards only the root, `CONTRIBUTING.md` and `docs/AGENTS.md`; the new plugin page is added to it, as recorded under finding 2.
- No `global-overrides` change is needed for the new `docs/*.md` pages; the `docs` node owns `docs/` by prefix, confirmed with `scripts/components.py check`.

## Round 2 — verdict: changes-required (0 critical, 1 high, 2 medium, 2 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 12 | high | Two specs still require content the plan moves out of `docs/AGENTS.md`: `agent-guidance-layout`'s OpenCode requirement (install/update/untrusted guidance plus its CODEOWNERS list) and `hooked-rule-delivery`'s untrusted-checkout warning, and neither gets a delta. | **Fixed.** The delta list now modifies `agent-guidance-layout`'s OpenCode requirement (full guidance and warning move to `docs/opencode-rules.md`; `docs/AGENTS.md` links it; the three new docs join its CODEOWNERS list and scenario) and adds a `hooked-rule-delivery` delta that moves the untrusted-checkout warning to `docs/opencode-rules.md`. The "no-delta" list is now only `commit-conventions`. |
| 13 | medium | The `docs/AGENTS.md` budget omits required content: the OpenSpec workflow list, the `opencode-rules` statement and the untrusted warning; the listed "stays" items already came to 43–45 lines, so the 40-line prototype did not hold. | **Fixed.** The 11-skill list and the workflow list move to `docs/openspec-skills.md`, which becomes the check's `DOCUMENTED_SKILLS` source; the plugin statement and untrusted warning move to `docs/opencode-rules.md` under the modified specs. `docs/AGENTS.md` drops to about 27 lines (purpose, commands, exit semantics, hook section, rules and notice). |
| 14 | medium | Root, `docs/AGENTS.md` and the macOS file all target exactly 40 with no headroom; a new skill, node map entry or `repo-*` rule would fail CI on an unrelated change. | **Fixed.** Moving the skill list gives `docs/AGENTS.md` about 13 lines of headroom. The root target is 39 and the macOS target is 39; the Agreed Approach states that a change adding a node, a `repo-*` rule or a root line must keep the file at or under 40 by compressing or moving a fact, and the adding-a-node procedure says so. `docs/openspec-skills.md` is the data page for a growing skill set. |
| 15 | low | The macOS heading cited by `native-bridge/host/scripts/macos-test-bundle.sh` and `docs/otp-23-lifecycle-scenarios.md` is not protected. | **Fixed.** The `## Signing identity and TCC (macOS)` heading is kept and both citing files are named in the cross-reference sweep. |
| 16 | low | The brainstorm claims two docs cite the Android signing heading; only `docs/otp-11-debug-controls.md` does. | **Fixed.** The text now names only `docs/otp-11-debug-controls.md` for the Android heading; `docs/otp-23-lifecycle-scenarios.md` is named against the macOS heading. |
| 17 | low | Round-1 #5 was inconsistent: the delta accepted the linked docs page for the fixture keystore while the destinations said the keystore names stay in `android/AGENTS.md`. | **Fixed.** The keystore and init-script names stay in `android/AGENTS.md`; only the build line and backup detail move to `docs/android.md`. |

## Round 3 — verdict: approve (0 critical, 0 high, 2 medium, 4 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 18 | medium | The plugin-range guard can silently drop `docs/AGENTS.md`: `PLUGIN_RANGE_DOCS` reaches it only through `DOCUMENTED_SKILLS`, which is repointed, and the test's `docs/AGENTS.md` case would break. | **Fixed.** The plan now says `PLUGIN_RANGE_DOCS` lists `docs/AGENTS.md` explicitly, separate from `DOCUMENTED_SKILLS`, adds the two new docs, and keeps the `docs/AGENTS.md` case in `test_plugin_range_not_repeated_in_docs`. |
| 19 | medium | The root pointer block grows by one line per hooked rule, so a new hooked rule can fail CI without a root prose edit, and the hooked-rule procedure does not state the cap. | **Fixed.** The headroom paragraph now counts hooked rules as growth, and the hooked-rule procedure in `docs/AGENTS.md` and `docs/hook-delivered-rules.md` states the root cap and the compression step. |
| 20 | low | The `docs/AGENTS.md` headroom is overstated at 13 lines; a fuller draft is 35–36 lines. | **Fixed.** The text now says several lines of headroom, gives the 27-line minimal prototype and the ~35-line fuller draft, and no longer claims 13 lines. |
| 21 | low | The OpenCode delta does not say whether the one-sentence plugin requirement stays in `docs/AGENTS.md` or is replaced by `docs/opencode-rules.md`. | **Fixed.** The delta now keeps the one-sentence statement in `docs/AGENTS.md` with a link and updates the "Plugin requirement documented" scenario to read the statement there and the guidance in the new page. |
| 22 | low | Text referring to the old file is not in the sweep: the checker's "in docs/AGENTS.md" messages and the fork divergence's "this file". | **Fixed.** Both are named in the cross-reference sweep, and the Tests bullet updates the two messages and the assertion. |
| 23 | low | Moved procedural text (git hooks, workflow list) loses owner review without a recorded decision. | **Fixed.** The plan records the deliberate decision: the moved text is documentation; `.githooks/`, `.agents/skills/` and the verifier scripts stay owned where they run. |

## Round 4 — verdict: approve (0 critical, 0 high, 0 medium, 1 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 24 | low | The Tests bullet adds the two new pages to `test_plugin_range_not_repeated_in_docs`, but `clean_files()` does not write them, so the test would raise `KeyError`, and the extra CODEOWNERS patterns need matching fixture lines for the one-finding assertions. | **Fixed.** The Tests bullet now says the clean fixture writes `docs/opencode-rules.md`, `docs/agent-guidance.md` and `docs/openspec-skills.md`, and `codeowners_text()` gains the three new owner lines. |

All round-3 fixes are present and match the code; no new critical, high or medium finding was raised.

Approved content: brainstorm.md sha256 3ae02aea3c496d32cb8a03d1fc6764f326dc9fe3e85a26970ff7ae115f3eab4f — re-pointed after the round-4 finding 24 fix (fixture pages and CODEOWNERS lines) and a factual correction to the fork-divergence cross-reference: the `(schema version v5)` mention stays in `docs/AGENTS.md`, so the reinstall checklist names that file rather than "this file".
