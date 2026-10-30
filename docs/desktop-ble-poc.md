# Desktop BLE PoC — Windows and macOS handoff

## What this branch provides

A standalone experimental baseline for a later production session: stable Chrome
launches one plain-Rust Native Messaging host, which exchanges deterministic
synthetic records with two nearby Android peripherals. Windows uses WinRT;
macOS uses CoreBluetooth through pinned `bluest` 0.6.9. The Tauri runtime is not
started, so its helper-window and zero-window exit issues are avoided.

The branch includes the prerequisite harness from #121; that PR need not be
merged first. Windows checkpoint: **`d5b6f2d`**, with the detailed build and test
instructions in [windows-ble-poc.md](windows-ble-poc.md).

## Experimental wire rule

Both ends use `min(ATT_MTU - 3, 512) - 4` record-payload bytes per full fragment.
Android enables it only through the isolated `.otp23` debug build with
`BuildConfig.OTP23_POC=true`. Normal debug/release builds retain the frozen wire
behavior; the generated release flag was checked false.

The experimental Rust crate uses the cap unconditionally. To reproduce the
original uncapped no-go experiment, use commit `1a34191`. Mixing the new host
with a normal Android APK at a high MTU is not a supported configuration. A
production wire-contract revision/version strategy remains future work.

## Reproduce on macOS

The phones can stay USB-connected to the Windows PC. They must be nearby and
advertising over BLE; the Mac needs no USB/adb connection. Use the Linux-built
isolated APKs and opt-in Android instrumentation fixture described in the Windows
handoff. Its readiness file is `files/otp23-ready`; its maximum hold is 30 minutes.

From `native-bridge/feasibility` on the Mac:

```sh
cargo build --locked --release --bin otp23-host
bash scripts/macos-package.sh
bash scripts/macos-register.sh
python3 scripts/macos_poc.py --diag-scan-only --out evidence/macos-poc-scan.json
python3 scripts/macos_poc.py --out evidence/macos-poc-physical.json --repeat-4096 3
python3 scripts/poc_background.py --flavour plain --out evidence/macos-poc-background.json
bash scripts/macos-unregister.sh
```

### Signing and permissions

- Packaging creates `target/macos-poc/VelesOtp23PoC.app`, bundle identifier
  `me.nagaev.veles.otp23.poc`, with Bluetooth usage descriptions and `LSUIElement`.
  Use the existing **Apple Development** identity so permission identity survives
  rebuilds. No installer, ad-hoc signing or key export was used in the tested run.
- Sign from the GUI/Aqua user context. SSH could enumerate the certificate but
  `codesign` returned `errSecInternalComponent`; the same script signed and
  strict-verified successfully from the Aqua session without changing keychain
  ACLs or credentials.
- The first tested scan returned CoreBluetooth authorization `denied`. The driver
  now classifies this as `access_denied`, rather than blaming a powered-on radio.
  The user enabled access in System Settings → Privacy & Security → Bluetooth.
  The **same signed binary** subsequently reported `allowed_always` and passed.
  Which Settings entry owned that grant (Chrome versus the host) was not observed,
  so this is not a formal TCC-attribution result. No TCC reset or bypass was used.
- A disposable Chrome `--user-data-dir` looks for user-level manifests inside
  that profile. The driver copies the validated exact-origin manifest into its
  owned profile **before** launching Chrome. It never writes the personal profile.
  Earlier "native host not found" was a profile-discovery error, not host failure.

Register/unregister only handles this PoC's native-host name and refuses unrelated
registrations. Re-registering an owned manifest can create a backup; unregister
reports explicitly if it restored that backup rather than removing registration.

## Physical results

### Windows

Windows 11 Pro 10.0.26300; stable Chrome 155.0.8059.85; Qualcomm FastConnect 7800;
OnePlus 10 Pro and OnePlus 6T, both Android 15:

- `poc-physical-final.json`: all 24 cases PASS. Concurrent byte-exact replies
  through 4096 bytes, repeated maximum records, stale-handle rejection, survivor
  after disconnect, real-link shutdown, zero host windows/network endpoints.
- Both connections negotiated ATT MTU 517; capped payload is 508 bytes.
- `poc-background-final.json`: PASS, two 4096-byte replies after more than
  610 seconds hidden; connector CDP target detached for 630 seconds, result
  present on first reattached read, no surviving host.

### macOS

macOS 26.5.2 / build 25F84 on MacBook Pro M1 Max (MacBookPro18,2), Broadcom BCM4387;
Chrome 155.0.8059.86; Rust/cargo 1.98.0; Xcode 26.6 build 17F113, SDK 26.5:

- `macos-poc-auth.json` and trace: denied/access_denied before the user grant.
- `macos-poc-authorized-scan.json`: PASS, both actual peripherals discovered.
- `macos-poc-physical.json`: PASS, all 25 cases including 4/178/179/180,
  505–511, 1015–1017, 4095/4096 bytes and three extra dual-4096 rounds, independent
  reply hashes, stale-handle rejection, survivor, shutdown and Chrome port close.
- CoreBluetooth measured maximum write-without-response length **512 bytes** on
  each link. The host derives effective MTU **515** (`512+3`), not a direct ATT-MTU
  observation. This yields the same 508-byte capped payload as Windows/Android.
- The host had no observed on-screen windows. Its signed executable SHA-256 is
  `a42900abd1006e693ee2af64ac23b13b749c03ba6bf35395adc3491699edca1e`.
- `macos-poc-background-final.json` and `.trace.log`: all eight cases PASS.
  Sends occurred 610.914 s and 610.920 s after scheduling, with the connector
  hidden; both 4096-byte replies were present at the first reattached read after
  the 630-second target-detached wait. The host trace independently records
  the replies before reattachment and a clean exit with no surviving host.
  The earlier passing run without a trace is retained as `*-untraced.json`;
  trace-path resolution was fixed and covered by a temp-directory regression.

All artifacts are under `native-bridge/feasibility/evidence/`. The Mac copy was
transferred as source archives without `.git`; historical `source_revision:null`
is expected, not proof of a specific commit. Host hashes and the build-identity
sidecars are the provenance anchors. New runs report an explicit reason when git
identity is unavailable. Signing adds variable metadata; no reproducible signed
binary claim is made.

## Verification and implementation provenance

- Windows Rust tests/Clippy passed before the Mac extension; macOS Rust suite now
  passes **160 tests**, with four real-radio tests ignored by default, and native
  Clippy `--all-targets -D warnings` passes. Ignored tests are not counted as passes.
- Driver unit tests and synthetic-byte regressions run without hardware. CI
  compiles/tests/lints the actual backend on Windows, Linux and `macos-26`.
- Initial implementation subagents used `ollama-cloud/glm-5.3-flash`, as requested.
  After the user's model change, fixes and integration tasks used local Claude
  Code **`claude-sonnet-5-5`**; CLI result metadata confirms the model. Mac-only
  signing and command execution were delegated to the Aqua user context.
- Whole-change review uses Claude Code **Opus 5.5**; see
  [the Mac review disposition](protocol/reviews/desktop-ble-poc-macos-opus.md) and
  [the Windows review](protocol/reviews/windows-ble-poc-opus.md).

## Boundaries for the production session

This PoC carries no real OTPs, trust state or cryptography, and enables no Android
notification-listener access. `sent` acknowledges GATT write completion, not an
authenticated application acknowledgement. Synthetic replies are independently
verified byte-exact.

The background measurements are PoC liveness evidence, **not formal OTP-23
acceptance**: browser-level CDP remains connected, not every timer exemption is
excluded, and the tested scenario uses a byte-verified pre-idle exchange. The
initial Windows cold-idle failure remains preserved separately.

Production work still needs protocol/version review, authenticated sessions,
complete denied/radio/lifecycle and standard-account matrices, fresh-account TCC
attribution, callback/cancellation/overflow hardening, Android multi-central
stress and installer decisions. In particular, macOS's experimental event stream
can overwrite notifications under heavier load; the tested bounded 4096-byte
scenario is not a throughput or adversarial-load guarantee. Do not treat this PR
as completion of OTP-24 or permission to bypass the remaining gates.
