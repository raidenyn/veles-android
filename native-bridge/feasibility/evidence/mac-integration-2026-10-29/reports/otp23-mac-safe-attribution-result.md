# OTP-23 Mac M-08 attribution A2 (source-proven causal inequalities): implementation result (2026-10-29)

## 0. Role envelope

- **Implementer:** Claude Code, `claude-opus-5-5`, session `752e1974-0617-470b-a425-dfbda97ac728`.
  Code only, no subagents.
- **Inputs:**
  - review: `/tmp/opencode/otp23-mac-final-data-review-opus.md` §1–3. Fresh independent reviewer, not
    this session and not the parent.
  - parent decision: `/tmp/opencode/otp23-mac-safe-analysis-parent-decision.json`
- **Historical roles, unchanged:**
  - GLM wrote the frozen analyzer and the M-08 code phase.
  - Kimi K3 did the code verification.
  - Opus 7b4ee6ca did the fix micro-verification and the Option-A design.
  - This session implemented A1 (now superseded) and A2.
- **M-11:** the M-11 captures were made by the parent's direct controller running the unchanged
  reviewed `976f61cf` script. That was not an Opus operator and not this session.
- **Not done:**
  - no hardware, Mac, phone, Chrome, TCC, APK, native, signing, personal-Chrome or mount action
  - no M-11 raw, code or matrix edit
  - no commit, push or GitHub, no row promotion, no OTP-24, no waiver
- **Matrix:** stays 23 PASS / 6 BLOCKED / 3 NOT RUN (waived), NO-GO. `acceptance/` and `docs/` are
  unchanged.
- **M-03 / M-04:** remain unmet under the unchanged grant and with no prompt replay. No waiver is
  inferred.

## 1. The premise correction (parent concern confirmed)

**A1 assumed:** `origin ≥ e16 − m16`, i.e. that the 20-byte proof `notify_api` is stamped before the
host logs `out … type=message`.

**Why that's wrong:** the stamp is taken only after `notifyCharacteristicChanged()` returns. The
binder call may already have queued the PDU, so the host can log the single-fragment echo first
(review §1.1). A1 (`69546ee5…`) is therefore **unsound as a reusable analyzer**, and the same
sentence in the frozen analyzer's docstring is wrong. That file is not edited; the review and this
record correct it.

**What A2 uses:**
- **Lower:** `lo = e_first − m4096 − q`, where `e_first` is the first fragment of the multi-fragment
  4096 echo. Main-looper sequencing guarantees it:
  - `nextFragment` sets `awaiting`
  - fragment k+1 can only be produced after `notificationComplete` runs in a later main-looper
    callback
  - so `e_first` < the final fragment < the host's reassembled message (review §1.2)
- **Upper:** `hi = min(e16 − i16 + q, e_first − i4096 + q)` (review §1.3).
- **q = 1 ms**, applied before feasibility.

The 16-byte lower value is recorded only as `bracket16_lower_not_causal_recorded_only`.

## 2. Code and source history (sha256)

| file | sha256 | status |
|---|---|---|
| `scripts/mac_release_attribution.py` | `ce75a8f1253157975b4004199d4db425d9f69ea02b2bb4360a829ed696d0634e` | **A2** (was A1 `69546ee5…`) |
| `scripts/test_mac_release_attribution.py` | `219c78bee389d74d9142905fabd502f4c5ee27859d6da9fd075fed178850f016` | **A2** (was `3151f62e…`) |
| `scripts/mac_release_bound.py` | `a49b3450…` | unchanged, imported for parsers and diagnostics, byte-pinned (mismatch → FAIL) |
| `scripts/test_mac_release_bound.py` | `4b8aa10d…` | unchanged (frozen hull tests not edited) |
| `scripts/scenario_matrix.py` | `f8bbf2d6…` | unchanged |
| `scripts/scenario_matrix_core.py` | `cb512418…` | unchanged |
| `scripts/macos_poc.py` | `e2131ad3…` | unchanged |
| `.github/workflows/otp23-feasibility.yml` | `df1305d4…` | unchanged; already selects `test_mac_release_attribution.py` in the portable block |

**Source history.** Before any edit, A1 was archived in the new
`evidence/mac-release-attribution-source-history-2026-10-29/`
(`SHA256SUMS` `2a550c1cf0add0ea863b216e78c45c7d3acf18f0ee666d729411690ece7a2854`):
- `a1-superseded/mac_release_attribution.py` `69546ee5…` and `a1-superseded/test_mac_release_attribution.py` `3151f62e…`, byte-identical
- `source-history.json`, which maps each old A1 output (`helper_self_sha256 69546ee5…`) to the
  archived bytes (asserted equal), records the old folder's `SHA256SUMS dd05dac4…`, and records the
  current A2 hashes

Nothing was appended to any frozen folder.

v4 pins: HEAD `scenario_matrix.py` is still `f8284c98…` (historical, at `9c3ddc5`; live differs only
by the approved Mac gate). The other six are live == HEAD.

## 3. A2 rules (all fail-closed)

**Credit rule:** `m08_creditable = verdict == PASS && target_teardown_scenario`.

**What fails closed** (verdict FAIL, `m08_creditable=false`):
1. **Frozen analyzer import:** the bytes must be `a49b3450`. Any structural hull problem is
   `fail_closed_structural_hull_problems`.
2. **Preconditions:**
   - exactly 2 distinct phones, clean telemetry, same run and peer
   - only 16 B and 4096 B record sizes
   - exactly 2 handles, each with one 16 B pair and one 4096 B pair

   Otherwise `fail_closed_preconditions`.
3. **Causal premises, checked per phone on its own burst.** Otherwise `fail_closed_causal_premises`.
   - **Notify sequence:** exactly `[20] + [512]*8 + [36]`.
   - **Calls and callbacks:** every `notify_api` has code 0 and a bound `notify_callback` with code
     0.
   - **Counters:** `notify_calls == api_success == callback_success == len(notifies)`; nonzero,
     pending and unbound counts are all 0.
   - **First fragment:** the analyzer's first-fragment event is the first of a multi-fragment echo,
     with at least one LATER fragment. A single 512-byte fragment is not a safe anchor.
   - **Clock stability:** `max − min(epoch_ms − elapsed_realtime_ms)` over the burst's events is
     ≤ 1 ms.
4. **Mappings:** all `O(p,h)` and all bijections are recorded.
   - 0 feasible → `fail_closed_zero_feasible_mappings`.
   - ≥ 2 feasible → `fail_closed_ambiguous_mapping`.
   - Only a unique mapping is used. Phone clocks are never compared.

**Unique mapping:** the four unchanged checks run on it. The verdict is PASS only if all four pass,
with no problems:
- release after the **global** last exchange (the own-link value is recorded only)
- within own exit + 30000 ms
- stop > DIS
- stop ≥ attributed `hi` + exit + 30000 (the hull-`hi` value is recorded alongside)

**Hull = diagnostics only (parent decision).** An old hull PASS is never a fallback for an
ambiguous mapping or unmet preconditions.

**Precision block:** it records the assigned widths, the 250 ms test-instrument target and
`all_within_instrument_target`. It states that this is not an RFC gate, not claimed, and not a
waiver.

## 4. Tests: actual results

**Targeted:** `python3 scripts/test_mac_release_attribution.py` → **Ran 17, OK** (none skipped). It
also passes with `pwd` blocked (Windows simulation), and both files parse as Python 3.9.

**Synthetic fixtures** use realistic complete telemetry:
- connected, mtu and subscribed events
- 20 B proof plus 8×512 + 36 fragments, each with a bound code-0 callback
- `elapsed_realtime_ms`
- all-success counters

**Hand-computed under A2** (handle A: 16B 1000→1050, 4096 1200→1700; handle B: 1060→1110,
1750→2250; global last 2250; exit 2330):

| test | expected result |
|---|---|
| unique | `O(X,A)=[OX−251, OX+26]` (277), `O(X,B)` feasible, `O(Y,A)=[OY+299, OY+86]` empty → unique. Hull FAIL → PASS. Y `DIS=[2274, 2551]` (margin 24); 277 ms > 250 not claimed |
| swapped `--transport` order | same mapping and verdict |
| **hull PASS + ambiguous safe mapping** | both mappings feasible (only the non-causal 16 B lower would separate them). Hull PASS, A2 **FAIL**, not creditable |
| zero feasible | Y fits neither handle while the hull stays non-empty → FAIL |
| **invalid 16 B lower bound would falsely exclude the true mapping** | `e16` stamped 25 ms after `m16`: the A1 lower `OX+24` exceeds the 4096 upper `OX+11`. A2 `O(X,A)=[OX−491, OX+11]`, unique, true mapping, PASS |
| global vs own | `DIS=[2074, 2351]`: after own-last 1700, before global 2250 → FAIL |
| quantization | an unpadded 1 ms miss becomes `[OX+25, OX+26]` |
| premise negatives | single 512 fragment, incomplete 8-fragment echo, 5 ms wall-clock step → all fail closed |
| valid-fixture premises | hold |
| structural (unclean telemetry) | FAIL |
| **preconditions (third handle) with hull PASS** | FAIL, not creditable |
| FAIL scenario | not creditable |
| wrong analyzer sha | FAIL |
| **frozen replay** | the exact §5 table, including all candidate widths, premises and spread ≤ 1 |
| **retained ABORTED** (4 sliced bursts) | `m08_creditable` false |

**Mutations** (exec'd in memory, no file copies). Every one fails tests:

| mutation | result |
|---|---|
| M1: hull envelope instead of per-handle windows | 8 F + 1 E |
| M2: pick the other mapping | 8 F |
| M3: own-link instead of global last | 1 F |
| M4: no ±1 ms pad | 7 F |
| **M5: re-add the 16 B lower bound (required)** | 7 F, including the hull-PASS-ambiguous and false-exclusion tests |
| M6: hull verdict as fallback for an ambiguous mapping | 1 F |
| M7: drop the sequence and later-fragment checks | 1 F |
| M7b: also drop the multi-fragment identity check | 2 F (single-fragment and incomplete-echo) |

**Full suite:** `python3 -m unittest discover -p "test_*.py"` → **Ran 511, OK (skipped=5)**. That is
the old 494 plus 17; the 5 skips are the Windows-machine tests. Per-suite:

| suite | result |
|---|---|
| release_bound | 13 OK |
| release_gate | 4 OK |
| clock | 6 OK |
| macos_poc | 58 OK |
| scenario_matrix | 136 OK (5 skipped) |
| attribution | 17 OK |

`matrix.py check` reports `matrix ok: NO-GO`, and `git diff --check` is clean.

## 5. Data outputs: `evidence/mac-release-attributed-safe-2026-10-29/` (NEW)

`SHA256SUMS` is `c05174f458bb151039e0e944f70994f170ba8ed470f4d21317e7256a643b1f96` (6 files). The
name sweep is clean.

| file | sha256 |
|---|---|
| `shutdown-1/release-bound-attributed.json` | `e9dc87d8e8ab…` |
| `port-disconnect-1/release-bound-attributed.json` | `3f04e4d1c47b…` |
| `tab-close-1/release-bound-attributed.json` | `811d96cf9f80…` |
| `derivation-record.json` | |
| `FINDING.md` | |
| `.gitattributes` | |

`derivation-record.json` holds the roles, the premise correction, the hull role, the precision
statement, the source binding (HEAD `9c3ddc54` plus dirty-diff sha and untracked A2 hashes), code
pins, the source-history pointer, the immutable-folder checks, the exact uniform commands and the
per-case summary.

**Causal premises** (all 6 transports): sequence `[20] + 8×512 + 36`, 8 later fragments,
API/callback complete, epoch−elapsed spread **1 ms** each. All met.

**All candidate widths** (ms; negative = empty) and **mappings:**

| case | O(6T,h1) | O(6T,h2) | O(10Pro,h1) | O(10Pro,h2) | feasible bits | unique |
|---|---|---|---|---|---|---|
| shutdown (h1 `0535`, h2 `e7b2`) | **−230** | 256 | 258 | 504 | [F, T] | 6T→e7b2, 10Pro→0535 |
| port-disconnect (h1 `5643`, h2 `c710`) | 273 | 533 | **−224** | 282 | [T, F] | 6T→5643, 10Pro→c710 |
| tab-close (h1 `0939`, h2 `7185`) | **−242** | 270 | 235 | 550 | [F, T] | 6T→7185, 10Pro→0939 |

**Per-phone checks** (trace ms; global last / exit = 4059/4139, 5951/6032, 5113/5190):

| case / phone | width | DIS_trace | margin vs global | ≤ exit + 30 s | stop > DIS | stop ≥ hi + exit + 30 s |
|---|---|---|---|---|---|---|
| shutdown 6T | 256 | [4143, 4399] | +84 | ✓ | ✓ | ✓ |
| shutdown 10Pro | 258 | [4152, 4410] | +93 | ✓ | ✓ | ✓ |
| port-disconnect 6T | 273 | [6036, 6309] | +85 | ✓ | ✓ | ✓ |
| port-disconnect 10Pro | 282 | [6005, 6287] | **+54** | ✓ | ✓ | ✓ |
| tab-close 6T | 270 | [5167, 5437] | **+54** | ✓ | ✓ | ✓ |
| tab-close 10Pro | 235 | [5173, 5408] | +60 | ✓ | ✓ | ✓ |

**Verdicts:**

| case | hull (diagnostic) | A2 verdict | m08_creditable |
|---|---|---|---|
| shutdown | PASS | PASS | **true** |
| port-disconnect | FAIL | PASS | **true** |
| tab-close | FAIL | PASS | **true** |

This is exactly the review's §2 safe table.
- **Unchanged from A1:** the mappings, DIS_trace lower bounds and margins.
- **Larger than A1:** the widths and DIS_trace upper bounds.

**Precision, explicitly not a waiver:**
- The widths are **235–282 ms**. The prior ≤ 250 ms figure was a test-instrument target, not an RFC
  gate, and **it is not claimed met** (`all_within_instrument_target=false`).
- Acceptance rests on the unchanged full-interval checks: the global last exchange (min +54 ms), exit
  + 30 s, and both stop guards. All pass with the wider intervals.

**Immutability:**
- raw `mac-retake-2026-10-29`: `f952c123…`, 79 entries
- A1 `mac-release-attributed-2026-10-29`: `dd05dac4…`, 6 entries

Both pass `sha256sum -c` before and after, with 0 files modified.

## 6. M-11 (read-only check only; no edits, no adjudication here)

- `/tmp/opencode/otp23-m11-controller-20261029/account-class.txt` sha256 is
  `a2bdc40c7d6119e01513ae3c3b9b71a287ad910d114ff172883043303050702d`, matching the parent record.
  I didn't modify it, and I didn't adjudicate M-11.
- The M-11 private code is unchanged: `m11_dev_capture.py 976f61cf…`, test `50aa32eb…`,
  `package-dev.sh fa6ba4e8…`.
- I touched no M-11 raws (the attempt dirs and `all-attempts.tgz`).
- Per the parent, the M-11 data is eligible for manual adjudication after the account-record review,
  at later integration.

STOPPED for an independent MICRO-review of the A2 diff, its tests and the safe outputs, before any
canonical promotion, commit, push or GitHub action.
