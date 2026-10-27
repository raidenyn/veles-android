# strict-mac-20261027 — macOS cold/prewarm strict runs + offline finalization

New (2026-10-27) macOS evidence for OTP-23 strict background delivery, run by the parent GLM-5.3-flash
session from the calibrated SSH Background context (uid 501, managername=Background, per the
preflight-2-approved calibration raw archived here) and finalized OFFLINE (ST-08) by the follow-up GLM-5.3-flash
session. This directory is ADDITIVE: the earlier evidence directories (macos-final, macos-background,
phone-evidence, windows-*) and the parent directory README/INTEGRATION/SHA256SUMS are untouched.
`docs/otp-23-requirements-matrix.md` rows M-09/M-10 stay **BLOCKED**; the dispositions PROPOSED from this
directory are recorded in /tmp/opencode/otp23-mac-final-adjudication-result.md for parent/Opus verification.
No file here is an OTP-23 acceptance; every strict result has `formal_otp23_acceptance: false`; the requirements
matrix, not any file here, adjudicates OTP-23.

## Raw run artifacts (produced by the parent session's runs; byte-identical to Sirius originals)

sha256 verified from the live remote tree `~/homepc/work/otp23-glm-dc5d127-cal/run/evidence/` on Sirius
(ssh read of `shasum -a 256`, 2026-10-27T17:0xZ) AND against the parent-recorded values — all equal:

| file | sha256 (first 16) |
|---|---|
| strict-mac-20261027-cold.json | `18a258f582eaf595` |
| strict-mac-20261027-cold.trace.log | `a2378dc6aee55fa9` |
| strict-mac-20261027-cold.foreground-ready.json | `4e84700683cb3cdf` |
| strict-mac-20261027-prewarm.json | `dae4b856433d7ac4` |
| strict-mac-20261027-prewarm.trace.log | `1352d93e1e4a57a1` |
| strict-mac-20261027-prewarm.foreground-ready.json | `e20b9e3d4ddead92` |

Full hashes: `SHA256SUMS` in this directory. Run facts (raw, unmodified): cold started 16:11:53Z, 719.7 s
idle, 0 CDP calls, ended BLOCKED only on ST-08 (no adb on the Mac; ST-08 finalized here, offline);
prewarm started 16:28:53Z, 717.7 s idle, ended BLOCKED only on ST-08. ST-01..ST-07 and ST-09..ST-15 PASS in
each raw (observed); `raw_consistency` in each adjudication confirms the fixed evaluator recomputes every
raw check with no disagreement. Chrome/156.0.8078.48, macOS 26.5.2. NOTE: an earlier summary typo wrote
156.0.8087.48 — WRONG, do not propagate; meta `browser_version` and the live binary say 156.0.8078.48.

## OS foreground gate (ST-15) — observed in raw, calibration-backed

Both raws record, at both sample points (gate + after_switch): owned pid (cold 90368, prewarm 96110 =
the spawned dedicated-profile Chrome), `bundle com.google.Chrome`, `ps_args` with the dedicated
`--user-data-dir`, native ctypes session guard `{verified: true, on_console: true, screen_locked: false}`,
and the page-state guard (`visibilityState == "visible"`, hasFocus recorded). The preflight-2 Q-1 evaluator
bug fix is verified independently against both raws: `core.evaluate(evidence, platform=raw["platform"])`
(trusted top-level) and `core.evaluate(evidence)` (fg self-recorded `platform: "macos"`) both give ST-15
PASS; removing `os_foreground` gives ST-15 BLOCKED (fails closed).

## Calibration records (distinction preserved; failed attempts kept)

* Positive calibration, SSH Background context — `calibration-cal-ssh-20261027T155808Z.jsonl`
  sha256 `bd2209db5217bbb1…` (1615 samples, 1 Hz, recorder context records + per-sample
  reader/session/ps_args; distinguishes own Chrome / Finder / personal-Chrome-pid / lock→loginwindow+locked).
  This is the FINAL frozen copy taken after the natural recorder expiry. Original verified on Sirius:
  `~/homepc/work/otp23-glm-dc5d127-cal/cal-ssh-20261027T155808Z.jsonl` = same sha256 (ssh, 2026-10-27).
* Reviewer snapshot — `calibration-preflight2-snapshot.jsonl` sha256 `a517e5e65f4edaff…` = the exact bytes
  the Opus preflight-2 reviewer read at /tmp/opencode/cal-ssh.jsonl (prefix preserved). This is a PREFIX
  of the final file; preserved separately so the review-time record is not conflated with the final copy.
  Failed Aqua-session attempts (JXA ObjC-bridge diagnosis) preserved as operator terminal captures:
  `operator-cal-output-1-aqua-153840Z.txt`, `operator-cal-output-2-aqua-155151Z.txt` (recorders 77775/82261,
  `raw_session: null`/`""`, `AQUA_DIAG: sym:function | dtype:function`); raws cal-aqua-20261027T153840Z.jsonl
  and cal-aqua-20261027T155151Z.jsonl remain on Sirius (not copied here; not modified).

## Offline finalization (ST-08) — this session, measured clock, explicit phone bindings

* `strict-mac-20261027-cold.adjudicated.json` — strict_result **PASS** (ST-01..ST-15 PASS, ST-08 now PASS),
  `formal_otp23_acceptance: false`. Produced by `scripts/strict_background_finalize.py` (exit 0), exclusive
  create; inputs: raw (reads only, sha recorded) + phone files + measured clock.
* `strict-mac-20261027-prewarm.adjudicated.json` — same, **PASS**.
* Phone inputs (copied BYTE-IDENTICAL from the parent's captures in /tmp/opencode, which were taken from
  the actual devices after each run):
  `phone-cold-2be02e41-6t.json` (sha `733368474be1c320…`), `phone-cold-864a3db0-10pro.json`
  (`7d24c3eed312789b…`), `phone-prewarm-2be02e41-6t.json` (`75d4d7211438b091…`),
  `phone-prewarm-864a3db0-10pro.json` (`ad79452d90d758b6…`); instrumentation logs
  `phone-cold-2be02e41-6t-instrument.log` (`75727fb504931091…`), `phone-cold-864a3db0-10pro-instrument.log`
  (`a35383a1f472e0e4…`), `phone-prewarm-2be02e41-6t-instrument.log` (`4ac66dd068bf9144…`),
  `phone-prewarm-864a3db0-10pro-instrument.log`   (`d9a759f30a9bf77c…`); and the parent-recorded bindings
  `phone-bindings.json` (`76bf853bdee23a0f…`) — parent-recorded serials 2be02e41 = OnePlus 6T ONEPLUS A6013,
  864a3db0 = OnePlus 10 Pro NE2215; fixtures armed cold 16:11:20Z / warm restart 16:28:17Z, 690 s unsolicited
  delay, no build/install. The parent read back the INSTALLED APKs on both phones: app sha256
  `063ac7ea719b8b2625690a5be5a9d57a7b3a24bf64242c34a1772b510f677a5e`, test apk
  `8ca89381dcc8573aba500e65ba4746e698ebdfa0bf3793345ddc4645712ede1c` — same on BOTH phones; prewarm used
  the identical installed hashes with no re-install.
  OBSERVED/SOURCE/OPERATOR-INFERRED distinctions: the salted per-file `source_hash` (sha256 of
  `"veles-otp23-phone-source:" + SERIAL`) proves distinct sources only under the parent's OPERATOR-provided
  SERIAL=PATH binding (the parent read the serials from the devices; the files themselves carry no device id).
  Corroboration is observational: distinct push timestamps per phone, two distinct host handles per run, and
  the same `peer_sha256_prefix` 576d2120 for both (ONE central BLE address hash — informational, not a
  distinctness claim). Cold 10pro instrument log is PARTIAL: it ends after the test-name line (`…SharingFeasibilityTest:`)
  with NO JUnit verdict (the parent's operator ack attributes this to the host app already being gone at
  16:27:28Z when the parent ended its own stale local adb client (pid 287219) — NOT a force-stop/uninstall and
  not a device wipe; that circumstance is operator-recorded, not in this log). NO JUnit PASS is claimed from
  it, and this is not a phone-test failure proof — a partial observation, preserved as-is. The cold-6t,
  prewarm-6t and prewarm-10pro logs each end with `OK (1 test)` (cold-6t: Time 882.093, consistent with the
  690 s delay). Parent stop markers ran after each scenario result; these files were captured after the stops.
* Clock provenance (measured, NOT inferred from timezone): `clock-round1-20261027T165233Z.json`,
  `clock-round2-20261027T165916Z.json` with raw per-sample RTTs and remote UTC stamps, produced by
  `clock-measure.py` (kept). Method: 7 samples per clock (Mac via ssh read of `time.time()*1000`; each phone
  via `adb -s SERIAL shell date +%s%3N` through 127.0.0.1:5037), midpoint-of-roundtrip as the per-read UTC
  stamp against the dev-container reference clock; per-sample uncertainty = half RTT + 1 ms; min-RTT sample
  selected. Desktop(Mac) − phone offsets, round 2: 6T +507.721 ms, 10 Pro +594.090 ms; relative Mac↔phone
  drift between rounds 403 s apart = 3.141/4.439 ms (≈0.0110 ms/s, measured); the common +147.830 ms shift in
  all three remotes between rounds is the reference container's own clock drift and cancels in the pairwise
  offsets. The adjudications use the round-2 mean offset 550.906 ms for both phones with
  uncertainty = max pair half-RTT (2314.44) + half-spread (43.185) + extrapolated relative drift over the span
  from the earliest evidence instant (16:11:20Z) to round 2 (2876 s → 31.679), rounded UP: **2390 ms**. The
  extrapolation assumes the measured relative drift rate is constant; no clock equality is assumed anywhere.
  Uncertainty was measured ~11.5–40 min AFTER the two runs (16:52–17:00Z vs runs ending 16:23:59/16:41:00Z),
  which the drift term deliberately covers.

## Evaluator provenance (real, not a clean-tree claim)

The adjudications were produced by the UNCOMMITTED working tree at git dc5d127819a8f5fc1fc554479190fee2c9808521
(branch contract/otp23-desktop-alignment). `evaluator_provenance` recorded in BOTH adjudication files:
`git_available: true`, `evaluator_files_dirty: true`, modified: `strict_background_core.py` only (the
preflight-2 Q-1 fix); SHA256 `strict_background_core.py` `8c7d33403fc6d484…`,
`strict_phone_validation.py` `93057ec40c895294…`, `strict_background_finalize.py` `15e4d152c78a77bc6…`.
Other live source at adjudication time: `strict_background.py` `478b836c444fa194…`,
`test_strict_background.py` `ce6897222dc76573…`, `macos_background_support.py` `e02062c3edfca461…`,
`macos_poc.py` `c3217afb3d956df1…`, `macos_cdp.py` `e962bcc94123faa6…`, `matrix.py` `b0db36b233e07bb5…`,
`docs/otp-23-strict-background.md` `f47aa35a0237946a…`, `docs/otp-23-requirements-matrix.md`
`4dec1ecbdb5a19cf…`, full uncommitted `git diff` sha256 `735008c786e4f08ae5bca1e53563b93f1637eb0d55f573c9f81e5775c9c4e66f`.
The tree ALSO carries Windows-side uncommitted work (lib.rs/winrt.rs/closejoin.rs) owned by another agent;
it changed neither the evaluated Python nor these bytes and is not part of the evaluator hash coverage.

## Per-run context identity — RESIDUAL (operator-inferred, recorded honestly)

Preflight-2 asked the run meta to record `launchctl managername`/SSH id/`who`; the parent's run meta does
NOT carry a per-run context record (meta keys: started/finished/browser_version/…, no context block). The
per-sample session guard IS observed per sample (on-console/unlocked recorded in both raws), and the reader
pipeline is the same driver code; the claim "runs executed in the preflight-2-calibrated SSH Background
context (uid 501, managername=Background)" is therefore OPERATOR-INFERRED from the parent's launch pipeline
(the runs were launched from the same SSH session as the calibration), not per-run observed. This is recorded
here and in the finalization result note rather than silently assumed.

## Not changed by this directory

Historical 14-check PASS adjudications and their BLOCKED-at-HEAD re-evaluation (phone-evidence/mac/) are
untouched; the parent RFC/matrix text is untouched; no Windows documentation or evidence touched; prior raw
files and manifests byte-unchanged (hashes re-verified at the end of the finalization session).
## Opus final review fixes (F-1/F-2/F-3) — appended 2026-10-27T17:3xZ

The independent Opus final review (session 2fe20385-5ef1-4622-bf94-00b8c0c6477e,
/tmp/opencode/otp23-mac-final-opus-review.md; 0 subagents, read-only) justified PASS for M-09/M-10 once
three IMPORTANT fixes were done. All three are done now; the fixes are additive — every pre-existing file
in this directory, INCLUDING the v1 adjudications and the v1 `SHA256SUMS` (sha256 `3487728c…a584`,
26/26 OK per the review) stays byte-identical; the new state is covered by `SHA256SUMS-v2`.

### F-1 — trusted platform in the finalizer (done)

`strict_background_finalize.py` now calls `core.evaluate(..., platform=raw["platform"])` for BOTH the
raw-check recomputation and the verdict; `strict_background_core._foreground_check` BLOCKs any
new-schema sample whose trusted result platform contradicts its self-recorded platform (a windows label
can no longer pass a macOS sample without the macOS guards, and vice versa), with a new test. Suite:
**348 tests OK**. The raws were re-finalized into NEW exclusive files
`strict-mac-20261027-{cold,prewarm}.adjudicated-v2.json` (strict_result PASS, all ST-01..ST-15 PASS,
`raw_consistency` empty, `formal_otp23_acceptance: false`); the v1 adjudications are retained as history.
v2 evaluator hashes (recorded inside each v2 file): `strict_background_core.py` `e44f616c04054707…`,
`strict_background_finalize.py` `5a1638808759a31b…`, `strict_phone_validation.py` `93057ec40c895294…`.

### F-2 — run-time harness/host provenance, closed after-run, read-only (done)

`f2-provenance-comparison.md` + the `f2-*` artifacts record: (a) the exact bytes that ran — the cal-dir
root copies hashed into `run/scripts/` at 16:04:16Z (parent-transcript record) equal the run-tree files
(driver `6c45f3765d68459b…`, evaluator `1576c370f47b1aeb…`), and five of the seven runtime modules plus
all four extension files and `identity.rs` are byte-identical to the live reviewed tree; (b) the two
differing files (driver/evaluator) changed only AFTER the runs' launch (live mtimes 16:13:31/16:14:36Z vs
run-tree 16:10:55Z) by enumerated, fail-closed-direction edits (preflight-2 MINOR ctypes hygiene +
docstring rewording + the Q-1 trusted-platform/session fixes); (c) the host executable is byte-equal to
the APPROVED fixture `e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c` (see codesign capture:
`me.nagaev.veles.bridge`, Team `7T39M26Z99`, Apple Development, strict verify OK); (d) the used NM manifest is on record from
registration (16:04:16Z, parent transcript, full JSON content incl. allowed_origins) to teardown
(16:41:50Z `"unregistered": true`), and the NM dir listing today shows no veles manifest
(NO_VELES_REGISTERED state). The dedicated temp profile dirs are NOT retained on disk, so no
profile-scoped manifest could be captured after the run — the user-scope global manifest of record above
is the manifest the runs used. NO BLOCKER: no material discrepancy between what ran and the reviewed
source/approved fixture; no rerun required (per the review).

### F-3 — wording corrections (done here and in the disposition text)

* Payload distinctness said plainly: all four phone records carry the SAME payload sha256 `bacb3ac9a8a0…`
  (identical 4096-byte synthetic record by design). Only the FILE bytes differ, and only through record
  timestamps. Distinctness of the two phones per run rests on the operator serial binding (salted
  hash-of-serial recorded by the finalizer) plus TWO DISTINCT host connection handles per run; the shared
  `peer_sha256_prefix 576d2120` (hash of the single central BLE address) is informational only.
* The gate statement, stated exactly: the driver's OS-foreground gate confirmed on POLL 1 in both raws
  (waited 0.149 s cold / 0.087 s prewarm; `foreground-ready` files written with `polls: 1`,
  `state: confirmed`) — the dedicated Chrome was already frontmost when polling began. The driver's
  instruction line asks for a physical click, and no synthetic input exists anywhere, but this evidence
  does NOT observe or time a human click; nothing between ask and confirmation is claimed.
* The obsolete disposition reasoning (rows kept BLOCKED via "the literal RFC 'no exemption of any kind'
  criterion is still not discharged" + `formal_otp23_acceptance=false` as a circular blocker) is
  REPLACED per the owner-approved 2026-10-27 clarification: the Native Messaging port itself is not a
  timer-throttling exemption; ST-13 (flags-only), the empty exemption scan and the same-build port-escape
  probe (156.0.8078.48 — the same build as the timer-probe evidence) meet the clarified criterion;
  `formal_otp23_acceptance=false` is script policy, and the requirements matrix — not any strict or
  adjudication file — adjudicates OTP-23. The corrected proposal (PASS CONDITIONAL on parent/Opus
  verification) lives in /tmp/opencode/otp23-mac-review-fixes-result.md; the matrix text itself was NOT
  edited (4dec1ecbdb5a19cf… unchanged).
