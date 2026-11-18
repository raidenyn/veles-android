# Review record: plan — reviewer Opus (Opus-5.5, medium)

Preset: opus (first Opus).
Upstream inputs:
- brainstorm.md sha256 3ae02aea3c496d32cb8a03d1fc6764f326dc9fe3e85a26970ff7ae115f3eab4f
- proposal.md sha256 79aa10cda5d78a94eff53ead15cfff5c267611e2846ef112a501367f309f9b48
- design.md sha256 fbdb53fff2a005ae241fc7e05604009ae735acd8e913d59092c7a4ab3ca11f00
- specs/agent-guidance-layout/spec.md sha256 8500b20b606d68c0570dc89ef71823618339b831d22a94f06ab635b478e9c0db
- specs/superspec-review-gate/spec.md sha256 7135daaba38f4065ba31ebaed08f6deb22f35900bf84b7b602deccc19f398ce1
- specs/openspec-skills-delivery/spec.md sha256 5f33452d3214973ed3e3256aacbd6238cbf38cbe40d089c0e55ab2d2af4b317b
- specs/hooked-rule-delivery/spec.md sha256 bc53cdb3f2e1422e30ee80130c4fd8b75c8477f845ef2338e83f2299d80ab595
- tasks.md sha256 a5a8caf84a8312236d4870be362a7c67e2c49fe73b1b026683ef82da0ac5be8e

Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (0 critical, 2 high, 2 medium, 7 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Moving `DOCUMENTED_SKILLS` to `docs/openspec-skills.md` makes the skill check fail open: a missing page is skipped, so parity is silently off. | **Fixed.** Task 9 Step 2 adds a missing-file error in `check_links`; Task 9 Step 5 adds `test_missing_documented_skill_list_fails`, which asserts the error and fragment. |
| 2 | high | Task 12 Step 2's `git diff --name-only HEAD~0` can never fail after the per-task commits. | **Fixed.** Task 11 now uses `git diff --name-only "$(git merge-base origin/master HEAD)" -- .agents/skills .claude/skills`. |
| 3 | medium | The old `test_root_line_boundaries` is never deleted, so it fails once the 60/120 constants go. | **Fixed.** Task 8 Step 1 deletes it explicitly. |
| 4 | medium | Task 9 Step 4's "failures only in the fixtures Task 10 updates" is uncheckable and the suite would be red for a task. | **Fixed.** The checker/guard/fixture work is merged into Task 9, and the root slimming moved into Task 8, so Task 8 and Task 9 each end with a green suite; no expected-failure list is needed. |
| 5 | low | Task 10 does not mention the test comment that calls `docs/AGENTS.md` the authoritative source. | **Fixed.** Task 9 Step 4 names it. |
| 6 | low | `skill_section(skills)` returns a list, so "write the page with it" is ambiguous. | **Fixed.** Task 9 Step 4 gives `"\n".join(skill_section(skills)) + "\n"`. |
| 7 | low | Task 2 hedges on whether the Gate B command stays in `docs/AGENTS.md`. | **Fixed.** It states that the command stays and only its explanation moves. |
| 8 | low | Task 6 Step 3 mixes a cap with a target range. | **Fixed.** It reads "at most 40 lines (target about 27)". |
| 9 | low | No step confirms `docs/otp-23-requirements-matrix.md` D-01 holds the Tauri rationale before the prose is dropped. | **Fixed.** Task 7 Step 2 checks D-01; Task 11 Step 4 records it in `traceability.md`. |
| 10 | low | Task 11's sweep greps only `docs/AGENTS.md` and the two headings, not the old section names. | **Fixed.** The grep includes "The agent-file check" and "Adding a rule or a node". |
| 11 | low | The plan does not say that intermediate commits after Tasks 6–7 and 8 may leave the checker red. | **Fixed.** Global Constraints states the red window and that nothing is pushed before Task 11 passes. |

Notes:
- The header, Global Constraints, Review Focus, files/interfaces, TDD order, real commands and commit points checked out.
- The `_check_line_cap` shape, the boundary test, the two-per-line fixture and the CODEOWNERS constants match the real checker and tests.

## Round 2 — verdict: approve (0 critical, 0 high, 0 medium, 4 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | Task 9 Step 3 does not name the test constant `EXPECTED_CODEOWNER_PATTERNS`, which feeds the fixture CODEOWNERS. | **Fixed.** Step 3 now names it. |
| 2 | low | Task 9 Step 1 leaves out the `check_links` docstring that names `docs/AGENTS.md`. | **Fixed.** Step 1 now includes it. |
| 3 | low | Global Constraints says Task 9 closes the red window, but Task 9 Step 7 runs only the unit suite. | **Fixed.** Step 7 now also runs the real-tree checker and expects exit 0. |
| 4 | low | Task 8 Step 6's "content files are already under 40" is misleading because the suite uses fixtures only. | **Fixed.** It now says the fixtures pass and the real tree stays red until Task 9. |

All eleven round-1 findings are fixed; the dependency order and D1–D8 coverage hold; no new critical, high or medium finding was raised.

Approved content: plan.md sha256 2e25b1576458ba994cc902eef30172802fec35563667d4ab347f32526cdd093d — includes the four round-2 low fixes, recorded without reopening review.
