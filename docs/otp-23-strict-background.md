# OTP-23 strict background validation: operational guide

Scope: how to run `native-bridge/feasibility/scripts/strict_background.py`, finalize it offline when the
phones are not attached to the driver's machine, and what the safeguards do and do not prove. It decides
nothing: `formal_otp23_acceptance` is always `false`; `docs/otp-23-requirements-matrix.md` (W-11, W-12 and the
macOS rows) adjudicates. The older `poc_background.py` evidence is untouched and is not this validation.

## What a PASS requires (checks ST-01 .. ST-14)

| Check | Meaning |
|---|---|
| ST-01..ST-03 | connector hidden and an ordinary tab visible+focused before the last DevTools client closes; no DevTools client (target or browser level) and zero CDP calls for >= 600 s; the loaded extension files contain no audio/WebRTC/socket/interval/lock/keepalive API |
| ST-04..ST-07 | each of two phones delivers exactly one byte-exact 4096-byte unsolicited record, received hidden/unfocused, >= 600 s after DevTools closed and before re-attach; host trace and the HTTP target-list title agree |
| ST-08 | phone evidence validated by `strict_phone_validation.py` for EVERY acquisition path (driver `--adb`, `--phone-evidence SERIAL=PATH`, offline finalizer): source provenance, distinct sources/files, freshness, queued/hash/delay, and each phone's `fired_at` matched to its own distinct page receipt within an explicit clock allowance. Missing validation, a bare path or missing clock evidence is BLOCKED; nothing falls back to a weaker check |
| ST-09 | cold: no desktop->phone send in the whole run; prewarm: exact warm-up reply hashes per phone and host-trace sends == number of phones |
| ST-10 | shutdown complete, Chrome closed, no surviving host process counted **after** Chrome closed |
| ST-11 / ST-12 | **preconfigured visibility/focus histories** (below) of the connector and the ordinary tab |
| ST-13 | recorded Chrome command line has no throttling/backgrounding/freezing exemption flag |
| ST-14 | `min_background_s >= 600` and not a diagnostic short run |
| ST-15 | Every platform: the real OS foreground owner (Windows: frontmost console window; macOS: the frontmost application of the user's login session) belongs to this run's own Chrome PID (directly or through its ancestry) before any BLE work and again after the ordinary-tab switch. On macOS each sample carries two pieces of locked-screen counter-evidence: the REQUIRED page-state guard (the sampled page's own `document.visibilityState` must be `visible`, `hasFocus` recorded — connector at the gate, ordinary tab after the switch — read over CDP at sample time; a locked console hides Chrome pages, so an owned pid during a lock cannot confirm) and the session's `CGSessionCopyCurrentDictionary` lock/console state (native ctypes query; on console `kCGSSessionOnConsoleKey` and unlocked `CGSSessionScreenIsLocked` absent/false; locked/off-console/NULL/unreadable leaves the sample unverifiable). A gate nobody confirmed, a missing page-state guard (macOS) or unverifiable foreground is BLOCKED; a mismatch after the switch, BLE work not provably after the confirmation, or a hidden page at the after-switch sample (macOS) is FAIL. Legacy raws that recorded `{"required": false}` and no gate (macOS before this gate existed) stay BLOCKED; the macOS gate is not waived |

### Histories (ST-11, ST-12)

Both pages record their own history with listeners only (no timers, intervals, audio, WebRTC or keepalive):
the connector in `extension/connector.js` from page load (`otp23.visibilityHistory()`), the ordinary
`about:blank` tab via `core.ORDINARY_HISTORY_JS`, installed before the tab is activated and before any
DevTools client closes. Each history holds the initial visibility/focus plus timestamped `visibilitychange`,
`focus`, `blur` (and informational `freeze`/`resume`) events with a contiguous `seq`. Both are read only after
the measurement, after re-attach. They are rejected when absent, truncated (2000-event cap, flagged, never
silent), gapped, missing initial visibility/focus, from a replaced page (token mismatch), installed after the
close, shorter than 600 s, or when any event inside the interval leaves the required state (connector hidden +
unfocused, ordinary tab visible + focused), even if a later event restores it. The cut time is
`cdp_reopened_wall`, taken **before** the re-attach; events after it (caused by attaching) are counted but
never used.

### OS-foreground gate (ST-FG, before hello and any BLE work)

`chrome.windows.update` cannot take the OS foreground from another application, and the driver uses no
synthetic input, `SetForegroundWindow`, `AttachThreadInput`, screen recording, key events or accessibility
control. After Chrome and the connector are up (and before the native hello and before any scan or connect,
so the phones' 690 s timer keeps its full >= 600 s margin) the controller writes
`<--out stem>.foreground-ready.json` with `state: "awaiting_physical_click"` (outside the browser), prints
the same, and polls the REAL OS foreground owner every 0.5 s for at most `--foreground-wait-s` (default 180;
`-ForegroundWaitSeconds` in `invoke-poc.ps1`) until it is the launched Chrome PID. A human must physically
click that Chrome window. The reader is platform-specific and never faked: Windows polls Win32
`GetForegroundWindow`; macOS queries `AppKit.NSWorkspace.sharedWorkspace.frontmostApplication` via an
`osascript -l JavaScript` one-liner (returned as `pid|name|bundleIdentifier`), which reads LaunchServices
state with no AppleEvent to another app (no Automation permission, therefore no prompt). That the reader
sees the console's frontmost application from the exact execution context the macOS runs use is required,
not assumed: a pre-measurement calibration in that context (raw `pid|name|bundle` outputs against human
clicks of the test Chrome, other applications including Finder — and, when open, the owner's personal
Chrome — and back to the test Chrome) is recorded and checked before any measured run; if the reader
cannot distinguish there, ST-15 stays BLOCKED and the gate is never relaxed. The ready file then becomes
`confirmed`, `timeout` or `unverifiable`; anything but `confirmed` stops the run before any BLE work and
leaves ST-15 BLOCKED. The poll lives only in the controller; no page-side timer or keepalive is added.
After the ordinary tab is created and focused, and **before every DevTools client closes**, the foreground
owner is sampled again; a different owner stops the run (ST-BG) and ST-15 FAILs. On macOS every sample
additionally carries two pieces of locked-screen counter-evidence:

* **Page-state guard (REQUIRED at both sample points).** The controller reads the page's own
  `document.visibilityState` + `document.hasFocus()` over CDP at sample time (connector page at the gate,
  ordinary tab after the switch — the connector is hidden there by design). A locked console makes Chrome
  pages hidden, so an `owned` pid read during a lock cannot look unlocked: confirmation additionally
  requires `ok` and ST-15 FAILs (or stays BLOCKED when the guard was not recorded) on macOS runs whose
  page state contradicts an unlocked, visibly-front console. This is the preflight-approved equivalent
  guard, recorded and required at both sample points; it is lock counter-evidence (a locked console hides
  Chrome pages), not a proof of foreground by itself — foreground ownership remains the pid/session checks.
* **Native session dictionary (recorded; verified when readable).** The session's lock/console state is
  read with `CGSessionCopyCurrentDictionary` called natively through ctypes (`CFPropertyListCreateXMLData`
  + plistlib) and recorded as `session`; a locked screen, an off-console session or an unreadable/NULL
  dictionary (including a non-GUI execution context) leaves the sample unverifiable, so a locked screen
  can never read as owned. The previous osascript-JXA route is retired: live macOS 26 calibration
  (2026-10-27) proved the ObjC bridge cannot call this C function there at all (the symbol reference and
  even a parenthesised call yield the bridge function object; every NULL wrapper is truthy; deepUnwrap
  prints nothing), so JXA is used only for the foreground reader, never for the session query.

Evidence: `evidence.os_foreground` (`gate`, `gate_confirmed_mono`, `ble_start_mono`, `after_switch`,
`reader`, `platform`, `page_state_guard`, `session_guard` on macOS). macOS has the same gate now;
`{"required": false}` legacy macOS evidence is BLOCKED, not waived.

Sample audit content (macOS): each sample in `gate.last` and `after_switch` keeps the foreground
application's `name` and `bundle` plus the foreground pid's `ps` command line (`ps_args`), so an
`owned` verdict is auditable (the pid is the dedicated-profile Chrome, not a recycled pid). Windows samples
keep their unchanged GetForegroundWindow semantics; Windows audit enrichment is not needed there.

### Ordinary tab setup (same window)

`Target.createTarget` + `activateTarget` can open the tab in a different window, leaving the connector
visible (first Windows cold run stopped early at ST-BG for that reason). The driver now runs, inside the
connector page, `chrome.tabs.getCurrent()`, `chrome.tabs.create({windowId: <connector window>, url:
about:blank#otp23-ordinary-<uuid>, active: true})` and `chrome.windows.update(<window>, {focused: true})`
(no manifest permission needed), finds the CDP target by that unique URL, asserts both targets report the same
`Browser.getWindowForTarget` window, installs the history, and requires connector hidden / ordinary tab
visible AND focused before any DevTools client closes (the run stops with ST-BG otherwise). The binding is
recorded as `evidence.same_window_binding`. No visibility/focus emulation, minimisation or keepalive is used.

### Chrome flags (ST-13) and the (clarified) Native Messaging caveat

The launched argv (`proc.args`, also set by `MediumProcess` on Windows; a single-string OS command line is tokenised, never skipped) is stored in
`evidence.chrome`. Flags such as `--disable-background-timer-throttling`, `--disable-renderer-backgrounding`,
`--disable-backgrounding-occluded-windows`, `--disable-ipc-flooding-protection` or a
`--disable-features`/`--enable-features` value touching throttling/freezing/occlusion/wake-up/backgrounding
FAIL the run; an unrecorded command line FAILs too. The static extension API scan is only supporting evidence.

**Clarified, not relaxed:** ST-13 proves absent *flags*, not Chrome-internal behaviour. The previously
recorded open conflict ("an open Native Messaging port may itself be an exemption, interpretation
`native_messaging_exemption_not_excluded`") is superseded by the owner-approved RFC clarification of
2026-10-27 (OTP-23 only): event delivery through the architecture's Native Messaging port is not a
timer-throttling exemption; ordinary timers and the port itself are permitted, and the criterion keeps
forbidding opt-outs, artificial keepalive, audio, WebRTC, exemption flags and DevTools clients during idle.
Its unsatisfied-report condition (a same-build comparison in which a port tab escapes intensive timer
throttling while a port-less control tab does not) belongs to the separate same-build probe evidence, which
on Chrome 156.0.8078.48 on Windows and macOS found port tab and port-less control both intensively
throttled. The ST-13 detail records `native_messaging_port` with this clarified reading; the requirements
matrix still adjudicates and `formal_otp23_acceptance` stays false.

### Short diagnostic runs

`--min-background-s` below 600 is refused unless `--diagnostic-short` is also given. Such a run is labelled
`diagnostic_short`, fails ST-14, and reports `DIAGNOSTIC_ONLY` at best. It can never be a formal strict PASS.
(The phones' `otp23_unsolicited_after_s` must still equal `--phone-delay-s`.)

## Running (existing signed Mac bundle, plain Windows host; no rebuild, no re-signing, no Android work)

Phones: both already run `SharingFeasibilityTest` with `-e otp23 true -e otp23_unsolicited_after_s 690` and
are online. Run **cold and prewarm as two separate runs**, one after the other, each >= 11.5 minutes of idle.

### macOS (Aqua session; the packaged `me.nagaev.veles.bridge` bundle at `target/macos-poc/VelesOtp23PoC.app`)

```bash
cd native-bridge/feasibility
./scripts/macos-register.sh                      # existing bundle only; unregister afterwards
python3 scripts/strict_background.py --scenario cold    --out evidence/strict-mac-cold.json
./scripts/macos-unregister.sh
./scripts/macos-register.sh
python3 scripts/strict_background.py --scenario prewarm --out evidence/strict-mac-prewarm.json
./scripts/macos-unregister.sh
```

The OS-foreground gate (ST-FG) now runs here too: after the connector page opens, a human must click the
test Chrome window (physical click, up to `--foreground-wait-s`, default 180 s) so the frontmost
application is the run's own Chrome; the run stops before any BLE work otherwise. Each macOS sample also
records the page-state guard (required: the sampled page's `document.visibilityState` must be `visible`)
plus the session's `CGSessionCopyCurrentDictionary` lock/console state (native ctypes query; unverified
when the screen is locked, the session is off-console or the state is unreadable) plus, for audit, the
foreground application's name/bundle and its `ps` command line. The phones are on the PC, so these end `BLOCKED` on
ST-08 only. Never overwrite them; finalize below.

### Windows (interactive desktop session; scheduled, limited token, owned cleanup)

```powershell
cd native-bridge\feasibility
.\scripts\invoke-poc.ps1 -Schedule -Script strict_background.py -Scenario cold `
    -AdbPath "C:\path\to\adb.exe" -PhoneSerials "SERIAL_A,SERIAL_B" -TaskName StrictCold
# poll evidence\poc-run-status.json for exit_code, then remove the exact task:
Unregister-ScheduledTask -TaskName Veles-OTP23-PoC-StrictCold -Confirm:$false
.\scripts\invoke-poc.ps1 -Schedule -Script strict_background.py -Scenario prewarm `
    -AdbPath "C:\path\to\adb.exe" -PhoneSerials "SERIAL_A,SERIAL_B" -TaskName StrictPrewarm
```

Output defaults to `evidence/strict-<scenario>.json`. The wrapper registers the plain host (HKCU only), runs
`python scripts\strict_background.py --scenario X --out ...` (never `--flavour`), always writes
`poc-run-status.json` (serials are never written; only their count), and unregisters only its own host.
The driver pulls the phone evidence itself after the idle interval; failures are recorded in
`evidence.phone_evidence_errors` and leave ST-08 BLOCKED, never a crash.

## Offline finalization (phones on the PC, driver on the Mac)

After each Mac run's idle interval has ended, on the PC:

```bash
adb -s SERIAL_A shell run-as me.nagaev.veles.otp23 cat files/otp23-unsolicited.json > phone-a-cold.json
adb -s SERIAL_B shell run-as me.nagaev.veles.otp23 cat files/otp23-unsolicited.json > phone-b-cold.json
```

Copy the two files to the Mac (one per physical phone) and run. Each file is passed as `SERIAL=PATH`: the
serial is the explicit per-file source identity and is published only as a salted hash. A bare path has no
provenance and can never PASS. `peer_sha256_prefix` hashes the *central's* address, so two real phones may
share it; it is recorded as information and is never used to claim or deny distinct phones. Phone->desktop
clock evidence is required (`--phone-clock-offset-ms` = desktop minus phone, `--phone-clock-uncertainty-ms`,
`--phone-clock-source` describing how it was measured, e.g. midpoint of N samples); without it ST-08 is
BLOCKED. Each phone's `fired_at` (+offset) must match a distinct page receipt within uncertainty + 30 s.
On Windows the same three values are `-PhoneClockOffsetMs`, `-PhoneClockUncertaintyMs`, `-PhoneClockSource`.

```bash
python3 scripts/strict_background_finalize.py --raw evidence/strict-mac-cold.json \
    --phone-evidence SERIAL_A=phone-a-cold.json --phone-evidence SERIAL_B=phone-b-cold.json \
    --phone-clock-offset-ms OFFSET --phone-clock-uncertainty-ms UNC --phone-clock-source "midpoint of 7 samples" \
    --out evidence/strict-mac-cold.adjudicated.json
```

The raw artifact is only read (sha256, size recorded); `--out` must not exist and is created exclusively.
Missing files are `BLOCKED`; duplicate files (path, bytes or peer), extra/stale pushes, a push outside the
raw run's window (+-120 s + uncertainty), a failed (unqueued) push, a wrong hash, length or delay, a phone whose
fired_at matches no distinct receipt, or a file older than the end of the idle interval are `FAIL`. Serials appear only as hashes. Exit 0 = PASS, 1 = adjudicated not PASS, 2 = usage/overwrite refusal. The output also re-evaluates
the raw checks and flags any raw check that disagrees with recomputation.

## Tests

```bash
cd native-bridge/feasibility/scripts
python3 -m unittest test_strict_background test_strict_background_finalize
node --test connector-api.test.mjs
python3 -m py_compile strict_background.py strict_background_core.py strict_background_finalize.py
```

## Limitations

- Histories use the page's wall clock (`Date.now()`) against the driver's `time.time()` on the same machine.
- ST-13 is flag-level only (see the Native Messaging caveat above).
- Finalization trusts the phone files as supplied; distinctness, freshness and content are checked, not their
  physical origin.
- `invoke-poc.ps1` strict mode is covered by source guards only (no PowerShell on the authoring host).
