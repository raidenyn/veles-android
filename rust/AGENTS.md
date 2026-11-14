# rust/AGENTS.md

The shared Rust crypto core (OTP-01 sub-project 1b), used by the Android JNI and browser WASM targets.
`rust/` is an independent Gradle build included by both the repository root and `android/`; every
product stays under the repository-root `build/` (`build/rust/target`, `build/rust/jniLibs`,
`build/rust/host-jni`, `build/rust-tools`, …). The toolchain is pinned by `rust/rust-toolchain.toml`
and the Android NDK version is defined once in `rust/ndk-version`.

## Build & Test

Run from the repository root:

```bash
# Install/verify exact auxiliary Rust build CLIs under build/rust-tools/
./gradlew rustInstall

# Format, lint host+WASM+Android targets, and run native+WASM tests
./gradlew rustFormat rustLint rustTest

# Build three Android JNI ABIs into build/rust/jniLibs/
./gradlew rustJni

# Build web-target WASM into web-extension/rust-wasm/pkg/
./gradlew rustWasm

# Build the test-only host-JVM cdylib into build/rust/host-jni/ (never packaged or released)
./gradlew rustHostJni

# Assert every declared generated output was removed by `./gradlew clean`
rust/scripts/assert-clean.sh
```

`rustTest` needs Node >= 22 (it runs a WASM smoke test). The JNI, WASM and host-JVM outputs are
test-only or product inputs, never released on their own: `rustHostJni` is loaded by
`testDebugUnitTest`, `:app:protocolJvmTest` and the session loopback child JVM. `clean` removes every
declared generated output, and `rust/scripts/assert-clean.sh` asserts none survived.

## Rules

Android CI also runs for your changes — `.github/components.json` maps `rust/` into the `android` node
— so before pushing read `.agents/rules/android-lint-before-push.md`; a change that reaches the Android
build must also pass that gate.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
