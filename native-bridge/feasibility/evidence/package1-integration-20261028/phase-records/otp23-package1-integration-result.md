# OTP-23 package1 integration phase — RESULT (2026-10-28; GLM-5.3-flash, 0 subagents, no commits/push)

Authorization applied: docs/curation + INTERIM W-11/W-12 integration only. W-02/W-07/W-08 canonical
promotions are HELD at BLOCKED until the parent obtains the v4 approval (the focused recheck request
hit `usage_limit_reached`). No hardware/phone/Chrome/Mac action; the owner's Chrome
update/restart remains declined ("Cannot restart Chrome now"; M-08 stays deferred). No commits,
pushes, or GitHub actions. Implementation model for every edit this phase: actual
ollama-cloud/glm-5.3-flash (this session), opencode-db safe metadata as recorded previously; 0
subagents.

## 1. Interim canonical state (exactly as directed)

`python3 scripts/matrix.py check` → `matrix ok: NO-GO`; `matrix.py decision` → NO-GO with 9 open
rows. Counts: **20 PASS / 9 BLOCKED / 3 waived (NOT RUN)** of 32 rows.

- **W-11, W-12 → PASS** (interim integration). Truthful additions recorded in each row:
  the 2026-10-28 latest-Chrome (156.0.8078.73) package1 strict runs
  (`package1-connected-2026-10-28/strict-windows-cold-2.json` / `strict-windows-prewarm.json`,
  driver PASS ST-01..ST-15, `formal_otp23_acceptance=false` retained), the foreground-gate precision
  correction (owned-pid visible+focused confirmed after poll 1 — `polls: 1`; NO synthetic input; the
  physical click was NOT instrumented; owner declared readiness), the ST-13 precision (actual
  `proc.args` 9 args, ZERO override-flag findings; argv bytes NOT retained verbatim; no
  "no flags anywhere" claim), phone validation PASS (union clock bound), honest residues (JUnit OK
  per hold; outlived-hold idle test processes remain running, app state untouched; HKCU registration
  present only between register/unregister; final reg query 0 veles keys), and the INDEPENDENT
  REVIEW labelled as **GPT** (`gpt-5.6-sol high`, session `ses_e7ef53f61ffesIPmRxLtwZ1efE`, report
  copy byte-identical at `package1-b5-v2-20261028/reports/otp23-windows-strict-gpt-review.md`).
  `account: admin` set (truthful: the existing administrator account, per the strict evidence);
  blocker cleared.
- **W-02, W-07, W-08 stay BLOCKED** — held pending v4 approval (steering holds promotion; the
  canonical rows, their historical actual/blocked text and raw `BLOCKED criterion_conflict` records
  are untouched).
- **M-08 truthful update** (stays BLOCKED): appended the 2026-10-28 record — the p2 Mac attempts +
  diagnostics (`package1-p2-20261028/p2m8-mac-logs/`, retained) show `Extensions.loadUnpacked`
  closing the tested Chrome sessions BEFORE any BLE work (zero peers; fixture `e7c716c5…` + grant
  untouched); the minimal invalid-manifest repro is INVALID INPUT; the valid real driver flow
  failed with cause UNPROVEN (not a proven universal defect, not a proven `.49` regression, update
  not proven to fix); OWNER DEFERRED the update/restart (record byte-preserved at
  `package1-b5-v4-20261028/mac-deferral-2026-10-28.json`, sha `ace5e31e…86e99`); the first
  standalone Mac SETUP capture was overwritten (`--overwrite`) → setup phase UNMEASURED (no BLE),
  never recreated; new attempts must use unique out-dirs. Blocker extended accordingly.
- **D-02** got the INTERIM INTEGRATION postscript: counts now 20/9/3; open rows W-02/W-07/W-08
  HELD pending v4 approval (usage_limit_reached, resets 2026-10-28T11:42:29Z) + W-10, M-03, M-04,
  M-08 (deferred), A-01, M-11; after v4 approval the expected final is 23/6/3; NO-GO and no OTP-24.
- No other row touched. Waiver rows stay exactly [W-01, M-01, M-02].

## 2. Authoritative owner amendment (RFC) — recorded NOW as directed

`docs/rfcs/2026-08-21-otp-external-devices.md` (`d1de4177be29690f23f9b2a68f06b364cac6d8d0274dbdcc7ca7f333013d3189`)
received the 2026-10-28 amendment in TWO places, following the established RFC pattern:
1. A Status-section amendment paragraph (after the 2026-10-27 pair) summarizing both owner decisions.
2. In the OTP-23 section, after the 2026-10-27 Background-criterion clarification: the
   **"W-02 window-criterion amendment"** paragraph with the EXACT owner-approved text (typographic
   fidelity: "The plain host owns no top-level window, visible or hidden, and no network endpoint.
   Chrome’s default Windows launcher may own one never-visible console window; any visible window or
   network endpoint owned by the host or its wrapper fails."), the question/answer
   ("Approve narrow clarification"), the owner-record sha `89d25538…23fd4`, operator-captured (not a
   GitHub approval), scope W-02 only, raw records stay byte-identical, verification still required,
   promotion only at integration; and the **"M-08 Mac deferral"** paragraph (exact answer, record
   sha, preserved attempts, unproven causes, `--overwrite` preservation gap).
`docs/otp-23-contract-alignment.md` (unchanged this phase: `0988841b…`) keeps the capture record
(section 18); the authoritative file is now amended.

## 3. Snapshot and checksum curation (honest publish mapping)

`package1-connected-2026-10-28/producer-pc-snapshot-20261028/` (236 captured files + `MANIFEST.json`):

- Original `MANIFEST.json` PRESERVED byte-identical (236/236 re-verified against the on-disk bytes;
  nothing missing, nothing mismatched; the only on-disk files beyond it are the new curation files).
- NEW `PUBLISH-MAP.md` (`c7c0b1bf…3923`) — honest mapping: `git check-ignore` probed every one of
  the 236 paths → **0 ignored**; no `__pycache__`/`target`/`node_modules`/`dist` or other cache
  exists in the snapshot (capture already excluded `__pycache__`; publisher re-verified); no raw
  measurement, launcher, listing or binary removed/rewritten; launchers/listings embed the run PC's
  local paths and account name and are published AS measurement records (no credential/secret/key
  material found); `host-binary/otp23-host.exe` (`8521de4d…ae33`, 726,528 bytes) is the intentionally
  frozen measured instance, tracked with exact size+hash and matching no ignore rule.
- NEW active `SHA256SUMS` (`f08a8ed6…86e5e`, **238/238 OK**) covering exactly the published tracked
  candidate bytes (236 + `MANIFEST.json` + `PUBLISH-MAP.md`; not self-listed); the parent-evidence
  `.gitattributes` (`* -text -whitespace`) makes export byte-identical.

## 4. Review/provenance curation (GPT labels; quota state; nothing archived)

- `package1-b5-v2-20261028/provenance/v4-review-attempt-quota-state.json`
  (`03a1454d…3923`): the v4 focused-recheck attempt failed with the safe quota facts ONLY —
  `APIError`, `usage_limit_reached`, HTTP 429, `isRetryable: true`, "The usage limit has been
  reached", resets 2026-10-28T11:42:29Z, report NOT created, reviewer session
  `ses_e7e49d2cbffepV653zHccm8N62` (the SAME authorized GPT review session; `gpt-5.6-sol`, high).
  NO error bodies, NO response headers, NO transcript/part content, NO cookies/auth state archived,
  and none may be quoted beyond the safe reason; consequence recorded: W-02/W-07/W-08 promotions
  HELD until the parent obtains the v4 approval.
- All review labels are truthful: the 2026-10-28 reviews (strict, P2/B5, fix verification, v3-final)
  are **GPT** (`openai/gpt-5.6-sol`); the only Opus roles are the earlier 2026-10-27
  review/preflight sessions (kept as different review-role counts); no report claims Opus authorship
  of GPT work. Report copies stay byte-identical (`cmp`-verified; unchanged this phase).
- v2 folder remains intact: 15/15 (the quota record is an unlisted addition;
  `provenance/` untouched otherwise). v3 9/9, v4 9/9 — preserved, NOT overwritten.

## 5. CI workflow test-selection wiring (the invariant tests now encode the interim truth)

`otp23-feasibility.yml` runs `test_scenario_matrix.py` and the other module test files directly —
the new B5 regressions are auto-selected (no wiring was needed there). The COMMITTED-MATRIX
invariants in `scripts/test_matrix.py` still encoded the pre-integration state and failed (6) after
the W-11/W-12 promotion; updated to the truthful interim state:

- `test_matrix.py` → `2ec15dafd7ffaa21431b796a07f08ab2d8dd0057d59055fceb719b6dd82686bd`
  (+ new `test_w11_w12_interim_integration_by_the_2026_10_28_latest_chrome_strict_runs` asserting:
  exactly W-11/W-12 PASS with `account: admin` + cleared blocker + the package1 strict evidence +
  the foreground/flags precision + the GPT review path existing + W-02/W-07/W-08 held BLOCKED +
  D-02 interim postscript with the quota note; and the remaining invariant tuples updated: PASS
  evidence bases now accept `package1-connected-2026-10-28/`, blocked tuples drop W-11/W-12,
  decision-row/subset assertions updated, the renamed
  `test_strict_background_history_and_mac_rows_under_the_clarification` keeps ALL retained-history
  assertions).
- `scripts/matrix.py` → `f4adbf488512118205b765ea91c0855f9531edbbe873221a25e6dae8df252c9a`: the
  rendered doc's Background-criterion paragraph no longer says "W-11/W-12 stay open" (stale
  claim); it records the 2026-10-28 promotion + the held rows. Re-rendered + checked.
- Rendered doc `docs/otp-23-requirements-matrix.md` →
  `73c0ffa453557810a150397336d4acdbc8d30176c3ef644f6506288cec018e14`; matrix json →
  `96098f9dd4ae7109230c47d526bab3f05a47dfb2aca000949ce179635efac612`.
- B5/v4 pins UNCHANGED (frozen): `scenario_matrix_core.py` `cb512418…`, `test_scenario_matrix.py`
  `307a7eb1…`, `package1_b5_v2_derive.py` `9cad1a70…` (self-pinning orchestration), plus the other
  four pinned scripts — the derivation record's 7-entry code list still verifies; any future change
  to a pinned core/analyzer file requires a FRESH derivation (v5+), never overwriting v2/v3/v4.

## 6. Full gates after the final edits (all exit codes observed, nothing masked)

- `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck --rerun-tasks` → exit 0
  (`gate3-spotless-rerun.log`).
- `./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` → exit 0, BUILD
  SUCCESSFUL (`gate3-gradle-full.log`).
- Both raw ci.yml android.util.Log guard checks → exit 0 (`gate3-logguards.log`).
- Full portable harness: `python3 -m unittest discover -s . -p 'test*.py'` → **456 tests OK, 5
  skipped** (was 455: +1 interim-integration invariant test).
- `python3 scripts/matrix.py render --write` + `check` → `matrix ok: NO-GO`; decision NO-GO, 9 open,
  waived exactly [W-01, M-01, M-02].
- `git diff --check` → exit 0.
- ALL checksum manifests verified unchanged or actively extended:
  post-grant 196/196, investigations 257/257, windows-cleanup 49/49, strict-mac 37/37,
  integration-2026-10-27 26/26, ci-portability 11/11 (frozen baselines UNCHANGED),
  package1-p2 34/34, package1-connected 181/181, v2 15/15, v3 9/9, v4 9/9,
  producer snapshot 238/238 (new active manifest; original MANIFEST.json preserved).

## 7. FINAL 3-ROW INTEGRATION CANDIDATE (outside the active canonical; NOT promoted)

Prepared, waiting ONLY on the parent's v4 approval (focused GPT recheck of the exact-branch
predicate + numeric_range boundary + self-pinned v4 provenance + W-02 wording):

- **W-02** — PASS via separate adjudication, candidate record
  `package1-b5-v4-20261028/w02-adjudication.json` (`c0d0b333…`): exact owner text (record
  `89d25538…`), six-sample census (zero host windows of all four kinds, zero host endpoints in all
  four families, exactly one never-visible `ConsoleWindowClass` wrapper window, no wrapper
  endpoints, positive controls present, session equality computed), raw `BLOCKED
  criterion_conflict` stays byte-identical, W-02-only scope; authoritative amendment already
  recorded in the RFC.
- **W-07** — PASS candidate on `phone-burst-association-v4.json` (`e430fc5c…`): P2
  `p2pd1-portdisconnect-20261028T0651Z` 6T **+2322 ms** `[629, 4015]`, 10Pro **+2406 ms**
  `[1482, 3330]` vs own exit `1793170763088`; conservative interval wholly after exit, below the
  30 s bound; lifecycle stdin_closed/exit 0/releases 2-3/join 2/2/process gone (raw trace pinned).
- **W-08** — PASS candidate on the same Chrome-provenance event (stdin EOF without shutdown
  request) + `tab-close-phone-bound-v4.json` (`ec1294c4…`): old `run-tab-close-2` corrected 6T
  **+2304 ms** `[819, 3789]` / 10Pro **+2310 ms** `[1388, 3232]`; the old 6T standalone DIS row
  stays documented-not-proof.
- Promotion mechanics at approval time: set the three rows PASS with their candidate actual text
  + evidence (v4 artifact paths + adjudication), D-02 postscript to the final 23/6/3 counts, exact
  owner amendment already in the RFC (no further doc edit needed beyond integration notes).

## 8. Exact remaining tasks (for the parent)

1. `usage_limit_reached` resets **2026-10-28T11:42:29Z** — obtain the focused v4 approval
   (gpt-5.6-sol, 0 subagents; safe-quota state recorded; review NOT produced yet).
2. On approval: promote W-02/W-07/W-08 per section 7 (23 PASS / 6 BLOCKED / 3 waived final),
   re-render/re-check the matrix, and add the integration note; the authoritative RFC amendment is
   already recorded (section 2) — only the integration note belongs to that step.
3. Remaining rows for OTP-23 completion: W-10, A-01, M-03, M-04, M-11 (+ M-08 owner-deferred),
   each needing its own later decision/permission work.
4. NO-GO stands; OTP-24 unauthorized. Then the parent's final tree review → commit/push (this
   session performed none).
5. M-08: nothing actionable until a NEW owner decision (update/restart declined; no Mac action
   authorized).

Files changed this phase (all uncommitted): `docs/rfcs/2026-08-21-otp-external-devices.md`,
`docs/otp-23-requirements-matrix.md`, `native-bridge/feasibility/acceptance/otp23-matrix.json`,
`native-bridge/feasibility/scripts/matrix.py`, `native-bridge/feasibility/scripts/test_matrix.py`,
`…/package1-connected-2026-10-28/producer-pc-snapshot-20261028/{SHA256SUMS,PUBLISH-MAP.md}`,
`…/package1-b5-v2-20261028/provenance/v4-review-attempt-quota-state.json`. NO raw measurement file
was removed or changed; P2/v2/v3/v4 hashes preserved exactly.