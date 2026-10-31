# OTP-23 Mac M-08 physical retake: independent evidence review and minimum remaining plan (Opus 5.5, 2026-10-29)

## 0. Role envelope (safe metadata only)

- **Reviewer:** a fresh, independent Claude Code session, model `claude-opus-5-5`. This is the same
  reviewer session (7b4ee6ca) that wrote `otp23-mac-m08-fix-verification-opus.md`. It is **not** the
  operator/implementer `752e1974-0617-470b-a425-dfbda97ac728`. No subagents.
- **Route:** the owner-authorized Opus route, `/tmp/opencode/otp23-mac-opus-continuation-owner-record.json`.
- **Inputs:**
  - `/tmp/opencode/otp23-mac-m08-retake-result.md`
  - the frozen `native-bridge/feasibility/evidence/mac-retake-2026-10-29/` (read-only)
  - the pinned repo sources
- **Not done:** no hardware, no source or evidence edits, no Gradle, no commit, push or GitHub.
- **Scratch:** tmpfs only (`/dev/shm`, all deleted):
  - stage.tgz extraction
  - analyzer re-runs
  - token file for the privacy sweep

  Bytecode writing was disabled. The only file written is this report.
- **Matrix:** `matrix.py check` reports `matrix ok: NO-GO`, 23/6/3. `acceptance/` and
  `docs/otp-23-requirements-matrix.md` are unchanged vs HEAD. **Do not promote any row yet.**

## VERDICT

1. **Raw evidence, producer pins, manifest, grant baseline, hygiene channel and warnings:** VERIFIED
   (§1). The driver PASS and all 20 host-side checks hold in all three runs.
2. **shutdown-1:** creditable now as M-08 shutdown evidence under the reviewed, pinned analyzer
   `a49b3450`. The verdict is PASS and `target_teardown_scenario=true`. It also passes the stricter
   option-(A) attribution (§2), so the credit doesn't depend on which method is chosen.
3. **port-disconnect-1 and tab-close-1:** not creditable under the pinned analyzer. Its FAIL is real
   and must stay recorded, although it comes from anchor looseness, not from an observed ordering
   violation.
4. **Option (A) resolves both failures from the frozen data, with no new hardware run.** It uses a
   rigorous per-phone × per-handle causal bijection, with every mapping checked. In each scenario
   **exactly one mapping is feasible**. Under that mapping, every phone's release interval lies
   after the trace's **global** last exchange, with margins of 55–94 ms, so the "after last
   proven-alive" clause holds unweakened.

   **(A) is the minimum correct path.** It needs a small, independently reviewed analysis addition
   plus tests (§3). After that review, port-disconnect-1 and tab-close-1 become creditable.
   (B) is legitimate but not minimal (§4). (C) is rejected as a substitute (§5).

## 1. Evidence verification (independently recomputed, not taken from the report)

| item | result |
|---|---|
| **Manifest** | `SHA256SUMS` `f952c123…` has 79 entries. `sha256sum -c` passes, and the disk set equals the manifest. `FINDING.md` `9f4c058e…`, `provenance.json` `6981e7eb…`. |
| **Producer pins** | Live repo: `scenario_matrix.py f8bbf2d6…`, `macos_poc.py e2131ad3…`, `mac_release_bound.py a49b3450…`, workflow `95f78691…`, all unchanged. `stage.tgz` members pass `stage-expected.sha256`, and every staged script, `identity.rs` and the 4 extension files are **byte-identical** to the repo working tree. The staged selftest equals `helpers/mac_listener_selftest.py` (`f40e7507…`). Each `lifecycle.json` meta records `scenario_matrix.py f8bbf2d6`, `scenario_matrix_core cb512418`, `macos_poc e2131ad3`, connector/manifest/identity as staged, and `host_binary_sha256 e7c716c5…`. |
| **Analyzer outputs** | I re-ran the pinned `mac_release_bound.py` on all three frozen dirs. The output is identical to the recorded `release-bound.json` except for timestamp and path: shutdown PASS, port-disconnect FAIL, tab-close FAIL. `helper_self_sha256 a49b3450`. |
| **Driver PASS** | All three `<scenario>.json` files have `status: PASS`. `detail.teardown`: shutdown `trace_mechanism=shutdown`, order ends `disconnected, disconnected, shutdown_complete`, exit 4139 code 0 drained, gone 0.004 s. port-disconnect `stdin_closed`, exit 6032 code 0 drained, gone 0.004 s. tab-close `stdin_closed`, exit 5190 code 0 drained, gone 0.113 s. All have `problems []`, `cleanup_problems []`, surviving `[]`, `w02` 0, `detached_close_env_unset true`, `connect_order scan-active`, `trace_released_count 0` (recorded; P0-A means it isn't gated on macOS). The shutdown pending request was answered with `error/internal/shutdown`, the expected pending-at-shutdown result. |
| **Grant baseline** | Every trace shows `bluetooth authorization before adapter init: allowed_always` and `adapter init: created=true authorization=allowed_always`, then `scan_state started` and `device_found`. That is a live authorized scan by the approved signed fixture `e7c716c5…` (`me.nagaev.veles.bridge`, team `7T39M26Z99`, strict verify recorded before and after). Chrome/156.0.8078.74. These are valid M-11 comparison baselines. |
| **Hygiene (recomputed from the raw `probe_during`)** | 3 × 6 = **18 samples, all `summary.problems == []`**. The host pid has `top_visible 0`, `all_layers 0`, `tcp4 []` and `pid_alive true` in every sample. Positive controls hold in every sample: `chrome_visible_top_level 14`, CDP listener present, and Chrome's own LISTEN+ESTABLISHED tcp4 seen. The observer is `Background` / uid 501 / ssh. `probe_after`: host `pid_alive false`. `hygiene_before`: 0/0. Two 16 B link proofs PASS with distinct request hashes. |
| **lsof channel / warnings** | 14 lines per run: **13 `benign` + 1 `clean_stderr`**. The clean line is the post-exit host probe, rc 1. The single benign block is the foreign `AppTranslocation` nullfs mount, with sanitized snapshot `328d66e8…` everywhere. Host `lsof -nP -a -p <pid> -i` gives rc 1 with 0 rows, i.e. no IP endpoint of any family. Listener selftest: with DIAG set, it returned the real port 52408 (`LISTEN`) while the warning was present (rc 0), and wrote a persisted diag line. With DIAG unset, `env=None` and the run is accepted. This matches the approved design. |
| **Phones** | Each transport is `final`, `final_after_service_stopped`, `clean`, untruncated, `peers_dropped 0`, with one peer `576d2120c83dc839` and exactly 1 connect + 1 disconnect. The run id is shared across both phones and differs per scenario. **Wall-clock stability:** `epoch_ms − elapsed_realtime_ms` varies by ≤ 1 ms across every burst (6/6 transports), so no wall step inside any anchor window. `stops.json` matches `stops.raw.txt`; stop_min − DIS is 75–94 s. |
| **Privacy** | The redacted-token sweep (tokens regenerated from the private archive, never printed) finds no hits in the 79 files or inside `stage.tgz`. The mount snapshot shows only `uid≠self`. The private originals dir is `700`, with files `600`/`700`. |
| **Report arithmetic** | Exit − global last message = 80 / 81 / 77 ms. Hull widths 125 / 119 / 142 ms. Lower-bound deltas +21 / −17 / −28 ms. All confirmed. |

**Nit (non-blocking):** a FAIL `release-bound.json` still carries `creditability: target_evidence`.
That field describes the scenario type, not the verdict. Any parent read-out must use
`verdict == PASS && target_teardown_scenario`. The (A) output should carry one explicit
`m08_creditable` boolean.

## 2. Option (A): rigorous handle↔phone bijection on the frozen data (computed here)

**Premises.** These are only the causal premises the reviewed analyzer `a49b3450` already relies
on, plus one fact.
- For one host connection handle `h`, the host trace logs `in … send` at `i_h` **before** the write
  reaches the phone.
- The phone's echo `notify_api` (epoch `e`, on that phone's own clock) happens before the host logs
  `out … message` at `m_h`.
- So `origin_p ∈ [e − m_h, e − i_h]`.
- The added fact: each phone held exactly one link in the run, so its 16 B and its 4096 B events
  belong to the **same** handle.

**Method.**
1. For every phone `p` and every handle `h`, form
   `O(p,h) = [e16 − m16_h, e16 − i16_h] ∩ [e4096first − m4096_h, e4096first − i4096_h]`. This uses
   the analyzer's own event set.
2. Enumerate **all** bijections between phones and handles.
3. A mapping is feasible iff every `O(p, map(p))` is non-empty.
4. The true mapping is necessarily feasible. So **if exactly one mapping is feasible, it is the true
   one by deduction**. There is no optimistic choice, and no phone epoch is ever compared with
   another phone's epoch.

**Results** (trace ms; widths before ±1 ms quantization):

| scenario | handles (16 B in/msg; 4096 in/msg) | O(6T,·) | O(10Pro,·) | feasible mappings | unique mapping |
|---|---|---|---|---|---|
| shutdown | `0535…` (2796/2847; 2996/3509), `e7b2…` (2860/2921; 3557/4059) | `0535` empty (−232), `e7b2` 61 | `0535` 51, `e7b2` empty (−179) | 1 of 2 | 6T→`e7b2`, 10Pro→`0535` |
| port-disconnect | `5643…` (4660/4712; 4851/5373), `c710…` (4732/4779; 5420/5951) | `5643` 52, `c710` empty (−199) | `5643` empty (−226), `c710` 47 | 1 of 2 | 6T→`5643`, 10Pro→`c710` |
| tab-close | `0939…` (3888/3948; 4108/4518), `7185…` (3971/4030; 4565/5113) | `0939` empty (−244), `7185` 59 | `0939` 60, `7185` empty (−138) | 1 of 2 | 6T→`7185`, 10Pro→`0939` |

The attribution is stronger than a bare bijection: **each phone on its own is feasible with only one
handle**. The infeasible alternatives miss by 138–244 ms, which is far above any ±1–2 ms stamp
quantization.

**Release checks under the unique mapping** (DIS_trace = DIS_phone − O):

| scenario / phone | DIS_trace | vs global last exchange | vs own-link last exchange | ≤ exit + 30 s | stop guards |
|---|---|---|---|---|---|
| shutdown 6T | [4144, 4205] | ≥ 4059 (+85) | ≥ 4059 (+85) | yes | pass/pass |
| shutdown 10Pro | [4153, 4204] | ≥ 4059 (+94) | ≥ 3509 (+644) | yes | pass/pass |
| port-disconnect 6T | [6037, 6089] | ≥ 5951 (+86) | ≥ 5373 (+664) | yes | pass/pass |
| **port-disconnect 10Pro** | [6006, 6053] | **≥ 5951 (+55)** | ≥ 5951 (+55) | yes | pass/pass |
| **tab-close 6T** | [5168, 5227] | **≥ 5113 (+55)** | ≥ 5113 (+55) | yes | pass/pass |
| tab-close 10Pro | [5174, 5234] | ≥ 5113 (+61) | ≥ 4518 (+656) | yes | pass/pass |

**What this shows:**
- The clause is satisfied against the **global** last proven-alive exchange, the stricter reading,
  so no semantic narrowing to "own link" is needed.
- The hull failures came from mixing the other link's in/out pairs into each phone's envelope, which
  widened the anchor from about 50 ms to 119–142 ms.
- Widening every bound by ±1 ms of quantization leaves ≥ 53 ms margins.
- Using the last 4096 fragment as an extra upper constraint changes nothing here (identical widths),
  so it is **not** needed. Keep the analyzer's event set.

## 3. Minimum approved remaining step: (A) as reviewed analysis code (no hardware)

**Implementation.** Add an attribution stage to `mac_release_bound.py` (additive), or a separate
pinned helper that imports its parsers. Output goes to a **new** file per scenario,
`release-bound-attributed.json`. The frozen `release-bound.json` stays byte-preserved.

**Rules (all fail-closed):**
1. Preconditions: exactly 2 phones, each with exactly one connect/disconnect burst; exactly 2
   handles, each with exactly one 16 B pair and one 4096 B pair. Otherwise: no attribution, and the
   hull verdict stands.
2. Compute `O(p,h)` as in §2, widened by 1 ms per bound for ms quantization. Enumerate every
   bijection.
3. Require **exactly one** feasible mapping. 0 feasible → FAIL. ≥ 2 feasible → "attribution
   ambiguous", and the hull verdict stands. Never pick among feasible mappings.
4. Under the unique mapping, run all four existing checks with the attributed interval:
   - `release_after_last_exchange` compares against the **global** last exchange message. This is
     unchanged; also record the own-link value.
   - `within_bound_of_own_exit` uses `exit + 30000`.
   - Both stop guards are computed with the attributed `hi`. The hull-`hi` values are also recorded;
     both pass today.
5. Record every `O(p,h)`, both mappings with their feasibility, the hull result alongside, and
   `m08_creditable = verdict == PASS && target_teardown_scenario`.
6. Apply the stage **uniformly to all three scenarios**, shutdown included. No per-case selection.

**Required tests:**
- **Synthetic cases:**
  - a unique mapping narrows the interval and passes
  - overlapping windows where both mappings are feasible → ambiguous, hull verdict kept
  - both mappings infeasible → FAIL
  - swapped `--transport` order gives the same mapping
  - a mapping-feasible case whose DIS_trace_lo falls below the global last exchange → FAIL (the
    clause is not relaxed)
- **Mutations that must fail:**
  - hull substituted for per-handle windows (port and tab revert to FAIL)
  - picking the other mapping
  - comparing against own-link instead of global
  - removing the ±1 ms widening, if it is asserted
- **Frozen replay:**
  - the three `mac-retake-2026-10-29` dirs reproduce the §2 mappings, widths and margins, with all
    PASS
  - the retained `mac-resume-2026-10-28` ABORTED replay stays `diagnostic_only_not_creditable`
- The existing 494-test suite stays green. Windows is untouched: the code is mac-only, and
  `release_gate("windows") == 2` is unchanged.

**Review gate.** An independent micro-verification of that diff and its replay outputs must happen
**before** any row decision. After it passes:
- port-disconnect-1 and tab-close-1 become creditable M-08 evidence (driver PASS + 20 host checks +
  attributed release proof PASS)
- shutdown-1 is double-confirmed

**No re-run is needed.**

## 4. Option (B), the pre-teardown hold: legitimate but not minimal

**It is legitimate.**
- It is a pre-declared **scenario-timing** parameter of the test driver, not product runtime and not
  a gate waiver. The criterion stays exactly "release after last proven-alive exchange, ± full
  uncertainty, within exit + 30 s, stop guards".
- A ≥ 500 ms hold exceeds the worst hull width (142 ms) plus margin, so the pinned analyzer could
  decide the clause without attribution.

**It is not minimal.** It needs:
- a driver change to `scenario_matrix.py`, which moves it further from the historical v4 source,
  with a new review and Windows default-0 regression tests
- a new hardware session
- two new captures

(A) needs none of these.

**If the parent nevertheless prefers (B), the approved design is:**
- **Parameter:** `--mac-pre-teardown-hold-ms`, default 0. Windows behaviour is byte-unchanged.
- **Which runs:** used only for port-disconnect and tab-close.
- **When the hold runs:**
  - It starts after the driver has observed the last echo-verified message and the
    `after-probes-active-scan` sample.
  - Active scan, both links and every control stay in place.
  - It ends with the unchanged teardown trigger.
- **What to record:** the hold start and end wall ms, the trace-offset estimate, and the elapsed
  time. Assert no link traffic in the trace during the hold.
- **Capture dirs:** new unique dirs `port-disconnect-2/` and `tab-close-2/`.
- **Attempts:** exactly one attempt each. On failure, stop and report; no re-roll.
- **Earlier captures:** the `-1` FAILs stay recorded as such.

## 5. Option (C), phone-clock ordering only: rejected as a substitute

On each phone's own clock, DIS follows that phone's last 4096 fragment `notify_api` by 259–876 ms.
That is valid single-clock ordering. But it proves only that the release followed the phone's own
API call:
- It does **not** reach the host-proven receipt time `m_h`. Relating the two needs cross-clock
  mapping or attribution.
- It drops the global-last-exchange clause and the uncertainty.

So it would weaken the criterion. It may be recorded as corroboration only. (A) makes it unnecessary.

## 6. Creditability summary

| capture | status now | after the (A) review passes |
|---|---|---|
| `shutdown-1` | **creditable** (pinned analyzer PASS, `target_evidence`) | creditable (attributed PASS too) |
| `port-disconnect-1` | not creditable (hull FAIL; keep recorded) | creditable if attributed PASS reproduces §2 |
| `tab-close-1` | not creditable (hull FAIL; keep recorded) | creditable if attributed PASS reproduces §2 |
| `listener-selftest/` | valid design check (DIAG set → real port; unset → None) | unchanged |
| `mac-resume-2026-10-28` | diagnostic only (ABORTED) | unchanged |

The three runs remain the signed live authorized-scan baselines for M-11. M-11 still needs the
M11-1 fix and its re-verification, in a separate code-only phase in `/tmp`. Row promotion is the
parent's decision, after all criteria are met. The matrix stays at 23/6/3, NO-GO, until then.
