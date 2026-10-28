# OTP-23 package 1 P2/B5 v2 independent fix verification

## Review identity and scope

- Independent read-only verification of the B5-1 through B5-4 repairs and the candidate W-02/W-07/W-08 recommendations. No hardware, phones, Chrome, Mac, adb/homepc, GitHub, commits, canonical-row edits, or subagents were used.
- OpenCode session `ses_e7e49d2cbffepV653zHccm8N62`; OpenCode `1.19.4`; session agent `build`; provider `openai`; model `gpt-5.6-sol`; variant `high`. Safe metadata captured read-only from `/home/dev/.local/share/opencode/opencode.db` at `2026-10-28T09:34:03Z`: 44 assistant records at capture, 43 tagged `build` and one automatic `compaction`, all using `(openai, gpt-5.6-sol, high)`. No message text, API headers, credentials, cookies, auth state, or error bodies were read or recorded.
- Repository HEAD remained `8f365fce0d31f975610c235085a95b9136ac25e2`; the pre-existing dirty worktree was preserved. The only authored deliverable is this report; portable tests used temporary/ignored output.
- This review does not change the canonical matrix, authorize OTP-24, broaden the W-02 owner clarification, or request action on owner-deferred M-08.

## Verdict

**NOT YET APPROVED. B5-1 and B5-3 are closed, and the actual measured data reproduce every expected corrected value. B5-2 and B5-4 each retain one narrow source-only defect. No hardware rerun is needed, but W-02/W-07/W-08 must remain BLOCKED until a superseding checksummed derivation closes both defects and is independently rechecked.**

| repair | result | independent finding |
|---|---|---|
| B5-1, phone-minus-PC conversion | **PASS** | Both analyzers subtract the measured offset. Positive and negative nonzero regressions prove the direction. Real-data recomputation gives the expected P2 and tab-close values. |
| B5-2, fail-closed completeness | **FAIL, narrow** | Missing/empty/mismatched run IDs, false/missing `final_after_service_stopped`, and missing/string stop epochs fail closed. However, `_num()` accepts IEEE `NaN` and `Infinity`, so both supposedly finite fixture-stop epochs can be non-finite and the validator still returns PASS. |
| B5-3, absolute trace origin | **PASS** | Sample positions are derived from the trace's absolute disconnect-requested anchors, not launch/spawn initiation. Both actual anchors agree exactly; corrected positions and brackets match the raw trace. |
| B5-4, exact provenance | **FAIL, narrow** | All six recorded code pins, 13 unique input pins, four output pins, owner records, and package manifests match current bytes. The code that actually orchestrates and constructs all four outputs, `package1_b5_v2_derive.py`, is named but omitted from every `code` pin list. |

The two defects are publishability/false-pass risks, not contradictions in the actual run. The actual stop epochs are finite integers, and the reviewed current derivation-script SHA-256 is `13719eed51f52e31d281bb31aa6b5e394d94c307b6d8ee6d8ffd9924b5a2e779`.

## Blocking findings

### B5-2: the finite-stop requirement still fails open

`scenario_matrix_core.py:760-762` defines `_num()` as `isinstance(value, (int, float)) and not isinstance(value, bool)`. Python floats `NaN`, `+Infinity`, and `-Infinity` satisfy that predicate. The stop guard at `scenario_matrix_core.py:945-969` says both epochs must be finite, but relies on `_num()` and therefore does not enforce that statement.

Direct semantic probes started from the passing two-phone fixture used by `PhoneBurstAssociation` and replaced one stop field at a time. All malformed variants incorrectly passed with the affected phone still associated and no problems:

```text
epoch_phone_ms nan PASS True []
epoch_phone_ms inf PASS True []
epoch_pc_ms nan PASS True []
epoch_pc_ms inf PASS True []
```

The regression at `test_scenario_matrix.py:1873-1890` says "finite" but covers only `None` and strings. Python's standard `json.loads` also accepts the non-standard `NaN`/`Infinity` tokens by default, so this is reachable at the parser boundary rather than merely an impossible internal object.

Minimum fix:

1. Make the shared numeric predicate reject non-finite floats while preserving integer epochs and excluding booleans. A safe shape is: integers are accepted; floats require `math.isfinite(value)`.
2. Add fail-closed tests for `NaN`, `+Infinity`, and `-Infinity` in each of `epoch_phone_ms` and `epoch_pc_ms`. Applying the finite predicate consistently to host anchors, clock values, and event epochs is preferable because those fields use the same helper.
3. Regenerate a new checksummed candidate from the unchanged raw inputs. The real-data values should remain identical because every actual numeric field is finite.

### B5-4: the derivation program is not pinned by its own evidence

`package1_b5_v2_derive.py:35-48` defines `REVIEWED_ANALYZERS`, and `:97-102` turns only those six entries into the provenance `code` list. The script itself is absent. `derivation-record.json:3` names the script path but provides no digest for it; `:4-35` contains only the six imported helper/producer/observer pins. The same incomplete list is copied into each output artifact.

Independent provenance audit result:

```text
code_pins 6 all_match True
derivation_script_declared native-bridge/feasibility/scripts/package1_b5_v2_derive.py
derivation_script_sha256 13719eed51f52e31d281bb31aa6b5e394d94c307b6d8ee6d8ffd9924b5a2e779
derivation_script_present_in_code_pins False
unique_input_pins 13 all_match True
inputs_missing_from_manifest []
four_recorded_output_pins all_match True
```

This omitted script is material code: it parses stop markers, assembles the phone documents, defines the expected hand values, applies conservative assertions, chooses the raw sample fields, computes the W-02 census, copies owner records, and serializes every output. A hash in the implementer's `/tmp` result report is useful corroboration but is not the promised self-contained v2 code provenance.

Minimum fix:

1. Include `native-bridge/feasibility/scripts/package1_b5_v2_derive.py` with its measured SHA-256 and an explicit derivation/orchestration role in the `code` list embedded in the derivation record and every output.
2. Regenerate a new checksummed candidate after the finite-number fix, preserving all raw captures and the old wrong-sign derived JSONs unchanged. Because this v2 package has already been checksummed and described as immutable, a superseding v3 package is the safest preservation approach; if project policy treats v2 as an unfrozen candidate, document any replacement explicitly.
3. Re-run manifest, code/input/output-pin, portable-test, and finite-value semantic checks before promotion.

## Verified repairs and values

### B5-1 clock conversion

- `_telemetry_bursts()` at `scenario_matrix_core.py:992-1045` computes `epoch_pc = epoch_phone - offset_phone_minus_pc_ms_median`.
- `row_epoch_ms()` at `package1_phone_bound.py:44-54` performs the same subtraction.
- `test_scenario_matrix.py:1745-1765` hand-checks both signs: `+406` produces gap `1544` and interval `[644,2444]`; `-406` produces gap `2356` and interval `[1456,3256]`. An adding implementation would fail both expectations.
- Independent raw-data recomputation reproduced P2 gaps `2322/2406 ms` and intervals `[629,4015]` / `[1482,3330]`.
- Independent old `run-tab-close-2` recomputation reproduced gaps `2304/2310 ms` and intervals `[819,3789]` / `[1388,3232]`.
- The whole actual uncertainty interval lies after own exit and below the predeclared 30-second bound. This is an assertion about these measured inputs, not a new generic rule forbidding every pre-exit release under uncertainty.

### B5-2 guards that are closed

- Every present phone document is checked for a same non-empty run ID; missing/empty and differing IDs are named failures.
- The declared run binding must be non-empty and match the shared document ID.
- `final_after_service_stopped` must be literally `True`.
- Missing and nonnumeric phone-side or PC-side stop epochs fail closed.
- The actual P2 documents share `p2pd1-portdisconnect-20261028T0651Z`, have `final_after_service_stopped=true`, and carry finite integer stop epochs. Their actual association remains sound despite the generic non-finite false-pass defect.

### B5-3 trace-origin correction

- `trace_origin_calibration()` at `scenario_matrix_core.py:670-693` computes each origin as `epoch_ms - at_ms` from trace-internal absolute disconnect-requested anchors.
- Both actual anchors are trace `9958 ms`, epoch `1793170763078`, yielding origin `1793170753120`; spread is `0`.
- Independent recomputation produced corrected sample positions `9371, 9473, 9584, 9686, 9944, 9951 ms`.
- The first send-in-flight sample is after `sent` at `9472 ms`; the second is after `sent` at `9685 ms` and before `message` at `9940 ms`. The linked-idle sample follows scan restart at `9370 ms`, and the last sample precedes disconnect requests at `9958 ms`.
- The launch-based offsets remain preserved and clearly labelled estimates; they are not used for the corrected brackets when an absolute anchor is available.

### Evidence integrity and preservation

- `package1-b5-v2-20261028/SHA256SUMS`: 15/15 OK; manifest SHA-256 `7b2e924f6fcdbedbd7ae4b45dc4572201c49485cbb7af9f0130ba3d10d85a44f`.
- `package1-p2-20261028/SHA256SUMS`: 34/34 OK.
- `package1-connected-2026-10-28/SHA256SUMS`: 181/181 OK.
- The principal immutable raw/historical hashes remain exactly those independently recorded in the prior review, including raw P2 scenario `967f4c04...`, P2 trace `22e0aecd...`, old wrong-sign association `615c4558...`, old tab-close scenario `ac8a2bad...`, old tab-close trace `53ec2e47...`, both old retry captures `5b81ce49...` / `d596ddf3...`, and old derived bound `147a5260...`.
- Every recorded v2 code pin currently matches; all 13 unique provenance input paths match their recorded hashes and none is missing from its source manifest; all four output hashes match `derivation-record.json`.
- The copied owner approval is byte-identical to `/tmp/opencode/otp23-package1-owner-approval.json` at SHA-256 `89d255383b21272590f7c3b3cc3370e7b8f1e688b2445a65dba022d529f23fd4`. Approved text, answer, and scope are exact.
- The Mac deferral copy is byte-identical at SHA-256 `ace5e31e99c25ee21b4d634208ef006af02624121624952e0c40238505086e99`; M-08 remains deferred and unmeasured.

## Row recommendations

| row | final recommendation now | verified basis | minimum remaining condition |
|---|---|---|---|
| W-02 | **WITHHOLD PASS; keep BLOCKED** | Six raw connected samples independently census zero host windows in all four kinds, zero host endpoints in all four families, exactly one never-visible `ConsoleWindowClass` wrapper window, zero wrapper endpoints, present Chrome window/CDP controls, and console-session equality. Raw status remains `BLOCKED/criterion_conflict`. Exact owner text occurs only in W-02. Corrected brackets are valid. | Close B5-4 by pinning the derivation script and regenerate/recheck the adjudication. B5-2 does not change the W-02 physical census. |
| W-07 | **WITHHOLD PASS; keep BLOCKED** | P2 trace independently parses as `stdin_closed`, distinct releases `2/3`, join `2/2` within 10 ms, own exit 0 with drained stdout, and no shutdown frames. Corrected actual phone intervals are wholly after exit and below 30 seconds. | Close B5-2's non-finite false-pass and B5-4's omitted derivation-script pin, regenerate, and recheck. No physical rerun is needed. |
| W-08 | **WITHHOLD PASS; keep BLOCKED** | P2 and independent `run-tab-close-2` traces both show the clean no-shutdown stdin-EOF lifecycle with distinct releases, join `2/2`, and own exit 0; corrected old phone bounds are decisive. | Same B5-2/B5-4 source-only closure as W-07. No extra tab-close or physical repeat is material. |

The W-02 artifact's `chrome_flags_note` also overstates that the raw detail "records both": the raw scenario records `VELES_OTP23_DETACHED_CLOSE` as unset, while absence of a direct-native-host flag/policy override comes from the pinned driver/source review. This does not overturn the census, but its wording should be corrected when the artifact is regenerated.

## Canonical and lifecycle boundary

- Canonical state remains exactly 18 PASS, 11 BLOCKED, and 3 waived/not-run rows. W-02, W-07, W-08, and M-08 remain BLOCKED.
- The exact owner-approved clarification appears only in W-02. No other row is promoted, waived, or re-scoped.
- M-08 remains owner-deferred under the exact answer `"Cannot restart Chrome now"`; no Mac action or approval is inferred. The lost unmeasured first setup record remains a documented preservation gap and was not recreated.
- Even after the two source-only fixes and a successful rereview, the expected five-Windows-row integration would leave the project NO-GO with M-03, M-04, M-08, M-11, W-10, and A-01 open. Nothing in this verification authorizes OTP-24.

## Verification performed

- Scoped portable suite: `python3 -m unittest test_scenario_matrix test_macos_poc` -> 174 tests OK, 5 skipped.
- Full portable suite: `python3 -m unittest discover -s . -p 'test*.py'` -> 449 tests OK, 5 skipped.
- `python3 -m py_compile` on the four repaired/new scoped Python files: PASS.
- `git diff --check`: PASS.
- Three package checksum manifests: v2 15/15, P2 34/34, connected 181/181, all OK.
- Independent real-data arithmetic, trace-origin, W-02 census, lifecycle parsing, owner-record equality, canonical-count/scope, and provenance-pin probes: results recorded above.
- Direct non-finite semantic probes: four false PASS results, establishing the remaining B5-2 blocker independently of the green suite.

## Final boundary

The correct next step is one small finite-number guard, comprehensive non-finite regressions, inclusion of the derivation script in the evidence code pins, and a new checksummed derivation from the same immutable raw inputs. Then perform one focused independent rereview. Do not rerun hardware, rewrite historical evidence, edit canonical statuses, act on M-08, or infer package-wide GO/OTP-24 eligibility before that rereview.
