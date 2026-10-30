# Windows BLE working PoC — handoff

## Purpose and boundary

This branch is an experimental foundation for a later production implementation
session. It includes the prerequisite OTP-23 harness from #121 and can be checked
out independently of that PR. The historical no-go artifacts remain unchanged;
new PoC measurements belong under `native-bridge/feasibility/evidence/poc-*`.

The chosen runtime is the **plain Rust WinRT host**, launched on demand by stable
Chrome Native Messaging. It does not start the Tauri event loop. Tauri's earlier
helper-window and exit findings are therefore avoided in this PoC, not claimed
fixed in Tauri.

## Experimental fragment rule

The PoC caps the characteristic value at 512 bytes:

```text
attribute bytes = min(negotiated ATT MTU - 3, 512)
fragment payload = attribute bytes - 4
```

At MTU 517 this is 508 payload bytes plus the four-byte fragment header. Android
and Rust must use the same cap; mixing this PoC with the old exact-MTU profile
will fail for fragmented records at high MTU. MTU-185 vectors remain compatible.

Android enables the new rule only for the isolated PoC APK built using
`native-bridge/feasibility/scripts/isolated-device.gradle`. Normal Android builds
keep the existing behavior. The frozen production protocol and its fixtures are
not revised by this experiment. The later production session must review a real
contract revision and compatibility strategy before using these bytes in OTP-24.

## Implementation model

Implementation subagents run through OpenCode with exact model
`ollama-cloud/glm-5.3-flash`. Session exports were checked for the actual provider
and model identity. The controller performs integration, Windows execution,
physical device tests and evidence review.

## Reproduce

Prerequisites: Linux Android build environment, Windows Rust/MSVC SDK, stable
Chrome, Python 3, and two physical Android 13+ phones with Bluetooth enabled and
USB debugging authorized. Build Android on Linux, not on the Windows PC.

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

Start the instrumentation on each phone in separate terminals. Readiness is
`adb -s SERIAL shell run-as me.nagaev.veles.otp23 ls files/otp23-ready`.
The fixture lasts up to 30 minutes; finish by creating `files/otp23-stop` with
`run-as`, which lets instrumentation clean up and return a real JUnit result.
If network-forwarded adb installs are slow, transfer the Linux-built APKs to the
PC once and install with its existing adb client/server; never restart adb.

From `native-bridge/feasibility` on Windows:

```powershell
cargo build --locked --release --bin otp23-host
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/invoke-poc.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/invoke-poc.ps1 `
  -Script poc_background.py -OutPath evidence/poc-background.json
```

`invoke-poc.ps1` registers only the plain development host under HKCU, launches
stable Chrome with a disposable profile, runs the selected driver, records a
nonzero exit on failure, and unregisters the host. Add `-Schedule -TaskName test`
when calling remotely from SSH: this uses the logged-in interactive Windows
session at limited integrity rather than trying to show Chrome in session 0.
Poll `evidence/poc-run-status.json`, checking its script and UTC timestamps to
avoid reading an earlier run. Remove the exact temporary task afterward:
`Unregister-ScheduledTask -TaskName Veles-OTP23-PoCtest -Confirm:$false`.

## Physical measurements

2026-10-25, Windows 11 Pro 10.0.26300, Chrome 155.0.8059.85, Qualcomm FastConnect
7800 adapter; OnePlus 10 Pro and OnePlus 6T, both Android 15/API35:

- `evidence/poc-physical-final.json`: **PASS**, 14.5 seconds on the final APK.
  Both phones negotiated MTU
  517, using 508-byte fragment payloads / 512-byte attributes.
- Byte-exact concurrent replies verified per handle at 4, 178, 179, 180,
  505–511, 1015–1017, 4095 and 4096 bytes, plus three extra dual-4096 rounds.
- Disconnected handles reject sends; the survivor still exchanges 4096 bytes.
- Graceful shutdown with a real link removes the host and cmd wrapper (see the
  measured duration in the final JSON).
- Plain host owns zero top-level windows and zero network endpoints.
- Measured host SHA-256:
  `a3f92ec2e776ce67ae6ece98e4e2c4bf471504c50b87d5f493145cfa66b56f9c`.
  The script's best-effort source revision was unavailable in the scheduled
  process; `evidence/poc-build-identity.json` supplies source-file and APK hashes.
  The final installed APK hash was read back from both physical phones and
  matched the Linux-built APK.

`evidence/poc-background-final.json` and its `.trace.log`: **PASS**. Both phones
returned byte-exact 4096-byte replies after sends at 610.378 s and 610.472 s
with the connector hidden. That target's CDP session stayed detached for 630 s
without polling; replies were present at the first read after reattachment
(`reattachment_may_have_woken_page = false`). Shutdown removed the host in 0.3 s,
and no host processes survived. The host trace independently places the replies
before reattachment. This is PoC liveness evidence only: browser-level CDP remains
connected, so all formal timer-exemption conditions are not established.

The first no-warmup ten-minute run returned `send_failed`; its result remains in
`poc-background-initial-failure.json`. Later warmup diagnostics also exposed two
driver bugs (an immediate read before asynchronous notify, and misplaced JS byte
conversion); both have regression coverage. The final background scenario uses
an actual byte-verified pre-idle exchange. Cold first-send-after-idle behavior
needs separate production validation; do not infer it from a warmed-up run.

## Checks and review

- Windows Rust suite: **147 passed**, four opt-in radio tests ignored by default;
  Clippy with warnings denied passed.
- Kotlin cap regression, default/frozen behavior, full JVM suite and required
  Spotless/Detekt/Android lint gate passed. The generated release BuildConfig
  explicitly has `OTP23_POC = false`; the isolated debug build has it true.
- Driver checks: three Node tests, eight pure trace-parser checks, and 25 Python
  background regression tests passed; these do not replace physical evidence.
- [Opus 5.5 review and disposition](protocol/reviews/windows-ble-poc-opus.md)
  records the model identity, addressed findings and deferred experimental notes.

The counts above come from actual test result lines. Earlier prose totals in
the historical OTP-23 investigation overstated its Rust test count; this handoff
uses the freshly measured Windows total.

## What the later production session must decide

- Incorporate/review the capped attribute rule in the real protocol, vectors and
  compatibility/version strategy; the normal app still uses the frozen rule.
- Keep a plain executable runtime and decide independently whether Tauri is
  useful for packaging. The Tauri event loop is not used by this PoC.
- Complete macOS CoreBluetooth/TCC, true standard-account/permission matrices,
  formal background-liveness acceptance, Android multi-central/radio stress,
  and production lifecycle hardening.
- Add authenticated sessions and real OTP delivery only in their planned tasks.
  This PoC uses deterministic synthetic data and grants no notification access.

This PoC is reusable test material and implementation reference, not a claim
that OTP-24 or the full OTP-23 acceptance gate is complete.
