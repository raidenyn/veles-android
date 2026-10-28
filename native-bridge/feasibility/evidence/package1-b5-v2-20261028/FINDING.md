# OTP-23 package1 B5 finish phase (2026-10-28) — FINDING (Windows correction + v2 derivation)

Implementation: `ollama-cloud/glm-5.3-flash` (this finish session, opencode-db metadata in
`provenance/`), source-only, ZERO hardware reruns, zero subagents; independent review roles were the
authorized `openai/gpt-5.6-sol` sessions (copies byte-identical in `reports/`). Everything here is
**non-canonical until the parent's independent fix verification and explicit integration instruction**
(`reports/otp23-p2-b5-gpt-review.md` defined that condition). Nothing is committed or pushed by this
phase.

## What was repaired (B5-1..B5-4; only the four derived-analysis defects, no raw rewrite)

1. **B5-1 clock sign.** `pc_phone_clock_probe.py` defines `offset_phone_minus_pc_ms_median =
   phone_epoch - pc_epoch`. The converters had encoded the opposite: `_telemetry_bursts()` used
   `epoch + offset_ms` and the old W-08 analyzer `row_epoch_ms()` used `+ offset_ms`. Both now
   SUBTRACT. The offset regression was replaced by hand-computed positive AND negative measured-offset
   cases (`test_clock_offset_subtraction_hand_computed_offsets`; an implementation that adds the
   offset would print 1544 where 2356 is required and vice versa).
2. **B5-2 fail-closed completeness guards.** `phone_burst_association()` now requires EVERY expected
   phone document to carry the SAME non-empty `run_id` (a missing/empty id is a named problem, never a
   silent skip), requires `final_after_service_stopped is True`, and requires BOTH numeric fixture
   stop epochs (phone-side and PC-side) for every expected document. The existing stop-confound guard
   (stop >= bound + uncertainty after own exit; disconnect strictly before the stop marker) is
   unchanged — no new universal after-exit rule was invented: a legitimate release after the teardown
   anchor but before process exit stays acceptable inside the declared window.
3. **B5-3 trace origin.** `trace_origin_calibration()` derives the trace's own absolute origin from its
   PC-absolute `disconnect requested epoch_ms` anchor(s) (`epoch_ms - at_ms`; P2: anchors 2, spread 0,
   origin `1793170753120`). `correlate_samples()` uses `wall_ms - origin` for bracketing (removing the
   1718 ms native-host spawn delay the launch-based estimate included); the raw launch-based estimate
   stays recorded as provenance and is no longer used. Legacy traces without an absolute anchor keep
   the launch-based estimate, explicitly labelled an estimate (no new Mac gate invented, and no fake
   "ref Startup" relabelling of the anchor).
4. **B5-4 v2 provenance.** Every v2 artifact pins its actual code bytes (all five scripts incl. the
   clock producer contract), every raw input with verified SHA-256 (inputs checked against the source
   manifests, mismatch = abort), the git head with an explicit dirty-worktree note, and its output
   hash in `derivation-record.json`. The run's old `p2-stage-manifest.txt` pins older staged bytes
   (`scenario_matrix_core.py=b33b015b…`, `test_scenario_matrix.py=2183b847…`) and does not list
   `package1_phone_burst_telemetry.py`: it is retained immutably as HISTORY, and the authority is the
   run's own self-recorded `source_sha256` in `p2-pd1/lifecycle.json`
   (`scenario_matrix.py=f8284c98…`, `core=527de610…`, `chrome_validate.py=78d53ca9…`) — no claim is
   made that the old manifest pinned the analyzers or the B5 analyzer.

## v2 artifacts (all from the immutable raw inputs; old derived JSONs never rewritten)

| artifact | key result (reproduces the reviewer's hand-computed values exactly) |
|---|---|
| `phone-burst-association-v2.json` | P2 run `p2pd1-portdisconnect-20261028T0651Z`: verdict PASS from the guarded source data; 6T gap **+2322 ms** interval **[+629, +4015]**, 10Pro **+2406 ms** **[+1482, +3330]** vs own exit `1793170763088`; conservative check holds (whole interval after own exit, below the 30 s bound); stop margins 42,851 / 48,166 ms |
| `tab-close-phone-bound-v2.json` | old `run-tab-close-2` recomputed with the corrected subtraction: 6T **+2304 ms** **[819, 3789]**, 10Pro **+2310 ms** **[1388, 3232]**; the old batch's 6T port-disconnect capture stays invalid/diagnostic (own CONNECT rotated out — recorded, not reconstituted) |
| `connected-sample-brackets-v2.json` | corrected sample positions **9371, 9473, 9584, 9686, 9944, 9951 ms** (origin `1793170753120`, 2 agreeing anchors, spread 0); the two send samples sit after their sent frames (the first still inside the sent→message interval); raw `BLOCKED criterion_conflict` scenario stays byte-identical |
| `w02-adjudication.json` | W-02 ONLY adjudication: exact owner-approved text (owner record sha `89d25538…23fd4`, operator-captured 2026-10-28T04:16:36Z, not a GitHub approval) applied to the six-sample census — zero host windows of all four kinds and zero host endpoints in all four families per sample, positive controls (Chrome visible top-level + CDP listener) present, exactly ONE never-visible `ConsoleWindowClass` on the Chrome-created wrapper, no wrapper endpoints, all sessions equal the active console session; the raw BLOCKED record is unchanged; no other row promoted or waived |

Owner decision records are preserved byte-identically (`owner-approval-w02-2026-10-28.json`,
`mac-deferral-2026-10-28.json`; sha-verified against the review-cited records).

## Honest statuses and limits (corrections to earlier derived narratives)

- **M-08 is owner-deferred, NOT PASS.** Owner decision `"Cannot restart Chrome now"`
  (record sha `ace5e31e…86e99`): no Chrome update/restart, no old-version switch, no alternate loader,
  approved fixture `e7c716c5…eb91c` + Bluetooth grant untouched. The retained attempts show an
  extension-loading/browser-process failure (`Extensions.loadUnpacked` closes the tested Chrome
  sessions) BEFORE any BLE work with zero peers: not a proven universal Chrome defect, not a proven
  `.49` regression, and an update is NOT proven to fix it (a minimal invalid-manifest reproduction is
  invalid input; the valid real driver flow failed with cause unproven). Chrome `.73` was simply the
  latest stable on the Windows PC; `.49` was simply what the Mac had.
- **The first standalone Mac SETUP record was lost to `--overwrite`.** Only its surviving stdout
  details remain: the Mac setup phase is therefore **unmeasured, no BLE**. The gap is recorded
  transparently; the lost bytes are never recreated, never claimed preserved, and `--overwrite` will
  not be used again (new attempts get unique output dirs).
- **"No launch flags anywhere" is not claimed.** The precise records: the driver launches no
  throttling/direct-launch override flags; ST-13 recorded `proc.args` (9 args) with zero override-flag
  findings in both strict runs; the retained launch-denied attempt recorded
  `command_line_not_recorded`; the argv bytes were not retained verbatim.
- **"Click observed" is corrected.** The foreground gate was instrumented as visible+focused owned pid
  confirmed after poll 1; no synthetic input; the physical click itself was not instrumented (owner
  declared readiness, gate re-verified).
- **Not "all package-1 rows done".** The five Windows candidate rows have reviewed evidence
  (W-11/W-12 recommended PASS by the strict review; W-02/W-07/W-08 conditionally recommended by the P2
  review pending fix verification); M-08 is owner-deferred. Remaining for package completion: W-10,
  A-01, M-03/M-04/M-11. After verification and ONLY-five-Windows-rows promotion the canonical matrix
  would stand at 23 PASS / 6 BLOCKED / 3 waived — that is an expectation for the parent phase, not a
  changed row here. Overall decision: NO-GO; OTP-24 stays unauthorised.

## Verification performed (this phase)

- Targeted first, then full portable suites: `python3 -m unittest discover` = **449 OK, 5 skipped**
  (Windows/mac smoke classes skip off-platform); scoped pair = 174 OK.
- Reviewer semantic probes (`missing_one_phone_run_id`, `missing_stop_pc`,
  `false_final_after_service_stopped`) now FAIL with named problems.
- `python3 -m py_compile` on the edited/derived files: PASS. `git diff --check`: PASS.
- Derivation-time assertions: input manifests verified (P2 34/34, connected 181/181 before any derive),
  all reviewer hand-values reproduced, conservative interval checks held, W-02 census clean.
- Gradle/Detekt/Spotless/Lint, the two raw CI Log guards and the matrix render/check are recorded in
  the phase result file; matrix `check` passes with statuses unchanged (W-02 expected/source narrow
  amendment only; decision NO-GO).

## Files

- `phone-burst-association-v2.json`, `tab-close-phone-bound-v2.json`,
  `connected-sample-brackets-v2.json`, `w02-adjudication.json` (+ their pinned provenance blocks),
  `derivation-record.json` (inputs/outputs/hashes/conservative outcomes),
  `owner-approval-w02-2026-10-28.json`, `mac-deferral-2026-10-28.json` (byte-preserved owner records),
  `reports/` (byte-identical review copies + SOURCE-SCOPES.md), `provenance/` (opencode-db model
  metadata), `SHA256SUMS`, `.gitattributes` (`* -text -whitespace`).