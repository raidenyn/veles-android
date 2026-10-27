# OTP-23 macOS F-1/F-2/F-3 fix verification and final M-09/M-10 disposition (Opus)

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`, every assistant message `claude-opus-5-5` (395 at capture), CLI `2.1.311`, 0 subagents.
- Read-only: no edits, hardware or GitHub. The only file written is this report.
- Windows I-A/I-B not reviewed here.

**Fingerprints (host clock 2026-10-27T23:06:59Z):** root HEAD `dc5d127`, uncommitted `git diff` sha256 `a4a47935fb8c5dd89c549259e6a00ba421804f62242e41d0bd5af58ae7638e14`.

| Item | sha256 (prefix) |
|---|---|
| Live `strict_background_core.py` | `e44f616c…` |
| `strict_background_finalize.py` | `5a163880…` |
| `strict_phone_validation.py` | `93057ec4…` |
| `strict_background.py` | `478b836c…` |
| `test_strict_background.py` | `469b357b…` |
| `test_strict_background_finalize.py` | `c84667a7…` |
| `SHA256SUMS-v2` | `66ba8956…`, 34/34 OK, list matches disk |
| `SHA256SUMS` v1 | `3487728c…` (unchanged); `-c` now fails only `./README.md`, as documented (README appended) |
| `adjudicated-v2.json` files (both) | `4e06914a…`, `15fcb8e2…` |
| `f2-*` (6 files) | `88d565de…`, `370045e5…`, `3c2140e1…`, `a164c0bd…`, `6c45f376…`, `1576c370…` |

The scripts suite passes: 348 tests OK.

## F-1: verified
- The finalizer now calls `evaluate(..., platform=raw["platform"])`. `_foreground_check` does the following:
  - BLOCKs a missing or unknown trusted platform on new-schema raws;
  - BLOCKs a **contradiction** between the trusted and recorded platform;
  - requires the exact session dict `{verified, on_console, unlocked}` and the page guard at both sample points on macOS.
- My own re-evaluation of the actual raws:
  - trusted `macos` → ST-15 PASS;
  - trusted `windows` → BLOCKED (cold and prewarm);
  - gate session off-console → BLOCKED;
  - gate page state removed → BLOCKED.
- New test `test_trusted_platform_contradicting_the_self_recorded_platform_is_blocked` covers both directions and the matching case.
- v2 adjudications, cold and prewarm:
  - `strict_result` PASS, 15/15 PASS;
  - `raw_consistency` empty;
  - `formal_otp23_acceptance` false;
  - trusted `platform: macos`;
  - evaluator provenance (git `dc5d127`, dirty files listed, hashes equal to the live files above);
  - raw input hashes `18a258f5…` and `dae4b856…`, unchanged.
- v1 adjudications are retained.

## Phone identity and clocks: verified
- **Phone source hashes:** `sha256("veles-otp23-phone-source:"+SERIAL)` recomputes to the recorded `c049779e…` (2be02e41, 6T) and `dd63603c…` (864a3db0, 10 Pro). They are non-empty and distinct, and bound by the explicit serial-to-path arguments, so there is no empty or invented identity.
- **Phone file hashes:** the input files match the archived copies.
- **Page receipts:** two distinct connection handles per run. All four records carry the same payload `bacb3ac9…`, as F-3 now states correctly.
- **Clock margins** (measured offset 550.906 ms, ±2390 ms):

| Run | Fire time, seconds after the last DevTools client closed (6T / 10 Pro) | Margin before re-attach |
|---|---|---|
| Cold | 688.3 / 690.3 | ≥29.5 s |
| Prewarm | 688.3 / 686.0 | ≥29.4 s |

  Every receipt is hidden and unfocused. Each receipt is about 0.4 s before the offset-adjusted fire time, which is well inside the uncertainty. No verdict depends on clock precision.

## F-2: verified (gap closed)
- **What ran:** the run-tree driver and core were fetched byte-exact (`6c45f376…`, `1576c370…`). Their diff against live is limited to:
  - ctypes hygiene (`c_long` length type, a `finally` release; same output on success);
  - docstrings;
  - the driver's in-process `evaluate(..., platform=PLATFORM)`;
  - the ST-15 tightening and trusted platform in core.

  The sampling code that produced the evidence (`sample_foreground`, session and page guards) is unchanged. All differences are fail-closed or cosmetic and post-date launch. Raw recomputation is consistent. **No material difference.**
- **Host:** the registration JSON captured verbatim (transcript line 327, 16:04:16Z) records `path` of the `otp23-cd971f0/…/VelesOtp23PoC.app/Contents/MacOS/otp23-host` bundle, plus:
  - `executable_sha256 e7c716c5…eb91c`;
  - `allowed_origins` exactly the fixed extension id.

  Post-run `codesign` shows `Executable=` resolving to that same path: identifier `me.nagaev.veles.bridge`, Team `7T39M26Z99`, Apple Development, signed 26 Oct 13:40:26, verify strict OK, sha `e7c716c5…`. This is the **approved fixture**, and the signing-provenance amendment is satisfied.
- **Teardown:** `"unregistered": true` at 16:41:50Z is captured verbatim.
- **Capture source:** the transcript file `/tmp/opencode/otp23-glm3-resume-events.jsonl` still hashes to the recorded `f1fe3f40…`.
- **Profile manifest:** the disposable profile manifest is gone. It is honestly recorded as not retained, with no reconstruction. That is acceptable, because the driver copies the validated registered manifest byte-identically into the profile.

## F-3: verified
- The README appendix and the result state the facts correctly:
  - the payload is identical by design;
  - phone distinctness rests on the serial binding plus two handles;
  - the gate confirmed on poll 1 with no click observed and no synthetic input;
  - the obsolete literal-criterion and "distinct bytes" reasoning is marked SUPERSEDED, not rewritten.

## Final disposition
**M-09 (cold) and M-10 (prewarm): PASS** (observational, strict) under the RFC background criterion as clarified by the owner-approved 2026-10-27 amendment. Evidence:
- `strict-mac-20261027/*.adjudicated-v2.json` with the raws, calibration, clock, phone and F-2 records;
- Chrome 156.0.8078.48, the same build as the macOS port-versus-control timer probe;
- macOS 26.5.2;
- approved signed host `e7c716c5…`;
- existing administrator account, recorded truthfully.

Residuals to keep in the row text, none disqualifying:
- **(a)** The launch context is operator-inferred. It is mitigated by the per-sample session and page guards, by `ps_args` pinning the dedicated profile, and by calibration in the same SSH Background context.
- **(b)** Phone identity is operator-bound; the payload is identical by design.
- **(c)** A linear-drift assumption in the ±2.39 s bound, against ≥29 s margins.
- **(d)** The cold 10 Pro JUnit log is partial. Nothing is claimed from it.

No new gate, no waiver, no rerun needed. `formal_otp23_acceptance=false` is script policy; the matrix adjudicates.

Other rows are unaffected: W-11/W-12 need their own disposition. The overall decision stays **NO-GO** while other rows remain open, and OTP-24 is not authorised.

## Minor integration notes (no blocker)
- **Checksums:** make the standard `SHA256SUMS` current, as planned. Keep v1 as historical, non-active text.
- **Timestamp in `f2-macos-host-bundle-codesign.txt`:** it says "as registered 16:10:56Z", but the captured registration event is 16:04:16Z. Align the wording, or cite both events if two registrations occurred.
- **Matrix row text:** cite the v2 files and the residuals above. Keep the legacy 14-check PASS and the BLOCKED-at-HEAD history.
