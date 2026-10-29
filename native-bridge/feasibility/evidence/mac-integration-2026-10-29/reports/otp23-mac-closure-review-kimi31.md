# OTP-23 Mac: bounded F1–F3/R01 closure review (Kimi K3.1 Pro, 2026-10-29)

## 0. Role and scope

- **Reviewer:** Kimi, actual configured model `kimi-code-plan-global/k3.1-pro`, selected by the owner in
  `/tmp/opencode/otp23-mac-kimi31-reviewer-approval.json`.
- **Role:** independent reviewer of the **remaining bounded closure only**. This is not a repeat of the
  earlier source, raw-data or physical reviews, not an implementation session, and not a new integrated-review
  loop.
- **Not done:** no subagents, source/doc/evidence edits, hardware, GitHub, staging, commits or pushes. No API
  headers/error bodies, event transcripts or full private transcripts were read.
- **File written:** this report only.
- **Baseline:** HEAD `9c3ddc54a3721eced84e49f02ed76ef156f0baed`, branch
  `contract/otp23-desktop-alignment`, with all WIP preserved. Current tracked-diff sha256 is
  `49ed1bcaf0719c14a97b55fe4be4574fefcc66e2f341787d5932e9d66a139845`, matching post-review gate 09.
- **Relied upon:** the fresh code approval from Opus `e30e474d` and the substantive final integrated review
  from Opus `faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b`. Their approved substance was not reopened.

## VERDICT: BOUNDED CLOSURE VERIFIED; PUBLICATION HANDOFF NOT YET COMPLETE

F1, F2, F3 and the authorized R01 update are closed within Opus `faee` §7. I found no new substantive defect
and no unauthorized proof/source change. The existing post-review gates are green, with the expected NO-GO
decision exit 1.

**Do not call the whole publication ready yet.** The active integration manifest is still explicitly
provisional, and two clerical handoff records remain: the implementer closure result is absent and the PR draft
still contains its pre-closure final-review placeholder. Complete the exact mechanical steps in §6, then the
commit/clean-export/green-CI conditions. No further integrated-review loop is needed if those steps add only
reports/metadata/maps/manifests and leave the reviewed source, matrix semantics and frozen evidence unchanged.

## 1. F1 — CLOSED, exact option (a)

Verified against the private original without reproducing the four unrelated names here:

- Private original:
  `/tmp/approved-otp23-privacy/mac-resume-2026-10-28/registration-record.json.private-original`
  - mode **600**; containing directories mode **700**
  - sha256 `a5c405ac18027c75311ff210275387eef8a73fe2641457c6d3f5628be403deac`
- Published `mac-resume-2026-10-28/registration/record.json`:
  - sha256 `61335dc55cf0e48b28a124cfd2dd0b6811552e55726a093c5a83b5158e3f21d6`
  - replacing the four original JSON string tokens, one each, with
    `<other application's manifest>` reconstructs the published file **byte-exactly**
  - array length remains **4**; `veles_manifest_present` remains **false**; every other JSON field is equal
- Scoped sweep of all **248** currently modified/untracked publication files for the four exact names and their
  filename stems: **0 hits**. This is only the authorized scoped sweep, not a broad privacy-absence claim.
- `curation/curation-map.json` contains the new redaction entry, both hashes and scope, without quoting the
  names.
- Manifest transition is exactly authorized:
  - old 35-entry manifest copy `manifest-history/SHA256SUMS.pre-registration-redaction` hashes to the required
    `9e8ed07767a0ed6f6da4094685bd6669d7f9a081f6975d9b8ecafed1917af022`
  - new active manifest: **36 entries**, sha256
    `e90d3c21a76009c40c7e56c0d06705ef91fc4aef529621672e2796be70212214`
  - new curation manifest sha256
    `60dc1017445eb6ccf9e0e6a7fb955836cc3c0fa3afbf396ecf18368f52d29ab5`
  - changed entries are exactly `registration/record.json`, `curation/curation-map.json` and
    `curation/SHA256SUMS`; the only added entry is the historical old-manifest copy; no entries were removed
  - every other old entry retains its old hash, so no raw case/proof bytes changed
- `FINAL-INTEGRATION.md` and `final-integration-map.json` map old → new and correctly leave copied historical
  reports unchanged.

## 2. F2 — CLOSED

`adjudications/m08-final-adjudication.md:117-125` now says:

- the parent preflight decision (`m08_eof_scope`) chose the W-08 extension-owned EOF reading;
- final integrated review `faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b` independently verified it;
- the dated erratum expressly says the earlier e30 micro-review attribution was wrong.

The direct-SSH EOF remains a `permission_pending` diagnostic, neither PASS nor waived. The Chrome-owned
port-disconnect/tab-close evidence and all substantive findings remain as previously approved.

## 3. F3 — CLOSED

- The inaccurate original `gates/06-matrix.log` is byte-preserved and labelled superseded pre-review history by
  `gates/PRE-REVIEW-NOTE.md`; it is not presented as a literal executed pipeline.
- `gates/post-review/06-matrix.log` records the actual commands and statuses:
  - `matrix.py render --write` exit 0
  - document sha256 before/after both
    `cb27c017b905d6df83f7b9d6a740db922851b740f4414d8e6d46f582f6c6d415`; unchanged comparison exit 0
  - render/staleness exit 0 and check exit 0
  - decision exit **1**, exactly the expected NO-GO for W-10/M-03/M-04/A-01
- The first post-review attempt is also preserved, with `06-matrix.attempt1-NOTE.md` accurately explaining its
  mislabelled HEAD/index comparison rather than hiding it.

## 4. R01 and source/proof integrity — WITHIN AUTHORIZED SCOPE

Byte-level reconstruction checks against the reviewed hashes prove the bounded deltas:

- `otp23-matrix.json`: removing only the new R01 final-review `actual` suffix and the final-review evidence
  reference reconstructs reviewed sha256
  `8a54f57139941db0f3f35c560b4a21bcd6aa3a93335add7018a8c992f356c28f` exactly. Current sha256 is
  `274ede059c500ee9f1dba4a9559eef34f2433dbbd8ca49720feaa5c72b009f7c`.
- `test_matrix.py`: removing exactly the three new R01 `assertIn` lines reconstructs reviewed sha256
  `4a7b106aaa3fedba44ae5dc6e1953b0e5c8c0f9ca757cda4229682e71e794f83` exactly. There are no other test
  changes since that reviewed version. Current sha256 is
  `aa676ae286311b1e1ba10e06a2a406e6f4cee3f2966af5e05a696270bacebdba`.
- The investigations and contract-alignment changes are append-only closure sections; removing those sections
  reconstructs their reviewed hashes exactly.
- The final Opus review copy is byte-identical to `/tmp/opencode/otp23-mac-final-integrated-opus-review.md`,
  sha256 `16152fa7cd6d0d5ed563d58ce4d7bdb08ca9015b72a2fc38fa19774363341e2d`; its safe whitelisted metadata
  and `/tmp` citation mapping are present.
- R01 keeps the PENDING history and appends the actual `faee` verdict, F1(a), closure implementer
  `9f6f8b0f`, and “APPROVED for commit/publication pending clean export and green CI.” It adds no OTP-24/GO
  claim.

Recomputed unchanged proof/source pins include:

- `mac_release_attribution.py` `ce75a8f1…634e`
- `test_mac_release_attribution.py` `219c78be…f016`
- `mac_release_bound.py` `a49b3450…6560`
- `test_mac_release_bound.py` `4b8aa10d…`
- `scenario_matrix.py` `f8bbf2d6…`
- `scenario_matrix_core.py` `cb512418…`
- `macos_poc.py` `e2131ad3…`
- `mac_clock_corroborate.py` `8e6b9bbb…`
- `matrix.py` `3d45aac1…`
- workflow `df1305d4…`

The seven unaffected frozen manifest pins also recompute exactly: retake `f952c123`, A2 safe `c05174f4`, A1
`dd05dac4`, source-history `2a550c1c`, M11-dev `071d14f1`, codephase `339505b6`, and TCC supplement
`a1e4d80b`.

## 5. Matrix, gates, M11 and roles — CONFIRMED

Independent read-only checks in this review:

- `matrix.py check` → `matrix ok: NO-GO`.
- `matrix.py decision` → exit **1** as designed; open exactly **W-10, M-03, M-04, A-01**; waived exactly
  **W-01, M-01, M-02**.
- `test_matrix.py` → **Ran 35, OK**.
- Matrix remains **25 PASS / 4 BLOCKED / 3 NOT RUN (waived) / 0 FAIL**. OTP-24 remains unauthorized.

The actual post-review logs record:

- forced Java 21 Spotless: exit 0, `BUILD SUCCESSFUL`, 3/3 executed;
- full Spotless/detekt/lintDebug/unit/protocolCheck: exit 0, `BUILD SUCCESSFUL`;
- both raw-Log guards: exit 0;
- Python: **Ran 512, OK (skipped=5)**, with the five Windows-machine smoke skips;
- pycompile: **50 files OK**, exit 0;
- tracked and untracked non-evidence whitespace checks: exit 0;
- all manifest-listed hashes: PASS. Independently recomputed here: **24 manifests / 1,307 listed entries**.

M11 remains a **scoped manual PASS**, not a raw PASS:

- attempt 1 retains its synthesized `port_closed` setup failure;
- attempts 2 and 3 retain `human_prompt_answer: null`, deadline endings (720.9 s and 3600.3 s), their original
  classifications and non-empty problems;
- late external answers remain outside the captures and unaccepted; no captured null is rewritten;
- account record `a2bdc40c…` is the actual post-run admin/uid-501 record;
- the original signed fixture/grant remains unchanged, while the distinct dev grant is recorded as persistent;
- the developer prompt is not transferred to M03, and M04 has no approved/performed OFF→ON run.

Roles remain accurate: GLM performed earlier code work; Opus performed its implementation and reviews; parent
`gpt-6.1-sol` was the direct M11 operator; Opus `9f6f8b0f` implemented this bounded closure; Kimi K3.1 Pro is
**review-only now**.

## 6. Exact remaining generated-data and publication steps

### A. Mechanical handoff still required

1. **Create the missing implementer as-built closure result** required by the closure task:
   `/tmp/opencode/otp23-mac-review-closure-result.md`. Populate it mechanically from the existing closure files
   and post-review logs; do not invent another implementation or review session.
2. **Append the authorized closure to `/tmp/opencode/otp23-mac-pr-update.md`.** It still has the pre-closure
   placeholder at lines 104–105. Record the actual Opus `faee` final review/F1–F3 closure and this bounded Kimi
   closure review without rewriting history. Fill the commit/clean-export/CI fields only after those real
   results exist. Keep #122/#86 unchanged and merge nothing.
3. **Publish this Kimi report byte-identically** under active `mac-integration-2026-10-29/reports/`, add only
   safe actual provider/model/session/role/verdict/report-hash metadata, and resolve its `/tmp` citation in the
   integration map. Do not copy API headers, error bodies, usage/cost bodies or transcripts.

### B. Refresh the active manifest last

Current active `mac-integration-2026-10-29/SHA256SUMS` is:

- **46 entries**, sha256 `667c70ed1207925af642c76ddc66d3591f67668c3b6da03e21b68c761351f07d`;
- internally valid for those 46 entries, but explicitly provisional;
- missing exactly **11** files, all under `gates/post-review/`; the active folder currently has **57** non-self
  files before the new Kimi/metadata handoff additions.

After every report/metadata/map addition, regenerate **only this active integration manifest** in its existing
sorted relative-path format. Then require:

- all 24 `sha256sum -c` checks pass;
- active disk set equals active manifest (excluding only `SHA256SUMS` itself), with no unlisted post-review or
  report/metadata files;
- the authorized mac-resume manifests remain `e90d3c21…` / `60dc1017…`;
- every other frozen manifest and all proof/source hashes remain unchanged;
- `git diff --check`, `matrix.py check`, and `test_matrix.py` remain green; decision remains expected exit 1
  NO-GO.

The already-completed post-review Java/Python gates remain the evidence for the unchanged source/doc tree. If
anything beyond mechanical reports/metadata/maps/manifests changes, stop and revalidate that change rather than
using this bounded approval to cover it.

### C. Existing publication conditions still required

1. Preserve the focused WIP in the actual commit on `contract/otp23-desktop-alignment`.
2. On a clean `git archive` export of that exact commit, verify all 24 manifests and active disk coverage, then
   run `matrix.py check` and `test_matrix.py` successfully (and confirm the expected NO-GO decision).
3. Obtain green main CI and green Linux, macOS and **Windows** feasibility CI.
4. Update PR123 from the completed draft with the real final-review and clean-export/commit/CI records; leave
   #122 and #86 unchanged and nothing merged.

## 7. Private backup — COMPLETE, no longer a remaining blocker

The earlier backup/receipt (`e60cb30e…`) did predate F1, but a refreshed unique backup now exists and was
verified in this review:

- archive: `/home/dev/.local/share/veles-private-evidence/otp23-mac-2026-10-29-final.tar`
- sha256 `0b2ab4034b272688c6965540a2934b7c1aa9108a5ea7288a2e27b3c99347dad4`
- archive mode **600**, parent directory mode **700**
- `tar --compare` against current `/tmp/approved-otp23-privacy` source: **exit 0**
- `/tmp/opencode/otp23-private-backup-final-receipt.json` matches that archive/hash and explicitly includes the
  new F1 original; the old archive/receipt and temporary citation paths remain preserved.

This is a local protected copy, not an off-host-backup claim. Preserve both archives/receipts.

## Final approval condition

Once §6’s mechanical handoff and final manifest refresh verify cleanly, the Opus `faee` conditional approval
stands: **APPROVED for commit/publication pending the clean Git export and green CI**, with no new integrated
review loop. Final publication approval follows only after those export/CI checks pass. The product decision
remains **NO-GO**, with W-10/M-03/M-04/A-01 open and only W-01/M-01/M-02 waived.
