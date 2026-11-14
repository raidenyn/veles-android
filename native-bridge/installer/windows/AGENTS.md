# native-bridge/installer/windows/AGENTS.md

The Windows installer frontend (OTP-25): the NSIS script `native-bridge/installer/windows/installer.nsi`
driving the Rust installer engine. No Tauri, no Node, no elevation, no service. The pinned NSIS is
provisioned into `build/native-bridge/installer/tools/nsis`; unsigned inputs are produced on the
`windows-2025` runner by `build-native-windows.yml` (two slots, `native-unsigned-windows` /
`native-fixture-windows`). User/developer instructions are `docs/otp-25-installers.md`.

## Build & Test

From the repository root (Windows runner or a Windows host):

```bash
./gradlew bridgeInstallerTest bridgeInstallerScriptsTest
# UNSIGNED producer (needs the approved tracked release-windows.json):
./gradlew bridgeInstallerUnsigned
./gradlew bridgeInstallerPackageFixture
```

The producers never sign: `sign-windows` in the owner-only `publish-release.yml` (`native-release`
environment) is the only place the installer is signed, and the release gate is `gate-installer`.
Never record a digest or certificate fingerprint that was not read from real tool output, and never
substitute a different signer.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
