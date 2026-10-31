# OTP-23 Mac M-08 release attribution (Option A), 2026-10-29: FINDING (derived; awaiting review)

Derived analysis only. No hardware, no new capture, and no matrix or row change.
- **Inputs:** the immutable `../mac-retake-2026-10-29/`. Its 79-entry `SHA256SUMS` is `f952c123…`
  and verifies before and after; nothing was appended to that folder.
- **Method:** `scripts/mac_release_attribution.py`, which imports the frozen, byte-pinned analyzer
  `mac_release_bound.py a49b3450…`. The design is
  `/tmp/opencode/otp23-mac-m08-physical-review-opus.md` §2–3.

## Method (unchanged clauses; one added fact)

**Per-phone, per-handle windows.** For each phone p and handle h:

`O(p,h) = [e16 − m16_h − 1, e16 − i16_h + 1] ∩ [e4096 − m4096_h − 1, e4096 − i4096_h + 1]`

The event set is the frozen analyzer's own. The ±1 ms quantization pad is applied before any
feasibility test.

**Mapping.** Both phone→handle bijections are enumerated. A mapping is used only if it is the
unique feasible one.

**Checks under that mapping, all unchanged:**
- release after the trace's **global** last exchange message (the own-link value is recorded only)
- within exit + 30 s
- both fixture-stop guards, using the attributed `hi` (hull-`hi` values recorded alongside)

**Credit.** `m08_creditable = verdict == PASS && target_teardown_scenario`.

## Result (applied uniformly to all three frozen cases)

| case | hull (frozen) | feasible mappings | unique mapping (6T / 10Pro) | attributed widths | margin vs global last | verdict | m08_creditable |
|---|---|---|---|---|---|---|---|
| shutdown-1 | PASS | 1 of 2 | `e7b2…` / `0535…` | 63 / 53 ms | +84 / +93 ms | PASS | true |
| port-disconnect-1 | FAIL | 1 of 2 | `5643…` / `c710…` | 54 / 49 ms | +85 / +54 ms | PASS (upgraded) | true |
| tab-close-1 | FAIL | 1 of 2 | `7185…` / `0939…` | 61 / 62 ms | +54 / +60 ms | PASS (upgraded) | true |

**The alternative mappings are infeasible.** Each phone's other handle misses its own window by 136–242 ms:

| case | 6T miss | 10Pro miss |
|---|---|---|
| shutdown-1 | −230 ms | −177 ms |
| port-disconnect-1 | −197 ms | −224 ms |
| tab-close-1 | −242 ms | −136 ms |

**Every phone passes all four unchanged checks.** The hull-`hi` stop guard is recorded and passes
too. These values reproduce the reviewer's independent table, with the pad applied: widths +2,
margins −1.

## Files

- `<case>-1/release-bound-attributed.json` holds, per case:
  - every `O(p,h)` and both mappings with their feasibility bits
  - the hull result alongside
  - the assigned interval, the global and own-link last values, the 30 s bound and both stop guards
  - all input pins: trace, scenario JSON, transports, stops, and the frozen `release-bound.json` sha
    and verdict, cross-checked
  - the self sha, the imported analyzer/core/driver shas and the source binding
- `derivation-record.json` holds the roles, source binding, code pins, frozen-folder check, the exact
  commands and the per-case output hashes.
- `SHA256SUMS` covers every file here.
