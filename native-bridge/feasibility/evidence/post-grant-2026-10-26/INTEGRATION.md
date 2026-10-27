# Post-grant evidence integration report (2026-10-26/27)

Authority: the generated `docs/otp-23-requirements-matrix.md` (from `acceptance/otp23-matrix.json`).
This file is the index and honest summary; it adjudicates nothing. **Decision: NO-GO for OTP-24.**
No functional code, device, subagent, commit or push was used to produce this integration.

## Layout (canonical vs duplicate)

| Folder | Use |
|---|---|
| `windows-initial/`, `macos-initial/` | canonical initial sweeps (0d75bb2 scripts), first lifecycle runs (Windows mostly BLOCKED), first (timed-out) Mac permission recovery |
| `windows-final/`, `macos-final/` | canonical later runs: Windows lifecycle scan-active rerun, Windows foreground-gated strict runs, Mac strict cold/prewarm raws, controlled Mac recovery (`recovery2-20261027`), M6 desktop raws |
| `windows-background/`, `macos-background/` | PARTIAL DUPLICATES (byte-identical to the `-final` copies where compared); kept, not cited as primary |
| `phone-evidence/` | NEW: copies of `/tmp/opencode` phone proofs, transport snapshots, offline Mac adjudications and fixture logs, unchanged |
| `windows-strict-history/` | NEW: copies of every earlier Windows strict result (setup failures, blur failures) so no failure is erased. It also holds two PASS duplicates, `otp23-windows-strict-cold2.json` and `-prewarm2.json`, byte-identical to `windows-final/.../*-foreground-2.json`; they are not failures (mapping below) |
| `m6/` | NEW: the five M6 source JSONs, the barrier file, the Opus manual adjudication (unchanged) |
| `reviews-and-provenance/`, `post-grant-model-provenance.json`, `post-grant-identity.json` | NEW: raw CLI metadata, clock measurements, identities |
| `signing-and-gates/` | NEW: real apksigner / codesign output, install readback, the parent's 3dbe3db gate log, the release-DEX search output, the per-class JUnit summary and the parent's final-gate logs (separate logs, see below) |

The 11 `._*` AppleDouble files from the macOS tar copies are git-ignored (`native-bridge/feasibility/.gitignore`) and not committed; they are archive artifacts, not evidence, and are excluded from `SHA256SUMS`.

## Row dispositions after this integration (15 PASS, 14 BLOCKED, 3 NOT RUN waived)

- **Promoted to PASS (actual fresh post-grant proof):** W-04, W-05, W-06 (Windows two-phone sweep 25/25), W-09 (Chrome shutdown with scan + 2 links),
  M-05, M-06 (controlled retry recovery2), M-07 (Mac two-phone sweep 26/26), A-02 (M6, manual adjudication supplementing a retained machine FAIL), A-03 (both phones' fixtures run).
- **Still BLOCKED, with new partial evidence:** W-02 (windows/endpoints measured only before connect), W-07 (port.disconnect with links BLOCKED; tab-close passed),
  W-08 (no Chrome-provenance stdin EOF with links), W-10 (radio-off/adapter-absent not run), M-04 (guidance text and prompt owner not recorded), M-08 (connected-phase windows unmeasured),
  A-01 (no physical duplicate-STATE_CONNECTED), M-03 (no Developer ID / CI signing provenance), M-11 (unsigned dev host never run).
- **Strict background W-11, W-12, M-09, M-10 are BLOCKED with OBSERVATIONAL PASS results:** all checks passed on Windows (retry runs) and Mac (offline-finalized), but Chrome's intrinsic Native Messaging exemption is not excluded
  and `formal_otp23_acceptance` is false. The RFC says no exemption of any kind; the matrix does not waive or rewrite it.
- **R-01 is PENDING:** the earlier e19c8ca-scoped Opus PASS is preserved and scoped to the source before the new harness/telemetry code; the final fresh Opus review of the post-grant source has not happened.

## Proposed (NOT approved) RFC clarification, for owner review

Allow the architecture-required Native Messaging port, but still forbid artificial keepalive, audio, WebRTC, any other exemption and any CDP client; require actual unsolicited delivery of the 4096-byte records
to a hidden connector while an ordinary tab is visible and focused for >= 600 s. If the owner approves, W-11/W-12/M-09/M-10 can be re-adjudicated from the existing evidence; until then they stay BLOCKED.

## Preserved failures (all retained, none relabelled)

- Windows lifecycle first run: LC-shutdown, LC-port-disconnect, LC-tab-close, LC-failure-isolation BLOCKED (connect_failed / access_denied); rerun: LC-port-disconnect and direct LC-stdin-eof BLOCKED. Cause UNPROVEN.
- Windows strict: two cold setup FAILs (connector visible, desktop lock), first cold and first prewarm foreground-gated runs FAIL ST-12 (ordinary-tab blur). Retries `-foreground-2` passed all 15 checks and do not erase them.
- Mac first permission recovery timed out (`ack_missing`): FAIL. Mac strict raws ended BLOCKED (missing phone file) and were finalized offline into separate adjudication files; the raws are unmodified.
- M6 machine adjudication FAIL (`same_phone_identity`, `real_io_intervals_overlap` under +/-3154.105 ms) is retained next to the Opus manual PASS and its six residuals.
- Pre-final snapshot caveat: the phone transport files are copied as produced; none was relabelled `final`.

## Data caveats stated in the matrix

- The initial two-phone sweeps' phone APK hash is not recorded in the raw files (installation history implies the e19c8ca APK `1e0ae023...`); the earlier narrower 24/24 and 25/25 runs have no preserved raw and are not cited.
- Mac `macos-strict-*-raw.json` checks were recomputed offline by the parent; the adjudications declare `formal_otp23_acceptance=false`.
- Operator attribution for M-05, M-06 and A-03 is the account owner's report; no TCC database or permission-grant mechanism was observed by an agent.
- The recovery driver's per-phase Mac signature readbacks carry `signature`/`cdhash` null; the stable signature is from `signing-and-gates/otp23-postgrant-mac-signature.txt`.
- Android: certificate `2081d17d...506e` matches the approved key; Mac: same bundle id/team/host hash; Windows: same host hash. No new signing-CI provenance is claimed.

## Still genuinely unmet

M-03 CI/distribution signing provenance; M-11 unsigned dev-host existing-account matrix; Windows-radio-off / adapter-absent classification (W-10); the intermittent Windows port-disconnect AccessDenied
(W-07/W-08); connected-phase window measurement (W-02/M-08); M-04 guidance/prompt-owner; A-01 physical duplicate-connect; the literal Native Messaging exemption acceptance; R-01 final review. OTP-24 is not implemented and no issue is closed.

## Review disposition (final post-grant Opus review, session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633`, actual `claude-opus-5-5`)

Raw result and report are copied unchanged to `reviews-and-provenance/otp23-postgrant-final-opus-result.json` and `otp23-postgrant-final-opus-review.md`.
The earlier integration pass (actual `claude-sonnet-5-5`, session `fe9d1222-792e-4494-a2af-3e5468bb4d6a`) raw is `reviews-and-provenance/sonnet-postgrant-integration-result.json`.
0 CRITICAL, 2 IMPORTANT, 7 MINOR. **R-01 stays PENDING until the parent independently verifies these corrections;** the decision stays NO-GO. No row status was promoted and no waiver was added.

| Finding | Disposition |
|---|---|
| IMPORTANT-1 evaluator provenance | Correction (b), no weaker legacy acceptance. See "Historical Mac adjudications" below. |
| IMPORTANT-2 M-11 reason | M-11 now says: not executed, no unsigned dev-registered host built or run on the existing account, the grant covers only the signed `me.nagaev.veles.bridge` bundle, no result, not waived. `test_matrix.py` guards against the old "cannot approve" wording. M-09/M-10 pre-grant sentences with that wording are labelled PRE-GRANT history. |
| MINOR-3 A-02 reproducibility | `scripts/m6_phone_clock_adjudication.py` (+ `test_m6_phone_clock_adjudication.py`) and its output `m6/otp23-m6-phone-clock-adjudication-output.json` (bound to the phone file sha256 `6515a400...ae478`): 16 overlapping pairs / 1505 ms, 100 records and 900 API / 900 callbacks per peer, 71 survivor records / 639 notifications. The generic merger stays FAIL; no authentication claim. Central hash prefix `576d2120` appears in every Mac strict phone file and `c3a3dc84` in every Windows strict phone file (corroborates the role mapping, not authentication). |
| MINOR-4 DEX / 334 tests | `signing-and-gates/otp23-3dbe3db-release-dex-inspection.txt` (re-read from the release APK whose sha256 equals the approved `5d770ede...3f10`; 0 hits for the nine needles) and `junit-summary-334.txt` (42 classes, 334 tests, 0 failures/errors/skipped, read from the current JUnit XML, not rerun). The original parent inspection output was not preserved; this is a re-read of the same APK, not a rebuild. `test_matrix.py` checks these files, not the identity JSON alone. |
| MINOR-5 derived bounds | W-05/M-07 now state the fragment-size bounds are derived from the measured MTU / max write size and the host fragmenter, not measured per fragment. The phone APK for the sweeps stays "inferred". |
| MINOR-6 Windows strict mapping | Table below. |
| MINOR-7 residuals | Listed in W-11/W-12 and M-09/M-10 `actual` and below. |
| MINOR-8 gate logs | Copied separately, see "Gate logs". |
| MINOR-9 AppleDouble | Reworded above. |

### Historical Mac adjudications (IMPORTANT-1)

`phone-evidence/mac/otp23-macos-strict-{cold,prewarm}-adjudicated.json` contain ST-01..ST-14 (all PASS) only. They were produced by the evaluator that existed before commit `db5d4db`
(the `3dbe3db`-era `strict_background_core.py`). That is **operator-inferred** from their timestamps (`2026-10-27T00:10:51Z` and `01:04:39Z`, before `db5d4db` at `01:09:50Z`) and the 14-check content;
the files record no evaluator revision or hash and nothing was retrospectively recorded. The originals are unchanged.

Since `db5d4db`, `evaluate()` always emits ST-15 (Windows OS-foreground gate), and the Mac raws have no `os_foreground` evidence, so the **current finalizer re-evaluating the same committed raw and phone files
(recorded clock, original per-file source hashes and collection mtimes) returns BLOCKED with ST-15 BLOCKED ("no foreground evidence recorded")** while ST-01..ST-14 stay PASS. Those re-evaluations are
saved as new exclusive-create files in `phone-evidence/mac/reevaluation-at-head/`; they record the evaluator provenance below. There is no legacy bypass (`test_strict_background_finalize.py` asserts none) and no status promotion:
M-09/M-10 stay BLOCKED, so the historical PASS cannot be reproduced at HEAD and is observational only.

Every NEW finalizer output now carries `evaluator_provenance`: `git_revision` (null when not a git checkout), `git_available`, `evaluator_files_dirty` / `evaluator_files_modified` (scoped to the three evaluator files;
unrelated uncommitted work is not reported as evaluator change, and null when unavailable), and the SHA256 of `strict_background_core.py`, `strict_phone_validation.py` and `strict_background_finalize.py`.
The two re-evaluations were produced at revision `db5d4dbe294caee68b0898c9e1670f30475a9826` with `evaluator_files_dirty: true` (the finalizer itself carried the uncommitted provenance change), and record the exact hashes of the code that ran.
Command: `python3 -I` inline script importing `strict_background_finalize` and calling `adjudicate()` on the committed raw and phone copies (the CLI cannot take the original source hashes because the adb serials are not stored).

### Windows strict run to phone-file mapping (MINOR-6)

| Run (`windows-final/post-grant-2026-10-27/`) | Result | Phone files (sha256 prefix -> file in `phone-evidence/windows/`) |
|---|---|---|
| `windows-strict-cold-foreground.json` (copy: `windows-strict-history/otp23-windows-strict-cold-foreground.json`) | FAIL ST-12 | `82e5c85d...` `otp23-win-cold-phone-6t.json`, `c8de9e82...` `otp23-win-cold-phone-10pro.json` |
| `windows-strict-cold-foreground-2.json` (**PASS duplicate** in history: `otp23-windows-strict-cold2.json`) | PASS | `c44c9486...` `otp23-win-cold2-phone-6t.json`, `201afcc4...` `otp23-win-cold2-phone-10pro.json` |
| `windows-strict-prewarm-foreground.json` (copy: `windows-strict-history/otp23-windows-strict-prewarm.json`) | FAIL ST-12 | `da5d6899...`, `17c67bc8...`: **phone files were NOT preserved**; only the FAIL result and its hashes remain |
| `windows-strict-prewarm-foreground-2.json` (**PASS duplicate** in history: `otp23-windows-strict-prewarm2.json`) | PASS | `cd955bbe...` `otp23-win-prewarm2-phone-6t.json`, `40921dd9...` `otp23-win-prewarm2-phone-10pro.json` |

`windows-strict-history/otp23-windows-strict-cold-first.json` and `-cold-second.json` (setup FAILs) have no phone files. Nothing was deleted.

### Strict proof residuals (MINOR-7)

Phone distinctness in ST-08 depends on the operator's `SERIAL=PATH` binding (the phone files carry no device id; for Mac it was applied offline on Linux; corroborated by two distinct desktop BLE handles).
The harness revision is operator-identified (the Mac driver's `source_revision` is unavailable, not a git checkout; the Windows `-foreground-2` runs executed from a directory named `otp23-scenarios-3dbe3db` relying on the `db5d4db` gate, whose ST-15 presence shows the gate code ran), not a recorded checkout.
macOS has page-reported focus/visibility only, no OS foreground gate. The fix-wave session results (`*-fixes.json`) were not captured; their model metadata is unrecorded and nothing is fabricated for them.

### Gate logs (MINOR-8): separate logs for separate checks

- `signing-and-gates/otp23-postgrant-final-gradle-gates.log`: the parent's final **Gradle** gates only (spotlessCheck, detekt, lintDebug, testDebugUnitTest, protocolCheck; BUILD SUCCESSFUL).
- `signing-and-gates/otp23-postgrant-final-log-guards.log`: the raw `android.util.Log` guard text, verbatim as recorded (`fail=0`).
- `signing-and-gates/otp23-postgrant-final-python-tests.log`: the feasibility Python test run (297 tests at that time).
- The parent also ran the Node suite (5), the shell suite (69 checks), protocol (165 plus text) and `matrix.py check` (all PASS). **Those results do NOT appear in the Gradle log** and no log of theirs is claimed here.
