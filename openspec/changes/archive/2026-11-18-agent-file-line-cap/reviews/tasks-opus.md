# Review record: tasks — reviewer Opus (Opus-5.5, medium)

Preset: opus (first Opus).
Upstream inputs:
- brainstorm.md sha256 3ae02aea3c496d32cb8a03d1fc6764f326dc9fe3e85a26970ff7ae115f3eab4f
- proposal.md sha256 79aa10cda5d78a94eff53ead15cfff5c267611e2846ef112a501367f309f9b48
- design.md sha256 fbdb53fff2a005ae241fc7e05604009ae735acd8e913d59092c7a4ab3ca11f00 — re-pointed after the non-triggering move of the traceability table from `verify.md` to `traceability.md`, made after this pass's round 1.
- specs/agent-guidance-layout/spec.md sha256 8500b20b606d68c0570dc89ef71823618339b831d22a94f06ab635b478e9c0db
- specs/superspec-review-gate/spec.md sha256 7135daaba38f4065ba31ebaed08f6deb22f35900bf84b7b602deccc19f398ce1
- specs/openspec-skills-delivery/spec.md sha256 5f33452d3214973ed3e3256aacbd6238cbf38cbe40d089c0e55ab2d2af4b317b
- specs/hooked-rule-delivery/spec.md sha256 bc53cdb3f2e1422e30ee80130c4fd8b75c8477f845ef2338e83f2299d80ab595

Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (0 critical, 1 high, 4 medium, 5 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | Task 5.4 writes the traceability table, drops and review-records audit into `verify.md`, which the schema regenerates each run; the table would be lost and the audit duplicated. | **Fixed.** 5.5 creates `openspec/changes/agent-file-line-cap/traceability.md`; 5.6 links it into `verify.md` and leaves the audit to verify check 6. Design Goals, Risks and Migration step 6 now name `traceability.md`, and the design hash is re-pointed. |
| 2 | medium | 3.4 does not add the three new CODEOWNERS patterns to the missing-coverage subtest loop or their `CODEOWNER_SAMPLE_PATHS` samples, so only the positive case is covered. | **Fixed.** 3.4 names the three patterns in both places. |
| 3 | medium | 2.3 leaves out three required `docs/AGENTS.md` items: the one-sentence plugin requirement and link, the `docs/openspec-skills.md` link, and a statement of the root cap and one-pointer-line rule. | **Fixed.** 2.3 now lists all three explicitly. |
| 4 | medium | No task adds the root-cap link to `docs/hook-delivered-rules.md`. | **Fixed.** New task 1.8 adds the link. |
| 5 | medium | The model-name grep over the workflow-instruction files that the roster delta extends is never run. | **Fixed.** New task 5.4 greps the roster's model names over the exact listed paths and expects hits only in the roster. |
| 6 | low | The host CI/SBOM prose has no destination task, and 1.6 does not warn that `docs/reproducible-builds.md` also feeds the build-contract gate. | **Fixed.** 1.5 and 1.6 add the host facts, and 1.6 says the host task/artifact/SBOM tokens must stay. |
| 7 | low | 3.2 omits the `check_links` docstring and 3.4 the test comment that names `docs/AGENTS.md`. | **Fixed.** Both are named. |
| 8 | low | Verification does not check that skills/schemas were left alone or rerun the `/opsx:` grep. | **Fixed.** 5.3 runs both. |
| 9 | low | 4.1 omits the `CONTRIBUTING.md` "node files link their detail" sentence and repoints the root pointer after 2.1. | **Fixed.** 2.1 points the OpenCode requirement at `docs/opencode-rules.md`; 4.1 adds the sentence. |
| 10 | low | 3.1 says the error fires "at 41" rather than above 40, inviting an equality test. | **Fixed.** It now says above 40 (`> AGENTS_LINE_MAX`). |

Notes:
- Added the transient-failure warning to 2.6: the check cannot pass between sections 2 and 3 because the skill list is gone until `DOCUMENTED_SKILLS` moves.
- All seven upstream hashes, the checkbox format, the nine over-cap files, the commands and the node selection checked out.

## Round 2 — verdict: approve (0 critical, 0 high, 0 medium, 3 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | 2.2 does not tell `android/AGENTS.md` to link `docs/releasing.md` for the `androidGitVersion` format. | **Fixed.** 2.2 now says to link it. |
| 2 | low | 3.4 does not name the `is listed under "## OpenSpec skills" in docs/AGENTS.md` message assertions that break when 3.2 changes the message. | **Fixed.** 3.4 names them. |
| 3 | low | 2.5 does not repeat the required Android-CI statement and rule pointer for `rust/AGENTS.md`. | **Fixed.** 2.5 names both items. |

All ten round-1 fixes are present; no new critical, high or medium finding was raised.

Approved content: tasks.md sha256 a5a8caf84a8312236d4870be362a7c67e2c49fe73b1b026683ef82da0ac5be8e — includes the three round-2 low fixes, recorded without reopening review.
