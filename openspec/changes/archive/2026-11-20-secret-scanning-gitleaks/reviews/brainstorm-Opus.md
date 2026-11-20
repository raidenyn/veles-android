# Review record: brainstorm — Opus

- Artifact: brainstorm
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: opus = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: none (brainstorm is first in the order)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | A literal fake key in the test file would be flagged by the hook, the full scan and push protection; keys ending in `EXAMPLE` are ignored by the default AWS rule, so the "refused" test would fail. | fixed: key assembled at runtime from pieces, must not end in `EXAMPLE`. |
| medium | Generalising `InstallGitCliffTask` left the differing parts (URL, archive name, inner path, version output, platform keys) and the release-workflow callers unspecified. | fixed: task inputs, `release-tools.toml` key names, unchanged `installGitCliff`, contract-test confirmation listed. |
| medium | Any non-zero exit blocks, so a tool error blocks every commit, against the convenience-layer intent. | fixed: dedicated leak `--exit-code` blocks; other non-zero warns and passes. |
| medium | History triage had no fallback for test data outside the three named paths; `.gitleaksignore` fingerprints are commit-bound. | fixed: fallback rule for other test-data paths; staleness accepted with reason. |
| low | `release-tools.toml` header names only git-cliff and its cross-check source. | fixed: header rewording with each tool's cross-check source. |
| low | `scripts/AGENTS.md` documents pinned tools but was not in the docs list. | fixed: added. |
| low | Repo-root discovery, `--config` and per-worktree `build/` unstated. | fixed: stated. |
| low | Wrong-digest check is manual, not a repeatable test. | fixed: recorded as a deliberate deviation with reason. |
| low | Missing-binary test setup unnamed. | fixed: `VELES_GITLEAKS` at a non-existent path. |
| low | Issue says "Severity: medium" but is labelled high. | deferred: no effect on the plan. |

### Round 2 — approve

Reviewed content sha256 f9b0f8411068b5e14ce38595a4384ba9e1a17d6cbb50ae25d99909de16ef4dab.

| Severity | Finding | Disposition |
|---|---|---|
| medium | A low-entropy fake key may not be flagged by the default rule. | fixed: random-looking base32 body, confirmed at apply. |
| low | Skip granularity reads as whole-suite. | fixed: per-test skips. |
| low | Platform-key resolver shape unspecified. | fixed: one shared resolver, per-tool map. |
| low | Claim that the hook never reads `.gitleaksignore` is wrong. | fixed: reworded. |
| low | No test for the tool-error branch. | fixed: stub-binary test case. |
| low | `scriptsTest` now downloads gitleaks too; mention in `docs/ci.md`. | deferred: carried into the docs task, not a brainstorm decision. |

Approved content: brainstorm sha256 490e0eb287dc4ef2ae56fad35981b90928aedf37321deaf1384f75a4aeac9705 — re-pointed from f9b0f841… after five non-triggering minor fixes from round 2 (fake-key entropy, per-test skips, shared resolver, `.gitleaksignore` wording, tool-error test).
