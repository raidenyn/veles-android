# OTP-23 Mac M-08 release attribution A2, 2026-10-29: FINDING (derived; awaiting review)

This is derived analysis only. No hardware, no new capture, and no matrix or row change: the matrix
stays at 23/6/3, NO-GO.
- **Inputs:** the immutable `../mac-retake-2026-10-29/` (79 entries, `SHA256SUMS` `f952c123…`, OK
  before and after).
- **Method:** `scripts/mac_release_attribution.py` A2. It imports the frozen analyzer
  `mac_release_bound.py a49b3450…`, used for its parsers and as diagnostics only.
- **Supersedes:** `../mac-release-attributed-2026-10-29/` (A1, `69546ee5…`). That folder is kept
  byte-for-byte as history, and the A1 source is archived in
  `../mac-release-attribution-source-history-2026-10-29/`.

## Premise correction (explicit)

**What was wrong.** A1, and the frozen analyzer's docstring, assumed that the phone's single-fragment
16-byte `notify_api` is stamped before the host logs `out … type=message`. **That is not causal.** The
phone takes the stamp only after `notifyCharacteristicChanged()` returns, and by then the binder call
may already have queued the PDU. The 16-byte lower bound `e16 − m16` is therefore recorded as a
non-causal diagnostic and never used.

**What A2 uses instead:**
- **Lower bound:** the first fragment of the 4096-byte echo, which is verified to be 9 fragments
  (8×512 + 36). Its `notify_api` is stamped on the phone's main looper before any later fragment can
  be issued. Each later fragment waits for a separate main-looper callback. So the first stamp
  precedes the final fragment, which precedes the host's reassembled message:
  `lo = e_first − m4096 − 1`.
- **Upper bound:** `hi = min(e16 − i16, e_first − i4096) + 1`. The host stamps `in send` before
  forwarding, and the phone echoes only after receiving the record.

**Premises verified on all six transports (fail-closed):**
- exactly one 20-byte proof plus the complete 9-fragment echo
- every API call and bound callback has code 0, and the counters are complete
- 8 later fragments after the first
- epoch−elapsed spread is 1 ms (no wall step)

## Result (all three cases, same command)

| case | feasible bijections | unique mapping (6T / 10Pro) | assigned widths | DIS_trace 6T / 10Pro | margin vs global last | verdict | m08_creditable |
|---|---|---|---|---|---|---|---|
| shutdown-1 | 1 of 2 (6T↛`0535`: −230) | `e7b2` / `0535` | 256 / 258 ms | [4143, 4399] / [4152, 4410] | +84 / +93 | PASS | true |
| port-disconnect-1 | 1 of 2 (10Pro↛`5643`: −224) | `5643` / `c710` | 273 / 282 ms | [6036, 6309] / [6005, 6287] | +85 / +54 | PASS | true |
| tab-close-1 | 1 of 2 (6T↛`0939`: −242) | `7185` / `0939` | 270 / 235 ms | [5167, 5437] / [5173, 5408] | +54 / +60 | PASS | true |

**Comparison with A1.** The mappings, DIS_trace lower bounds and margins equal A1's. Those depend
only on `hi`, which is unchanged. The widths and DIS_trace upper bounds are larger. The result
reproduces the independent review's §2 table exactly.

**The checks themselves are unchanged and all pass:**
- release after the **global** last exchange
- within the host's own exit + 30 s
- disconnect before the fixture stop
- stop ≥ `hi` + exit + 30 s

## Precision, not a waiver

The assigned widths are 235–282 ms. The earlier ≤ 250 ms figure was a test-instrument precision
target, not an RFC acceptance clause, and **it is not claimed as met**. Acceptance rests on the
unchanged full-interval checks above, all evaluated over the wider intervals, and none was relaxed.

## Hull role

The frozen hull result is recorded per case as a diagnostic only:
- shutdown: PASS
- port-disconnect: FAIL
- tab-close: FAIL

It is never a fallback proof. An ambiguous mapping, unmet preconditions or premises, or structural
problems all fail closed with `m08_creditable=false`.
