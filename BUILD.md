# Building & Installing Veles

This document describes prerequisites, build commands, installation instructions, and multi-component toolchains for Veles.

---

## Prerequisites

- **JDK 21** installed and configured (e.g. via `JAVA_HOME`).
- **Android Studio** or standalone **Android SDK** with build tools for API 33+.
- **ADB** (Android Debug Bridge) for deploying to a physical device or emulator.
- *(Optional, for external toolchains)*:
  - **Node.js** (LTS) & npm for `web-extension/`.
  - **Rust** & Cargo for `rust/` core and `native-bridge/`.

---

## Building the Android App

From the repository root:

### Debug Build
```bash
./gradlew assembleDebug
```
The output APK is generated at:
```
app/build/outputs/apk/debug/app-debug.apk
```

### Run Unit Tests
```bash
./gradlew testDebugUnitTest
```

### Release Build
```bash
./gradlew assembleRelease
```
See [docs/releasing.md](docs/releasing.md) for signing configuration and release runbooks.

---

## Installing on a Device

1. Connect your Android device (Android 13+ / API 33+) via USB or wireless debugging and verify connection:
   ```bash
   adb devices
   ```

2. Install the debug APK:
   ```bash
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```

3. Open **Veles** and complete the permission flow:
   - **Notification access:** Settings → Notifications → Notification access → Veles → Allow.
   - **Send notifications:** Prompted via the standard `POST_NOTIFICATIONS` runtime dialog.

> [!IMPORTANT]
> **Android 15+ Users:** Android 15 automatically redacts OTP and banking details from notifications unless companion permissions are granted. Follow the in-app pairing wizard or refer to the [Sensitive Notifications Guide](docs/sensitive-notifications.md) for ADB instructions.

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

Refer to [docs/ci.md](docs/ci.md) and individual subdirectories for component-specific build steps.

---

## Verifying Release Artifacts

Each GitHub Release is built deterministically in CI and published with attestations and SHA-256 manifests. You can verify downloaded artifacts with the GitHub CLI:

```bash
gh attestation verify veles-android-X.Y.Z.apk --repo raidenyn/veles-android
```

For more details on build reproducibility, see [docs/reproducible-builds.md](docs/reproducible-builds.md).
