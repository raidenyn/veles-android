# Review record: design — reviewer Opus (Opus-5.5, medium)

Preset: opus (first Opus).
Upstream inputs: brainstorm.md sha256 3ae02aea3c496d32cb8a03d1fc6764f326dc9fe3e85a26970ff7ae115f3eab4f (the fork-divergence correction is non-triggering for this design).
Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (0 critical, 0 high, 4 medium, 4 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | D6 tells the builder to add macOS identity/TCC detail to `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md`, contradicting the brainstorm (the detail is already there) and the spec's canonical-node rule. | **Fixed.** D6 now says those pages already hold the history and this change adds nothing canonical; `macos/AGENTS.md` keeps the canonical identity statement and the no-substitution, exact-bundle, no-fallback and publish-only rules under its cited heading; only prose is compressed. A missing detail goes to `docs/desktop-ble-poc.md` and is cited from the node, never moved out. |
| 2 | medium | Verification and Non-Goals miss the CI nodes the doc edits select (`installer`, `native-host`, `supply-chain`), not just the host build-contract suite. | **Fixed.** Non-Goals says the doc edits select those nodes through their ci-paths, and Migration step 6 runs `components.py changed --base ...` and the checks of every selected node. |
| 3 | medium | Fork divergence 5's "this file" is unresolved, and the brainstorm's answer (it becomes `docs/openspec-workflow.md`) is wrong because the v5 text stays in `docs/AGENTS.md`. | **Fixed.** D7 now says the version mention stays in `docs/AGENTS.md` and the reinstall checklist names that file explicitly; the brainstorm text was corrected and its approval hash re-pointed with a note. |
| 4 | medium | The `superspec-review-gate` delta does not add the new workflow-instruction files to the list that must not name a model. | **Fixed.** D7 extends that list with `docs/openspec-workflow.md` and `docs/agent-guidance.md`. |
| 5 | low | The design drops the brainstorm's explicit cross-reference list. | **Fixed.** D6 has a cross-reference sweep naming root `AGENTS.md`, `CONTRIBUTING.md`, `docs/ci.md`, `docs/otp-11-debug-controls.md`, `docs/otp-23-lifecycle-scenarios.md`, `macos-test-bundle.sh`, `macos/AGENTS.md`, `scripts/AGENTS.md`, the checker docstrings/messages and the test comment. |
| 6 | low | The `androidGitVersion` fact has no stated single home; `scripts/AGENTS.md` points at `android/AGENTS.md` for it. | **Fixed.** `docs/releasing.md` is its single home; D5's mapping row says so and `android/AGENTS.md` and `scripts/AGENTS.md` link it. |
| 7 | low | Goals and Risks disagree on what counts as a home, and the verification record is unnamed. | **Fixed.** Goals allow a docs page, an existing rule or a recorded drop in the change's `verify.md`; Risks name `verify.md` as the section-by-section record. |
| 8 | low | D5's `docs/AGENTS.md` keep-list omits the one-sentence OpenCode plugin statement, and the CLI range would remain in two places. | **Fixed.** The keep-list includes the statement; the OpenSpec CLI range is single-homed in `CONTRIBUTING.md`, and `docs/openspec-workflow.md` links it. |

Notes:
- D8 now also says `nested_text()` gains a `target_lines` pad for the nested 40/41 cases, which currently cannot reach an exact boundary.
- The line budgets were prototyped outside the change; `verify.md` will list the final `git ls-files '*AGENTS.md' | xargs wc -l` output instead of asking a reviewer to trust the claim.

## Round 2 — verdict: approve (0 critical, 0 high, 0 medium, 2 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | The D5 macOS row still reads as a move of signing/TCC detail, while D6 says nothing canonical moves. | **Fixed.** The row now says the prose is compressed in place, the history is already in the two docs, and a missing detail goes to `docs/desktop-ble-poc.md`. |
| 2 | low | Non-Goals says the *doc* edits select `supply-chain`; the `docs/checks/` checker/test edits do. | **Fixed.** The sentence now says the doc and checker edits select the nodes and names the three ci-paths. |

All eight round-1 fixes check out; no new critical, high or medium finding was raised.

Approved content: design.md sha256 fbdb53fff2a005ae241fc7e05604009ae735acd8e913d59092c7a4ab3ca11f00 — re-pointed after the two round-2 cosmetic fixes, three non-triggering spec-alignment edits (the OpenCode warning is tool-neutral and cross-linked from `CONTRIBUTING.md`, the root-cap policy is single-homed in `docs/AGENTS.md`, and the roster list adds `docs/opencode-rules.md`) and the non-triggering move of the traceability table from the regenerated `verify.md` to `traceability.md`.
