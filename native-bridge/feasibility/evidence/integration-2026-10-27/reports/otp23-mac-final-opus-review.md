# OTP-23 macOS strict runs — final independent Opus review

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`, every assistant message `claude-opus-5-5` (348 at capture), CLI `2.1.311`, 0 subagents.
- Read-only: no edits, no hardware, no GitHub. The only file written is this report.
- Windows files were ignored.

**Fingerprints (2026-10-27T17:13:24Z):**

| Item | Value |
|---|---|
| Repo | `/home/dev/repos/veles-android` HEAD `dc5d127`, uncommitted |
| `git diff` sha256 | `735008c786e4f08ae5bca1e53563b93f1637eb0d55f573c9f81e5775c9c4e66f` (same as the finalizer's capture) |
| Evidence `strict-mac-20261027/SHA256SUMS` | sha256 `3487728c…a584`, 26/26 OK; file list matches disk |
| Live evaluator `strict_background_core.py` | `8c7d3340…` (= recorded in both adjudications) |
| `strict_phone_validation.py` | `93057ec4…` |
| `strict_background_finalize.py` | `15e4d152…` |
| `strict_background.py` (live) | `478b836c…` |

The scripts suite passes: 347 tests OK.

## Verified independently
- **Raws are unmodified**, and their sha256 matches the adjudications' `inputs.raw.sha256`:
  - cold `18a258f5…`, prewarm `dae4b856…`;
  - both raws `platform: macos`, `browser_version: Chrome/156.0.8078.48`;
  - both raws BLOCKED only on ST-08.
- **Both adjudications:** `strict_result` PASS with ST-01..ST-15 all PASS, `raw_consistency` empty, `formal_otp23_acceptance: false`. The evaluator provenance (git revision, dirty flag, 3 file hashes) is recorded inside each adjudication.
- **Calibration:** `calibration-preflight2-snapshot.jsonl` is byte-identical to the file I reviewed in preflight 2 (`/tmp/opencode/cal-ssh.jsonl`, `a517e5e6…`). The frozen file `bd2209db…` is kept separately.
- **Phone files:** the archived copies equal the adjudication input hashes (`73336847…`, `7d24c3ee…`, `75d4d721…`, `ad79452d…`). The salted per-serial source hashes are distinct (6T `c049779e…`, 10 Pro `dd63603c…`). The parent's serial↔model binding is recorded in `phone-bindings.json`, along with installed APK hashes.
- **ST-15 at both sample points** (raw observed values):
  - pid = the run's Chrome (`90368` cold / `96110` prewarm);
  - `ps_args` shows the dedicated temporary profile;
  - session `{verified: true, on_console: true, screen_locked: false}`;
  - page state visible with focus.

  The gate confirmed at poll 1 (Chrome was already frontmost; no synthetic input).
- **Strict idle:** `cdp_calls_during_idle = 0`; browser-level connection closed; intervals 719.75 s (cold) and 717.76 s (prewarm). No interruptions in either the connector history or the ordinary-tab history; flags clean; exemption scan empty; cold host trace has 0 sends; prewarm has 2 warm-up sends.
- **Clock margins:** phone fire time is about 687.7 s into the hidden interval (cold), so about 87 s above the 600 s floor and about 30 s before re-attach. The uncertainty of ±2.39 s, built from measured half-RTT plus spread plus a stated linear-drift assumption, cannot change any verdict. No timezone inference was used.
- **Same build:** the Chrome build equals the macOS timer-probe build (156.0.8078.48). So the approved clarification's same-build condition is supported by existing evidence: the port tab was throttled just like the control tab.

## IMPORTANT (minimum fixes before writing PASS into the matrix)
**F-1. The finalizer does not use the trusted platform.**
- `strict_background_finalize.py:73/76` calls `core.evaluate(evidence)` without `platform=`. Only the live driver (`strict_background.py:603`) passes `PLATFORM`.
- So the adjudications relied on the self-recorded `os_foreground.platform`, contrary to the Q-1 claim. I re-evaluated the cold raw with the trusted platform `macos`: ST-15 PASS, so the outcome is unchanged.
- Also, a trusted platform of `windows` applied to this macOS raw gives ST-15 PASS without the macOS guards: a mismatch between the trusted and recorded platform is not detected.
- **Fix:**
  - Have the finalizer pass `platform=raw["platform"]`.
  - Make `evaluate` return BLOCKED when the trusted and recorded platforms differ, and add a test.
  - Re-finalize into **new** files (exclusive-create), keep the current ones as history, and record the new evaluator hashes.

**F-2. The run-time harness and host identity are not recorded.**
- Raw `meta.source_revision` is null.
- Neither the raws nor the README record the sha256 of the driver, the connector extension files, `macos_*` modules and launch scripts that actually ran from `~/homepc/work/otp23-glm-dc5d127-cal/run/`.
- Nor do they record the registered host manifest and bundle executable (expected to be the approved fixture `e7c716c5…eb91c`, Team `7T39M26Z99`).
- The live `strict_background.py` (`478b836c…`) is not shown to be the file that ran.
- **Fix:** a read-only, after-the-fact capture on the Mac, labelled "captured after the run":
  - `shasum -a 256` of the remote run tree's driver, extension, support modules and launch script;
  - the NativeMessagingHosts manifest the profile used;
  - `codesign -dv` plus the executable sha of the registered bundle;
  - then compare each with the live repo hashes and with the approved fixture.
- If the bundle hash or the driver logic differs materially from the reviewed source, name it as a blocker. Otherwise it is a closed provenance gap. No rerun is needed.

**F-3. The disposition text (finalizer §5) is out of date and contains a factual overclaim.**
- It keeps the rows BLOCKED because "the literal RFC 'no exemption of any kind' criterion is still not discharged … formal_otp23_acceptance=false". That reasoning is obsolete:
  - the RFC criterion was clarified by owner approval;
  - ST-13 (flags) plus the exemption scan plus the same-build probe meet the clarified criterion;
  - `formal_otp23_acceptance=false` is script policy, and the matrix is the adjudicator.
- "distinct bytes" is wrong. All four phone records carry the **same** payload sha256 `bacb3ac9…` (same seed). Only the file bytes differ, through timestamps. Distinctness of the phones rests on the operator serial binding plus two distinct host connection handles per run. The shared central peer prefix is informational.

## Disposition recommendation
**M-09 (cold) and M-10 (prewarm) PASS is justified under the clarified criterion**, once F-1 (re-finalization with the trusted platform) and F-2 (post-hoc provenance capture showing the approved bundle and the reviewed driver) are completed. Neither needs a rerun.

The PASS must record these residuals honestly:
- **(a)** The launch context is operator-inferred, not recorded per run. This is mitigated by the per-sample observed session and page guards and by `ps_args` pinning the dedicated-profile Chrome. The calibration showed the reader discriminating correctly in this SSH Background context.
- **(b)** Phone identity is operator-bound: salted serial hashes plus two distinct handles; payload bytes are identical by design.
- **(c)** A linear-drift assumption in the ±2.39 s bound. Margins are ≥30 s.
- **(d)** The cold 10 Pro JUnit log is partial. No PASS is claimed from it; ST-08 rests on the queued phone file plus the page receipt.

Other requirements:
- Keep the legacy 14-check PASS adjudications and the BLOCKED re-evaluation at HEAD as history.
- Promoting M-09/M-10 does not change the NO-GO decision while other rows remain open.
- W-11/W-12 need their own disposition under the same clarification. Their raws predate the session/page guards, which do not apply on Windows. Their residuals (operator-identified harness revision) must be weighed separately; this review does not decide them.
- No new gate is introduced and nothing is weakened by this recommendation.

## MINOR
- A locked screen at the after-switch sample yields BLOCKED rather than FAIL. That is acceptable, since it fails closed.
- The README should say the gate confirmed at poll 1 because Chrome was already frontmost; "a human must click" did not occur.
