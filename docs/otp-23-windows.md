# OTP-23 Windows feasibility

## Decision: NO-GO for OTP-24

Windows-first investigation is implemented as a disposable harness under
`native-bridge/feasibility/`. Physical Windows testing found two blockers:

1. **The frozen fragmentation profile is incompatible with Android's 512-byte
   attribute limit at negotiated MTU 517.** Its exact non-final size is MTU − 3
   = 514 bytes including the fragment header. A 4095-byte synthetic round trip
   crashes the Android peripheral with `IllegalArgumentException: notification
   should not be longer than max length of an attribute value` at
   `BluetoothGattServer.notifyCharacteristicChanged`. This needs an explicitly
   reviewed protocol correction (for example a 512-byte attribute cap), changes
   to both receivers/senders, and new maximum-MTU contract vectors. This harness
   deliberately preserves the frozen wire contract rather than hiding the bug
   by clamping MTU locally or claiming a maximum-record pass.
2. **The Tauri runtime is not windowless on the interactive Windows desktop.**
   Tauri 2.6.0 creates a visible `Tao Thread Event Target` top-level tool window
   at `(0,0,13,13)`, style `0x94000000`, ex-style `0x080800a0`. Earlier session-0
   runs reported it hidden; those do not establish desktop behavior. The plain
   Rust baseline creates no window. The runtime also needs a forced-exit fallback
   after `AppHandle::exit` (see exit-probe artifact). Retaining Tauri for packaging
   alone needs revisiting independently of running its event loop.

**Do not start OTP-24 or close #86 based on this PR.** Revisit the RFC for these
findings and complete the remaining Windows matrix after corrections. macOS is
deferred by the user's Windows-first direction; no CoreBluetooth/TCC claim is made.

## Environment and identities

- Source base: `6e083e0ec5cb70d1bc8c5f98abca536e190722e1`; harness added in this PR.
- Windows 11 Pro 10.0.26300 x64, Qualcomm FastConnect 7800 Dual Bluetooth Adapter.
- Stable Chrome 155.0.8059.85; separate disposable profiles, fixed extension ID
  `gmekhgkipjkikmaadaajljinkgfielei`, exact-origin per-user HKCU manifests.
- Rust 1.98.0, `x86_64-pc-windows-msvc`; MSVC 14.51.36231, Windows SDK
  10.0.26100.0; Tauri 2.6.0 / tauri-build 2.3.1.
- OnePlus 10 Pro (NE2215), Android 15/API35, and OnePlus 6T (ONEPLUS_A6013),
  Android 15/API35. Both attached by USB; actual record transport was BLE.
- Controller builds Android locally. Opt-in init script installs a separate
  `me.nagaev.veles.otp23` debug package, preserving existing app data/signatures.
  `SharingFeasibilityTest` holds advertising for up to 30 minutes only with
  explicit `-e otp23 true`. No notification-listener permission was enabled.
- Account is an administrator; Chrome/host were measured at medium integrity in
  interactive session 1. This is **not** a fresh standard non-admin account test.

## Physical matrix (2026-10-25)

| Case | Expected | Actual |
|---|---|---|
| Stable Chrome launch | on-demand native host, correlated hello/ready | Plain: 12/12 driver cases passed in interactive session; Tauri: 11/12, visible helper window fails CH-03 |
| Two independent phones | distinct connection handles, MTU before CCCD | PASS: MTU 517 on both; setup completed in <3.3 s |
| Initial whole-record write | usable characteristics after discovery | Initially failed `RO_E_CLOSED` (`0x80000013`); fixed by retaining `GattDeviceService` for the link lifetime; physical retry passed |
| Concurrent synthetic records | byte-exact reply on each original handle | PASS at 4, 178, 179, 180, 505, 506, 507 bytes per phone; distinct request IDs and per-phone hashes. These do not bracket the actual MTU-517/512-byte attribute edges |
| 4095/4096-byte records | independent maximum-record delivery | FAIL at 4095: both sends reported GATT completion but no response; OnePlus 10 Pro instrumentation records Android 514-byte notification exception; 4096 not reached |
| Disconnect-one/keep-other | second phone still exchanges 4096 bytes | Not reached after size failure; MTU-185 simulation passes (does not model Android's attribute cap), physical remains pending |
| Real-link shutdown/EOF/port close | release links and leave no process | Baseline scan cleanup and simulated link cleanup pass; complete real-link matrix pending |
| Background record after ten minutes | phone event with ordinary tab focused and no exemptions | Pending; old debugger-attached scan-expiry diagnostic is not acceptance |
| Denied/unsupported states | classified errors | Unit mappings and absent-device real-radio case pass; actual denied/no-radio matrix pending |
| Non-admin account | launch and BLE under standard account | Pending; measured medium integrity under admin account only |
| macOS | CoreBluetooth + unsigned/signed TCC matrix | Deferred |

The 6T hold test eventually expired after the controller did not stop it within
30 minutes; this timeout is a test-fixture failure, not evidence of a BLE error.
The 10 Pro hold test crashed on the oversized notification. Neither result is
counted as a passing instrumentation run.

The 4095-byte crash establishes a 514-byte notification failure, not the smallest
failing record. In particular 508/509-byte records bracket the 512-byte attribute
ceiling and 510/511 bracket the MTU-517 fragment edge; those were not measured in
the original run. After review the script includes these cases (plus 1020/1021)
for the next physical run, without rewriting the historical observations.

## Reproduction

Build APKs **on Linux**:

```sh
JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew \
  --init-script native-bridge/feasibility/scripts/isolated-device.gradle \
  assembleDebug assembleDebugAndroidTest
adb -s SERIAL install -r -g app/build/outputs/apk/debug/app-debug.apk
adb -s SERIAL install -r -g app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb -s SERIAL shell am instrument -w \
  -e class me.nagaev.veles.sharing.SharingFeasibilityTest -e otp23 true \
  me.nagaev.veles.otp23.test/androidx.test.runner.AndroidJUnitRunner
```

Run the instrumentation command for each phone in separate terminals; wait for
`run-as me.nagaev.veles.otp23 ls files/otp23-ready`. On Windows, register the
host as documented in the harness README, then run from an interactive session:

```powershell
python scripts/physical_validate.py --flavour plain --out evidence/physical-plain.json
python scripts/physical_validate.py --flavour tauri --out evidence/physical-tauri.json
```

The physical script has no simulated fallback. Missing phones and setup errors
fail the run. It stops at the first unexpected transfer result. To release a
still-running fixture: `adb -s SERIAL shell run-as me.nagaev.veles.otp23 touch
files/otp23-stop`; the instrumentation test cleans up its service and marker files.

## Evidence and dependency cost

Artifacts live in `native-bridge/feasibility/evidence/`:

- `physical-plain.json` / `.trace.log`: two successful connects and seven sizes,
  then 4095-byte failure after both `sent` replies. Failed PH-01 never reports PASS.
- `physical-tauri.json`: only one peripheral available after the first run;
  **not a Tauri physical connection pass**.
- `diag-plain-medium.json`, `diag-tauri-medium.json`: current interactive-session
  Chrome matrix; previous `chrome-*` files are preliminary session-0 diagnostics.
- `background-10min-plain.json`: scan-expiry event after 622 seconds, with CDP
  attached; **diagnostic only**, not phone delivery or throttling acceptance.
- `tauri-exit-probe.txt`: forced-exit observation. The fallback is explicit in source.
- `cost.json`, charts and `sbom/*.cdx.json`: baseline, dependency-linked Tauri,
  and actual Tauri-runtime comparison. These earlier binaries predate the service
  lifetime fix; costs are measurements of those builds, not final artifact hashes.
  The `rustc` field contains a cargo version string, so use the toolchain pin above.
  Production runtime choice remains NO-GO pending the findings above.

Safe traces record frame sizes/types/request IDs and BLE setup stages, never
record payloads. Synthetic response hashes verify independent records. The host
persists no payload or trust state. Generated local output directories are ignored.

## Local checks

Windows suite: 151 tests passed after the churn regression fix; Linux portable
suite: 150 passed (four radio tests ignored by default). Windows opt-in radio suite:
4/4 passed. Clippy with warnings denied passed on Windows and Linux. Android JVM
suite including synthetic fixture boundary coverage and `spotlessCheck detekt
lintDebug` passed. Production bridge formatting/lint and the connector API check
also passed. The final Opus review is recorded in
[`protocol/reviews/otp-23-windows-opus.md`](protocol/reviews/otp-23-windows-opus.md).
Known physical failures above are intentional feasibility evidence,
not green acceptance claims.

## Follow-up ownership

Track Android interoperability findings and the successfully observed MTU-before-
CCCD ordering in #120. The existing #120 M6 case is **two central clients of one
phone**; two phones connected to this one Windows central do not discharge M6.
Remaining #120 items and the broader lifecycle matrix remain owned by that issue
and Gate C. The protocol cap revision and Tauri runtime decision block OTP-24.
