# OTP-23 package 1 P2/B5 v3 focused final recheck

## Scope and identity

- Focused recheck only of the two remaining minimums and wording correction identified in `/tmp/opencode/otp23-package1-p2-gpt-fix-verification.md`; no broad rereview, hardware, phone, Chrome, Mac, source/evidence/canonical edits, GitHub, or subagents.
- OpenCode session `ses_e7e49d2cbffepV653zHccm8N62`; OpenCode `1.19.4`; session agent `build`; provider `openai`; model `gpt-5.6-sol`; variant `high`. Safe read-only metadata captured from `/home/dev/.local/share/opencode/opencode.db` at `2026-10-28T09:48:36Z`: 59 assistant records at capture, 58 tagged `build` and one automatic `compaction`, all `(openai, gpt-5.6-sol, high)`. No transcript text, credentials, API headers, cookies, auth state, or error bodies were read.
- Repository HEAD remained `8f365fce0d31f975610c235085a95b9136ac25e2`; pre-existing worktree changes were preserved. This report is the only authored deliverable; tests used temporary/ignored output.

## Verdict

**PARTIAL CLOSURE. B5-4 and the W-02 wording correction are fully closed. B5-2 now rejects every tested `NaN`/`+Infinity`/`-Infinity` stop, clock, event, and host-anchor value, including the four probes from my prior review. However, the exact stated finite-number contract, “integers accepted; booleans excluded; floats require `math.isfinite`,” is not fully implemented: `_num(10**1000)` raises `OverflowError` because `math.isfinite` is applied to integers too.**

This is one narrowly scoped remaining code defect. It does not contradict the actual v3 results, whose integer epochs are ordinary finite values, and it does not affect W-02. It does prevent an unconditional B5-2 closure and therefore prevents final W-07/W-08 PASS recommendations under the prior stated condition.

| row | final recommendation from this recheck |
|---|---|
| W-02 | **PASS recommended by separate adjudication.** B5-4 is closed; corrected trace brackets, owner-approved W-02-only scope, clean six-sample census, and corrected flags wording verify. This is a recommendation, not an automatic canonical status change. |
| W-07 | **WITHHOLD PASS; keep canonical BLOCKED.** Actual evidence remains physically sufficient, but the exact B5-2 finite-number minimum is not fully closed because oversized integers can raise instead of producing a controlled result. |
| W-08 | **WITHHOLD PASS; keep canonical BLOCKED.** The corrected tab-close/P2 evidence remains sufficient, but the same previously declared B5-2 closure condition applies. |

No recommendation authorizes OTP-24 or changes project-wide NO-GO.

## B5-2 focused result

`scenario_matrix_core.py:761-765` now excludes booleans and rejects non-finite floats:

```python
return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
```

Verified behavior:

- Ordinary positive/negative integer epochs and finite floats return `True`.
- `True` and `False` return `False`.
- `NaN`, `+Infinity`, and `-Infinity` return `False`.
- All six combinations of the three non-finite floats in `epoch_phone_ms` and `epoch_pc_ms` return validator FAIL and set the affected phone `associated=false`.
- Both host-anchor fields, both clock fields, and connected/disconnected event epochs independently reject all three non-finite floats in direct semantic probes.
- My prior four false-PASS probes now fail with the named `fixture stop marker missing/unusable` problem:

```text
epoch_phone_ms nan FAIL False
epoch_phone_ms inf FAIL False
epoch_pc_ms nan FAIL False
epoch_pc_ms inf FAIL False
```

Remaining exact-contract failure:

```text
_num(10**1000) -> OverflowError: int too large to convert to float
```

Python JSON accepts arbitrary-size integers, and every Python integer is mathematically finite. A huge input should not escape the fail-closed validator as an uncaught exception. This also differs from the prior requested safe shape: accept non-boolean integers directly; apply `math.isfinite` only to floats.

Minimum remaining repair, with no hardware or broad harness work:

1. Implement the branch explicitly, for example: non-boolean `int` -> `True`; `float` -> `math.isfinite(value)`; everything else -> `False`.
2. Add one regression that a very large positive and negative integer is accepted without raising, while existing bool/non-finite-float tests remain green.
3. Regenerate a superseding checksummed derivation because the core code hash changes, then perform one focused hash/value check. No raw, v2, v3, hardware, or lifecycle rerun is needed.

## B5-4 focused result

**Closed.** The derivation program measures its own current bytes at runtime, places itself at `code[0]` with an explicit derivation/orchestration role, checks for in-process drift, and embeds the same seven-entry code list in the derivation record and every v3 output. There is no hardcoded self-hash cycle.

Independent audit:

```text
record_code_count 7, all live-file hashes match
code[0] package1_b5_v2_derive.py = eaaf6b33852f8a05cf32808c0a03411cb27c4765c46a91206a860c76b558c064
derivation declaration matches code[0]
every output has the exact same seven-entry code list with self-pin first
13 unique input pins all match; inputs_missing_from_manifest=[]
all four output pins match
prior fix-verification citation hash matches
```

Exact seven code pins:

| code | SHA-256 |
|---|---|
| `package1_b5_v2_derive.py` | `eaaf6b33852f8a05cf32808c0a03411cb27c4765c46a91206a860c76b558c064` |
| `scenario_matrix_core.py` | `8fb2d60814e6cef36295b506675bf6f00c798e02e0754749d36469320044689c` |
| `package1_phone_burst_telemetry.py` | `c6fcfd6eab1e90e9861232fa6d06ef2d8166e39b2b613e7e37fd77e12296663d` |
| `package1_phone_bound.py` | `40d8f8242385671eb3122c812cffc4ad33d135b001ca7b0a5adfd4b1c3ecf48f` |
| `scenario_matrix.py` | `f8284c98b9dc200bf8ec83e09b4d040643a725e4ca637d5854b09494fc31ffc1` |
| `pc_phone_clock_probe.py` | `06f0de1b64775615f30abcf2582d04113601632da2006aad98b317a01637b629` |
| `chrome_validate.py` | `78d53ca98c839e78bccf404beab5fb978f421f485f7945e5578d09a8b71a62f6` |

The focused test source is `test_scenario_matrix.py` SHA-256 `2bf3e0df00db19fa77ba6950d56dc39862ac53cd7f649a40aef94ece76338ff3`.

## V3 evidence

All v3 outputs reproduce the previously hand-checked values:

- P2 gaps `2322/2406 ms`; intervals `[629,4015]` / `[1482,3330]`.
- Tab-close gaps `2304/2310 ms`; intervals `[819,3789]` / `[1388,3232]`.
- Corrected sample positions `9371/9473/9584/9686/9944/9951 ms`; trace origin `1793170753120`; two-anchor spread `0`.
- W-02 census remains six usable samples with no host windows/endpoints, exactly one never-visible wrapper `ConsoleWindowClass` per sample, no wrapper endpoints, positive controls, and console-session equality.

The conservative lower-after-exit and upper-below-30-second assertions are applied to these actual measured intervals in the derivation. The generic association logic still permits the pre-existing `-uncertainty <= gap` comparison; v3 does not create a generic policy banning every pre-exit point estimate.

The `chrome_flags_note` overclaim is corrected: it attributes only the legacy environment fields to the raw scenario and explicitly attributes override-flag absence to the separate pinned source/argv review. It also states that argv bytes were not retained verbatim.

Exact v3 package hashes:

| file | SHA-256 |
|---|---|
| `SHA256SUMS` | `3bf5a49389a79039bb46a0cb76d3356f82fe4bfe3f2def26493e442a897e4841` |
| `phone-burst-association-v3.json` | `2f74f34db15f21bce4ccfbb9a4846cddbd2c841181875fdb42a63031aa641d1a` |
| `tab-close-phone-bound-v3.json` | `0a987c6f6f01a53f205e50eda853693382312735a054a93e6ea95393ea7b16e4` |
| `connected-sample-brackets-v3.json` | `81e3fe5e5262c71baea6c0d627b2c0c781475bf7969481a9b42e1eac9a24a7ac` |
| `w02-adjudication.json` | `946c74bc56d7c2a5986fb461c435703beb1e6d2a67507f7f15e437240c930609` |
| `derivation-record.json` | `c2589da389272f8305a68f75e7e9e1507aa872012e915abf57219bab1a85e44a` |
| `FINDING.md` | `5acba0c8b2c6e7de3b0b433fa3a6df23eb3f79da0da9728d30c09ca11071555a` |
| `owner-approval-w02-2026-10-28.json` | `89d255383b21272590f7c3b3cc3370e7b8f1e688b2445a65dba022d529f23fd4` |
| `mac-deferral-2026-10-28.json` | `ace5e31e99c25ee21b4d634208ef006af02624121624952e0c40238505086e99` |

Manifest verification:

- v3: 9/9 OK.
- Preserved v2: 15/15 OK; manifest SHA-256 `7b2e924f6fcdbedbd7ae4b45dc4572201c49485cbb7af9f0130ba3d10d85a44f`.
- Immutable P2 raw package: 34/34 OK; manifest SHA-256 `addf32b2f68474fc65447ddadf1ae38b86dbd8bf24b05f1b740aa177d529697d`.
- Connected package: 181/181 OK; active manifest SHA-256 `218da0f6a13daf83336981e297c0fbd5bbed4330023e33daabed01a14986a643`.
- Every v3 `supersedes` pointer matches the preserved v2 artifact hash: `b0840066e435a1bba1c27ece046b665b98410e314a2ffad6421d38986771d243`, `3deac5e26119eb25e42ba3319a1aa628030b233292f27d97e69fce081cd85c60`, `cea77aa22afb6f96e7cda8c22fe1e2161380e178e54b7a62aa43457d1af672e2`, and `4bbdf5124235024619835573ef099c7a881b918ddd8fa3aa3ff10acc16426a1a`.

## Checks

- Focused `PhoneBurstAssociation`: 27 tests OK.
- `python3 -m unittest test_scenario_matrix`: 134 tests OK, 5 skipped.
- Full portable discovery: 453 tests OK, 5 skipped.
- Scoped `py_compile`: PASS.
- `git diff --check`: PASS.
- Exact prior non-finite probes and expanded stop/clock/event/anchor probes: all expected FAIL outcomes observed.
- Oversized-integer semantic probe: reproducible `OverflowError`, the sole remaining blocker identified by this focused recheck.

## Publishability boundary

W-02 is publishable as a PASS recommendation through its separate owner-scoped adjudication, subject to the normal integration step and the addendum's requirement to record the authoritative amendment in `docs/rfcs/2026-08-21-otp-external-devices.md`. W-07 and W-08 become publishable without new measurement after the one predicate branch, one oversized-integer regression, regenerated checksums/provenance, and one focused verification.

Canonical state remains 18 PASS / 11 BLOCKED / 3 waived-not-run; this report performs no automatic status update. The previously accepted Windows strict findings are unaffected. M-08 remains owner-DEFERRED under `"Cannot restart Chrome now"`; no Mac action is authorized. Overall state remains NO-GO and OTP-24 remains unauthorized.
