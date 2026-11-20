# Review record: proposal — Opus

- Artifact: proposal (`proposal.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 e6d95b4ca2c63cf64f1d30f5914a3b8431c02e3d09f5515acaac0b0968c65d20

## Pass 1

### Round 1 — approve

Reviewed content: the first draft of `proposal.md`. Its hash was not captured before the fixes below.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| M1 | medium | "Every tracked non-test `verify/` file selects supply-chain" includes `verify/AGENTS.md`, which `global-overrides` maps to `docs`. | fixed: the exclusion is named. |
| M2 | medium | The guard parses only `*.py` files, while the regex scan covered every file type in `docs/checks/`. | fixed: the guard rejects any file under `docs/checks/` other than a `*.py` file (none exist today). |
| L1 | low | A stale "Agent call" in the brainstorm says the guard is a unittest module and needs no new command, which contradicts its Design §1. | deferred: `design.md` states the separate-step decision, which supersedes it. Editing the brainstorm would exceed its minor-fix allowance. |
| L2 | low | The impact list leaves the `check-docs.yml` header comment and the `docs/ci.md` command-table row implicit. | deferred: named in `design.md` and `tasks.md`. |
| L3 | low | The trade-off is not stated: a docs-only PR can loosen the guard. | fixed: stated in the Reason line. |
| L4 | low | The delta must name the requirement "Workflow contract suites move with the workflows" and the "Docs node", "Docs workflow edit" and site-guard scenarios. | deferred: handled in the spec delta. |

Approved content: proposal sha256 cf2885c184c19e6b680e4fa769928b760385965923ecd994803b877064ac5a6d — re-pointed from a5fed58a… after 4 non-triggering minor fixes: M1, M2 and L3 above, plus the Impact list naming `docs/checks/check_agent_files.py`, `docs/checks/test_check_agent_files.py` and `docs/agent-guidance.md` (design review round 1, H1 and L8).
