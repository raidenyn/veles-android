# native-bridge/host/AGENTS.md

The production Native Messaging host (OTP-24): the plain-Rust crate `veles-ble-host`, a headless
stdio process that Chrome launches to relay BLE scan/connect/GATT-send requests to the phone. No
Tauri, no Node/npm. The wire protocol is `protocol/docs/veles-v1.md`.

## Build & Test

Run from the repository root:

```bash
./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest
./gradlew bridgeHostBuild     # locked release veles-native-host + SHA256SUMS
python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py' -v
```

The toolchain is pinned to 1.98.0 by `native-bridge/host/rust-toolchain.toml`; cargo's target
directory is `build/native-bridge/host-target` (never `native-bridge/host/target` or `.../build`).
Windows and macOS runners compile the real WinRT / CoreBluetooth adapters; Linux runs the portable
checks.

CI uploads `native-host-windows` (executable + `SHA256SUMS`) and `native-host-macos` (tar +
`SHA256SUMS` + sidecar) as check artifacts, never release assets; `native-host.cdx.json` is the SBOM.

Platform frontends and the macOS Bluetooth E2E rule:
`native-bridge/installer/macos/AGENTS.md` and `.agents/rules/native-bridge-test-app-tcc.md`.
Windows: `native-bridge/installer/windows/AGENTS.md`.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
