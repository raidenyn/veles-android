# Device and Chrome runs

Real-environment evidence for Task 10 of the gradle-cache-safe change. Commands are quoted as run;
hashes and version values are read from the tools' own output.

## Handset: version matches `:app:androidGitVersion`

- Precondition: `git tag -l 'android-v9*'` → empty output (no lines, so no throwaway tag leaked into
  the worktree), exactly as step 10.3 requires.
- Build: `cd android && ./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug`
  at `792a082a`; APK `android/app/build/outputs/apk/debug/app-debug.apk`, sha256
  `17f4e88e4f982b082e1142d1aecbc8b7450531d7986377d8a403c96f00f21baf`.
- Install: `adb -s 2be02e41 install -r app-debug.apk` → `Success`.
- Launch: `adb -s 2be02e41 shell am start -n me.nagaev.veles.otp23/me.nagaev.veles.permissions.PermissionsActivity`
  → `topResumedActivity=… me.nagaev.veles.otp23/…PermissionsActivity`.
- Installed package: `adb shell dumpsys package me.nagaev.veles.otp23` →
  `versionCode=100`, `versionName=0.1.0-53-792a082-chore_gradle-cache-safe`.
- Derived: `cd android && ./gradlew -q :app:androidGitVersion` →
  `androidGitVersion.name	0.1.0-53-792a082-chore_gradle-cache-safe`, `androidGitVersion.code	100`.
- Result: name and code are equal. The fixture signing identity was reused from the repository's
  git-ignored `android/config/android_keys/`; no key was generated, no data cleared.

The running app showed the permissions screen. The device-sharing card was toggled from
"transport does not share OTPs yet" to "Available over Bluetooth. OTP sharing is not enabled." by
tapping **Enable transport**, which started `SharingService`; it was stopped again afterwards.

## The pairing-code screen does not exist in the app

The plan's example action for exercising the Rust JNI was "open the pairing-code screen". No such screen
exists: `PairingWindow` (`android/app/src/main/java/me/nagaev/veles/pairing/PairingWindow.kt`) is held by
`SharingService` and has no Compose consumer. `SharingCard` only starts/stops the Bluetooth transport.
There is therefore no in-app action that renders the code to tap through on the handset.

On-device JNI coverage instead comes from the instrumentation suite run in Task 5 on serial `864a3db0`:
`82/77 completed. (5 skipped) (0 failed)`. `PairingInstrumentedTest` has four tests; two executed
against the Rust JNI — `productionJniRoundTripRejectsWrongCodeAndReplay` and
`frozenInvalidPointsFailClosedThroughProductionJni`, both driving `VelesCrypto.pairingCommand` — and
two were skipped as opt-in cases (`separateConformanceLibraryMatchesEveryFrozenPairingVector`,
`productionJniStateExpiresOnNativeMonotonicClock`). That run's log and result are recorded in
`cc-reuse.md`.

## End-to-end pairing is unavailable in this environment

`10.5` asked for the handset app paired with the extension through the existing flow. It cannot run here:
the handset is attached to the home PC, while the extension runs on the Mac (no Native Messaging host
registered), and the pairing transport is the handset's Bluetooth advertising service
(`SharingService`). The two machines are not one pairing domain, and the app exposes no code-entry UI to
bridge them. Recorded as environment-unavailable rather than reported as tested. The user was asked and
explicitly chose this option: record 10.5 as environment-unavailable.

## Chrome: extension builds, loads and runs its WASM

Run on the MacBook ("Sirius") by Claude Code there, through `homepc --host mac`, in a synced copy of the
change (`git archive` of `792a082a`, no `.git`).

- **Build.** `./gradlew rustWasm` → BUILD SUCCESSFUL (produced `web-extension/rust-wasm/pkg`), then
  `cd web-extension && npm ci --ignore-scripts && npm run build` → succeeded; `dist/manifest.json` and
  `dist/assets/veles_crypto_bg-Ky2eafkw.wasm` (158,815 bytes) exist.
  *Deviation:* `package.json` requires Node >= 22; the Mac only has nvm 18 and 20, so the build ran on
  **Node 20.10.0**. It completed, but it is outside the declared engine range.
- **Load.** Google Chrome 157.0.8103.79, throwaway profile, `--remote-debugging-port=9333
  --enable-unsafe-extension-debugging`; Playwright `connectOverCDP` and CDP `Extensions.loadUnpacked`.
  Chrome 157 ignores `--load-extension`, even with `--disable-features=DisableLoadExtensionCommandLineSwitch`.
  Extension id `njbaogfiohaikdccmhjkogpanininjcp`; its `background.js` service worker ran.
- **Popup.** `popup.html` renders title "Veles OTP" and the text "Veles OTP"; console shows one line
  `Veles OTP popup loaded`; no errors or page errors. The popup is a skeleton and loads no WASM, so it
  says nothing about WASM either way.
- **Connector page.** Shows "Native bridge not installed", which is expected because no host is
  registered on this Mac. Console and page errors: none. Its WASM asset is fetched with HTTP 200.
- **WASM instantiation.** In an extension page under the real extension CSP, the Rust-generated glue
  (copy of `rust-wasm/pkg/veles_crypto.js`, with a byte-identical `cmp` of the built `dist` wasm next to
  it) initialised: `init: "ok"`, `initType: "function"`, exports
  `["default","initSync","pairing_command","reverse_bytes"]`, `wasmMemoryBytes: 1114112`, and
  `reverse_bytes([1,2,3,4])` returned `[4,3,2,1]`; no thrown error, no missing imports, empty console.
  *Limit:* this exercised the generated glue and the same wasm bytes, not Vite's bundled copy inside
  `dist/chunks/connector-*.js`; the bundled path is supported indirectly by the 200 fetch and the clean
  console. Chrome was closed and the throwaway profile deleted.

`pairing_command` is exported but was not called (no native host), so the pairing core's own entry point
is untested in Chrome; the on-device instrumentation suite covers the same Rust JNI path.

Pairing transport confirmation: `web-extension/src/connector.ts` and `native-bridge/host/AGENTS.md` show
the connector relays BLE scan/connect/GATT requests through a Native Messaging host, so the phone must be
in Bluetooth range of the browser's machine — which is why 10.5 is unavailable here, in addition to the
missing host registration.
