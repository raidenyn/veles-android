<!-- PUBLIC REDACTED COPY: names of unrelated applications' Native Messaging manifests on the owner's account replaced by a placeholder; the private original is kept outside the repository (see ../curation-map.json). Otherwise byte-identical. -->
# OTP-23 Mac M-08 code verification — independent reviewer: Kimi K3 (2026-10-28)

## Identity and scope

- **Role:** independent code verification reviewer, READ-ONLY. No source written by me; no
  repo edits, no subagents, no commits/push/GitHub, no fixture/Chrome/registration/TCC/build
  work. Reviewer selection: owner record
  `/tmp/opencode/otp23-mac-kimi-reviewer-approval.json` (`"Use kimi k3"`,
  `authorized_reviewer_model: ollama-cloud/kimi-k3`; Opus code verification was 429-blocked and
  produced no report; Opus design-preflight authorship retained).
- **Model metadata (safe fields only, not API headers/message bodies):** provider
  `ollama-cloud`, model `kimi-k3`, session role: independent read-only code verifier;
  implementation session was ollama-cloud/glm-5.3-flash (distinct session, no overlap).
- **Files written:** only this report + scratch under `/tmp/opencode/otp23-m08-kimi-review/`.
- **Inputs read in full:** `otp23-mac-m08-code-result.md` (GLM code result),
  `otp23-mac-m08-preflight-opus.md` (Opus preflight), `otp23-mac-m08-parent-preflight-decision.json`
  (parent P0-A), `otp23-mac-m11-preparation.md`, `otp23-mac-permission-owner-record.json`, the full
  actual git diff, all four new/changed scripts + their tests, the retained evidence dirs, the M-11
  private stage scripts (local `/tmp/opencode/otp23-m11-prep/` AND staged on the Mac).
- **Mac contact was read-only SSH only** (`id_sirius_ed25519`, Mac awake, lease held by the
  parent): `shasum -a 256` on the M-11 staged files + fixture binary + raw counterpart,
  `codesign --verify --strict` (read-only), `ls` of the user-scope NativeMessagingHosts dir, and a
  24-file source hash comparison vs the repo's pinned `cd971f0`. One scratch file I scp'd to the
  Mac `/tmp` for the comparison was immediately removed. Nothing else touched.

## M-08 VERDICT: NOT approved as-is — ONE concrete blocking bug found; minimum fix below.

Everything else in the code phase checks out (evidence itemized in §3). The single blocking defect
makes the retake's own acceptance evidence self-label as non-creditable; it must be fixed before
any hardware run. After the P1 fix + suite re-run, the code is approved for (1) the Opus check-2
listener self-test (read-only, no Chrome), then (2) the three fresh Chrome-owned retake scenarios.

## 1. Finding F-1 (BLOCKER, must fix before any retake) — creditability gate reads the wrong JSON level

**`native-bridge/feasibility/scripts/mac_release_bound.py:147-148`:**

```python
detail = scenario_doc.get("detail", {}) or {}
target_teardown = scenario_doc.get("status") == "PASS" and "trace_exit" in detail
```

The real driver does NOT put `trace_exit` at the top level of `detail` for Chrome scenarios.
`scenario_matrix.scenario_chrome` stores it as `detail["teardown"] = verify_gone(...)`, and
`verify_gone` (scenario_matrix.py:460) puts `trace_exit` inside that returned dict. Verified against
a real PASS artifact
(`evidence/post-grant-2026-10-26/.../windows-lifecycle-scan-active/shutdown.json`): `status: PASS`,
`trace_exit` present ONLY nested under `detail.teardown` (keys: `owned_gone_after_s`,
`trace_mechanism`, `trace_exit`, `host_output_order`, `trace_released_count`, `problems`).

**Impact:** for every fresh, genuinely-passing M-08 Chrome scenario the analyzer would output
`target_teardown_scenario: false` → `creditability: "diagnostic_only_not_creditable"` — i.e. the
retake's phone-side release proofs (the REQUIRED P0-A exchange) could never be credited as target
teardown evidence. Reproduced end-to-end (scratch `/tmp/opencode/otp23-m08-kimi-review/repro/`):
a nested-shape `{"status":"PASS","detail":{"teardown":{"trace_exit":…}}}` scenario JSON with valid
synthetic transports/stops → `verdict: PASS` but `creditability: diagnostic_only_not_creditable`.

**Why the tests did not catch it:** the hand-computed fixture
(`test_mac_release_bound.py:write_case`) writes `{"detail": {"trace_exit": …}}` at the TOP level —
a shape the Chrome driver never produces (a direct-mirror of the analyzer's assumption, not the
actual data shape), and the retained replay only asserts `False` (correct for ABORTED runs, but
passing for the wrong reason here).

**Minimum fix (one line + test):**

```python
teardown = detail.get("teardown") or {}
target_teardown = scenario_doc.get("status") == "PASS" and "trace_exit" in teardown
```

and change `test_mac_release_bound.py` `write_case()` to the REAL nested shape
(`{"status":"PASS","detail":{"teardown":{"trace_exit":{"at_ms":3667,"code":0}}}}`), so the existing
`test_hand_computed_intersection_interval_and_release_checks` assertion
(`creditability == "target_evidence"`) becomes the regression test for it. Re-run
`test_mac_release_bound.py` + `test_mac_release_gate.py` + `test_macos_poc.py` +
`test_scenario_matrix.py` + full discover; re-pin the two new file hashes in the inventory.

## 2. Non-blocking findings (M-08 phase)

- **F-2 (recommended, fold into the same edit — diagnosability only):**
  `mac_release_bound.py:213-214` propagates `anchor_problems` only inside the `if dis_lo is not
  None …` branch; when the anchor intersection is EMPTY (`lo > hi`, e.g. a wrong burst pairing) the
  specific message `origin intersection empty: 16B [..] ∩ 4096B [..]` is dropped and the generic
  "cannot map the release interval" is all that lands in `document["problems"]` (verdict still
  correctly FAIL and the brackets survive in the phone record — fail-closed). Move the two lines one
  level out so the precise reason is always propagated. No verdict change.
- **F-3 (documentation error, not code):** `otp23-mac-m08-code-result.md` §6 quotes the raw
  FINDING.md pin as `9a7ffbab…`; the actual pin everywhere is
  `2518c1637fb20ff765c5df88579ecbbd23007f2d0077b6468ff81dd5fd63e5d1` — verified identical in the
  32-entry `SHA256SUMS`, in `curation-map.json`, and by my own `sha256sum`. Fix the prose.
- **F-4 (housekeeping):** an empty 0-byte `SHA256SUMS.tmp` leftover sits in
  `evidence/mac-m08-codephase-2026-10-28/` (not covered by its SHA256SUMS). Remove before any
  commit. The pinned artifact itself (`dry-probe-lsof-classifier.txt`, `beaffc58…`) is intact and
  the probe output matches the claims.
- **F-5 (observation, by design):** `uncertainty = half-width + 1 ms` is recorded but the declared
  retake acceptance criterion "anchor intersection width ≤ 250 ms" (Opus check 6 / retake §8.4) is
  adjudicator-side, not enforced by the analyzer; the full interval is always used conservatively in
  the release checks, so a wide-but-honest anchor cannot fake a PASS. Optional improvement: emit a
  machine-visible `anchor_width_le_250ms` field. Not required.
- **F-6 (nit):** `_lsof_diag_record` hardcodes `"argv": ["lsof","-nP","-a","-p",str(pid),"-i"]`
  instead of echoing the argv actually used (`macos_poc.py` diag path). Currently identical; if the
  actual argv ever changed the record would silently lie. Optional.
- **F-7 (nit):** `foreign_nullfs_mounts()` parses `mounted by` greedily to end-of-options; on
  macOS the ownership clause is last in the fs-specific options (verified against the real captured
  mount line), so no live issue; `mounted-by-not-last` format drift could misclassify a self-owned
  mount as foreign. The pinned mount-snapshot hash makes any such case auditable. Optional comment.

## 3. Verified positives (all independently re-checked, not trusted from the result doc)

1. **Repository state:** HEAD `9c3ddc54a3721eced84e49f02ed76ef156f0baed`, no commits, no stash;
   `git status` = exactly 4 modified + 4 new scripts + 2 evidence dirs. Nothing else touched.
2. **Hashes:** every claimed sha256 in the result-doc inventory matches disk exactly
   (`macos_poc.py 3d2eccae…`, `scenario_matrix.py f8bbf2d6…`, `test_macos_poc.py a729dcce…`,
   `mac_release_bound.py 7e779e97…`, `mac_clock_corroborate.py 50e2fe2c…`,
   `test_mac_release_gate.py 7f61cdee…`, `test_mac_release_bound.py a66e5ca4…`, workflow
   `081924dc…`).
3. **P0-A gate (`scenario_matrix.py`):** diff is exactly 1 new pure `release_gate(platform)` +
   5 callsites (`:604 shutdown`, `:608 port-disconnect`, `:612 tab-close`, `:626 failure-isolation
   shutdown`, `:811 direct stdin-EOF`); `grep "min_released"` shows no ungated `min_released=2`
   callsite (the one remaining `2` is docstring prose); `require_join=…=="windows"` untouched;
   `MacAdapter.hygiene()` untouched. Windows behaviour byte-identical in effect
   (`release_gate("windows")==2`). Frozen `scenario_matrix_core.check_teardown` already supports
   `min_released=None` (read at :322-365) and is byte-unmodified. `verify_gone` keeps recording
   `trace_released_count` honestly.
4. **Adapter-path unit test is real** (not a function mirror): `test_mac_release_gate.py` reads
   `WindowsAdapter.platform` and constructs `MacAdapter()` and asserts the gate through those exact
   attributes; plus unknown-platform → None and a frozen-core pairing (Mac gate tolerates 0 release
   lines, Windows shape still rejects them).
5. **lsof classifier (`macos_poc.py`):** diff hunks confined to the lsof area; `grep '"-w"'` over
   `macos_poc.py` + `scenario_matrix.py` is EMPTY; argv unchanged; hygiene probe and both positive
   controls (`chrome_visible_top_level`, `chrome_cdp_listener_present`) untouched; I-1 semantics
   preserved (parse failure → None; `[]` only for a genuinely successful query); benign acceptance
   requires the env-set diagnostics write (fail-closed otherwise); classifier is exact-3-line-blocks
   and foreign-mount-membership only. The diagnostics record carries VERBATIM stderr + sanitized
   mount snapshot (mount points verbatim, source paths dropped, owner names `uid=self/uid≠self`),
   snapshot hash pinned per line.
6. **Unit tests (`test_macos_poc.py`):** the full Opus Task-1 case list is present and assertive
   (verbatim-stderr payload, no `-w` argv, snapshot redaction proven by content checks, rc=1 honest
   empty, two-block acceptance, missing-dev/mount-not-in-set/self-owned/extra-line/non-nullfs/rc-2/
   env-unset/write-failure all → None, clean-stderr recorded as `clean_stderr`, mount unmeasurable
   → None). I re-ran everything myself:
   `test_macos_poc.py` 58 OK (46 historical + 12 new); `test_scenario_matrix.py` 136 OK, 5 skipped
   (the Windows-only skips); `test_mac_release_gate.py` 4 OK; `test_mac_release_bound.py` 9 OK;
   full `unittest discover` = **484 tests OK, 5 skipped, 0 failures** (vs v4-era 455+5 baseline →
   +29 new, exactly the claimed delta). `matrix.py check` → `matrix ok: NO-GO` (23 PASS / 6 BLOCKED
   / 3 waived). `py_compile` on all 7 changed/new python files PASS. Workflow diff = exactly 2 added
   run lines in the portable block (indentation consistent).
7. **v4 pins intact:** HEAD `scenario_matrix.py` bytes == pin `f8284c98…`; the other six pins
   (`package1_b5_v2_derive.py 9cad1a70…`, `scenario_matrix_core.py cb512418…`,
   `package1_phone_burst_telemetry.py c6fcfd6e…`, `package1_phone_bound.py 40d8f824…`,
   `pc_phone_clock_probe.py 06f0de1b…`, `chrome_validate.py 78d53ca9…`) all verified `live == HEAD`.
   No v4 artifact rewritten; the documentation that future v4 re-derivation must run at `9c3ddc5`
   bytes is present in the `release_gate` docstring and result doc.
8. **`mac_release_bound.py` design vs spec:** in-band causal anchors implemented as specified —
   16-byte proof pairs (`record_base64.len=24`) and 4096-byte probe pairs (`.len=5464`,
   `driver.LINK_PROOF_BYTES`/`core.SIZE` shapes = 16/4096 verified), conservative envelopes
   `[e − max(t_msg), e − min(t_in)]` per size, intersection of both sizes (empty ⇒ FAIL),
   `offset = midpoint`, `uncertainty = half-width + 1`, full interval recorded, DIS mapped with the
   full interval, `DIS_lo ≥ t_last_exchange_message`, `DIS_hi ≤ t_exit + declared 30 s`, fixture-stop
   guard `stop_min > DIS_phone` AND `stop_min ≥ origin_hi + t_exit + bound`, telemetry proof
   (`final/final_after_service_stopped/clean/!truncated/peers_dropped=0`, exactly one peer, one
   burst, one run id + one peer prefix across both phones), all input pins + helper self-sha256
   (verified `SELF_SHA` == file sha), `absolute_mac_anchor: none` asserted. Hand-computed test
   arithmetic re-derived by me and matches ([B−2318,B−2199] width 119 u 60.5; DIS [3672,3791] /
   [3499,3618]). Ambiguous-burst, missing-notify, stop-confound-early, stop-before-DIS, missing
   stop-markers cases all FAIL, corroboration labelled not-acceptance-proof, and the retained
   ABORTED lifecycle-1 replay runs end-to-end with narrow intersections (the 250 ms assertion) and
   — for those ABORTED runs — the correct `diagnostic_only_not_creditable` label (this part passes
   for the wrong reason per F-1; still correct output for that data).
9. **`mac_clock_corroborate.py`:** Mac clock read in ms via ssh python3 (no whole-second Mac
   truncation bias), phone via `date +%s%3N`, ms stamps need no centring (whole-seconds fallback
   applies `s*1000+500`), raw `started`/`finished` (seconds + ms) recorded per sample, self-sha256
   embedded, role `corroboration_only_not_acceptance_proof` + explicit statement it must never carry
   the release bound, conservative sum uncertainty. Nits (non-blocking, corroboration role only):
   the docstring claims "+500 ± 500" centring for a whole-seconds fallback but `summarise()` does
   not add the 500 ms truncation term to `uncertainty_ms` — if the fallback ever fires the recorded
   uncertainty understates by 500 ms (the acceptance anchor never uses this helper); and the ssh key
   path is hardcoded to this Linux box (fine for the planned retake wiring, worth a comment).
10. **Curation / publication honesty:** raw `mac-resume-2026-10-28/` + 32-entry SHA256SUMS →
    32/32 OK, byte-unchanged (FINDING.md == `2518c163…`). Public copy `FINDING.public.md`
    (`821ce855…`): the other user's account name and the foreign app name are gone (verified by grep
    against the raw), the mount point + `uid≠self` fact kept; additions labelled. `curation-map.json`
    (`50831d4d…`) documents the mapping — **but note honestly: the map's `original_text` field
    itself contains the private names**, so `FINDING.md` **and** `curation-map.json` must both stay
    out of any publication set; only `FINDING.public.md` (+ the rest of the raw dir, which grep
    shows clean of the names) is publication-eligible. Codephase artifact dir self-consistent (see
    F-4). No secrets or full private API logs anywhere I was asked to review.

## 4. Conditions for running the physical retake (after the P1 fix lands)

1. Apply F-1 (+F-2 recommended); re-run the 5 suites locally (expected: still green, 0 failures);
   update the hash inventory.
2. **Opus check-2 listener self-test first (read-only, no Chrome):** on the Mac, with
   `VELES_OTP23_LSOF_DIAG` set, `lsof_endpoints` on a Python child holding a `127.0.0.1:0` listener
   must return that port; the diagnostics JSONL must hold the verbatim warning classified benign;
   env unset → None. Then the three fresh Chrome-owned scenarios exactly per result-doc §8 (unique
   dirs, per-scenario run id on both phones,
   `VELES_OTP23_LSOF_DIAG=<attempt>/lsof-diagnostics.jsonl`, `VELES_OTP23_DETACHED_CLOSE` unset,
   failure-isolation excluded, direct stdin-EOF stays BLOCKED-retained).
3. Acceptance criteria unchanged: 6/6 usable samples with both positive controls every sample,
   `all_layers=0`, `tcp4=[]`, diagnostics line count == invocations all benign-or-none, teardown
   per mechanism, gone ≤ 10 s, and `mac_release_bound.py` per-phone PASS (anchor ≤ 250 ms, single
   burst, both bounds, stop guard) on a run whose (fixed) creditability reads `target_evidence`.
4. No commit/matrix-row/OTP-24 action from the retake session; rows move only through the parent
   after independent review.

## 5. SEPARATE VERDICT — M-11 plan (execution is for LATER, post-retake): CONDITIONAL

Approved for later execution **after one concrete plan fix and one sequencing condition**. All other
M-11 constraints were verified met:

- **Owner authorization verified:** `otp23-mac-permission-owner-record.json` — verbatim "Approve
  separate dev test", `m11_approved: true`, scope = separate developer host + distinct identity +
  disposable profile, automatic ad-hoc signature recorded, `m04_permission_toggles_authorized:
  false` (honored: no toggles anywhere in the plan; the read-only "VelesOtp23PoC" Settings label is
  correctly treated as NOT original-prompt-owner proof and M-03 stays undecided),
  `not_a_tcc_reset_or_existing_identity_change_approval: true`.
- **Staging verified read-only ON THE MAC today:** all 7 staged file hashes match the prep table
  exactly (`package-dev.sh fa6ba4e8…`, `m11_dev_capture.py 833a0f52…`, extension `6cc440fe…`/
  `2d8a048a…`/`c1c33331…`/`d43f1b59…` — byte-identical to repo HEAD, zero extension delta);
  approved fixture binary `e7c716c5…` + `codesign --verify --strict` OK; raw unsigned counterpart
  `c36894dc…` present; user-scope `NativeMessagingHosts/` contains NO veles manifest (only
  <other applications' manifests>); `target-dev/` absent (packager correctly not run).
- **Source identity verified:** all 21 files under `cd971f0 src/**` + `Cargo.toml c92e382f…` +
  `build.rs 15b0b79f…` + `Cargo.lock e50ce3bd…` on the Mac byte-match the repo's pinned `cd971f0`
  tree (computed here, compared over SSH; prose nit: the prep says "24 files under src/**" — actual
  count is 21; harmless).
- **No-signing invariant verified:** `package-dev.sh` contains no codesign invocation at all (only
  read-only `codesign -dvv` recording), pins the raw sha `c36894dc…`, refuses overwrite;
  `m11_dev_capture.py` uses `codesign` read-only (`-dvv`, `--verify --strict`), gates on approved
  fixture identity drift before and after, writes only inside its own `--out-dir` + the disposable
  profile, never auto-clicks (the nonce `PROMPT-WATCH-REQUESTED` → owner reads the EXACT prompt name
  FIRST, then Allow/Deny is bound to the nonce in the answer file; a Deny/pending is a valid
  recorded observation, never rewritten into a pass).

**M-11 FIX REQUIRED (F-M11-1): the profile-local manifest default is unsupported on macOS.**
Chrome discovers native-messaging manifests on macOS ONLY in `/Library/Google/Chrome/
NativeMessagingHosts/` (system) and `~/Library/Application Support/Google/Chrome/
NativeMessagingHosts/` (user) — verified against the current Chrome native-messaging documentation
today; there is no profile-dir (`--user-data-dir`) location. The plan's LAYER A default
("the disposable Chrome profile carries its own `<profile>/NativeMessagingHosts/…dev.json` —
the same verified mechanism the M-08 driver used") misstates the evidence: every working M-08 macOS
run had the USER-SCOPE `me.nagaev.veles.otp23.plain.json` present (verified in
`evidence/mac-resume-2026-10-28/registration/record.json`; the frozen driver's own
`check_registration` requires it), and the only "profile-local works" statement in the project is a
**mocked-Popen** unit (`test_macos_poc.py:656` class) plus a driver comment — Chrome's actual
discovery was never measured. If M-11 runs profile-local-only, `connectNative` will fail with
"Specified native messaging host not found": fail-closed, but no host launch, no TCC dialog, and no
M-11 observation. **Minimum fix:** run with the opt-in `--user-scope-manifest` layer (or flip it to
the default/only layer): the distinct filename `me.nagaev.veles.otp23.dev.json` cannot collide with
`…plain.json` (and the dir currently holds no veles file at all), removal is byte-verified own-file
only, and the disposable profile still isolates browser state. Update the runbook commands
(prep README §5/prep doc §6) to include the flag and correct the "same verified mechanism" claims in
`m11_dev_capture.py` docstring and `README-M11-PREP.md §3`. The profile-local copy may stay written
(harmless) but must not be the relied-upon layer.

**M-11 SEQUENCING CONDITION (grant-intactness proof):** sha + `codesign --verify --strict`
before/after is identity proof, not grant proof. Execute M-11 only AFTER the three fresh signed
M-08 retake scenarios (their live authorized scans with the approved fixture are the real
baseline), and keep the plan's existing owner read-only Settings re-check after the dev run
(`VelesOtp23PoC` entry unchanged; a new `VelesOtp23Dev` entry = recorded fact, never removed).
That sequencing is already consistent with the prep plan; it is now a stated condition.

## 6. Non-goals honored

No repo edits, no commits/push, no subagents, no device/Chrome/fixture/TCC/registration/build
actions; matrix untouched; the v4 scenario pin was NOT rewritten and no fresh Windows derivation was
demanded; the historical pin stays the source at `9c3ddc5`, with the intentional Mac-only evolution
recorded by the implementation.
