# OTP-23 Mac final micro-verification: M-08 attribution A2 and the M-11 account-class condition (Opus 5.5, 2026-10-29)

## 0. Role envelope (safe metadata only)

- **Reviewer:** Claude Code, model `claude-opus-5-5`. This is the fresh independent session that wrote
  `/tmp/opencode/otp23-mac-final-data-review-opus.md`.
- **Not:**
  - the implementer `752e1974-0617-470b-a425-dfbda97ac728`, which authored A1 and A2
  - the parent controller (`gpt-6.1-sol`)
  - no subagents
- **Not done:** no device, Mac, phone, Chrome, TCC, signing, registration, Gradle, GitHub, commit or
  push. No repo, source, evidence or `/tmp/opencode` record edits.
- **Scratch:** `/dev/shm/e30rev`, deleted at the end. It held:
  - a copy of `scripts/*.py` for in-memory-style mutations (the repo copies were never edited)
  - my independent replay parser
  - CLI re-run outputs
- **Bytecode:** tests ran with `-B` / `PYTHONDONTWRITEBYTECODE=1`. The pre-existing, gitignored
  `scripts/__pycache__` gained no new `.pyc`.
- **Only file written:** this report.

## VERDICT: APPROVED for normal M-08 / M-11 integration

**M-08:** A2 `ce75a8f1` / test `219c78be` implements exactly the corrected premise set of my review
§1–3 and the parent decision.
- The 16 B lower bound is diagnostic only.
- The lower bound is the first 4 KiB fragment. The upper bound is min(16 B, 4 KiB first).
- The data premises are verified fail-closed, and the hull is diagnostic only.
- The acceptance checks are unchanged.
- My independent raw replay reproduces every candidate, the mapping, the intervals, the margins and
  the checks of the new safe outputs **exactly**.
- shutdown-1, port-disconnect-1 and tab-close-1: **PASS, `m08_creditable` true.**

**M-11:** the account-class condition of my review §6.1 is satisfied by a real file (§4).
- I recommend a **scoped manual M-11 PASS**, with every caveat in §5.
- The raw data stays as captured. No answer is bound after the fact.

**Matrix after the parent's integration:** expected **25 PASS / 4 BLOCKED (W-10, M-03, M-04, A-01) /
3 NOT RUN (waived: W-01, M-01, M-02), NO-GO**.
- Current: `matrix ok: NO-GO`, 23/6/3.
- The non-PASS rows today are W-01, W-10, M-01, M-02, M-03, M-04, M-08, A-01 and M-11.
- M-03 and M-04 stay unmet.

## 1. Integrity and immutability (recomputed)

| item | result |
|---|---|
| A2 `scripts/mac_release_attribution.py` | `ce75a8f1…634e` ✓ |
| A2 `scripts/test_mac_release_attribution.py` | `219c78be…f016` ✓ |
| frozen `mac_release_bound.py` | `a49b3450…6560` (mtime 10-28 17:54) |
| `test_mac_release_bound.py` | `4b8aa10d…` |
| `scenario_matrix.py` | `f8bbf2d6…` |
| `scenario_matrix_core.py` | `cb512418…` |
| `macos_poc.py` | `e2131ad3…` |
| workflow | `df1305d4…` (selects the attribution test; unchanged) |
| raw `mac-retake-2026-10-29` | `SHA256SUMS f952c123…`, 79 entries, `sha256sum -c` OK; disk set = manifest |
| A1 outputs `mac-release-attributed-2026-10-29` | `dd05dac4…`, 6 entries, OK |
| Changes to either folder since my previous review | **0 files** newer than that review |
| Source history `mac-release-attribution-source-history-2026-10-29` | `SHA256SUMS 2a550c1c…` OK. `a1-superseded/` holds byte-exact `69546ee5…` / `3151f62e…`. `source-history.json` maps the three A1 outputs (`de2980b5…`, `33079bce…`, `437657de…`, `helper_self 69546ee5…`) to those bytes and marks A1 SUPERSEDED, premise invalid |
| New `mac-release-attributed-safe-2026-10-29` | `SHA256SUMS c05174f4…1f96`, 6 entries, OK; disk set = manifest |

## 2. A2 diff against my review §1–3 and the parent decision

| requirement | A2 code | result |
|---|---|---|
| 16 B lower not causal, diagnostic only | `candidate()` records `bracket16_lower_not_causal_recorded_only = e16−m16−q` and never uses it | ✓ |
| Safe lower = first fragment of the multi-fragment 4 KiB echo | `lo = e_first − m4096 − q` | ✓ |
| Upper uses both terms | `hi = min(e16−i16+q, e_first−i4096+q)` | ✓ |
| q = 1 applied before feasibility | `feasible = lo <= hi` on the padded values | ✓ |
| All bijections, unique-only, no cross-phone clock comparison | enumeration unchanged; 0 → `fail_closed_zero_feasible_mappings`, ≥ 2 → `fail_closed_ambiguous_mapping` | ✓ |
| Acceptance checks unchanged | `attributed_checks()` is not in the diff: global last exchange (own-link recorded only), exit + 30000, stop > DIS, stop ≥ attributed `hi` + exit + 30000 (hull-`hi` recorded) | ✓ |
| Premise guard: notify sequence `[20] + 8×512 + 36` | exact list equality | ✓ |
| Premise guard: every `notify_api` code 0 with its own bound callback code 0; counters complete/all-success | yes | ✓ |
| Premise guard: analyzer's first-fragment event is `notifies[1]`, with later fragments | yes | ✓ |
| Premise guard: `max−min(epoch−elapsed)` ≤ 1 ms over all burst events (every event must carry both stamps) | yes | ✓ |
| Hull is diagnostic only, never a fallback | `verdict = PASS` iff `status == unique_mapping` and no problems. Structural, no-trace, preconditions, premises, zero and ambiguous all FAIL with `m08_creditable` false. Hull fields renamed `*_diagnostic_only`. | ✓ |
| No 250 ms claim | `precision.all_within_instrument_target` is false. The note says it is not an RFC gate, not claimed, not a waiver. No 250 ms gate exists in `acceptance/otp23-matrix.json` (searched). | ✓ |
| Frozen-hull cross-check | kept (a mismatch with the frozen `release-bound.json` → FAIL) | ✓ |

**On fail-closed instead of hull fallback.** The parent chose this stricter policy explicitly. Given
the invalid premise, a hull PASS on a non-unique or premise-violating case is not independent proof.
Not demanding the old fallback is correct; it is not an over-restriction.

## 3. Tests, mutations and independent raw replay

**Tests (my runs):**
- targeted: `Ran 17 … OK`
- full suite (`python3 -B -m unittest discover -p "test_*.py"`): `Ran 511 … OK (skipped=5)`. That
  is 494 + 17; the 5 skips are the Windows-machine tests.

**My own mutations** (scratch copy; each against the 17 targeted tests):

| mutation | result |
|---|---|
| M5: re-add the 16 B lower bound into `lo` | **7 F** |
| M6: hull PASS fallback for ambiguous or preconditions | **2 F** |
| M8: drop the clock-spread guard | **1 F** |
| M10: drop the 4 KiB upper term | **4 F** |
| M11: own-link instead of global last | **1 F** |
| M12: no pad on `lo` | **7 F** |
| M9: drop only the per-notify bound-callback check | **survives** (0 F) |

**M9 is non-blocking.** That check is redundant with the tested counter check
(`notify_calls == api_success == callback_success == len(notifies)`, `unbound == 0`,
`pending == 0`) and with the hull's clean-telemetry gate. It is a test gap only; an optional single
test would close it.

**Independent raw replay.** My own parser, not the repo modules, read the frozen trace, transports and
stops. It used only `lo = e_first − m4096 − 1` and `hi = min(e16 − i16, e_first − i4096) + 1`, and
re-checked the premises on the raw events. Every value equals the safe output files:

| case / phone → handle | width | DIS_trace | margin vs global | 4 checks |
|---|---|---|---|---|
| shutdown 6T → e7b2 | 256 | [4143, 4399] | +84 | all true |
| shutdown 10Pro → 0535 | 258 | [4152, 4410] | +93 | all true |
| port-disconnect 6T → 5643 | 273 | [6036, 6309] | +85 | all true |
| port-disconnect 10Pro → c710 | 282 | [6005, 6287] | +54 | all true |
| tab-close 6T → 7185 | 270 | [5167, 5437] | +54 | all true |
| tab-close 10Pro → 0939 | 235 | [5173, 5408] | +60 | all true |

**Candidates.** All four candidate widths per case match:
- shutdown −230/256/258/504
- port-disconnect 273/533/−224/282
- tab-close −242/270/235/550

Exactly 1 of 2 bijections is feasible in each case. The outputs show:
- `attribution_status unique_mapping`
- verdict PASS and `m08_creditable` true
- `helper_self_sha256 ce75a8f1`, `imported_analyzer a49b3450`
- hull diagnostic PASS / FAIL / FAIL

**Premises hold on all 6 transports:**
- sequence `[20] + 8×512 + 36`
- 8 later fragments
- all codes and bound callbacks 0
- spread 1 ms

**CLI re-run.** I re-ran the A2 CLI with the recorded uniform command into scratch. The three outputs
are **identical** to the committed-candidate files apart from `engineered_utc`, `source_binding` and
the absolute `lifecycle_dir`. `derivation-record.json` binds HEAD `9c3ddc54…`, the dirty-diff sha
`2376ca99…`, and the untracked A2 hashes.

**Precision.** Widths are 235–282 ms, above the old 250 ms instrument target. That target is
explicitly not claimed, and it is not an RFC gate. Acceptance rests on the full-interval checks, all
retained and all passing (global margins +54…+93 ms).

## 4. M-11 account-class record (condition from my review §6.1)

**File:** `/tmp/opencode/otp23-m11-controller-20261029/account-class.txt`
- sha256 `a2bdc40c7d6119e01513ae3c3b9b71a287ad910d114ff172883043303050702d` ✓ (matches the parent
  decision)
- 372 B, mtime 11:01:19 UTC

**Content, read verbatim**, in the order of the requested command (`date -u`, `id -un`, `id -u`,
`id -Gn`, `dseditgroup`):
- `2026-10-29T11:01:19Z`
- `raiden`
- `501`
- a group list that includes `admin`
- `yes raiden is a member of admin`

**Binding.** uid 501 equals `chrome.context.uid 501` in attempts 2 and 3, and the user equals the
`/Users/raiden/…` paths in the captures.

**Label for curation: post-run, same account.**
- The record was taken 3 h 40 min after attempt 3 ended (07:21:19).
- It is operator-captured.
- Group membership is not a per-run variable.
- The file itself carries no label, so the implementer's new M-11 curation must state this label.
  That is not a raw edit.

**Account class: admin.**

**M-11 raws unchanged:**
- captures `9f9e33d2…` / `209fe71d…` / `f04c9045…`
- `owner-answer.json` `0bda401f…`

`OPERATOR.md` gained an "Attempt 3 completion" section (mtime 10:54). It explains the never-renamed
`.tmp` (the `test ! -e m11-capture.json` guard) and states the answers as external and unaccepted.
That is consistent with my review §4.

## 5. M-11 recommendation: scoped manual PASS

**Scope:** the unsigned (automatic linker ad-hoc) dev-registered host `c36894dc…`, with distinct
identity `me.nagaev.veles.bridge.dev` / Native Messaging `me.nagaev.veles.otp23.dev`, on the existing
**admin** account (uid 501).

**Measured facts:**
- Chrome launched it from a disposable profile (attempts 2 and 3).
- Authorization went `not_determined`, read from the host's `permission_pending`, to authorized,
  shown by `scan_state started` at 1.0 s.
- Compared with the signed `e7c716c5…`: `allowed_always` plus scan started.

**Caveats that must be carried verbatim into the adjudication record:**
1. **Attempt 1** is a setup FAIL (relative manifest path from the runbook's `--stage .`), retained.
2. **Deadlines:** attempts 2 and 3 ended by deadline (720.9 s and 3600.3 s). `answer: null` and the
   `problems` stay as captured. Attempt 2 stays `pending_at_host_budget_not_denial`.
3. **Late external owner answers, never accepted by the script.** Neither answer is bound to its
   capture after the fact.
   - The "VelesOtp23Dev"/Allow report was written 36 min after attempt 2 ended.
   - The attempt-3 "no dialog" report came 3 h 17 min after that run ended (unaccepted `.tmp`).
   - The VelesOtp23PoC "Still enabled, unchanged" report is a read-only Settings observation.
4. **No dev trace, no explicit authorization enum.** `post_init_authorization: null` means not
   traced. It is not an unknown state, and no enum is substituted. "Authorized" is inferred from the
   actual reply (PoweredOn is required for scan start).
5. **No discovery comparison:** no phone advertised. Discovery is not claimed.
6. **Script provenance is controller-attested.** `976f61cf…` is not self-recorded in the capture; the
   local stage copy matches.
7. **The dev TCC grant persists** on the account. No TCC action was taken, and the Settings entry was
   not inspected.
8. **M-03:** the original signed prompt owner is **unobserved**. The dev dialog name is not
   transferred.
9. **M-04:** the tied clause stays **unmet**. No reset or toggle was approved or done.
10. **Raw status:** nothing is marked PASS in the raw data. PASS comes from a separate manual
    adjudication only.

## 6. Non-blocking notes

- **M9 test gap** (§3): an optional single test would close it.
- **Future M-11-style runs** (from my review §6.3; not required now):
  - resolve `--stage` / `--out-dir` to absolute paths
  - `chmod +x package-dev.sh`
  - end the watch soon after `authorized_started`
  - self-record the script sha
- **Frozen analyzer docstring:** the 16 B sentence in `mac_release_bound.py` stays unedited, by
  design. It is corrected by the A2 docstring, `FINDING.md` and `source-history.json`.
