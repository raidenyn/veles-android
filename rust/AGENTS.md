# rust/AGENTS.md

The shared Rust crypto core (OTP-01 1b), used by the Android JNI and browser WASM targets. `rust/` is
an independent Gradle build included by the root and `android/`. Outputs stay under the root
`build/` (`build/rust/target`, `build/rust/jniLibs`, `build/rust/host-jni`, …), except `rustWasm`,
which writes `web-extension/rust-wasm/pkg/`. Toolchain: `rust/rust-toolchain.toml`; NDK:
`rust/ndk-version`.

## Build & Test

Run from the repository root:

```bash
./gradlew rustInstall        # exact auxiliary CLIs under build/rust-tools/
./gradlew rustFormat rustLint rustTest   # host, WASM and Android targets; needs Node >= 22
./gradlew rustJni            # three Android JNI ABIs into build/rust/jniLibs/
./gradlew rustWasm           # WASM package into web-extension/rust-wasm/pkg/
./gradlew rustHostJni        # test-only host-JVM cdylib, never packaged or released
rust/scripts/assert-clean.sh # assert `./gradlew clean` removed every declared output
```

The JNI, WASM and host-JVM outputs are test-only or product inputs, never released on their own:
`rustHostJni` is loaded by `testDebugUnitTest`, `:app:protocolJvmTest` and the session loopback JVM.

## Rules

Android CI also runs for your changes — `.github/components.json` maps `rust/` into the `android`
node — so before pushing read `.agents/rules/android-lint-before-push.md`.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
