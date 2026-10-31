# OTP-23 package1 B5 v3 derivation (2026-10-28) — FINDING (two narrow closures over the preserved v2)

Scope: ONLY the two B5 defects from
`/tmp/opencode/otp23-package1-p2-gpt-fix-verification.md` (`ee342efa…3ff0bd`, verified inside the
derivation record) plus its wording note. Same unchanged raw inputs, same independently verified
values; the v2 candidate folder (`../package1-b5-v2-20261028/`, 15/15 `SHA256SUMS`) is preserved
immutable and superseded by pointer chain; NOTHING was committed/pushed; no hardware/phones/Chrome/Mac
action; M-08 stays owner-deferred (`"Cannot restart Chrome now"`). The W-02/W-07/W-08 canonical rows
stay BLOCKED — NOTHING here claims publishability until one focused independent recheck.

## Closures

1. **B5-2 (finite guard).** `scenario_matrix_core._num()` now requires `math.isfinite` for floats
   (ints accepted, bools excluded): `import math` added; NaN/+Inf/-Inf are rejected at the parser
   boundary (`json.loads` accepts those tokens). New regressions in `test_scenario_matrix.py`:
   the helper semantics, NaN/+Inf/-Inf in EACH of `epoch_phone_ms` and `epoch_pc_ms` (6 subtests),
   the same predicate through the clock block (offset/uncertainty × the three non-finites), a
   non-finite event epoch, and non-finite host anchors. The four independent false-PASS probes from
   the fix verification were re-run against the real validator: all four now FAIL with the named
   stop-marker problem. No new acceptance gate was introduced — this is the same guard, honest.
2. **B5-4 (derivation-program self-pin).** `package1_b5_v2_derive.py` (orchestration kept in place;
   it now emits v3 outputs) measures its SHA-256 at runtime (`sha256(__file__)`) and pins itself as
   `code[0]` with the explicit "derivation program (orchestration)" role in the derivation record AND
   in every output's `provenance.code` list — never a hardcoded self-hash (verified: recorded sha
   equals the actual file bytes, self-pin drift aborts).
3. **chrome_flags_note wording fix** (in `w02-adjudication.json` only): the raw scenario record pins
   ONLY the legacy environment (`VELES_OTP23_DETACHED_CLOSE` unset in the driver env and
   `trace_env_passed_to_chrome`); the absence of a direct-native-host flag/policy override is NOT
   claimed from that field — it rests on the separate pinned driver/source review plus the recorded
   actual Chrome argv of the strict runs (ST-13 `proc.args`, 9 args, zero override-flag findings;
   argv bytes not retained verbatim).

## Values (identical to the verified v2 numbers)

- P2 association: 6T **+2322 ms** `[+629, +4015]`, 10Pro **+2406 ms** `[+1482, +3330]` vs own exit
  `1793170763088`; PASS from guarded raw data; conservative interval check held.
- Old `run-tab-close-2`: 6T **+2304 ms** `[819, 3789]`, 10Pro **+2310 ms** `[1388, 3232]`.
- Corrected sample positions: **9371, 9473, 9584, 9686, 9944, 9951 ms** (origin `1793170753120`).
- W-02 census: unchanged and clean (6 samples; zero host windows/endpoints; exactly one never-visible
  `ConsoleWindowClass` wrapper window; positive controls present; session equality computed).

## Files in this folder

`phone-burst-association-v3.json`, `tab-close-phone-bound-v3.json`,
`connected-sample-brackets-v3.json`, `w02-adjudication.json` (each with the pointer chain
`supersedes` → v2 artifact / `also_supersedes` → the historical v1 derived JSON, raws unchanged),
`derivation-record.json` (self-pinned orchestration program, all code/input/output hashes, fix
verification citation + closures, owner-record copies), byte-identical owner records
(`owner-approval-w02-2026-10-28.json` `89d25538…`, `mac-deferral-2026-10-28.json` `ace5e31e…`),
`FINDING.md` (this file), `.gitattributes` (`* -text -whitespace`), `SHA256SUMS` (this folder).

Review-report copies and session model metadata are NOT duplicated here — they are pinned in the
v2 folder (`reports/`, `provenance/`), remain authoritative for the same implementer/reviewer set,
and are referenced by the result file.

## Gates for this increment

Focused `python3 -m unittest test_scenario_matrix` → 134 OK, 5 skipped; full portable discover →
**453 tests OK, 5 skipped**; `py_compile` PASS; `git diff --check` PASS; the four fix-verification
false-PASS probes now FAIL; input manifests verified inside the derivation (P2 34/34, connected
181/181); all four reviewer hand-value asserts held; v2 pointer shas resolved and match the v2
manifest; record self-pin verified against the running file. Gradle/Android gates were re-run after
the previous documentation edits (`spotlessCheck detekt` exit 0); the only later code change is this
Python increment (no Android/Rust/Node change).

## Boundary

Not publishable until the parent's FOCUSED recheck of exactly this v3 increment (do not redo the old
review). The later canonical owner amendment integration must be recorded in the AUTHORITATIVE
`docs/rfcs/2026-08-21-otp-external-devices.md` (not only `docs/otp-23-contract-alignment.md`) — that
documentation is deliberately left for the integration phase after v3 verification. Expected
integration outcome after five-Windows-rows promotion stays 23 PASS / 6 BLOCKED / 3 waived; M-03,
M-04, M-08 (deferred), M-11, W-10, A-01 open; NO-GO overall; OTP-24 not authorized.