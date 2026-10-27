# OTP-23 post-grant final review (fresh, read-only)

- Reviewer: Claude Code session running on `claude-opus-5-5` (the model ID in this session's system context). The authoritative model metadata is the `modelUsage` block of this CLI run's JSON result. Nothing here is a self-assigned alias.
- Scope: `7afd164..db5d4db` (0d75bb2, 15f57a4, 3dbe3db, db5d4db) plus the **uncommitted** working-tree integration: the 5 modified files and the untracked `native-bridge/feasibility/evidence/post-grant-2026-10-26/`.
- Mode: read-only. No repository edits, devices, subagents, commits or pushes. The only file written is this report. Scratch outputs went to `mktemp` directories, which were deleted afterwards.
- Date: 2026-10-27.

## Verdict

- **No CRITICAL findings.**
- **2 IMPORTANT findings.** Both concern documentation or evidence truthfulness and reproducibility. Neither changes a row status.
- **7 MINOR findings.**
- Every acceptance promotion in the matrix is backed by real raw files that I checked independently (details below).
- No acceptance criterion has been rewritten. For every row, `requirement`, `expected`, `source`, `group`, `human_gated` and `waiver` are unchanged against HEAD; only `status`, `actual`, `evidence`, `blocker`, `operator` and `account` changed.
- The waiver covers only W-01, M-01 and M-02.
- Strict background stays BLOCKED.
- **OTP-24: NO-GO.**

## What I verified myself (not taken from the integration notes)

| Claim | How I checked | Result |
|---|---|---|
| Matrix and generated doc agree | `python scripts/matrix.py check` (exit 0, `matrix ok: NO-GO`); render compared byte for byte | OK: 15 PASS / 0 FAIL / 14 BLOCKED / 3 NOT RUN |
| Every matrix evidence path exists | script over all rows | 0 missing |
| Evidence checksums | `sha256sum -c SHA256SUMS` | 170/170 OK. The 11 extra `._*` AppleDouble files are git-ignored (`native-bridge/feasibility/.gitignore:6`) and will not be committed |
| Raw files copied unchanged | `cmp` against the `/tmp/opencode` originals | 4 Opus raws, 5 M6 JSONs + barrier, both Mac adjudications and recovery2 `result.json` are byte-identical. M6 copies in `m6/` are identical to `windows-final/` and `macos-final/` |
| Mac adjudications bind the raws | raw sha256 recorded in each adjudication vs the committed raws | `883a3100…` (cold) and `2c462434…` (prewarm) match |
| Windows `-foreground-2` phone proofs | phone-file sha256 in `phone_validation.files` vs the copied files | cold-fg-2 = `cold2-phone-{6t,10pro}`; prewarm-fg-2 = `prewarm2-phone-*`; first cold-fg FAIL = `cold-phone-*` |
| Earlier failures retained | statuses of the history files | cold-first, cold-second, cold-foreground, prewarm(-foreground) are FAIL; Mac raws are BLOCKED; first Mac recovery is FAIL; first Windows lifecycle has 4 BLOCKED; M6 machine verdict is FAIL with `m6_discharged=false` |
| M6 phone-clock adjudication | my own recomputation from `otp23-m6-phone-transport.json` (group 9 `notify_api` per record, record end = last bound callback, `elapsed_realtime_ms`) | Exactly **16 overlapping record pairs, 1505 ms summed, 100 records/peer, each 8×512+36, 900/900 API SUCCESS and 900/900 GATT_SUCCESS per peer, 0 unbound/pending/dropped/after-close, 71 survivor records / 639 notifications** after peer `576d2120…` disconnected. `run_id` matches `pair_run_id`. MTU 517 on both |
| Mac recovery2 | `result.json` and host trace | Same Chrome pid 60558, `load_unpacked_calls=1` in every phase, same sentinel `15e86c2b…`, binary `e7c716c5…` at start and end. Trace sequence: `allowed_always` → `denied` → `scan_failed/access_denied` (retryable) → `allowed_always` + `device_found` → `stopped` → `shutdown_complete` last. Prompt `unknown`, `popup_owner` "not inferred" |
| Windows LC-shutdown (W-09) | `shutdown.json` | Scan active plus 2 links proven with echo-verified 16-byte records. Pending request resolved. Output order ends `…disconnected, disconnected, shutdown_complete`. Trace exit 0 with stdout drained. Host gone in 0.64 s. Medium integrity, unelevated |
| Windows sweep (W-04/05) | sweep JSON and trace | 25 cases PASS; sizes include the full W-05 list; `mtu=517` logged before `subscribed` on both connections |
| Release stripping | unzipped the release APK `5d770ede…` (sha matches) and searched the DEX | 0 hits for `TransportTelemetry`, `SyntheticTransportFixture`, `scheduleSyntheticPush`, `OTP23_POC`, `otp23-transport-evidence`, `otp23-unsolicited`, `notify_api`, `callback_after_close`, `GATT_FAILURE` |
| 334 JVM tests | current `app/build/test-results/testDebugUnitTest/*.xml` (42 files) | 334 tests, 0 failures, 0 errors, 0 skipped. `TransportTelemetryTest` adds the 13 new tests (321 + 13) |
| Feasibility suites (CI order) | ran each one | poc_background 26, strict_background 90, matrix 27, finalize 22, scenario_matrix 59, macos_permission_recovery 28, macos_poc 36, macos_background_support 9: all OK. Node connector/isolated: 5 pass. trace-parser passes. macos-package-lib: 69 checks pass |
| CI raw `android.util.Log` guard | ran both greps from `ci.yml` | No hits |
| `git diff --check` | ran it | Clean |
| Gradle gates log | `/tmp/opencode/otp23-postgrant-final-gates.log` | spotlessCheck, detekt, lintDebug, testDebugUnitTest and protocolCheck all ran; `BUILD SUCCESSFUL`. This log contains only the Gradle gates; the other items in this table I ran myself |

## Findings

### IMPORTANT-1: The HEAD finalizer cannot reproduce the committed Mac strict adjudications, and the adjudications don't record which code produced them

- **Files:** `native-bridge/feasibility/scripts/strict_background_core.py:304-325` (`_foreground_check`) and `:497`; `evidence/post-grant-2026-10-26/phone-evidence/mac/otp23-macos-strict-{cold,prewarm}-adjudicated.json`; `INTEGRATION.md`; alignment §15 ("finalized OFFLINE with the strong validator").
- **Evidence:**
  - The committed adjudications contain checks ST-01..ST-14 only, all PASS. They were made at `2026-10-27T00:10:51Z` and `01:04:39Z`, before db5d4db was committed (`01:09:50Z`).
  - At HEAD, `evaluate()` always emits ST-15. When `os_foreground` is missing it returns BLOCKED.
  - The Mac raws predate the gate and have no `os_foreground`. (A HEAD-era Mac run would write `{"required": false}`; see `strict_background.py:392`.)
  - I re-ran the HEAD finalizer on the committed raw plus phone files with the recorded clock (offset 1240.779 ms, uncertainty 5013 ms). Result: `strict_result=BLOCKED`, with `ST-15: BLOCKED "no foreground evidence recorded"`.
  - The adjudication JSON records no evaluator commit or file hash. A reader therefore cannot tell that the PASS came from the pre-db5d4db evaluator. Nothing in the repository can regenerate it.
  - Status impact: none, because W-11, W-12, M-09 and M-10 stay BLOCKED. But the "observational PASS" offered for a future owner re-adjudication cannot be reproduced at the reviewed head.
- **Required correction (pick one, then record it):**
  - (a) Add explicit, tested handling for legacy macOS raws. Example: `platform == "macos"` with absent `os_foreground` is treated as `required: False`, recorded as `legacy_raw_no_gate`, and never applied on Windows. Then re-adjudicate into **new** files; the existing ones are exclusive-create and must not be overwritten.
  - (b) State in INTEGRATION.md, alignment §15 and the M-09/M-10 `actual` text that the adjudications were produced by the 3dbe3db-era finalizer, and that HEAD yields BLOCKED (ST-15 absent) for these legacy raws.
  - In either case: make `strict_background_finalize.py` record the evaluator's git revision and the sha256 of `strict_background_core.py` / `strict_phone_validation.py`. Add a test that re-runs the finalizer on the committed raws and compares statuses with the committed adjudications.

### IMPORTANT-2: The M-11 blocker gives a reason that is no longer true after the grant

- **File:** `native-bridge/feasibility/acceptance/otp23-matrix.json`, row M-11 (`actual` and `blocker`), and the generated `docs/otp-23-requirements-matrix.md`.
- **Evidence:**
  - The row says the authorization "cannot be granted now (the user cannot approve Bluetooth permissions)".
  - In this phase the account owner did approve Bluetooth permissions: they toggled VelesOtp23PoC ON and OFF in recovery2, and both phones' grants exist.
  - The status (BLOCKED) is correct. The stated reason is out of date and reads as if the test were impossible, which comes close to an implicit waiver of a row that is explicitly **not** waived.
- **Required correction:** Rewrite the M-11 `actual`/`blocker` to say what is true now: "not executed: no unsigned dev-registered host was built or run on the existing account in this phase; the existing grant covers only the signed `me.nagaev.veles.bridge` bundle; no result exists; not waived." Re-render the doc. Optionally add a `test_matrix.py` assertion that the M-11 blocker does not claim the user cannot approve permissions.

### MINOR-3: The A-02 PASS depends on a manual computation that isn't committed as a reproducible tool, and one residual is understated

- **Files:** matrix row A-02; `m6/otp23-m6-desktop-adjudication.json`. The adjudicator's disclaimer says "no override exists".
- **Evidence:**
  - The PASS is an explicit, documented override of the machine FAIL. That is acceptable, and the machine FAIL stays as the raw record.
  - However, the overlap figures exist only in an LLM review. I reproduced them exactly from the phone file (see table), so the claim is supportable.
  - Residual (2), "peer mapping inferred from timing", is weaker than stated. Peer prefix `576d2120…` is the same central prefix the phone recorded in the Mac strict runs, and `c3a3dc84…` matches the Windows strict runs. Those are independent runs with one desktop each.
- **Correction:** Commit a small deterministic phone-clock M6 adjudicator, or record its exact algorithm and output in the evidence. It should cover: grouping 9 fragments per record, interval = first API call to last bound callback, pairwise overlap, survivors after the first disconnect. Note the cross-run central-prefix correlation as corroboration; it is still not authentication. The scope "no cryptographic identity / protocol auth; orderly not fault-injected isolation; not OTP-24 GO" is correct and supported.

### MINOR-4: The release-DEX inspection and the 334-test count are stated as facts, but the inspection outputs aren't in the evidence

- **Files:** alignment §15 ("the R8/DEX inspection … found no …"); `post-grant-identity.json` (honestly says "the inspection output itself is not among the copied files"); `test_matrix.py::test_post_grant_identity_and_gate_counts_come_from_real_outputs`, which compares 334 only with the identity file it is testing.
- **Evidence:** Both claims are true; I checked them myself (table above). But a reader of the committed tree cannot check them.
- **Correction:** Copy the DEX search output and a JUnit XML summary (count per class, totals) into `signing-and-gates/`, or add "(inspection output not preserved)" to §15.

### MINOR-5: Some W-04/W-05/M-07 details are derived, not measured

- **Evidence:**
  - The sweep files do not record the phone APK hash. The rows correctly say e19c8ca `1e0ae023…` is inferred from install history. The requirement says "fresh build of this branch", and e19c8ca is an earlier, pre-harness build of the branch. Acceptable, and disclosed.
  - "fragments ≤ 512 / attribute 512, payload 508" is derived from `mtu=517` and the host fragmenter. The sweep trace logs no per-fragment sizes.
- **Correction:** Mark the fragment bound as "derived from MTU 517" in W-05 and the APK identity as "inferred" (already done). No status change.

### MINOR-6: Some Windows strict artifacts can be miscounted or don't line up

- **Evidence:**
  - `windows-strict-history/` is described as copies of "every earlier … failure". It also contains `otp23-windows-strict-cold2.json` and `-prewarm2.json`, which are PASS results byte-identical to `windows-final/.../*-foreground-2.json`.
  - The phone files named `cold2`/`prewarm2` belong to the `-foreground-2` runs.
  - The phone files of the first prewarm-foreground FAIL run (`da5d6899…`, `17c67bc8…`) are not preserved, though its FAIL result is.
- **Correction:** Add a mapping table to INTEGRATION.md (run file → phone files → sha256). Either label the two PASS duplicates in `windows-strict-history/` as such or drop them from the "history" description. State that the prewarm-fg-1 phone files were not preserved.

### MINOR-7: Provenance of the strict evidence has small gaps

- **Evidence:**
  - Phone distinctness in strict ST-08 depends on the operator's `SERIAL=PATH` binding. For Mac it was applied offline on Linux; the phone files carry no device id. This is corroborated by two distinct desktop BLE handles, but it is still an operator binding.
  - The Mac driver's `source_revision` is unavailable (not a git checkout). The Windows `-foreground-2` runs ran from a directory named `otp23-scenarios-3dbe3db` while relying on the db5d4db gate. The harness revision is identified by the operator, not recorded in the artifact. (ST-15's presence proves the gate code ran.)
  - The macOS strict PASS has no OS-level foreground corroboration (page-reported focus/visibility only). That is acceptable but asymmetric with Windows.
  - The fix-wave session results (`*-fixes.json`) are not copied; `post-grant-model-provenance.json` says so.
- **Correction:** List these as residuals in the W-11/W-12/M-09/M-10 `actual` text. Record the harness file hashes in future driver outputs. Copy the fix-wave raws or explicitly state that their model metadata is unrecorded.

### MINOR-8: The final gates log doesn't cover the non-Gradle CI checks

- **Evidence:** `/tmp/opencode/otp23-postgrant-final-gates.log` has no output from the feasibility Python and Node suites, `matrix.py check`, the raw Log guard or `git diff --check`.
- **Correction:** None needed for correctness; I ran all of them and they pass (table). Include them in the gates log if that log is cited as the gate record.

### MINOR-9: A wording issue in INTEGRATION.md

- **Evidence:** It says the 11 `._*` files "are left in place". They are git-ignored, so they will not be committed. Harmless.
- **Correction:** Optionally reword to "git-ignored; not committed".

## Code review notes (no defect found)

- **`TransportTelemetry` / `SharingGattServer`:**
  - Opt-in only (needs a run-id regex match); default `null`.
  - Every call site is behind `BuildConfig.DEBUG`; release stripping verified.
  - Bounded: 16 peers, 4096 events per peer, 16 pending; any overflow sets `truncated` or `peersDropped`.
  - `clean` needs: `seen == callbacks == apiSuccess`, zero non-zero codes, no unbound/pending/dropped/after-close.
  - The raw `callbackSeen` is recorded before the looper hop, against a `@Volatile closed`.
  - The final snapshot is written after a bounded service-stop wait inside `NonCancellable`, and `final_after_service_stopped` is recorded.
  - Peers are SHA-256 prefixes; no MAC or bytes are stored. Unit tests cover these cases.
- **`strict_phone_validation`:**
  - Fails closed: no clock → BLOCKED; bare path → BLOCKED; duplicate path, hash or source → FAIL.
  - Checks stale subscription windows, fire-before-minimum and fire-after-reattach.
  - Requires a distinct receipt per phone within uncertainty + 30 s.
- **Finalizer:** exclusive-create output; never overwrites a raw.
- **ST-15:** gate confirmed before `ble_start_mono`, owner re-verified after the tab switch. Unverifiable → BLOCKED; mismatch → FAIL.
- **`invoke-poc.ps1`:** strict-only parameters are validated and serials redacted in the status file. PowerShell string formatting is culture-invariant, so the double clock parameters are safe.

## Acceptance disposition

- **Can be discharged (PASS is supported by the raw evidence, with the stated residuals):**
  - W-04, W-05, W-06, W-09
  - M-05, M-06 (operator = account owner's Settings report; prompt `unknown`; no TCC-database claim)
  - M-07
  - A-03
  - A-02 (scoped manual adjudication on the single phone clock; numbers reproduced; machine FAIL retained; not an auth or identity claim)
- **Must stay BLOCKED:**
  - W-02, M-08: connected-phase windows not measured
  - W-07, W-08: Chrome-provenance port-disconnect / stdin-EOF with links BLOCKED; Windows `access_denied` cause unproven
  - W-10: radio-off / adapter-absent not run
  - W-11, W-12, M-09, M-10: observational PASS only; the Native Messaging intrinsic exemption is not excluded; `formal_otp23_acceptance=false`; the proposed amendment is pending owner review and not approved
  - M-03: Apple Development is not Developer ID / CI provenance
  - M-04: guidance text and prompt owner not recorded
  - M-11: not executed (fix the rationale; IMPORTANT-2)
  - A-01
  - R-01 (see below)
- **NOT RUN (waived, non-blocking):** W-01, M-01, M-02 only.
- **R-01 for this phase:** this review found no CRITICAL defects. R-01 may move to PASS only after:
  1. IMPORTANT-1 and IMPORTANT-2 are corrected and verified.
  2. The reviewed working-tree integration is committed unchanged apart from those fixes.
  3. The PASS row cites this review's raw CLI result (session id and `modelUsage`) bound to that commit.

  Until then, keep R-01 BLOCKED/PENDING. Even with R-01 PASS, the decision stays **NO-GO** because of the BLOCKED rows above.

## Mergeability

PR 123 **can merge as reviewed NO-GO documentation/harness work** once:

1. IMPORTANT-1 and IMPORTANT-2 are fixed;
2. the matrix is re-rendered and `matrix.py check` and the test suites pass again;
3. the CLAUDE.md local gates (`spotlessCheck detekt lintDebug` + Log guard) are re-run on the exact commit;
4. the OTP-23 feasibility CI jobs pass on ubuntu, windows-2025 and macos-26.

The MINOR items can be fixed in the same pass or explicitly deferred. Merging is **not** an OTP-24 GO. Nothing here approves implementation or Tauri packaging, and no issue should be closed on this basis.
