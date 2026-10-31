# OTP-23 Mac M-08 release attribution (Option A): implementation result (2026-10-29)

## 0. Role envelope

- **Implementer:** Claude Code, `claude-opus-5-5`, session `752e1974-0617-470b-a425-dfbda97ac728`,
  no subagents. This session is distinct from the reviewer, Opus 7b4ee6ca.
- **Approved design:** `/tmp/opencode/otp23-mac-m08-physical-review-opus.md` §2–3, with Option A
  selected by the parent.
- **Historical roles, unchanged:**
  - GLM wrote the frozen analyzer and the M-08 code phase.
  - Kimi K3 did the code verification.
  - Opus 7b4ee6ca did the fix micro-verification and the physical review.
- **Not done:**
  - no hardware, Mac, phone, Chrome, TCC, signing or fixture action
  - no M-11 action: the private stage stays frozen at `976f61cf…` / test `50aa32eb…`
  - no row promotion and no OTP-24
  - no commit, push or GitHub
- **Matrix:** stays 23 PASS / 6 BLOCKED / 3 NOT RUN (waived), NO-GO; `acceptance/` and `docs/` are
  unchanged.

## 1. Code (sha256)

| file | sha256 | status |
|---|---|---|
| `scripts/mac_release_attribution.py` | `69546ee581bed37977304c4cf65cc93e6765959c21a5e249553e2ea1b150be0c` | NEW |
| `scripts/test_mac_release_attribution.py` | `3151f62efc9a428b3976cb70a651650a7b4211618f049ce467cca5bcc3aed8cc` | NEW |
| `.github/workflows/otp23-feasibility.yml` | `df1305d4d3729318d4e9cef123a1c0cd1fcbcf46e4ffaea09e7f0e45f1ba6291` | was `95f78691…`; +1 portable line `python scripts/test_mac_release_attribution.py` after `test_mac_release_bound.py` |
| `scripts/mac_release_bound.py` | `a49b3450…` | UNCHANGED, imported and byte-pinned (`REVIEWED_ANALYZER_SHA256`; a mismatch is a fail-closed FAIL) |
| `scripts/scenario_matrix.py` | `f8bbf2d6…` | unchanged |
| `scripts/scenario_matrix_core.py` | `cb512418…` | unchanged |
| `scripts/macos_poc.py` | `e2131ad3…` | unchanged |

- The native fixture is untouched.
- v4 pins: HEAD `scenario_matrix.py` is still `f8284c98…`, the historical source at `9c3ddc5`. Live
  differs only by the already approved Mac gate. The other six (`9cad1a70`, `cb512418`, `c6fcfd6e`,
  `40d8f824`, `06f0de1b`, `78d53ca9`) are live == HEAD.
- Windows: `release_gate("windows")==2`, `macos→None`. The new code is analysis-only.

## 2. What the module does

**How it runs:** it calls the frozen `mrb.analyse()` and keeps that result as the **hull result**,
alongside its own. It takes the per-phone `proof_notify` / `first_fragment_notify` /
`disconnect_phone_ms` from that output (the analyzer's own event set), and the per-handle in/out
pairs, global last exchange and exit from the frozen `mrb.host_record_bundles()`.

**Rules (all fail-closed):**
1. Any hull `problems` (structural: missing or ambiguous events, unclean telemetry, empty anchor,
   run/peer mismatch) → `not_applicable_structural_hull_problems`, FAIL. These are never bypassed.
2. Preconditions:
   - exactly 2 distinct phones, clean telemetry, the same run and peer
   - only 16 B and 4096 B record sizes
   - exactly 2 handles, each with exactly one 16 B pair and one 4096 B pair

   If any fails: `not_applicable_preconditions`, and the hull verdict stands.
3. Windows: `O(p,h) = [e16−m16−1, e16−i16+1] ∩ [e4096−m4096−1, e4096−i4096+1]`, with the ±1 ms pad
   applied before any feasibility test.
4. Mappings: both bijections are enumerated, with phones sorted by serial and handles by id, so the
   result doesn't depend on order.
   - 0 feasible → FAIL.
   - ≥ 2 feasible → `ambiguous`, and the hull verdict stands. No choice is ever made.
   - Phone epochs are never compared across clocks.
5. Under the unique mapping, the four unchanged checks run:
   - `release_after_last_exchange` against the **global** last exchange; the own-link value is
     recorded only
   - `within_bound_of_own_exit` (exit + 30000)
   - `stop_after_disconnect`
   - `stop_after_bound_plus_uncertainty`, using the attributed `hi`; the hull-`hi` stop guard is
     recorded too

   A hull FAIL is upgraded only by a unique mapping with all four checks PASS.
6. `m08_creditable = verdict == PASS and target_teardown_scenario`, using the hull's own flag.
7. Pins recorded per output:
   - self sha, and the imported analyzer/core/driver shas
   - trace, scenario JSON, transports and stops
   - the frozen `release-bound.json` sha and verdict, cross-checked against the recomputed hull (a
     mismatch is FAIL)
   - source binding (HEAD plus tracked dirty-diff sha)
8. The CLI refuses to overwrite an existing output.

## 3. Tests

**Targeted** (`python3 scripts/test_mac_release_attribution.py`): **12 OK**. They also pass with
`pwd` blocked (Windows simulation), and both files parse as Python 3.9.

| test | what it proves (synthetic values hand-computed) |
|---|---|
| unique mapping | hull FAIL is upgraded to PASS. Exact padded brackets, Y's `DIS` = [2274, 2326], margin 24, own-last 1700 |
| swapped `--transport` order | identical mappings and verdict |
| ambiguous (both feasible) | no assignment, and the hull FAIL stands |
| zero feasible (both phones fit only handle A) | FAIL |
| global vs own | `DIS` [2074, 2126] is after own-last 1700 but before global 2250 → FAIL; the own-link check is recorded true |
| quantization | an unpadded 1 ms miss becomes feasible only with the pad; width 1 |
| structural hull problem (unclean telemetry) | FAIL; attribution never applied |
| preconditions (third handle) | not applicable; the hull verdict stands |
| FAIL scenario | verdict PASS but `m08_creditable` false |
| wrong imported analyzer sha | FAIL |
| **frozen replay** | the exact §4 table below for all three cases |
| **retained ABORTED** (mac-resume-2026-10-28, 4 sliced bursts) | `target_teardown` false and `m08_creditable` false |

**Mutations** (exec'd in memory, no file copies):

| mutation | result |
|---|---|
| M1: hull envelope instead of per-handle windows | 7 failures + 1 error. On the frozen data, all three cases become ambiguous, so the hull verdict stands: port-disconnect and tab-close revert to FAIL, shutdown stays PASS |
| M2: pick the other mapping | 7 failures |
| M3: own-link instead of global last | 1 failure (global-vs-own) |
| M4: no ±1 ms pad | 6 failures |

**Full suite:** `python3 -m unittest discover -p "test_*.py"` → **Ran 506, OK (skipped=5)**. That is
the old 494 plus these 12; the 5 skips are the Windows-machine tests. Per-suite:

| suite | result |
|---|---|
| release_bound | 13 OK |
| release_gate | 4 OK |
| clock | 6 OK |
| macos_poc | 58 OK |
| scenario_matrix | 136 OK (5 skipped) |
| attribution | 12 OK |

`matrix.py check` reports `matrix ok: NO-GO`, and `git diff --check` is clean.

## 4. Derived evidence: `evidence/mac-release-attributed-2026-10-29/` (NEW)

The folder's `SHA256SUMS` is `dd05dac4b63f67336370a8a203d2dff47471e3fbd10eaee73a1692a54b763355`,
covering 6 files. The name sweep is clean.

| file | sha256 |
|---|---|
| `shutdown-1/release-bound-attributed.json` | `de2980b5e901…` |
| `port-disconnect-1/release-bound-attributed.json` | `33079bce96eb…` |
| `tab-close-1/release-bound-attributed.json` | `437657dec58f…` |
| `derivation-record.json` | roles, source binding, code pins, exact commands, frozen check |
| `FINDING.md` | |
| `.gitattributes` | |

**The frozen folder is immutable:** `mac-retake-2026-10-29/SHA256SUMS` is `f952c123…` (79 entries),
`sha256sum -c` passes before and after, 0 files are newer than the pre-check, and nothing was
appended. The old FAIL `release-bound.json` files are unchanged and recorded by sha in each output.

The same command (in `derivation-record.json`) was applied uniformly to every case:
`python3 scripts/mac_release_attribution.py --lifecycle-dir <frozen>/<case>-1/mac --scenario <case>
--transport …2be02e41.json --transport …864a3db0.json --stops <frozen>/<case>-1/stops.json
--hull-release-bound <frozen>/<case>-1/release-bound.json --out <derived>/<case>-1/release-bound-attributed.json`

**Mapping proof.** Every candidate `O(p,h)` with the pad, as width (ms; negative = empty). Trace
times: global last / exit = 4059/4139, 5951/6032 and 5113/5190.

| case | O(6T, h1) | O(6T, h2) | O(10Pro, h1) | O(10Pro, h2) | mapping 1 | mapping 2 | unique |
|---|---|---|---|---|---|---|---|
| shutdown (h1=`0535`, h2=`e7b2`) | −230 | **63** | **53** | −177 | 6T→0535: infeasible | 6T→e7b2: feasible | 6T→e7b2, 10Pro→0535 |
| port-disconnect (h1=`5643`, h2=`c710`) | **54** | −197 | −224 | **49** | 6T→5643: feasible | 6T→c710: infeasible | 6T→5643, 10Pro→c710 |
| tab-close (h1=`0939`, h2=`7185`) | −242 | **61** | **62** | −136 | 6T→0939: infeasible | 6T→7185: feasible | 6T→7185, 10Pro→0939 |

**Per-phone checks under the unique mapping (trace ms):**

| case / phone | handle | DIS_trace | margin vs global | own last | exit + 30 s | 4 checks | hull-`hi` stop guard |
|---|---|---|---|---|---|---|---|
| shutdown 6T | e7b2 | [4143, 4206] | +84 | 4059 | ≤ 34139 | PASS | PASS |
| shutdown 10Pro | 0535 | [4152, 4205] | +93 | 3509 | ≤ 34139 | PASS | PASS |
| port-disconnect 6T | 5643 | [6036, 6090] | +85 | 5373 | ≤ 36032 | PASS | PASS |
| port-disconnect 10Pro | c710 | [6005, 6054] | **+54** | 5951 | ≤ 36032 | PASS | PASS |
| tab-close 6T | 7185 | [5167, 5228] | **+54** | 5113 | ≤ 35190 | PASS | PASS |
| tab-close 10Pro | 0939 | [5173, 5235] | +60 | 4518 | ≤ 35190 | PASS | PASS |

**Verdicts:**

| case | hull (frozen) | attributed verdict | upgraded from FAIL | `m08_creditable` |
|---|---|---|---|---|
| shutdown | PASS | PASS | false | **true** |
| port-disconnect | FAIL | PASS | true | **true** |
| tab-close | FAIL | PASS | true | **true** |

This reproduces the reviewer's independent §2 table exactly, with the pad applied: widths +2, DIS
intervals ±1, margins −1. The infeasible alternatives miss by 136–242 ms, far beyond the 1 ms
quantum.

## 5. Invariants held

- Analyzer `a49b3450`, driver `f8bbf2d6`, MacPoC `e2131ad3` and core `cb512418` are not edited.
- No hull structural error is bypassed. The global-last-exchange clause is unchanged. There is no
  optimistic mapping choice and no cross-clock comparison.
- All 3 cases were treated uniformly.
- The frozen 79-entry evidence and its FAIL `release-bound.json` files are immutable.
- The retained ABORTED runs stay non-creditable.
- The old 494 tests are green.
- The matrix stays 23/6/3, NO-GO. The new physical data is verified, but the analysis awaits
  independent review.

STOPPED for independent micro-review of this diff and its replay outputs before any row decision.
