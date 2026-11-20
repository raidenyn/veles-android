# Review record: plan — Opus

- Artifact: plan (`plan.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 e6d95b4ca2c63cf64f1d30f5914a3b8431c02e3d09f5515acaac0b0968c65d20; proposal sha256 cf2885c184c19e6b680e4fa769928b760385965923ecd994803b877064ac5a6d; design sha256 645156347d01d1056114c8b3e3c6ced53c509b0bdd42ca95400461899e5e5671; specs/ci-pipeline/spec.md sha256 309373c2cb0e3a4b52282ad343ac2564b601c1a01bdc983ebd9c5563b6f37d37; tasks sha256 9f7066694a31ff236b22c83c9a7afd5cbbdc430120938aeeb373b0cf0ec3d2b4

## Pass 1

### Round 1 — approve

Reviewed content: plan sha256 f1831aa282e92ab5fb2999d60870235951c16d3fae5679e5d5df010387bd0ebe

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | The `git push` mutation adds a fifth run, so the count check throws first and the new check is never exercised. | fixed: the `environment:`/`git push` checks run before the count; the mutation appends to an existing run line; the throws match `/environment/` and `/git push/`. |
| 2 | medium | Task 1 tests only a subset of D1's rejected forms. | fixed: a table-driven `test_rejects_every_listed_form` covers the rest, and the star-import fixture uses an allowed module. |
| 3 | low | The extraction regex `r"…"` has no left boundary, so a quoted word in a comment could match. | fixed: `(?<![\w"])r"`, and Task 3 forbids double-quoted text in its comment. |
| 4 | low | Task 3's commit is test-only but typed `ci`. | fixed: `test(installer)`. |
| 5 | low | The spec's "file and line" is not asserted. | fixed: `test_finding_has_path_and_line`. |
| 6 | low | The Python 3.8 syntax fallback misses 3.9+ standard-library APIs. | deferred: the fallback is recorded as NOT RUN, as the plan already says. |
| 7 | low | Scratch-branch SHAs are lost after the branch is deleted. | deferred: `apply.md` records each scratch commit's changed file next to its output; the file name is enough to reproduce. |
| 8 | — | Builder guesses: messages for new throws (settled by fix 1), scratch commit subjects (any valid `test(docs): …`), #174/#175 expected sets (recorded as observed), and whether `test_components.py` goes in the Task 4 commit (yes, with Steps 3 and 5). | deferred: settled here for the builder; no plan edit, to stay within the minor-fix allowance. |

Approved content: plan sha256 cc35b7390ba6c600147367b3a71d9aa510b26484aba865d3ea756f25497bf314 — re-pointed from f1831aa2… after 5 non-triggering minor fixes (findings 1–5).
