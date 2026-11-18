# native-bridge/installer/macos/AGENTS.md

The macOS installer frontend (OTP-25): a SwiftPM app wrapping the Rust installer engine, built on
`macos-26` under the pinned Xcode. No Tauri, no Node. Instructions: `docs/otp-25-installers.md`; PoC
identity and TCC history: `docs/desktop-ble-poc.md`. This file is the canonical home of the macOS
signing identity and TCC rules; Android has its own separate identity in `android/AGENTS.md`.

## Signing identity and TCC (macOS)

Reuse the exact approved signing identity/team and bundle ID. No ad-hoc substitution, key export or
TCC reset; use the Aqua keychain context when required. Different production signing is a separately
planned migration with revalidation; never borrow debug credentials or record unread fingerprints.

The test bundle (`native-bridge/host/scripts/macos-test-bundle.sh`, `VelesOtpDebug.app`,
`me.nagaev.veles.debug`) selects the approved Apple Development certificate read-only by its exact
recorded name and stops (BLOCKED) when it is absent or ambiguous; never add a fallback identity.
Signing and notarization run only in the owner-only `publish-release.yml` (`sign-macos`).

Real macOS Bluetooth E2E runs follow `.agents/rules/native-bridge-test-app-tcc.md`: one fixed bundle
identifier and signing identity, a LaunchServices or Chrome launch, and no TCC reset.

## Build & Test

From the repository root:

```bash
./gradlew bridgeInstallerTest bridgeInstallerScriptsTest
./gradlew bridgeInstallerUnsigned          # needs the approved tracked release-macos.json
./gradlew bridgeInstallerPackageFixture
```

Native Swift/Xcode, `codesign` and notarization steps do not run on Linux; report them as NOT RUN /
BLOCKED, never as passes.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
