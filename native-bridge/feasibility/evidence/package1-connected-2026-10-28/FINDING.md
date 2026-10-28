# OTP-23 package1 connected-phase evidence (2026-10-28) — FINDING

Scope: rows W-02, W-07, W-08 (Windows connected-phase lifecycle/hygiene). Implementation
ollama-cloud/glm-5.3-flash per owner instruction; independent Opus reviews (two preflights) applied
before physical runs. Fixed host identity `otp23-host.exe`
sha256 `8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33` whose `src/*.rs` bytes are
byte-identical to this repo at HEAD `8f365fc` (verified file-by-file, see tree-manifest.json). Chrome:
only the latest installed stable on the PC — **156.0.8078.73** (auto-updated from 156.0.8078.48 after
the earlier packages; version recorded per run from CDP `Browser.getVersion`). No launch flags.

## Runs

| run | scenario | result | host own exit | phone DISCONNECTED vs host exit (pre-declared bound 30 s) |
|---|---|---|---|---|
| lifecycle5/shutdown | shutdown (2 byte-proven links + scan + pending) | BLOCKED criterion_conflict | exit code=0 @9.403s, stdout_drained=true, releases 2, join 3/3, owned gone 0.563 s | (superseded by run-shutdown-3) |
| run-shutdown-3* | shutdown | BLOCKED criterion_conflict | own exit 0 | 6T +3.8 s, 10Pro +3.1 s (within bound) |
| run-port-disconnect-2* | port.disconnect() | BLOCKED criterion_conflict | exit code=0 @~8.0 s, stdin_closed mechanism, releases 2, join 2/2, gone 0.561 s | 10Pro +3.1 s; 6T DIS row present at +3.8 s (own CONNECT rotated out of the 10-row phone ring) |
| run-tab-close-2* | connector tab close | BLOCKED criterion_conflict | exit code=0 @~8.6 s, stdin_closed, releases 2, join 2/2, gone 0.567 s | 6T +3.8 s, 10Pro +3.1 s (within bound) |
| run-failure-isolation-2* | deliberate disconnect + survivor + shutdown | BLOCKED criterion_conflict | own exit 0, releases 2 (survivor), join 1/1 | 6T +3.3 s, 10Pro +3.0 s (within bound) |
| lifecycle5/stdin-eof + run-stdin-eof* | direct stdin EOF (no Chrome) | PASS | exit code=0, releases 2, join 2/2 | 10Pro +3.1 s; 6T DIS row present at +3.7 s (own CONNECT rotated) |
| lifecycle5/stdin-eof-truncated | direct stdin truncation | PASS | exit code=2 (framing rejection) | n/a (no links connected by design) |

\* per-scenario runs exist because the phone ConnectionState history is a small ring; lifecycle5's own
phone rows rotated out except for the scenarios captured in time. All original runs and every failed
attempt are retained (`retained-*` directories):

- `retained-lifecycle1-failed`: window_inventory `class_of` NameError (driver regression) — environment FAILs.
- `retained-lifecycle2-failed`: endpoint `family_api` set-literal TypeError (driver regression).
- `retained-lifecycle3-failed`: `verify_gone` require_join signature drift (driver regression).
- `retained-tabclose-launch-denied-1`: one transient Chrome-launch PermissionError on the temp profile.
- lifecycle4 was refused by the result guard (dir already existed from the fixture-hold-exploded run) and
  `run-shutdown-2`/`run-port-disconnect(-2)`/`run-failure-isolation(2)` PC-side adb captures contain
  `'adb' is not recognized` (adb is not on the scheduled-task PATH) — phone evidence for those runs comes
  from local captures named `*-retry.txt`/`*-POST*.txt` instead. None of these were retried into a pass;
  every driver regression was fixed in code with new tests first.

## What the evidence shows (W-07 / W-08 semantics, Chrome-launched)

1. **The host exits BY ITSELF on both EOF paths.** In every Chrome-launched teardown (`port.disconnect()`
   from the connector page and connector-tab removal), the host traced its own `exit code=0`, with
   `stdout_drained=true`, every linked connection `released` before exit, the bounded shutdown join
   (N/N within 10 ms) before exit, and the process was confirmed gone in 0.56-0.57 s — far inside
   Chrome's ~2 s lingering-host kill. A host that merely disappears would have failed; none did.
2. **No shutdown handshake on EOF paths** — `shutdown_complete` appeared on none of the disconnect paths
   (driver asserts; mechanism `stdin_closed`).
3. **No frames after closure; stdout EOF at a frame boundary** (direct stdin runs assert
   `stdout_eof_state=clean`; the Chrome EOF runs verify trace exit + drained output).
4. **`VELES_OTP23_DETACHED_CLOSE` unset** — recorded for the driver environment AND the environment
   actually passed to Chrome (`trace_env_passed_to_chrome`).
5. **Phone link release** — the pre-declared bound (PLAN.md, 30 s) held with large margin: the phone's
   ConnectionState history shows DISCONNECTED for the central's masked address 3.0-3.8 s after the
   host's own exit line in every retained-burst run (`phone-link-release-bound.json`), using clock
   offsets measured before the runs (`clock-offset.json`: 6T +726 ms ±1485 ms, 10Pro +402 ms ±922 ms).
   The host trace still states link-down as unconfirmed at the object level; the phone history is the
   link-down proof.

## W-02 connected-phase observation (W-02 itself stays owner-decided)

Per connected-phase sample (after both links byte-proven, while a 4096-byte record is in flight and
after its echo, with an active scan; positive controls per sample):

- **The host owns zero windows of every kind** (top-level visible, hidden, message-only, children) and
  **zero TCP/UDP endpoints in all four families** at every sampled point in every run.
- **The wrapper** (Chrome's default `%COMSPEC%` launch) owns exactly **1 never-visible console window**
  in every sample — `ConsoleWindowClass`, `IsWindowVisible=false`, owner = cmd.exe pid; session 1.
  This reproduces the historical "wrapper hidden 1" and — per the matrix's literal W-02 wording
  ("zero windows owned by the host and its wrapper") — is a **criterion conflict**, recorded as
  `w02_wrapper_hidden_window_max: 1` with the scenario status **BLOCKED `criterion_conflict`**, not
  silently passed and not silently weakened. Proposed owner wording (from PLAN.md): *"The plain host
  owns no top-level window (visible or hidden) and no network endpoint; the Chrome-created cmd.exe
  wrapper of Chrome's default Windows launch may own one never-visible console window; any visible
  window fails."* The optional direct-launch diagnostic (feature flag, non-acceptance) was NOT run.
- Positive controls held in every sample: the same enumeration saw the owned Chrome pid's visible
  top-level window(s) and the same endpoint table saw the Chrome CDP listener; observer, Chrome, wrapper
  and host session ids all matched the active console session (1) in every sample (the enumeration
  context was auditable).
- Samples are labelled `sampled` (5 timestamps per run, each trace-correlated via
  `trace_offset_ms_estimate` + `trace_bracket`, e.g. between the `sent` and `message` lines of the two
  link probes). No continuity claim is made.

## Provenance

- Console session launches: Chrome and host ran in the logged-in interactive console session (session 1)
  via `/IT` scheduled tasks; per-sample session identity equals `WTSGetActiveConsoleSessionId` (preflight
  `console-preflight.json`; session equality asserted per sample).
- Driver/binary identity: `tree-manifest.json` (driver scripts sha256 per file; fixed binary sha256;
  Chrome path + version). `source_sha256` inside each scenario JSON carries the driver files the run
  actually used (measured by the driver itself).
- Registration: HKCU `me.nagaev.veles.otp23.plain` was ABSENT before registration and was written with
  the scoped manifest pointing at a per-user copy of the fixed binary (registration output recorded the
  exact hash). It is removed after the batch (see package1 result report for the unregistration record).
- Phone fixtures: installed APKs hash-verified against the approved fixtures before starting
  (`fixture-identity.json`); no reinstall, no wipe.
- Clock: phone-minus-PC offsets measured pre-run over 9 paired samples per phone
  (`clock-offset.json`), used only for the pre-declared 30 s bound correlation.
## W-11 / W-12 strict background (Windows, latest installed stable Chrome)

Both strict runs reuse the accepted strict driver/finalizer logic unchanged (`strict_background.py` +
`strict_phone_validation.py`; `formal_otp23_acceptance=false` by design; the matrix adjudicates):

| run | result | key facts |
|---|---|---|
| `retained-strict-cold-launch-denied-1.json` (attempt at 04:24:15Z) | RETAINED FAIL | one transient `PermissionError` reading `DevToolsActivePort` from the fresh temp profile (same race as the lifecycle tab-close launch denial); `ChromeRun` verified standalone OK in the identical /IT task context immediately after (Chrome/156.0.8078.73) |
| `strict-windows-cold-2.json` (W-11 COLD, 04:31:34Z) | driver PASS | Chrome/156.0.8078.73; exemption scan findings=[] (no opt-outs/CDP/keepalive/flags); foreground gate confirmed before ANY BLE work (owned pid 99388, page visible+focused; the window had just appeared and taken foreground, owner present clicking) AND the after-switch guard re-verified owned-pid visible+focused; two phones scanned and connected (two byte-proven handles via the record digests); NO desktop→phone send (COLD); ordinary tab focused, connector hidden, every DevTools client closed; idle 720.0 s; BOTH unsolicited pushes arrived during the closed-CDP idle (each len=4096, visibility=hidden, focused=false, same record digest both phones); phone evidence pulled for both serials, strict phone validation PASS (clock offset −538 ms ±1854 ms union bound over the two individually measured offsets); shutdown complete, host gone |
| `strict-windows-prewarm.json` (W-12 PREWARM, 05:01:43Z) | driver PASS | same gates; BEFORE the idle exactly one byte-verified 4096-byte exchange per phone was completed (seeds 41/53, sha256 recorded per phone); idle 717.8 s with every DevTools client closed; both unsolicited pushes arrived during the idle (len=4096, hidden, unfocused); phone validation PASS; shutdown complete, host gone |

Phone holds: fixtures restarted fresh with `-e otp23_unsolicited_after_s 690` immediately before each run
(restarts 04:31:22Z COLD, ~05:01:2XZ PREWARM; both phones `otp23-ready` = advertising before each launch).
The 6T instrumentation log holds JUnit `OK (1 test)` results for both holds (`Time: 605.726` at
04:41:27Z, `Time: 827.261` at ~05:15:26Z); the 10Pro hold finished cleanly as well (`fixture-10Pro.instrument.log`).
Idle test processes of `me.nagaev.veles.otp23.test` remain running on both phones after the holds
(the known outlived-hold residue from previous packages; no force-stop was performed and no app state was touched).

Registration hygiene: HKCU `me.nagaev.veles.otp23.plain` queried before and after the whole batch —
present only between registration (before COLD) and unregister (after PREWARM), then removed with the
install root; final `reg query` finds 0 veles keys.

Owner approval for W-02 wording recorded verbatim in `owner-approval-record.json` (scope: W-02 only).

## SUPERSEDING CORRECTIONS (2026-10-28 finish phase; appended — the sections above stay as history)

The B5 finish phase (GLM-5.3-flash, source-only, no hardware) re-derived the phone timing and sample
bracket claims and corrects four narrative over-claims in this FINDING. All raw files in this folder
and in `../package1-p2-20261028/` stay byte-identical (their `SHA256SUMS` verify 181/181 and 34/34);
the corrections live in `../package1-b5-v2-20261028/`:

1. **Clock offset direction (B5-1).** The measured field is `offset_phone_minus_pc_ms_median =
   phone_epoch - pc_epoch`, so the PC-comparable conversion SUBTRACTS it. The phone gaps printed
   above (from `phone-link-release-bound.json` and the P2 association) were computed with a `+`
   addition and OVER-STATE the release times: corrected values are 6T **+2322 ms** `[+629, +4015]`
   and 10Pro **+2406 ms** `[+1482, +3330]` for the P2 port-disconnect run (`phone-burst-association-v2.json`),
   and 6T **+2304 ms** `[+819, +3789]`, 10Pro **+2310 ms** `[+1388, +3232]` for old `run-tab-close-2`
   (`tab-close-phone-bound-v2.json`). Both full uncertainty intervals remain after the host's own exit
   and far below the pre-declared 30 s bound; the physical conclusion is unchanged, the arithmetic was
   not.
2. **The old 6T standalone DIS row is NOT a bound proof.** For old `run-port-disconnect-2` the 6T
   phone's own CONNECT had rotated out of the 16-row history; its retained DISCONNECTED row
   ("+3.8 s" in the run table above) stayed a diagnostic only (the old bound JSON itself already
   records `burst_own_run_found: false` with a problem). W-07's release-bound claim rests on the P2
   transport-telemetry association v2, not on that row or on any pre-correction burst-settled claim.
3. **"No launch flags" is narrowed (this page's Chrome sentence).** No evidence supports a universal
   "no launch flags anywhere" claim. Precise record: the driver passes no throttling/backgrounding/
   direct-launch override flags; ST-13 in `strict-windows-cold-2.json` / `strict-windows-prewarm.json`
   recorded the actual Chrome command line source `proc.args` (9 args) with **zero** override-flag
   findings; the retained cold-launch-denied attempt honestly recorded `command_line_not_recorded`;
   the 9 argv bytes themselves were not retained verbatim. Chrome is additionally recorded from CDP as
   `Chrome/156.0.8078.73` (latest at run time, auto-updated from `.48` between packages).
4. **Foreground "clicking" is narrowed (W-11 COLD row).** The instrumented foreground gate was: the
   owned Chrome pid visible+focused, confirmed after ONE poll per `strict-windows-cold-2.foreground-ready.json`
   (instruction recorded, `state: confirmed`, `polls: 1`). No synthetic input was injected; the physical
   click itself was NOT instrumented — the owner declared readiness and the gate re-verified its state.
5. **W-02 wording owner amendment (2026-10-28, operator-captured):** the approved clarification
   ("The plain host owns no top-level window, visible or hidden, and no network endpoint. Chrome's
   default Windows launcher may own one never-visible console window; any visible window or network
   endpoint owned by the host or its wrapper fails.") is recorded in RFC section 18 and applied to
   W-02 ONLY in `../package1-b5-v2-20261028/w02-adjudication.json`. The raw `BLOCKED criterion_conflict`
   scenario records above are NOT rewritten; no other row is affected.
6. **Status honesty.** The five Windows candidate rows (W-02, W-07, W-08, W-11, W-12) have reviewed
   or conditionally-recommended evidence; Mac M-08 is owner-DEFERRED (not measured to PASS): the owner
   declined the Chrome update/restart (`mac-deferred` record sha `ace5e31e…e99`), so no Mac lifecycle
   measurement exists and none may be fabricated. Remaining open for package completion: W-10, A-01,
   M-03/M-04/M-11. The overall decision is NO-GO and OTP-24 is not authorised; the v2 artifacts await
   the parent's independent fix verification before any canonical promotion. The first standalone Mac
   SETUP record was overwritten at capture time (`--overwrite`); only surviving stdout details remain,
   so the Mac setup phase stays unmeasured (no BLE) — the gap is recorded, never reconstructed.
