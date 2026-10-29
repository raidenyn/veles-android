# OTP-23 Mac integration provenance and reports (2026-10-28 … 2026-10-29)

This folder is curation only. It holds the reports, owner route records and whitelisted model
metadata of the Mac M-08 / M-11 work chain, each with its as-of scope. It promotes **no** matrix row:
the matrix stays 23 PASS / 6 BLOCKED / 3 waived, NO-GO.

- Reports are byte-exact copies of the operator-workspace files (`curation-map.json`). There are three
  exceptions, each published as a `*.public.md` redacted copy with its private original mapped by
  hash:
  - the two GLM resume notes, which named another local user and a foreign app
  - the Kimi code verification, which named unrelated applications' Native Messaging manifests
- Every `/tmp` or `/home` path cited inside these reports and in `../mac-m11-dev-2026-10-29/` is
  resolved in `path-map.json`.

## Reports, in chronological order

Each report is true only as of its own time. Later rows correct or supersede earlier ones; nothing
earlier is rewritten.

| file | author / session | role | as of (UTC) | scope and status |
|---|---|---|---|---|
| `reports/otp23-mac-resume-result.public.md`, `…human-action.public.md` | GLM-5.3-flash `ses_e7cde50b…` | implementer/operator | 2026-10-28 15:50 | First Mac M-08 resume attempt (aborted; lsof foreign-mount blocker). Redacted copies. |
| `reports/otp23-mac-m08-preflight-opus.md` | Opus `1b447229` | independent design preflight | 2026-10-28 16:03 | P0 options, lsof classifier design, release-proof design. Its **16-byte lower-bound premise was later shown non-causal** (see the final data review). |
| `reports/otp23-mac-m08-parent-preflight-decision.json` | parent | decision | 2026-10-28 16:09 | P0-A selected. |
| `reports/otp23-mac-m08-code-result.md` | GLM `ses_e7cde50b…` | implementer | 2026-10-28 17:55 | M-08 code phase (gate, classifier, `mac_release_bound.py`, `mac_clock_corroborate.py`). |
| `reports/otp23-mac-m08-code-verification-kimi.public.md` | Kimi-K3 `ses_e7c65ccb…` | independent code verification | 2026-10-28 17:49 | Found F-1 (blocker) to F-7, and F-M11-1. Redacted copy (unrelated app manifest names). |
| `reports/otp23-mac-m08-fix-result.md` + `…fix-opus-implementer-metadata.json` | Opus `752e1974` | implementer | 2026-10-29 04:13 | Completed the minimum fixes after GLM's 429. |
| `reports/otp23-mac-m08-fix-verification-opus.md` | Opus `7b4ee6ca` | independent micro-verification | 2026-10-29 04:23 | M-08 approved; found M11-1. |
| `reports/otp23-mac-m08-retake-result.md` | Opus `752e1974` | operator/curator | 2026-10-29 04:46 | Physical retake: shutdown PASS, port/tab hull FAIL (recorded). |
| `reports/otp23-mac-m11-preparation.md` | GLM (§1–9), Opus `752e1974` (§10–11 appended) | prep | 2026-10-29 04:52 | M-11 stage and runbook. **The runbook's `--stage .` was a defect** (see the M-11 folder). |
| `reports/otp23-mac-m11-loop-fix-result.md` | Opus `752e1974` | implementer | 2026-10-29 04:53 | M11-1 loop fix (script `976f61cf`). |
| `reports/otp23-mac-m08-physical-review-opus.md` | Opus `7b4ee6ca` | independent physical review | 2026-10-29 04:58 | Evidence verified; Option A proposed. **Its 16-byte premise was later corrected.** |
| `reports/otp23-mac-m11-loop-verification-opus.md` | Opus `7b4ee6ca` | independent review | 2026-10-29 05:05 | M-11 execution approved (script `976f61cf`). |
| `reports/otp23-mac-m08-attribution-result.md` | Opus `752e1974` | implementer | 2026-10-29 05:07 | A1 attribution. **SUPERSEDED, premise invalid.** Source archived in `../mac-release-attribution-source-history-2026-10-29/`; outputs in `../mac-release-attributed-2026-10-29/` (history). |
| `reports/otp23-mac-final-data-review-opus.md` | Opus `e30e474d` | fresh independent review | 2026-10-29 10:57 | Corrects the 16-byte premise; safe recomputation; M-11 adjudication (conditional on the account record). |
| `reports/otp23-mac-safe-analysis-parent-decision.json` | parent | decision | 2026-10-29 11:03 | A2 required; hull is not a fallback; precision target not claimed. |
| `reports/otp23-mac-safe-attribution-result.md` | Opus `752e1974` | implementer | 2026-10-29 11:11 | A2 implementation; outputs in `../mac-release-attributed-safe-2026-10-29/`. **Independent micro-review pending** at curation time. |

## Route records (`route/`, exact bytes)

| file | records |
|---|---|
| `otp23-mac-resume-owner-record.json` | the owner asked to resume Mac tests; Chrome was updated to 156.0.8078.74 |
| `otp23-mac-kimi-reviewer-approval.json` | owner "Use kimi k3" (2026-10-28) |
| `otp23-mac-opus-continuation-owner-record.json` | owner "Use Opus 5.5 via Claude code". The date field reads 2026-10-29; the parent corrected it from a mistyped 2026-10-28 before publication. |

The M-11 owner approval and grant records are in `../mac-m11-dev-2026-10-29/owner-records/`.

## Model metadata

`model-metadata.json` holds structured fields only:
- the opencode DB `modelID`/`providerID` per session, with counts
- the Claude Code envelope `session_id`, model keys, `is_error`, `terminal_reason`, `num_turns` and
  `subagents_spawned`

It contains no messages and no result texts. Quota failures carry only `api_error_status 429` /
`usage_limit_reached`:

| session | what happened |
|---|---|
| `1b447229` | interrupted code verification; approved nothing |
| `7b4ee6ca` | causal-premise attempt hit quota |
| `c401a87c` | physical M-11 operator attempt hit quota before any tool; zero model usage |

## Related folders

| folder | role |
|---|---|
| `../mac-retake-2026-10-29/` | raw M-08 retake, 79 entries, immutable |
| `../mac-release-attributed-2026-10-29/` | A1 outputs, superseded history, immutable |
| `../mac-release-attribution-source-history-2026-10-29/` | A1 source, archived with its hash map |
| `../mac-release-attributed-safe-2026-10-29/` | A2 outputs, pending review |
| `../mac-m11-dev-2026-10-29/` | M-11 captures and the conditional adjudication draft |
| `../mac-resume-2026-10-28/` | first resume attempt. Active privacy curation: 35 entries, `SHA256SUMS 9e8ed077…`. The archived original manifest is 32 lines over 31 paths; 30 non-narrative paths are byte-identical; only `FINDING.md` differs (private original). Re-verified at curation time. |

## Boundaries

- **M-03 and M-04 stay BLOCKED.** No original prompt can be re-observed under the existing grant,
  the owner has not approved an M-04 off/on toggle, and no waiver is inferred.
- No production UX or backlog work was done.
