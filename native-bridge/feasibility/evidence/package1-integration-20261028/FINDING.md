# OTP-23 package1 integration folder (2026-10-28) — FINDING

Purpose: publication/provenance folder for the package-1 finish + integration chain. Curation-only
(no source changes, no hardware, no Mac/Chrome action, no commits/push; implementation stays
ollama-cloud/glm-5.3-flash with 0 subagents).

## Current canonical truth (FINAL, matrix after the authorized 3-row integration)

FINAL counts **23 PASS / 6 BLOCKED / 3 waived (NOT RUN)** — exactly W-01, M-01, M-02 waived; NO-GO
(`matrix.py check` → `matrix ok: NO-GO`; 6 open rows: W-10, M-03, M-04, M-08 (owner-deferred),
M-11, A-01) — OTP-24 NOT authorized. **W-11/W-12 PASS** (2026-10-28 latest-Chrome strict runs +
the independent GPT strict review `520c2edd…`, labelled GPT). **W-02/W-07/W-08 PASS** (the FINAL
INTEGRATION authorized by the parent after the independent chain: GPT P2/B5 `15a28c80…` + fix
verification `ee342efa…` + v3-final recheck `217e6f2f…`, then the **user-authorized Opus 5.5
focused final verification** — report published byte-identically at
`reports/otp23-p2-opus-v4-final.md` (`d85d4f57…`), session `1b447229-…` METADATA CONFIRMED and
captured in two preserved snapshots (`provenance/opus-review-model-metadata-partial.json` while
running + `provenance/opus-review-model-metadata-final.json` after completion); the interim 20/9/3
state and the GPT quota block are recorded HISTORY, never the final blocker). **M-08 stays
owner-deferred and unmeasured** ("Cannot restart Chrome now" — owner record preserved in the
v2/v3/v4 folders; retained attempts + the honest `--overwrite` setup-record preservation gap
documented in the RFC amendment and the matrix). Overall decision: **NO-GO; OTP-24 not
authorized**.

## Contents

- `reports/otp23-package1-integrated-opus-review.md` (`cc0b6225…35654`) — byte-identical publication
  of the FINAL INTEGRATED review (Opus 5.5, session `1b447229-…`): **APPROVED for commit and
  publication, pending clean Git-export checks and green CI**; all findings non-blocking
  (curation/wording); no row status change; whole-session metadata
  `provenance/integrated-opus-review-model-metadata-final.json` (MEASURED: 162 assistant records =
  161 `claude-opus-5-5` each with envelope `effort=high` + 1 harness `<synthetic>`; the preserved
  69-entry snapshots are correct for the v4 part only).
- `reports/otp23-package1-observer-code-gpt-final.md` (`33e8233e…53575`) and
  `reports/otp23-package1-observer-code-gpt-verification.md` (`1a14ebd1…43dbc`) — byte-identical
  publications of the two observer-code GPT reports (actual GPT session `ses_e7fbe535…YjIL`,
  gpt-5.6-sol high), previously cited only from /tmp.
- `phase-records/otp23-package1-integration-result.md` (`9a6c4117…7680f8e`) and
  `phase-records/otp23-package1-final-integration-result.md` (`72e874f6…34a1a48`) — byte-identical
  GLM phase historical records, publishing the /tmp citations that `gates/SOURCES.md` and the frozen
  Opus v4 report make (the frozen report is NOT edited).
- `reports/otp23-p2-opus-v4-final.md` (`d85d4f57…4ff1e`) — byte-identical publication of the
  user-authorized Opus 5.5 FINAL focused verification (sole remaining defect CLOSED; recommends
  W-02/W-07/W-08 PASS; applied by the final integration).
- `reports/otp23-p2-gpt-fix-verification.md` (`ee342efa…3ff0bd`) and
  `reports/otp23-p2-gpt-v3-final.md` (`217e6f2f…40d4d2`) — byte-identical publications of the two
  GPT reports the chain references which had no published counterpart (the two earlier GPT reviews
  live, immutable, in `../package1-b5-v2-20261028/reports/`). The v3-final copy RESOLVES the
  `/tmp` citation inside the v4 derivation record (`answers_fix_verification.record.path`) — the
  v4 source and pins are unchanged, only the documented capture path now has a published home.
- `owner-records/opus-reviewer-approval-2026-10-28.json` (`9211bbf8…a6313`) — byte-identical copy
  of the operator-captured owner instruction authorizing the current review route ("Use Opus 5.5
  via Claude Code").
- `provenance/opus-review-model-metadata-partial.json` (PRESERVED, captured while running) +
  `provenance/opus-review-model-metadata-final.json` (NEW, after completion) — actual review model
  metadata extracted ONLY from assistant envelopes (model/sessionId/role/timestamp): session
  `1b447229-ceaa-4cee-a5be-2181dfdd8838` finished with 69 assistant entries, 68 `claude-opus-5-5`
  + 1 harness-generated `<synthetic>` (counts differ between snapshots only by capture moment).
  No transcript content, headers or API errors read.
- `gates/gate3-*.log` + `gates/SOURCES.md` — byte-identical copies of the last completed phase's
  full gates (forced spotless rerun 0, full gradle chain 0/BUILD SUCCESSFUL, both ci.yml Log guards
  0) with their EXACT source commands; full harness at that state: 456 OK/5 skipped; matrix NO-GO.
- `PROVENANCE-MAP.md` — the complete /tmp→published mapping (reports, owner records, the unpinned
  events file that must NOT be published, the frozen-v2 + unlisted-quota-addition honesty note,
  author-session metadata) and the label rules.
- `SHA256SUMS` — the ACTIVE manifest of this folder (own `.gitattributes` `* -text -whitespace`;
  byte-identical export), including the preserved-but-unlisted v2 quota addition BY RELATIVE PATH.

## Boundary (publication curation)

The 3-row promotion happened INSIDE the canonical matrix files (authorized); the FINAL INTEGRATED
Opus review APPROVED that exact tree state for commit and publication PENDING clean Git-export
checks and green CI — this curation closed its non-blocking findings 1-3 and applied the minimal
findings 4/6/7 (dated FINAL suffix on W-11/W-12; M-08 evidence now lists the published deferral
record + specific retained attempt logs; the RFC now cites the published repo owner-record paths
alongside the /tmp capture hashes). No row status changed (23 PASS / 6 BLOCKED / 3 waived; NO-GO;
OTP-24 unauthorized). No source changes: all B5/v4 pins frozen and verified (core
`cb512418…`, test_scenario_matrix `307a7eb1…`; `test_matrix.py` `245f696c…` (final-truth
invariants) and `matrix.py` `2ba2cabf…` are NOT part of the pinned derivation set;
the derive program self-pins at runtime). No commit/push (the parent performs the final
integrated committed-tree review and publication). All existing work (P2/v2/v3/v4) preserved
byte-exact; every old manifest unchanged.
