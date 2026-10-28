# OTP-23 package1 B5 v4 derivation (2026-10-28) — FINDING (exact-branch predicate + controlled numeric range)

Scope: ONLY the one remaining numeric-predicate defect from
`/tmp/opencode/otp23-package1-p2-gpt-v3-final.md` (`217e6f2f…40d4d2`, cited and re-verified inside the
derivation record). Same unchanged raws; the v3 (9/9) and v2 (15/15) candidates stay preserved
immutable, superseded by the pointer chain; nothing committed/pushed; no hardware/phones/Chrome/Mac
action; M-08 stays owner-deferred; canonical rows unchanged pending the parent's next focused check.

## Closure

1. **Exact branch contract for `_num()`** (`scenario_matrix_core.py`), implemented literally as
   specified — booleans rejected first, non-boolean ints accepted WITHOUT any float conversion
   (every Python int is finite; `math.isfinite(10**1000)` used to raise OverflowError), floats
   accepted only when `math.isfinite(value)`:
   ```python
   if isinstance(value, bool):
       return False
   if isinstance(value, int):
       return True
   return isinstance(value, float) and math.isfinite(value)
   ```
   New regressions (existing bool/non-finite tests stay green): `core._num(10**1000)` and
   `core._num(-(10**1000))` return True without raising
   (`test_num_accepts_huge_ints_without_overflow`).
2. **Giant inputs never escape the validator.** The association validator is wrapped: a value the
   predicate accepts (a huge int is reachable through `json.loads`) that overflows LATER arithmetic
   (the fixture-stop guard divides the stop margin by 1000 to format its problem) becomes a
   controlled FAIL with a `numeric_range` problem — never an uncaught exception, NO new acceptance
   gate. Verified probes: giant-positive stop epochs complete under the UNCHANGED criteria (the
   stop sits far past the declared window → PASS); giant-negative PC stop → controlled
   `numeric_range` FAIL; both-negative stops trip the existing phone-side named checks. Ordinary
   finite data takes the same code path as before
   (`test_giant_stop_epochs_never_escape_the_validator`).

## Values (identical to the verified v2/v3 numbers)

P2 association PASS: 6T **+2322 ms** `[+629, +4015]`, 10Pro **+2406 ms** `[+1482, +3330]`;
old `run-tab-close-2`: **+2304 ms** `[819, 3789]` / **+2310 ms** `[1388, 3232]`; sample positions
**9371, 9473, 9584, 9686, 9944, 9951 ms**; W-02 census unchanged and clean; the chrome_flags_note
wording (raw pins only the legacy env; flag absence rests on source review + actual argv) retained
verbatim.

## Files

`phone-burst-association-v4.json`, `tab-close-phone-bound-v4.json`,
`connected-sample-brackets-v4.json`, `w02-adjudication.json` (each `supersedes` = the two-entry
pointer chain to the preserved v3 and v2 artifacts, `also_supersedes` = the historical v1 wrong-sign
JSON), `derivation-record.json` (7-entry code list, self-pin first with in-process drift guard;
fix-verification citation `217e6f2f…` + closures; owner records), byte-identical
`owner-approval-w02-2026-10-28.json` / `mac-deferral-2026-10-28.json`, this `FINDING.md`,
`.gitattributes`, `SHA256SUMS`. Review copies and model metadata stay pinned in the v2 folder.

## Gates

Focused `test_scenario_matrix` → **136 OK, 5 skipped**; full portable discover → **455 OK, 5
skipped** (was 453: +2); `py_compile` PASS; `git diff --check` PASS; the three oversized-integer
probes behave as above; input manifests verified at derive time (P2 34/34, connected 181/181); the
4 reviewer hand-value asserts held; v3/v2 chain shas verified against the preserved folders; the
record's self-pin re-verified against the live file. Gradle/Rust/Node: no change this increment
(Android-side gates last verified green at the same tree state).

## Boundary

W-07/W-08 publishability still awaits ONE focused parent verification of this predicate branch +
numeric-range boundary (per the v3-final report's own condition); W-02 remains a PASS recommendation
through its separate owner-scoped adjudication, subject to normal integration, with the authoritative
owner amendment also recorded in `docs/rfcs/2026-08-21-otp-external-devices.md` during integration
(left untouched here). Canonical 18 PASS / 11 BLOCKED / 3 waived NOT changed; overall NO-GO; OTP-24
unauthorized; M-08 owner-deferred (`"Cannot restart Chrome now"`, no Mac action).