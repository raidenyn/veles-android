# native-bridge/installer/macos/AGENTS.md

The macOS installer frontend (OTP-25): a SwiftPM app in `native-bridge/installer/macos/`
(`Package.swift`, `Sources/`) wrapping the Rust installer engine, built on `macos-26` under the
pinned Xcode. No Tauri, no Node. User/developer instructions are `docs/otp-25-installers.md`; the
macOS PoC identity and TCC history are in `docs/desktop-ble-poc.md`.

This file is the **canonical** home of the macOS signing identity and TCC rules. Android keeps its
own separate identity in `android/AGENTS.md`; there is no cross-platform key.

## Signing identity and TCC (macOS)

Always reuse the **same existing signing identity within each platform/app identity** for subsequent
app and fixture builds. This is one stable key per app identity, not one cross-platform key: the
Android and macOS identities are separate.

- **macOS:** reuse the exact approved signing identity/team and bundle ID. No ad-hoc substitution,
  key export, or TCC reset; use the Aqua keychain context when required.
- Different production signing is a separately planned migration with revalidation. Do not borrow
  debug credentials. Never record fingerprints that were not read from real output.
- The OTP-23 macOS test bundle (`native-bridge/host/scripts/macos-test-bundle.sh`) selects the
  approved Apple Development certificate read-only by its exact recorded name and stops (BLOCKED)
  when it is absent or ambiguous; never add a fallback identity. It is a test vehicle, not the
  production bundle.
- Signing and notarization run only in the owner-only `publish-release.yml` (`sign-macos` under the
  `native-release` environment), never in the unsigned input producers.

## Build & Test

From the repository root:

```bash
./gradlew bridgeInstallerTest bridgeInstallerScriptsTest
# macOS-only producer (needs the approved tracked release-macos.json):
./gradlew bridgeInstallerUnsigned
./gradlew bridgeInstallerPackageFixture
```

Native Swift/Xcode, `codesign` and notarization steps do not run on Linux; report them as NOT RUN /
BLOCKED, never as passes.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
