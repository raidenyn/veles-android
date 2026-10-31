# OTP-23 Mac M-08 — code phase result (2026-10-28, operator: ollama-cloud/glm-5.3-flash, 0 subagents, no model substitution)

Scope honored: CODE-PHASE-ONLY before the physical retake. Inputs read in full:
`/tmp/opencode/otp23-mac-m08-preflight-opus.md` (Opus preflight) and
`/tmp/opencode/otp23-mac-m08-parent-preflight-decision.json` (parent P0-A). No device work, no
Mac access, no Chrome/fixture/registration/TCC/build action; leases already ended. HEAD stays
`9c3ddc54a3721eced84e49f02ed76ef156f0baed` (contract/otp23-desktop-alignment); NOTHING committed,
pushed or promoted; matrix unchanged (23 PASS / 6 BLOCKED / 3 waived — `matrix.py check` OK: NO-GO;
OTP-24 not authorized).

## 1. P0-A implemented — Windows-only release gate (precise source gate)

Changed ONLY in `native-bridge/feasibility/scripts/scenario_matrix.py` (new sha256
`f8bbf2d6ea158d2bd30bee35b97a655c727a7ca7fc677a5c9a3a53eafe6b7f28`, diff = 1 new pure function +
5 call-site expressions, verified hunk-by-hunk):

- NEW at `scenario_matrix.py:439`: `release_gate(platform)` → returns `2` ONLY for
  `platform == "windows"`, else `None`. Docstring records the mismatch (the approved macOS fixture
  `e7c716c5…` exits before awaited `release_device`, so Mac traces never contain
  `conn N released`), that the Mac release count stays recorded honestly (`trace_released_count`,
  typically 0), and that every macOS acceptance scenario needs independent phone-side release
  proof (`scripts/mac_release_bound.py`).
- Callsites now `min_released=release_gate(adapter.platform)`: `shutdown` (:604, :626),
  `port-disconnect` (:608), `tab-close` (:612), direct stdin-EOF check (:810–811).
  `require_join=adapter.platform == "windows"` UNCHANGED at every site; Windows behaviour
  byte-for-byte identical (gates 2 + bounded join). `scenario_matrix_core.check_teardown` (frozen)
  already supports `min_released=None` and was NOT touched.

**v4 pin replay, documented as required:** the seven-entry v4 pin map stays valid AT HEAD
`9c3ddc5`. Verified against HEAD bytes today: `scenario_matrix.py` pin
`f8284c98b9dc200bf8ec83e09b4d040643a725e4ca637d5854b09494fc31ffc1` == HEAD bytes (the pin remains
the valid source at `9c3ddc5`); the OTHER six pins (`package1_b5_v2_derive.py`
`9cad1a70…`, `scenario_matrix_core.py` `cb512418…`, `package1_phone_burst_telemetry.py`
`c6fcfd6e…`, `package1_phone_bound.py` `40d8f824…`, `pc_phone_clock_probe.py` `06f0de1b…`,
`chrome_validate.py` `78d53ca9…`) are all still `live == HEAD`. The live `scenario_matrix.py`
hash changes with this authorized fix; any future v4 re-derivation MUST run at the v4-era bytes
(commit `9c3ddc5`), not at the live tree. No v4 artifact was rewritten; nothing claims the live
hash equals the pin.

## 2. lsof classifier (Opus Task 1 design, parent-approved; `-w` rejected)

`native-bridge/feasibility/scripts/macos_poc.py` (new sha256
`3d2eccaee8f02c9bc206300463e4b95f76665e2b9acb495d350019056c69fee2`; hunks only in the lsof area):

- `classify_lsof_stderr(stderr, foreign_nullfs_mounts)` — stderr acceptable ONLY when it consists
  ENTIRELY of one or more EXACT 3-line blocks (`lsof: WARNING: can't stat() nullfs file system
  <point>` / indented `Output information may be incomplete.` / indented
  `assuming "dev=<hex>" from mount table`), each block's mount point a member of the
  foreign-nullfs set. Anything else (extra line, missing `assuming dev` line, non-nullfs type,
  self-owned mount, unknown text) → not benign → the census stays `unavailable` (None), never zero.
- `foreign_nullfs_mounts()` — recovered from the MEASURED mount table ONLY (`/sbin/mount`, parsed
  `(nullfs, …, mounted by <name>)` lines with `<name>` ≠ the current user's passwd name), cached
  once per process; mount failure/unparsable → empty set so the warning stays unavailable.
- `lsof_endpoints()` — argv UNCHANGED (`["lsof", "-nP", "-a", "-p", <pid>, "-i"]`; grep proves no
  `"-w"` anywhere), all I-1 rules kept (rc ∈ {0,1}; parse failure → None; `[]` only for a genuinely
  successful query), and "any stderr → None" replaced by "stderr empty, OR classified benign AND
  the mandatory diagnostics record was written". Diagnostics: env
  `VELES_OTP23_LSOF_DIAG=<run-dir>/lsof-diagnostics.jsonl` → one JSON line per invocation
  `{wall_ms, pid, argv, rc, stdout_rows, stderr_verbatim, classification, mount_snapshot_sha256}` +
  the sanitized nullfs mount snapshot written ONCE to `<run-dir>/mount-nullfs.txt` (mount POINTS
  verbatim; source paths dropped entirely — they carry other users' home dirs/app names; owner
  names redacted as `uid=self` / `uid≠self`; the snapshot hash is pinned in every diagnostics
  line). Fail-closed: env unset, any snapshot/diagnostics write failure → None (a benign warning
  is NEVER accepted unaudited). Captured diagnostics hold the warning verbatim INCLUDING the mount
  point path (which itself carries no account/app names).
- No change to `hygiene_probe` / `summarise_probe` / any Chrome control: identical mandatory
  positive controls (`chrome_visible_top_level ≥ 1`, `chrome_cdp_listener_present`) per sample.
- `MacAdapter.hygiene()` (ready-time, `scenario_matrix.py`) stays as the recorded residual
  (frozen otherwise; untouched — parent authorized only the release gate there).

Unit tests added to `native-bridge/feasibility/scripts/test_macos_poc.py` (new sha256
`a729dcceb71c2bf8123c2529b59db37f8ede0bf5f9af170bd3b9f2f4f7d3fb3a`) — the full Opus case list,
portable with mocked `subprocess`: exact block + rows → rows (and the record holds the VERBATIM
stderr + normal argv + snapshot hash, snapshot carries no foreign account/app names); exact block
rc=1 no rows → `[]`; two exact blocks for two foreign mounts → rows; missing `assuming dev` line →
None; path not in the foreign set → None; self-owned mount → None; any extra stderr line → None;
non-nullfs type → None; rc 2 with the block → None; benign block with env unset → None;
diagnostics write failure → None; clean stderr with env set recorded as `clean_stderr`; mount
table unmeasurable → None; historical I-1 cases unchanged and green.

**Linux dry probe (Opus acceptance check 1):**
`evidence/mac-m08-codephase-2026-10-28/dry-probe-lsof-classifier.txt` (sha256
`beaffc589f27413f53b8c726bb70947ad067ae89bed0bce0c80f8b0c5e3dae44`; mocked `/sbin/mount` + lsof,
both fakes recorded in the artifact): the verbatim preflight block classifies benign ONLY with the
foreign mount point recovered there, the census passes only WITH the written diagnostics record,
stays None without the env, and no `-w` exists. The foreign set in the artifact is the preflight's
recorded Mac mount-table fact — explicitly labelled; it is re-measured on the Mac at retake time.

## 3. Release-proof analyzer `scripts/mac_release_bound.py` (new; sha256
`7e779e97834d6b3e153200deec951b505ab37a915d1fc31b9082027ce40234f5`)

Per the reviewed Task 3 spec, on the M-08 retake scenarios (shutdown / port-disconnect / tab-close;
failure-isolation explicitly out of the retake scope):

- Pairs each scenario with its telemetry burst (exactly ONE connect→disconnected pair of the ONE
  peer = the Mac central; two bursts or zero peers → ambiguous FAIL).
- In-band causal anchors per phone: 16-byte proofs (`in/out type=send|message record_base64.len=24`)
  and the 4096-byte probes (`.len=5464`, driver shapes reused from the pinned files
  `driver.LINK_PROOF_BYTES` / `core.SIZE`); per-size conservative ENVELOPE
  `[e − max(t_msg), e − min(t_in)]` (no handle-to-phone mapping needed, never narrower than truth),
  then the INTERSECTION of the 16-byte and 4096-byte envelopes; empty ⇒ FAIL.
- `offset_phone_minus_trace` = interval midpoint, `uncertainty = half-width + 1 ms`, FULL interval
  recorded; release interval `DIS_trace = DIS_phone − origin` mapped with the full interval; checks
  `DIS_trace_lo ≥ t_last_exchange_message` and `DIS_trace_hi ≤ t_exit + declared bound (30 s
  pre-declared)`.
- Fixture-stop guard on the phone clock: stop bracket's earliest stamp > `DIS_phone` AND
  ≥ `origin_hi + (t_exit + bound)` — a stop-induced release cannot count.
- Telemetry proof: `final / final_after_service_stopped / clean / !truncated / peers_dropped=0`,
  exactly one peer, same run id and same peer prefix across BOTH phones (`samepeer`).
- Pins: trace, scenario JSON, transports, stop markers, corroboration file — all sha256 recorded in
  the output; the helper embeds its OWN runtime self-sha256; `no absolute Mac anchor` asserted in
  the document; the retained ±16-17 s raw clock JSON is referenced as CORROBORATION ONLY.
- `target_teardown_scenario` / `creditability=diagnostic_only_not_creditable` for any replay of
  ABORTED runs — the retained lifecycle-1 cleanup exits can never count as teardown PASS.

Tests `scripts/test_mac_release_bound.py` (new; sha256
`a66e5ca4a620214d8a120a6ec1beba78e172c40c0cd8b08f8f10f57d45f2425c`) — targeted, hand-computed, NO
numeric-schema fuzz: exact intersection interval/uncertainty/DIS-mapping arithmetic for two phones
(hand values `B−2318/B−2199` width 119 u 60.5, `DIS [3672, 3791]`; `B−2718/B−2599`, `DIS
[3499, 3618]`); brackets + input-pin hashes recorded; ambiguous two-burst FAIL; missing-notify
events FAIL; stop confound (after DIS but before the mapped bound) FAIL; stop before DIS FAIL;
missing stop markers FAIL; corroboration labelled not-acceptance-proof; self-pin equality; and the
REPLAY of the retained ABORTED `mac-resume-2026-10-28/lifecycle-1` data: all four scenarios get a
non-empty anchor intersection (widths 114–119 ms, ≤ 250; matches the preflight's own validation on
this data: implied offsets inside ±57 ms class), shutdown/port-disconnect/tab-close bound checks
hold on the retained cleanup exits, EVERYTHING marked `diagnostic_only_not_creditable` (skipped
cleanly if the retained dir is absent).

## 4. Mac-clock corroboration helper `scripts/mac_clock_corroborate.py` (new; sha256
`50e2fe2c4702e990d7a7fae719f4d302c3ab23c6cb72f7ae3c7477205e234094`)

The retained `mac-resume-2026-10-28` probe was reviewed per the preflight: whole-second
macOS-`date` truncation leaves a 500 ms centring bias uncovered (Defect 1) and its ±15.8–16.8 s
uncertainty cannot carry a 30 s bound (Defect 2). Decision per the parent: the OLD raw stays pinned
unchanged as a **known-biased raw diagnostic, never acceptance proof** (labelled in FINDING.public
§4 and inside the analyzer), and the NEW corroboration-only helper fixes exactly: Mac clock read in
MILLISECONDS (ssh python3), per-sample RAW started/finished recorded, whole-seconds fallback uses
`s*1000+500 ± 500` centring, self-sha256 embedded, conservative sum uncertainty; role recorded as
`corroboration_only_not_acceptance_proof` (smoke-tested offline with mocked adb/ssh stamps
— 1800/1500 ms fakes — no device touched).

## 5. CI selection + tests

`.github/workflows/otp23-feasibility.yml` (new sha256
`081924dc5a3c594885992ce7b24f0c5e57baefcbe2ac29e3df2b30aa5a6f54ed`): exactly two added run lines
(`test_mac_release_gate.py`, `test_mac_release_bound.py`) in the feasibility job's portable block.

Test results on this Linux box (Python 3.14; the harness is portable 3.9+, mock-based, no
Android/Chrome needed):
- `test_mac_release_gate.py`: 4 OK — includes: `WindowsAdapter.platform`/`MacAdapter().platform`
  (the ACTUAL adapter attribute the callsites read) gate to 2/None; unknown platforms → None
  (never a silent 2); all five callsites use `release_gate` and no ungated `min_released=2,`
  remains; a frozen-core pairing shows the Mac trace gate tolerating 0 release lines while the
  Windows gate still rejects them.
- `test_mac_release_bound.py`: 9 OK (see §3).
- `test_macos_poc.py`: 58 OK (46 historical + 12 new classifier cases).
- `test_scenario_matrix.py`: 136 OK, 5 skipped (FROZEN — unchanged, still green over the gate
  change).
- Full portable `unittest discover -p "test_*.py"`: **484 tests OK, 5 skipped, 0 failures**
  (v4-era baseline was 455+5).
- `python3 scripts/matrix.py check` → `matrix ok: NO-GO` (unchanged).
- `py_compile` on every changed/new Python file: PASS.

## 6. Publication-preserving curation (no raw retcon)

- `evidence/mac-resume-2026-10-28/` raws + the 32-entry `SHA256SUMS` + historical captures: ALL
  verified byte-unchanged today (sha256sum -c = 32/32 OK).
- NEW additive `evidence/mac-resume-2026-10-28/curation/` (own `.gitattributes
  * -text -whitespace` + `SHA256SUMS`, self-consistent): `FINDING.public.md` (sha256
  `821ce855efd12c9509fa3c2b331e768e10d64f2f8ee199d0a4b35327a78aeedf`) redacts the other local
  user's account name and the foreign app name from the raw FINDING (keeps the mount-point path
  and the `uid≠self` fact); `curation-map.json` (sha256
  `50831d4d11c2507abf9b03980a75648190f982ed176890f19182e1a264013012`) pins the raw FINDING.md
  sha256 (`2518c1637fb20ff765c5df88579ecbbd23007f2d0077b6468ff81dd5fd63e5d1`, the actual value in
  the 32-entry manifest — corrected 2026-10-28 minimum-fix per reviewer F-3; an earlier prose here
  wrongly quoted the historical b5-v4 FINDING hash `9a7ffbab…`) as the PRIVATE original, lists the
  exact redaction mapping, and states the non-retcon policy. The raw itself stays exactly as captured;
  how the private original reaches any repository is the parent's call.
- `evidence/mac-m08-codephase-2026-10-28/` (new, additive, own gitattributes+SHA256SUMS): the
  Linux dry-probe artifact.

## 7. Changed/new file hash inventory (working tree; NOT committed)

Modified: `.github/workflows/otp23-feasibility.yml` `081924dc…`,
`scripts/macos_poc.py` `3d2ecdae…`→see §2 (full: `3d2eccaee8f02c9bc206300463e4b95f76665e2b9acb495d350019056c69fee2`),
`scripts/scenario_matrix.py` `f8bbf2d6ea158d2bd30bee35b97a655c727a7ca7fc677a5c9a3a53eafe6b7f28`,
`scripts/test_macos_poc.py` `a729dcceb71c2bf8123c2529b59db37f8ede0bf5f9af170bd3b9f2f4f7d3fb3a`.
New: `scripts/mac_release_bound.py` `7e779e97…`, `scripts/mac_clock_corroborate.py`
`50e2fe2c…`, `scripts/test_mac_release_gate.py` `7f61cdee…`, `scripts/test_mac_release_bound.py`
`a66e5ca4…`; evidence dirs as in §6 (the mac-resume raw dir remains the 32-entry capture from the
measurement session, plus the additive curation subdir). Everything else in the tree (all frozen
v4 pins, Windows records, old evidence) is byte-identical to HEAD.

## 8. Planned physical retake (NOT executed — parent + Opus verify this code first)

Three SEPARATE fresh per-scenario runs (avoids the 4-burst ambiguity entirely), each in a unique
out-dir (`.../mac-resume-2026-10-28/retake-<scenario>-<n>/`, never `--overwrite`), each with its
OWN phone fixture run id on BOTH phones:

1. `mac-m08-<scenario>-<UTCcompactZ>` run ids: `shutdown`, `port-disconnect`, `tab-close`.
2. Per scenario: start both phones instrumented with that scenario's run id
   (`otp23_transport_evidence true`); run
   `python3 scripts/scenario_matrix.py lifecycle --scenario <name> --out-dir <unique>` in the
   staged work dir with `VELES_OTP23_LSOF_DIAG=<attempt-dir>/lsof-diagnostics.jsonl` exported
   (fail-closed acceptance wiring: the endpoint census needs its mandatory records; `mount-nullfs.txt`
   snapshot lands beside it) and `VELES_OTP23_DETACHED_CLOSE` unset.
3. Acceptance target per scenario (unchanged controls): 6/6 connected-phase samples `usable=true`;
   host `all_layers=0` and `tcp4=[]`; `chrome_visible_top_level ≥ 1` AND
   `chrome_cdp_listener_present=true` in EVERY sample; context audit usable; diagnostics lines =
   lsof invocations, all benign or none; teardown per mechanism
   (shutdown: `shutdown_complete` last, exit 0; port-disconnect/tab-close: `stdin_closed`, no
   shutdown frames, exit 0, drained) + process gone ≤ 10 s, no survivors.
4. Stop each phone's fixture only after the host's own exit + >30 s + uncertainty, with bracketed
   phone dates + MAC-ms readback by ssh (stops JSON); pull transport evidence; then analyze each
   scenario with `mac_release_bound.py --lifecycle-dir <attempt-dir> --scenario <name>
   --transport both.json --stops stops.json --corroborate clock.json` → per-phone PASS (anchor
   intersection ≤ 250 ms, single burst, both bound checks, stop guard) — the REQUIRED external
   release proof for every accepted Mac scenario (P0-A exchange).
5. Direct SSH stdin-EOF stays OUT of the retake (W-08-precedent reading, parent recorded): the
   retained `permission_pending` BLOCKED record stays as a separate unexecuted comparison — never
   a PASS or waiver; this reading is stated in the M-08 adjudication and gets independent review
   before any row change.
6. Afterward: fixture identity + grant re-verified read-only; owned registration removed; matrix
   rows only via the parent after independent review (no canonical PASS from this session).

## 9. Remaining Mac rows (recorded positions only; NOT implemented now)

- **M-03:** stays BLOCKED. The signed fixture's original prompt is past and not re-observable
  without a TCC reset/removal of the grant entry (excluded); no read-only Settings observation can
  be credited as prompt ownership. Owner decision required (defer vs a non-reset observation
  definition); not implemented here.
- **M-04:** the minimal connector-guidance path (`guidanceFor` + owner toggle OFF/ON in one
  session) is POSSIBLE FUTURE work with an owner-observed entry name; it is NOT a default scope
  waiver and the prompt-owner clause stays tied to M-03. Nothing implemented now.
- **M-11:** requires a distinct identity (different bundle id/host name, disposable-profile-scoped
  registration) approved explicitly by the owner, so the approved fixture's grant can never be
  disturbed; the owner observes and answers the prompt. Not started.

## 10. STOP

Stopping here for INDEPENDENT VERIFICATION (parent + Opus) of this code phase, per the parent's
instruction: no further hardware, Chrome, fixture, registration, TCC, signing or device work until
the reviewer approves; then the retake (§8) runs. No commit/push/GitHub/canonical promotion has
been or will be made by this session.