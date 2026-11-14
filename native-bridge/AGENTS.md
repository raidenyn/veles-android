# native-bridge/AGENTS.md

The Tauri 2.x packaging/build scaffold for the Native Messaging host (OTP-01 sub-project 1c).
**Tauri is not the approved production path.** Per the RFC amendment of 2026-10-26 the production
host is the plain Rust executable in `native-bridge/host/` that never starts the Tauri event loop;
retaining Tauri solely for packaging is not approved and not justified
(`docs/otp-23-requirements-matrix.md` D-01), and OTP-25 picks another packaging pipeline or brings a
separately reviewed justification. The commands below are existing **tooling** commands, not the
approved architecture.

The production host and the standalone installers are separate, Tauri-free crates; read their own
files before changing them: `native-bridge/host/AGENTS.md`, `native-bridge/installer/AGENTS.md`.

## Build & Test

Run from the repository root:

```bash
# Install npm deps, check format/lint/typecheck, run npm+cargo tests
./gradlew bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeTest

# Build the unsigned host (cargo tauri build --no-bundle -- --locked)
./gradlew bridgeBuild

# Emit Chrome native-messaging host manifests
./gradlew bridgeManifests

# Platform-specific deterministic packaging into build/native-bridge/
./gradlew bridgePackage
```

Node >= 22 and npm are required for these scaffold commands. The Rust **compiler toolchain** is
pinned to 1.98.0 by `native-bridge/src-tauri/rust-toolchain.toml` (enforced by `build.gradle.kts`);
crate **dependencies** are pinned by `native-bridge/src-tauri/Cargo.toml` plus the committed
`Cargo.lock`, and the Tauri CLI and TypeScript tooling are exact-pinned in
`native-bridge/package.json` with a committed lockfile.

## Rules

None.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
