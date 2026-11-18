# native-bridge/AGENTS.md

The Tauri 2.x packaging/build scaffold for the Native Messaging host. **Tauri is not the approved
production path.** The production host is the plain Rust executable in `native-bridge/host/`; the
rationale and the packaging decision are in `docs/otp-23-requirements-matrix.md` (D-01).

Read the Tauri-free crates before changing them: `native-bridge/host/AGENTS.md` and
`native-bridge/installer/AGENTS.md`.

## Build & Test

Run from the repository root (Node >= 22 and npm required):

```bash
./gradlew bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeTest
./gradlew bridgeBuild       # unsigned host via cargo tauri build --no-bundle -- --locked
./gradlew bridgeManifests   # Chrome native-messaging host manifests
./gradlew bridgePackage     # deterministic packaging into build/native-bridge/
```

The Rust compiler is pinned to 1.98.0 by `native-bridge/src-tauri/rust-toolchain.toml` (enforced by
`build.gradle.kts`); dependencies are pinned by `Cargo.toml` plus `Cargo.lock`, and the Tauri CLI and
TypeScript tooling are exact-pinned in `native-bridge/package.json`.

## Rules

- `.agents/rules/native-bridge-test-app-tcc.md` — one fixed macOS test-app identity and the reusable
  Bluetooth grant for real E2E runs under this node.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
