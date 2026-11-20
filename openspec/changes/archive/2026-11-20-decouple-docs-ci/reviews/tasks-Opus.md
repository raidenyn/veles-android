# Review record: tasks — Opus

- Artifact: tasks (`tasks.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 e6d95b4ca2c63cf64f1d30f5914a3b8431c02e3d09f5515acaac0b0968c65d20; proposal sha256 cf2885c184c19e6b680e4fa769928b760385965923ecd994803b877064ac5a6d; design sha256 645156347d01d1056114c8b3e3c6ced53c509b0bdd42ca95400461899e5e5671; specs/ci-pipeline/spec.md sha256 309373c2cb0e3a4b52282ad343ac2564b601c1a01bdc983ebd9c5563b6f37d37

## Pass 1

### Round 1 — approve

Reviewed content: tasks sha256 fd3fd99796d2020462fd5a8e7f668167cfe404073152c3702160f8b97bda77bd

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | The guard lists files with `git ls-files`, so its own new files are not scanned until they are tracked. | fixed: 1.3 runs `git add` on them first. |
| 2 | medium | The spec's "runs it with other arguments" scenario has no mutation test. | fixed: 2.1 adds a changed-guard-arguments mutation. |
| 3 | medium | The boundary-superset test has no negative tests. | fixed: 2.3 makes it a pure function with four negative cases. |
| 4 | medium | 6.1 lists fewer checks than the nodes' `AGENTS.md` files. | fixed: the expected node set is named, "every command" is required, and the missing commands are added. |
| 5 | low | The tree is red between 4.2 and 4.3. | fixed: 4.2 says to commit it together with 4.3. |
| 6 | low | 3.2 edits the file the 2.3 test reads but does not re-run that test. | deferred: `plan.md` re-runs `workflow-contracts.test.mjs` after 3.2. |
| 7 | low | No dependency problem in the 2.2/2.4 order. | no action |
| 8 | — | Builder guesses: an in-process `main(argv)` for exit codes, the #174/#175 SHAs and `--head`, and a fallback when Python 3.8 is unavailable. | deferred: settled in `plan.md`. |

Approved content: tasks sha256 9f7066694a31ff236b22c83c9a7afd5cbbdc430120938aeeb373b0cf0ec3d2b4 — re-pointed from fd3fd997… after 5 non-triggering minor fixes (findings 1–5).
