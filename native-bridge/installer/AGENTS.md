# native-bridge/installer/AGENTS.md

The standalone OTP-25 installers (crate `veles-bridge-installer` plus the NSIS and SwiftPM
frontends): signed, per-user installers for the plain-Rust Native Messaging host. No Tauri, no
Node, no elevation, no service. User and developer instructions are `docs/otp-25-installers.md`;
the crate README (`native-bridge/installer/README.md`) holds the layout and the evidence model.

## Build & Test

Run from the repository root:

```bash
# Checks (run on Linux, Windows and macOS CI)
./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest
./gradlew bridgeInstallerScriptsTest          # Python suites incl. test_build_contract.py (not on Windows)

# UNSIGNED input producers (Windows/macOS only)
./gradlew bridgeInstallerUnsigned             # PRODUCTION channel; needs a tracked, approved release-<platform>.json
./gradlew bridgeInstallerPackageFixture       # FIXTURE channel from the tracked fixture-<platform>.json
./gradlew bridgeInstallerDebug                # DEBUG fixture-channel installer (Windows/macOS)
./gradlew bridgeInstallerSupplyChain          # verify/verify-supply-chain.sh incl. native-installer.cdx.json
```

Output lands under `build/native-bridge/installer/{target,slots,out,tools}`. The producers never
sign: signing, notarization and the release gate run only in the owner-only `publish-release.yml`
(`sign-windows` / `sign-macos`, `gate-installer`). Native steps (NSIS, `signtool`, Swift/Xcode,
`codesign`, notarization, the physical matrix) do not run on Linux; report them as NOT RUN /
BLOCKED, never as passes. Exit 0 from the evidence validator is never acceptance.

## Platform frontends

- Windows NSIS frontend: `native-bridge/installer/windows/AGENTS.md`.
- macOS SwiftPM frontend and the canonical signing identity: `native-bridge/installer/macos/AGENTS.md`.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
