# Building & Installing Veles

This document describes prerequisites, build commands, installation instructions, and multi-component toolchains for Veles.

---

## Prerequisites

- **JDK 21** installed and configured (e.g. via `JAVA_HOME`).
- **Android Studio** or standalone **Android SDK** with build tools for API 33+.
- **Android NDK 29.0.14206865** for the native Rust core. The Android Gradle Plugin no longer
  installs the NDK automatically, so install the pinned version once:
  ```bash
  sdkmanager "ndk;29.0.14206865"
  ```
  The version is defined once in [`rust/ndk-version`](https://github.com/raidenyn/veles-android/blob/master/rust/ndk-version); the Rust build finds it
  through `ANDROID_NDK_HOME`, then `$ANDROID_SDK_ROOT/ndk/<version>`, then
  `$ANDROID_HOME/ndk/<version>`, then the `sdk.dir` of the repository-root `local.properties`,
  then the `sdk.dir` of `android/local.properties`. The independent Android build reads the SDK
  location from `android/local.properties` (or `ANDROID_HOME`/`ANDROID_SDK_ROOT`); a
  repository-root `local.properties` is not used by it.
- **ADB** (Android Debug Bridge) for deploying to a physical device or emulator.
- *(Optional, for external toolchains)*:
  - **Node.js** (LTS) & npm for `web-extension/`.
  - **Rust** & Cargo for `rust/` core and `native-bridge/`.

---

## Building the Android App

`android/` is an independent Gradle build. Both entry points work for the common commands:
the thin root aliases below forward to the included Android build, and running from `android/`
addresses the build directly. Use the `android/` form whenever a command needs a task option
that the root alias cannot carry — notably `--tests` for a single unit test class.

### Debug Build
```bash
# From the repository root (root alias)
./gradlew assembleDebug

# Directly in the Android build
cd android
./gradlew assembleDebug
```
The output APK is generated at:
```
android/app/build/outputs/apk/debug/app-debug.apk
```

### Run Unit Tests
```bash
# From the repository root (root alias)
./gradlew testDebugUnitTest

# A single test class: the root alias cannot accept --tests
cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"
```

### Release Build
```bash
./gradlew assembleRelease
```
See [docs/releasing.md](https://github.com/raidenyn/veles-android/blob/master/docs/releasing.md) for signing configuration and release runbooks.

---

## Installing on a Device

1. Connect your Android device (Android 13+ / API 33+) via USB or wireless debugging and verify connection:
   ```bash
   adb devices
   ```

2. Install the debug APK:
   ```bash
   adb install -r android/app/build/outputs/apk/debug/app-debug.apk
   ```

3. Open **Veles** and complete the permission flow:
   - **Notification access:** Settings → Notifications → Notification access → Veles → Allow.
   - **Send notifications:** Prompted via the standard `POST_NOTIFICATIONS` runtime dialog.

> [!IMPORTANT]
> **Android 15+ Users:** Android 15 automatically redacts OTP and banking details from notifications unless companion permissions are granted. Follow the in-app pairing wizard or refer to the [Sensitive Notifications Guide](https://github.com/raidenyn/veles-android/blob/master/docs/user/sensitive-notifications.md) for ADB instructions.

---

## Sub-Project Toolchains (OTP External Devices)

Veles supports multi-device delivery of OTPs via external device toolchains:

- **`rust/`** — Shared Rust core with Android JNI and browser WASM bindings.
- **`web-extension/`** — npm/Vite MV3 browser extension with deterministic packaging.
- **`native-bridge/`** — Tauri 2.x headless Native Messaging host (Windows & macOS).
- **`verify/`** — Supply-chain policy checks (SBOM, license compliance, script audits).

### Web extension

The extension source imports the generated WASM core, so build the package first from
the repository root:

```bash
./gradlew rustWasm
cd web-extension
npm ci
npm run build       # or: npm run package (build + deterministic zip)
```

`npm run typecheck`, `npm test`, `npm run test:bundle` and the Playwright browser check
resolve the same import and all require `rustWasm` beforehand.

Refer to [docs/ci.md](https://github.com/raidenyn/veles-android/blob/master/docs/ci.md) and individual subdirectories for component-specific build steps.

---

## Build caches

The configuration cache is on and strict in all three builds (the repository root, `android/` and
`rust/`): every `gradle.properties` sets `org.gradle.configuration-cache=true` with
`org.gradle.configuration-cache.problems=fail`, so a second run of the same command reuses the entry
and a configuration-cache problem fails the build instead of warning.

The build cache is **off by default** — no committed `gradle.properties` enables it. Opt in for one
invocation:

```bash
./gradlew assembleDebug --build-cache
```

or for every build on the machine, in `~/.gradle/gradle.properties`:

```properties
org.gradle.caching=true
```

What is cached:

- **Rust products** — only `rustJni` (the three Android JNI libraries) and `rustHostJni` (the test-only
  host-JVM library) are cacheable. Their keys cover the Rust sources and lockfile, the pinned Rust
  toolchain, the auxiliary CLI pins and the NDK version for `rustJni`, the host identity and
  `cc --version` for `rustHostJni`, and hold no absolute path, so a second worktree at a different path
  gets a hit. `rustWasm` (the browser package) is **not** cacheable: wasm-pack writes
  `web-extension/rust-wasm/pkg/.gitignore`, Gradle drops that file from a cached directory output, and
  a restored package would be incomplete; the task carries `doNotCacheIf` with that reason.
- **AGP, Kotlin and Java tasks** keep the cacheability their plugins declare.
- **Every other repository task** — installs, toolchain and version probes, test and verification
  tasks, packaging, git state — is explicitly non-cacheable, so a hit never hides a download or a host
  probe.

Rust caching is refused (no load and no store) when:

- the environment inherited by Gradle may change the output: a non-empty `CARGO_ENCODED_RUSTFLAGS`,
  `CARGO_BUILD_RUSTFLAGS`, `CARGO_INCREMENTAL`, `RUSTC`, `RUSTC_WRAPPER`, `RUSTC_WORKSPACE_WRAPPER`,
  `RUSTC_BOOTSTRAP`, `RUSTUP_TOOLCHAIN`, `MACOSX_DEPLOYMENT_TARGET`, `SDKROOT`, `CC`, `CXX`, `AR`,
  `CFLAGS`, `CXXFLAGS` or `LDFLAGS` variable;
- a non-empty `CARGO_PROFILE_*`, `CARGO_BUILD_*`, `CC_*`, `CFLAGS_*`, `AR_*` or `CARGO_TARGET_*`
  variable is set (`CARGO_TARGET_DIR` excepted);
- a cargo configuration file exists: `config` or `config.toml` under `$CARGO_HOME` (default
  `~/.cargo`), or `.cargo/config` or `.cargo/config.toml` in `rust/` or any of its ancestor
  directories;
- the task is `rustHostJni` on Windows, where the MSVC linker identity is not probed; it carries
  `doNotCacheIf` with that reason, so a Windows host-JNI build never gets a hit.

Release and verification workflows always pass `--no-build-cache --no-configuration-cache`, so a
machine-wide opt-in never reaches a release
([docs/reproducible-builds.md](https://github.com/raidenyn/veles-android/blob/master/docs/reproducible-builds.md)
lists the guarded files). The check workflows (`check-android.yml`, `check-bridge-scaffold.yml`,
`check-native-host.yml`) run with the committed defaults — configuration cache on, build cache off —
so CI never reads or writes the build cache. A local **signed** `assembleRelease` adds
`--no-configuration-cache` as well: the flag keeps the signing values, which are read while the build
is configured, out of the configuration-cache entry (with the configuration cache on, AGP stores the
resolved signing values in the entry, encrypted):

```bash
./gradlew assembleRelease --no-configuration-cache
```

---

## Verifying Release Artifacts

Each GitHub Release is built deterministically in CI and published with attestations and SHA-256 manifests. You can verify downloaded artifacts with the GitHub CLI:

```bash
gh attestation verify veles-android-X.Y.Z.apk --repo raidenyn/veles-android
```

For more details on build reproducibility, see [docs/reproducible-builds.md](https://github.com/raidenyn/veles-android/blob/master/docs/reproducible-builds.md).
