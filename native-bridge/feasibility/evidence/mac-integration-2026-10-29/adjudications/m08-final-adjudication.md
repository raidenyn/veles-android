# M-08 final adjudication (2026-10-29): PASS, scoped

**Row:** M-08, "No window; port closure, stdin EOF and shutdown each leave no process", which is
W-02/W-07/W-08/W-09 on macOS. **Account:** existing admin account (`raiden`, uid 501).

**Who did what:**
- **Integration:** written by the Claude Code `claude-opus-5-5` integration session.
- **Approval basis:** the independent micro-review
  `../reports/otp23-mac-safe-attribution-verification-opus.md`, by the fresh Opus session
  `e30e474d-65cc-44c2-99c7-d0195cfc4631`, which is neither the implementer nor the parent. Its
  verdict: **APPROVED for normal M-08/M-11 integration**.
- **Parent decision:** `../reports/otp23-mac-safe-analysis-parent-decision.json`, which requires A2,
  rules out the hull as a fallback and does not claim the precision target.

## Basis 1: owner-resumed prerequisite (not a waiver)

**The 2026-10-28 deferral stays recorded and is not rewritten.** The owner's answer was "Cannot
restart Chrome now" (`../../package1-b5-v4-20261028/mac-deferral-2026-10-28.json`, `ace5e31e…`).

**The owner later resumed the deferred prerequisite:**
- request: "Let's continue with Mac tests"
- completion answer: "Updated and relaunched"
- record: `../route/otp23-mac-resume-owner-record.json`

After that answer the operator observed:
- Chrome **156.0.8078.74**
- the signed host `e7c716c5…eb91c`, `me.nagaev.veles.bridge`, Team `7T39M26Z99`
- `codesign --strict` success

This record completes the deferred precondition. It is **not** a waiver, and it changes no gate.

## Basis 2: frozen physical retake (`../../mac-retake-2026-10-29/`)

The retake has 79 entries, with `SHA256SUMS f952c123…`.

**Fixture.** The approved signed fixture was unchanged: `e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c`,
`me.nagaev.veles.bridge`, Team `7T39M26Z99`, on actual Chrome 156.0.8078.74.

**Driver result.** All three Chrome-owned scenarios (shutdown-1, port-disconnect-1, tab-close-1)
have driver **PASS**, and all 19 acceptance read-out checks are true in each
`acceptance-check.json`.

**Connected phase:**
- 18/18 usable connected-phase samples (6 per scenario), each with an active scan and two distinct
  16-byte byte-proven links
- the host owned **0 windows at every layer** and had **no IP endpoints** (`tcp4=[]`) in every sample
- same-sample Chrome positive controls held in every sample: 14 visible top-level windows plus the
  CDP listener

**Listener self-test.** It proves the known-warning classifier on a real channel. Every unknown
warning still yields `unavailable`, no `-w` was used, and no foreign mount was changed.

**Teardown:**

| scenario | teardown frames | process exit | owned host gone |
|---|---|---|---|
| shutdown | `shutdown_complete` last | exit 0, drained | 0.004 s |
| port-disconnect | `stdin_closed`, no shutdown frames | exit 0, drained | 0.004 s |
| tab-close | `stdin_closed`, no shutdown frames | exit 0, drained | 0.113 s |

There were no survivors and no wrappers.

**Mac trace release count.** `released` is recorded honestly as **0**. Under parent decision P0-A
the platform-only driver gate is Windows-only, so on macOS release relies on the external phone proof
below.

## Basis 3: phone release proof, ADOPTED A2 only

The adopted outputs are in `../../mac-release-attributed-safe-2026-10-29/`: 6 entries,
`SHA256SUMS c05174f4…`.
- **Analyzer:** `scripts/mac_release_attribution.py` `ce75a8f1…`, with test `219c78be…`.
- **Imported frozen analyzer:** `a49b3450…`, used for diagnostics only.

**Lower bound.** It is the source-proven first fragment of the multi-fragment 4 KiB echo:
`lo = e_first − m4096 − 1`. The 16-byte lower bound is **not causal**: it is recorded as a diagnostic
only and is never used.

**Upper bound:** `hi = min(e16 − i16, e_first − i4096) + 1`.

**Guards, all fail-closed:**
- the complete 9-fragment sequence (`[20] + 8×512 + 36`)
- every API and bound callback code 0
- epoch−elapsed clock spread ≤ 1 ms
- every bijection enumerated, with exactly one feasible mapping required (zero or ambiguous fails)

**Checks.** These are unchanged and all retained:
- the global last exchange lower check
- own exit + 30 s
- both stop guards

| case / phone → handle | width (ms) | DIS_trace (ms) | margin vs global last (ms) | 4 checks |
|---|---|---|---|---|
| shutdown 6T → e7b2 | 256 | [4143, 4399] | +84 | all true |
| shutdown 10Pro → 0535 | 258 | [4152, 4410] | +93 | all true |
| port-disconnect 6T → 5643 | 273 | [6036, 6309] | +85 | all true |
| port-disconnect 10Pro → c710 | 282 | [6005, 6287] | +54 | all true |
| tab-close 6T → 7185 | 270 | [5167, 5437] | +54 | all true |
| tab-close 10Pro → 0939 | 235 | [5173, 5408] | +60 | all true |

All three cases are `attribution_status unique_mapping`, verdict **PASS**, `m08_creditable` **true**.
The independent reviewer's own raw replay reproduced every value exactly.

**Precision correction.** The widths are 235–282 ms. The earlier ≤ 250 ms figure was only a
test-instrument precision target, **not an RFC gate, and it is NOT claimed as met**. Acceptance rests
on the full-interval normative checks above, all of which pass. The measurand is the phone-observed
disconnect callback, not a controller link-layer timestamp.

## Retained history (byte-unchanged, not adopted)

- **Original hull results.** The `release-bound.json` per scenario inside the retake gave shutdown
  PASS, port-disconnect FAIL and tab-close FAIL. These stay the recorded results of the frozen
  analyzer, and are now diagnostic.
- **A1.** `../../mac-release-attributed-2026-10-29/`, with source `69546ee5…` and test `3151f62e…`
  archived and mapped in `../../mac-release-attribution-source-history-2026-10-29/`. It is
  **SUPERSEDED**: its precision and proof premise are invalid. Its bounds are not adopted and nothing
  was regenerated.
- **Direct SSH stdin EOF** (`../../mac-resume-2026-10-28/lifecycle-1/stdin-eof.json`). It is retained
  as a `permission_pending` diagnostic: in the ssh/Background launch context the TCC duty falls on
  the launching process. It is **neither PASS nor waived**. M-08's extension-owned EOF clause is met
  by the literal Chrome port-disconnect / tab-close `stdin_closed` teardown above, under the W-08
  interpretation. That reading was chosen by the parent preflight decision (`m08_eof_scope`,
  `../reports/otp23-mac-m08-parent-preflight-decision.json`) and independently verified by the final
  integrated review (`faee6a0e-43c5-41d7-9bc4-a2cd3f3de09b`, `../reports/otp23-mac-final-integrated-opus-review.md`).
  (Erratum 2026-10-29, review F2: an earlier wording attributed this verification to the e30 micro-review,
  which does not address the EOF reading.)
- **Earlier text.** The pre-grant, post-grant, 2026-10-28 deferral and preservation-gap text in the
  row stays verbatim.

## Outcome

**M-08 PASS (scoped)**, for:
- the approved signed fixture on the existing admin account
- Chrome 156.0.8078.74
- Chrome-owned shutdown, port-disconnect and tab-close, with window and endpoint absence measured
  during the connected phase

This does not change M-03, M-04, M-11 or any other row, and it does not authorize OTP-24.
