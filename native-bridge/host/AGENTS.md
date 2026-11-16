# native-bridge/host/AGENTS.md

The production Native Messaging host (OTP-24): the plain-Rust crate `veles-ble-host`, a headless
stdio process that Chrome launches to relay BLE scan/connect/GATT-send requests to the phone. It is
portable control plane only — no Tauri, no Node/npm, no platform BLE stack compiled on unsupported
hosts. The wire protocol is `protocol/docs/veles-v1.md`.

## Build & Test

Run from the repository root:

```bash
# Toolchain, format, lint and native tests (cargo fmt/clippy/test under build/native-bridge/host-target)
./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest

# Build ONLY veles-native-host (locked, release, no features; the test-driver bin is feature-gated
# and never built) -> build/native-bridge/host/veles-native-host[.exe] + SHA256SUMS
./gradlew bridgeHostBuild

# Static build-contract checks (also the `native-host / build-contract` CI job)
python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py' -v
```

The Rust toolchain is pinned to 1.98.0 by `native-bridge/host/rust-toolchain.toml`; cargo's target
directory is `build/native-bridge/host-target` (never `native-bridge/host/target` or
`native-bridge/host/build`).
`bridgeHostToolchainCheck` is not a prerequisite for the static Python suite. Windows and macOS
runners compile the real WinRT / CoreBluetooth adapters; Linux only runs the portable checks.

## CI artifacts and SBOM

`check-native-host.yml` uploads `native-host-windows` (executable + `SHA256SUMS`) and
`native-host-macos` (mode-preserving tar + `SHA256SUMS` + archive sidecar) as check artifacts, never
release assets. The `native-host.cdx.json` SBOM is generated and validated by
`build-supply-chain.yml` / `publish-release.yml`, alongside the other component SBOMs.

## Platform frontends

- macOS installer app, the canonical signing identity and the macOS Bluetooth E2E rule:
  `native-bridge/installer/macos/AGENTS.md`; `.agents/rules/native-bridge-test-app-tcc.md` applies to
  host runs and loads under `native-bridge/host/**`.
- Windows installer: `native-bridge/installer/windows/AGENTS.md`.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
