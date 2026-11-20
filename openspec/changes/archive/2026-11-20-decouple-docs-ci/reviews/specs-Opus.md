# Review record: specs — Opus

- Artifact: specs (`specs/ci-pipeline/spec.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 e6d95b4ca2c63cf64f1d30f5914a3b8431c02e3d09f5515acaac0b0968c65d20; proposal sha256 cf2885c184c19e6b680e4fa769928b760385965923ecd994803b877064ac5a6d; design sha256 645156347d01d1056114c8b3e3c6ced53c509b0bdd42ca95400461899e5e5671

## Pass 1

### Round 1 — approve

#### specs/ci-pipeline/spec.md

Reviewed content: specs/ci-pipeline/spec.md sha256 7ba8c899c56f85dd8e60377a6a514b276bf2c4e608c0d23da4801c62983e62fd

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | "Tooling-owned workflow" is ambiguous: `project-flow.yml` is also selected only by `tooling`. | fixed: the exception is defined by the installer's `TOOLING_OWNED_WORKFLOWS` tuple, and selection by tooling alone is said not to qualify. |
| 2 | low | "The nodes that own the file" is not spelled out for the installer boundary scenario. | fixed: exactly `installer`, `protocol`, `supply-chain` and `tooling`. |
| 3 | low | "First step" is literally false: checkout comes first. | fixed: "first `run:` step". |
| 4 | low | D4 says the site-guard scenario is modified, but the delta adds a separate offline-guard scenario. | rebutted: same behaviour, and the delta's form is the better one. D4's intent ("four pinned commands, the guard first") is met. |
| 5 | low | The `verify/test/` scenario overstates (a future `AGENTS.md` there also selects `docs`), and its reason is inexact. | fixed: guidance files are excluded and the reason says "the exact top-level files the map lists". |
| 6 | low | The scenario title says "global option", but `clone` is a subcommand. | fixed: renamed "Forbidden git option or subcommand". |

Approved content: specs/ci-pipeline/spec.md sha256 309373c2cb0e3a4b52282ad343ac2564b601c1a01bdc983ebd9c5563b6f37d37 — re-pointed from 7ba8c899… after 5 non-triggering minor fixes (findings 1, 2, 3, 5 and 6).
