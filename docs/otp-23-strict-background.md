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

### Chrome flags (ST-13) and the Native Messaging caveat

The launched argv (`proc.args`, also set by `MediumProcess` on Windows; a single-string OS command line is tokenised, never skipped) is stored in
`evidence.chrome`. Flags such as `--disable-background-timer-throttling`, `--disable-renderer-backgrounding`,
`--disable-backgrounding-occluded-windows`, `--disable-ipc-flooding-protection` or a
`--disable-features`/`--enable-features` value touching throttling/freezing/occlusion/wake-up/backgrounding
FAIL the run; an unrecorded command line FAILs too. The static extension API scan is only supporting evidence.

**Known literal conflict, not relaxed:** ST-13 proves absent *flags*, not that Chrome has no *internal*
exemptions. An open Native Messaging port may itself exempt the page from timer throttling/freezing. The RFC
sentence ("no ... other timer-throttling exemption active") can therefore be literally unsatisfiable for any
Native Messaging transport. The result carries `native_messaging_exemption_not_excluded: true`; whether that
is an exemption the RFC means is for the matrix/owner to decide.

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

The phones are on the PC, so these end `BLOCKED` on ST-08 only. Never overwrite them; finalize below.

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
