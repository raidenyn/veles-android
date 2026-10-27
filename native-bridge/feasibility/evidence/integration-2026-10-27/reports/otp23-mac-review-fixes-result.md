# OTP-23 macOS review-fixes result (GLM-5.3-flash finalize session, round 2)

Performer: actual GLM-5.3-flash (providerID `ollama-cloud`), opencode session
`ses_e81bc7103ffe3F21OMCsqIRppL` (opencode-db captured; 0 subagents; no commits/push/GitHub). Executed
every Opus-F-1/F-2/F-3 minimum fix from /tmp/opencode/otp23-mac-final-opus-review.md. No rerun of the
≥10-min runs (per the review). Windows files untouched; the Windows agent's files appeared/changed
independently (lib.rs, winrt.rs, closejoin.rs, windows_closejoin_compare.py, windows-cleanup-2026-10-27/).

## F-1 — trusted platform in the finalizer (DONE)

* `strict_background_finalize.py`: verdict + raw-check recomputation now call
  `core.evaluate(..., platform=raw["platform"])` (trusted top-level platform; docstring updated).
* `strict_background_core.py`: `_foreground_check(fg, platform)` now BLOCKs a new-schema sample whose
  trusted result platform CONTRADICTS its self-recorded platform (windows can no longer pass a macos
  sample without the macOS guards; macos over windows likewise). Legacy raws stay audit-only as before.
* New test `test_trusted_platform_contradicting_the_self_recorded_platform_is_blocked` (mismatch →
  BLOCKED → whole run not PASS; matching platform still PASS). Suite: **348 tests OK** (was 347; +1).
* Re-finalization into NEW exclusive-create files; v1 adjudications retained byte-identical as history:

| file | strict_result | checks | raw_consistency | adjudicated_utc |
|---|---|---|---|---|
| strict-mac-20261027-cold.adjudicated-v2.json | **PASS** | ST-01..ST-15 all PASS | empty | 17:23:26Z |
| strict-mac-20261027-prewarm.adjudicated-v2.json | **PASS** | ST-01..ST-15 all PASS | empty | 17:23:27Z |

  `formal_otp23_acceptance: false`, ST-15 actual carries the TRUSTED `platform: "macos"` + page_required.
  v2 evaluator hashes: core `e44f616c04054707…`, finalize `5a1638808759a31b…`, phone-validation
  `93057ec40c895294…` (recorded inside each v2 file; v1 stays with its own recorded hashes).
  v1 files' sha256 retained (cold `e6085807…`, prewarm `db681699…`).

## F-2 — after-run read-only Mac provenance capture + comparison (DONE, gap CLOSED)

Captured 17:18–17:26Z, all read-only; nothing modified on the Mac. Evidence dir additions:
`f2-provenance-comparison.md`, `f2-run-tree-driver-strict_background.py` `6c45f3765d68459b…`,
`f2-run-tree-evaluator-strict_background_core.py` `1576c370f47b1aeb…` (byte-exact fetches of WHAT RAN),
`f2-run-stdout-logs.txt`, `f2-macos-host-bundle-codesign.txt`,
`f2-register-manifest-launch-teardown-capture.jsonl` (verbatim parent-transcript records; source-file
sha256 + line numbers recorded).

Findings (details in f2-provenance-comparison.md):
* Launch chain pinned: parent's 16:04:16Z call copied driver+core (cal-dir root == run-tree, hash-equal)
  + py_compile + registered the NM manifest; launches at 16:12:01Z (cold) and 16:28:54Z (prewarm) captured
  verbatim; no separate launch .sh existed for the runs (plain ssh+nohup commands, recorded). Runtime
  modules byte-match the reviewed tree except the TWO enumerated post-run files (driver/evaluator), whose
  live edits (16:13:31/16:14:36Z — AFTER run launch) are: preflight-2 MINOR ctypes hygiene + docstring
  reword + the Q-1 trusted-platform/session fixes — all fail-closed-direction; the runs' recorded
  evidence is unchanged, and `raw_consistency` is empty for both v2 adjudications.
* **Host bundle: byte-equal to the APPROVED fixture** `e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c`
  (codesign: `me.nagaev.veles.bridge`, Team `7T39M26Z99`, Apple Development: yuriy@nagaev.me (ZWYYS8X98S),
  signed 26 Oct 2026 13:40:26; `codesign --verify --strict -v` OK; Info.plist `7d4efdc7…` = recorded
  value). Codesign's Executable= resolves to the cd971f0 original; the run-target bundle bytes hash equal.
* **Used NM manifest on record (honest post-hoc proof)**: registered 16:04:16Z with
  `executable_sha256 e7c716c5…` + full JSON content (name/description/path/type stdio/allowed_origins
  `chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/`) captured in the parent transcript; teardown
  16:41:50Z `"unregistered": true` captured; today's GlobalExtensionInstall dir listing = no veles
  entry (NO_VELES_REGISTERED now confirmed). The dedicated temp profile dirs are NOT retained on disk, so
  no profile manifest could be captured after the run — recorded as such rather than reconstructed.
* Older identity captures preserved with eras (otp23-mac-identity.log `aa242c4deb…` 26 Oct = a pre-final
  build; otp23-post-grant-mac.log = the e7c716c5 fixture registration 26 Oct 16:14Z) — not modified.
* Verdict: NO BLOCKER — what ran is pinned; the host is the approved signed fixture; no material
  discrepancy with the reviewed source/approved fixture.

## F-3 — wording corrections (DONE)

* In the evidence dir README (appended section): payload distinctness stated exactly (all four records
  SAME payload sha256 `bacb3ac9a8a0…` by design; only FILE bytes differ, via timestamps; phone distinctness
  = operator serial binding + two distinct host handles; shared central hash informational).
* Gate statement stated exactly: confirmed on POLL 1 in both raws (waited 0.149/0.087 s;
  foreground-ready `polls: 1, state: confirmed`) — the dedicated Chrome was already frontmost when polling
  began; the driver's ask is recorded, but no human click is OBSERVED or timed, and no synthetic input
  exists anywhere. My earlier `/tmp/opencode/otp23-mac-final-adjudication-result.md` §5 is marked
  SUPERSEDED (kept as reviewed history) — its literal-criterion reasoning (obsolete, F-3) and
  byte-phrasing are replaced by the corrected text here.
* Corrected criterion basis: the owner-approved 2026-10-27 clarification (NM port is not a timer-
  throttling exemption); ST-13 (flags-only) + empty exemption scan + same-build port-escape probe
  (Chrome 156.0.8078.48 = the timer-probe build) meet the clarified criterion;
  `formal_otp23_acceptance=false` is script policy; the matrix adjudicates.

## UPDATED proposed dispositions — PASS CONDITIONAL (NOT applied to the matrix)

M-09 (cold) / M-10 (prewarm): **proposed PASS (observational) — CONDITIONAL** on (F-1 ✅ done,
F-2 ✅ done) **and** the parent's/Opus verification of these fixes. Both rows' final status in the matrix
must be written only by the parent after that verification; the matrix doc was NOT edited by this session
(`docs/otp-23-requirements-matrix.md` sha256 `4dec1ecbdb5a19cf…` unchanged). Proposed disposition text
essence: fresh 2026-10-27 strict-mac-20261027 evidence (v2 adjudications: PASS, all 15 checks,
trusted-platform evaluator, recorded dirty-tree evaluator provenance, explicit SERIAL=PATH bindings,
measured clock); host = approved signed fixture (codesign-captured); used manifest on record
register→teardown; residuals recorded honestly:
(a) launch context operator-inferred (per-run launchctl/SSH line not recorded; per-sample session+page
guards observed; calibration discriminated in this context);
(b) phone identity operator-bound; payload bytes identical by design;
(c) 2390 ms clock uncertainty includes a stated linear-drift assumption (measured 0.0110 ms/s rate; margins ≥30 s);
(d) cold 10 Pro JUnit log partial (no PASS claimed from it; ST-08 rests on the phone file + page receipt).
Promoting M-09/M-10 does NOT change the NO-GO decision while other rows stay open; W-11/W-12 need their
own disposition under the same clarification (not decided here). No gate introduced; nothing weakened.

## Checksums + checks (this session)

* Evaluator (live): core `e44f616c04054707…`, finalize `5a1638808759a31b…`, phone-validation
  `93057ec40c895294…`, driver `478b836c444fa194…`; tests: test_strict_background.py `469b357b0f5a7525…`
  (was `ce689722…` before the new test), test_strict_background_finalize.py `c84667a76fe1ade5…` (fixture
  modernized for the trusted-platform behavior); full-suite 348 OK.
* Fresh uncommitted `git diff` sha256 (after F-1):
  `a4a47935fb8c5dd89c549259e6a00ba421804f62242e41d0bd5af58ae7638e14` (previous capture, pre-F-1,
  `735008c786e4f08a…66f` = the value in the Opus review fingerprints and the v1 README/README-adjudication
  records; the F-1 code diff accounts for the change).
* Evidence dir: `SHA256SUMS` (v1, Opus-fingerprinted) FILE UNTOUCHED — sha256 `3487728ce4feaddf…a584`
  matches the review's fingerprint; as a RECORD its README entry is intentionally stale now (the README
  gained the F-1/F-2/F-3 appendix), so `sha256sum -c SHA256SUMS` reports exactly one mismatch (./README.md)
  — expected and documented; the authoritative current-state checksums are `SHA256SUMS-v2` (all 34 files,
  `sha256sum -c` clean; v1 remains preserved history and its `./README.md` entry pins the reviewed bytes).
* `matrix.py check` → `matrix ok: NO-GO` (unchanged); matrix doc byte-unchanged.
* Model metadata: opencode-db capture in /tmp/opencode/otp23-mac-finalize-glm-model-metadata.json
  (session `ses_e81bc7103ffe3F21OMCsqIRppL`, glm-5.3-flash / ollama-cloud).
* Raw evidence byte-preservation re-verified after all edits (8 originals unchanged).

## Session end state

Mac: awake, no homepc lease/app/Chrome owned by this session (nothing to end — verified
`lease: none`, app/chrome not running; captures were ssh-read-only). PC: NOT session-ended (the Windows
agent owns the active lease). No commits/push; nothing global edited.

Result: **all F-1/F-2/F-3 minimum fixes executed; no blockers; no reruns made or needed** — awaiting
parent/Opus verification of the fixes for the proposed PASS-CONDITIONAL M-09/M-10 dispositions.