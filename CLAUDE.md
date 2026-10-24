# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Build debug APK
./gradlew assembleDebug

# Build release APK (R8-minified; signed only if VELES_KEYSTORE_* env vars are set)
./gradlew assembleRelease

# Run unit tests (JVM, no device needed)
./gradlew testDebugUnitTest

# OTP-02 contract: fresh Node crypto evidence plus Kotlin, Rust and both TypeScript consumers
./gradlew protocolCheck

# Run a single unit test class
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"

# Run instrumented tests (requires connected device or emulator)
./gradlew connectedDebugAndroidTest

# Run a single instrumented test class
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests

# Web extension (OTP-01 sub-project 1a) — requires node >= 22 and npm on PATH
cd web-extension
npm ci                          # install deps from lockfile
npm run format:check            # prettier check
npm run lint                    # eslint
npm run typecheck               # tsc --noEmit
npm test                        # vitest source-level tests (smoke + manifest guard)
npm run test:bundle             # vitest bundle tests (requires `npm run build` first)
npm run package                 # vite build + deterministic zip + sha256 sidecar -> ../build/web-extension/

# Rust core (OTP-01 sub-project 1b) — shared crypto core for Android JNI and browser WASM
# Install/verify exact auxiliary Rust build CLIs under build/rust-tools/
./gradlew rustInstall

# Format, lint host+WASM+Android targets, and run native+WASM tests
./gradlew rustFormat rustLint rustTest

# Build three Android JNI ABIs into app/build/generated/jniLibs/
./gradlew rustJni

# Build web-target WASM into web-extension/rust-wasm/pkg/
./gradlew rustWasm

# Native-bridge (OTP-01 sub-project 1c) — Tauri 2.x headless Native Messaging host
# Install npm deps, check format/lint/typecheck, run npm+cargo tests
./gradlew bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeTest

# Build the unsigned host (cargo tauri build --no-bundle -- --locked)
./gradlew bridgeBuild

# Emit Chrome native-messaging host manifests
./gradlew bridgeManifests

# Platform-specific deterministic packaging into build/native-bridge/
./gradlew bridgePackage

# OTP-01 sub-project 1d — verification & supply chain
# Local aggregate verifier (NOT used by CI). Runs all six component verifiers
# in order and stops on the first failure. Exit 0 = all matched, 1 = mismatch,
# 2 = usage/pin/environment/identity error.
verify/verify-all.sh <apk> <git-ref> <native-run-a-dir> <native-run-b-dir>

# Component verifiers — OPT-IN LOCAL DIAGNOSTICS (single-build pipelines: CI
# runs no reproducibility comparison, and publication requires none. Exception:
# build-supply-chain.yml invokes verify-supply-chain.sh as its policy job —
# a supply-chain policy check, not a reproducibility comparison).
verify/verify.sh <apk> <git-ref>                    # Android rebuild + signature-stripped compare
verify/verify-web.sh                                # web-extension candidate/reference byte compare
verify/verify-rust.sh                               # Rust JNI/WASM candidate/reference byte compare
verify/verify-native.sh <commit> <run-a> <run-b>    # two native runs, identity gate, stable-payload compare
verify/verify-supply-chain.sh                        # SBOM + license + install-script + remote-code (CI policy job + local)
verify/aggregate-checksums.sh <commit>               # build/verification/SHA256SUMS.toolchains (local only)

# Supply-chain verifier local prerequisites (CI provisions these in
# build-supply-chain.yml; locally you must run them first):
#   npm ci --ignore-scripts --prefix verify   # cyclonedx-npm + license-checker -> verify/node_modules/.bin/
#   ./gradlew installCargoDeny                 # cargo-deny -> build/verify-tools/ (cargo-cyclonedx is installed by generate-sboms.sh)
# `verify/verify-supply-chain.sh` asserts Node 26.8.1 / npm 11.19.0 and the
# active Rust toolchain (generate-sboms.sh shells out to ./gradlew verifyCargoCyclonedx).

# Assert every declared generated output was removed by `./gradlew clean`.
rust/scripts/assert-clean.sh
```

Clean APK assembly now requires rustup/cargo and NDK r29 (`rustJni` is a dependency of
`:app:assembleDebug`/`assembleRelease`). Node >= 22 is required for `rustTest` (which
runs a WASM smoke test), the WASM/extension commands, and the native-bridge commands
above — it is not needed to build the APK on its own.

**Verification Node (OTP-01 1d).** Reproducibility and supply-chain environments
require an **exact** Node pin — Node **26.8.1** with bundled npm **11.19.0** —
asserted by `verify/Dockerfile.web`, `verify/Dockerfile.rust`, and
`verify/verify-supply-chain.sh`. The developer-facing package contract remains a
floor (`engines.node = ">=22.0.0"`); only the reference environments require the
exact pin. `verify/package.json` pins `@cyclonedx/cyclonedx-npm` 6.0.1 and
`license-checker-rseidelsohn` 5.0.1 (whose CLI banner prints a stale `4.4.2` — the
lockfile is authoritative; this is a documented upstream discrepancy).

**Reusable workflow ownership (OTP-01 1d, single-build pipelines).** Each
component is built exactly once per invocation in its own reusable workflow;
none of them invokes a reproducibility verifier, creates A/B run transports,
or requires a Docker reference build:

| Workflow | Builds (neutral artifact name) |
|---|---|
| `build-android.yml` | one signed-or-unsigned release APK + mapping + `SHA256SUMS` (`android-release`); JNI allow-list runs against the APK actually built |
| `build-web-extension.yml` | extension ZIP + sidecar + `SHA256SUMS` (`web-extension`); bundle tests |
| `build-rust.yml` | `rust-jni-wasm` package |
| `build-native-windows.yml` | Windows bridge executable + NSIS/MSI installers, packaged once via `bridgePackage` (`native-windows`); provisions the pinned WiX/NSIS cache and exports its absolute `NSIS_PATH` |
| `build-native-macos.yml` | macOS bridge executable + `.app`/DMG, packaged once via `bridgePackage` (`native-macos`) |
| `build-supply-chain.yml` | SBOM/license/audit reports (`supply-chain-reports`); report validation + policy enforcement |

`release-build.yml` composes these with the neutral names, and its manual
prerelease publisher depends on the guard plus every producer/policy job;
`release.yml` (tag releases) builds once, publishes the draft's assets after a
digest check, and attests provenance — no rebuild gate. The former
`build-toolchain-manifest.yml` aggregate workflow is retired;
`verify/aggregate-checksums.sh` remains an optional local tool.

Windows runs on `windows-2025`; macOS runs on `macos-26` with job-level
`DEVELOPER_DIR=/Applications/Xcode_26.6.app` (Xcode build `17F113`, SDK
`macosx26.5`). Runner labels are versioned, not immutable image SHAs — the
project makes no image-SHA claim, and production jobs make no runner-identity
comparison. The `ImageOS`/`ImageVersion`/`RUNNER_ARCH` identity gate belongs
to the optional local `verify-native.sh` two-run comparison: it compares the
stable payload (host executable + Chrome manifests) across matched identities,
exempting Tauri installer bytes (NSIS/MSI/DMG, `.app` metadata) and the
enclosing archive/sidecar, which drift with producer-controlled timestamps.
Identity mismatch exits 2 and prints `re-run on matched image`.

**Clean contract (OTP-01 1d).** Root `./gradlew clean` removes every declared
generated product, verification, SBOM, checksum, and tool output under `build/`
plus the two named source-tree exceptions (`web-extension/dist` and
`web-extension/rust-wasm/pkg`) and the native-bridge source-tree outputs.
`rust/scripts/assert-clean.sh` asserts none survive. See
`docs/reproducible-builds.md` for the full verification supply chain, output
paths, and the six-step pinned-tool upgrade procedure.

## Architecture Overview

Veles is a single-module Android app (minSdk 33) that intercepts bank OTP notifications, extracts the OTP code, amount, and merchant, then re-presents the OTP as a concise notification with a one-tap "Copy" action.

### Core Flow

```
Incoming notification
  → NotificationListener (NotificationListenerService)
      → CompositeMessageHandler
          → RegexMessageHandler (one per bank, configs loaded from Room DB)
              → UserNotifierOtpMessageHandler — posts simplified notification
                  → CopyDataReceiver (BroadcastReceiver) — copies OTP to clipboard on tap
```

If a handler returns `ACCEPTED`, the original notification is cancelled and the chain stops. Otherwise `FILTERED` is returned and the next handler is tried.

`NotificationListener` accepts injected `MessageHandler` and `NotificationStatePreferences` via its constructor (defaulting to production instances), which enables mocking in tests without Android runtime.

### Bank Handler Configuration

Bank configs live in a Room database (`bank_handler_configs.db`, entity `BankHandlerConfig`). `BankHandlerDatabase.SeedCallback.onCreate` inserts the initial UOB Thailand entry on first install.

`RegexMessageHandler` uses three regexes per bank:
- `otpRegex`: group 1 = OTP id/prefix, group 2 = OTP value
- `moneyRegex`: group 1 = currency code, group 2 = amount
- `merchantRegex`: group 1 = merchant name

All three must match for the message to be `ACCEPTED`.

### Test Screen

The app has a built-in test harness for validating handler configs without a real bank SMS:

1. `TestScreen` lets the user type a notification text and tap "Send".
2. `TestNotificationSender` posts it as a notification on channel `VelesTestChannel`.
3. `NotificationListener.onNotificationPosted` intercepts it, runs the handler chain, then writes the result to the singleton `TestResultFlow.current`.
4. `TestViewModel` observes `TestResultFlow` and updates `TestState.lastResult`, which the UI renders as a "Matched ✓" or "No match" badge.

`TestResultFlow` is reset in `TestViewModel.onCleared()` to prevent stale badges on re-entry.

`TestNotificationSender` also posts a `VISIBILITY_SECRET` verification probe (`postProbe`) for the sensitive-notifications flow; the probe carries a random OTP-like code so the listener can tell redacted from readable text. Verification (`PermissionsViewModel.verifySensitiveAccess`) resets `TestResultFlow` (before posting and after the probe resolves) so the probe does not leave a stale badge on the Test screen.

`MessageHandlingResult` carries the extracted `OtpMessage` (`otpMessage` field) when a template matches, which the Test screen result card displays.

### Sensitive notifications (Android 15+)

On Android 15 (SDK 35)+, the OS redacts OTP/2FA content inside notifications before handing them to any `NotificationListenerService`. Veles detects and grants the `RECEIVE_SENSITIVE_NOTIFICATIONS` access entirely in-app:

- **Static detection** — `SensitiveNotificationsStatus.check()` combines the runtime permission (role-based grant) with the hidden AppOp (`unsafeCheckOpNoThrow`), returning `NotApplicable` below SDK 35, `Granted(via = Role|AppOp)`, or `NotGranted`.
- **CDM watch-profile grant** — `CompanionAssociationService` drives `CompanionDeviceManager.associate` with `DEVICE_PROFILE_WATCH`, which is the only profile that carries the sensitive-notifications role. `IntentSenderLauncher` wraps the activity-result contract so the system's "pick a nearby Bluetooth device" dialog is launched from the `PermissionsActivity`. The returned grant is verified, not trusted.
- **Verification probe** — `TestNotificationSender.postProbe()` posts a `VISIBILITY_SECRET` notification with a random OTP-like code through the same test-notification pipeline the Test screen uses. `NotificationListener.onNotificationPosted` runs the handler chain; `PermissionsViewModel.verifySensitiveAccess` compares the text the listener saw against the text posted. Readable ⇒ `RedactionState.Readable`; redacted ⇒ `RedactionState.Hidden`; timeout ⇒ `Unknown`.
- **Merged state** — `PermissionsViewModel.mergedSensitiveState` combines the static grant with the live `RedactionState` into `SensitiveNotificationsUiState` (`NotApplicable`, `NotGranted`, `Verifying`, `Granted`, `GrantedButRedacted`, `Unknown`), exposed on `PermissionsState`.
- **Card-only UI** — `SensitiveNotificationsCard` (in `permissions/ui/components/`) renders only when the state is `NotGranted`, `Verifying`, `GrantedButRedacted`, or `Unknown`; it is absent on Android 14 and below and after a confirmed grant. The card offers companion pairing with a browser link to the pairing guide, a collapsible "More options" section (OEM redaction settings, Enhanced-notifications toggle, copyable adb command, and adb guide link), and a "Check now" verify button.

### Package Structure

| Package | Responsibility |
|---|---|
| `otp/` | `NotificationListener` service, `CopyDataReceiver` |
| `otp/handlers/` | `MessageHandler` / `OtpMessageHandler` interfaces, `RegexMessageHandler`, `CompositeMessageHandler`, `UserNotifierOtpMessageHandler` |
| `otp/config/` | `BankHandlerConfig` (Room entity), `BankHandlerConfigDao`, `BankHandlerDatabase` (with seed), `BankHandlerRepository` |
| `permissions/` | `PermissionsActivity` launcher |
| `permissions/services/` | `PermissionProvider` per type (`ACCESS_NOTIFICATIONS`, `SEND_NOTIFICATIONS`, `RECEIVE_SENSITIVE_NOTIFICATIONS`), `SensitiveNotificationsStatus`, `CompanionAssociationService`, `SensitiveNotificationPermissionProvider`, `IntentSenderLauncher` |
| `permissions/viewmodal/` | `PermissionsViewModel` (StateFlow MVVM), `PermissionsState`, `PermissionsActions`, `SensitiveNotificationsUiState` |
| `permissions/ui/` | `VelesPermissionsApp` (NavHost), `PermissionsScreen`, `SensitiveNotificationsCard`, sub-components |
| `testing/` | `TestNotificationSender`, `TestScreen`, `TestViewModel`, `TestState` |
| `common/` | `NotificationStatePreferences`, `TestResultFlow`, `TestInputPreferences`, `VelesLinks`, UI theme, `TestTags` |

### Navigation

`VelesPermissionsApp` wraps a Compose `NavHost` in a `Scaffold` with a persistent bottom
`NavigationBar` (`VelesBottomBar` in `common/ui/`) with three destinations: `permissions`
(Home, start), `bank-configs` (Templates), and `test` (Test). `bank-config-edit?id={id}` is a
full-screen route that hides the bottom bar. The theme is a static emerald/gold brand palette
(`Colors.kt`/`Theme.kt`) derived from the launcher icon; Material You dynamic color is
intentionally not used. The gold "success" accent is mapped onto `colorScheme.tertiary`.

### Testing Approach

- **Unit tests** (`src/test/`) use MockK; no Android runtime. Focus on `RegexMessageHandler` regex logic and `CompositeMessageHandler` dispatch.
- **Instrumented tests** (`src/androidTest/`) include Compose UI tests (`VelesPermissionsAppTests`) and `NotificationListener` service tests using `mockkStatic` to mock Android framework classes.
- `TestTags` in `common/ui/TestTags.kt` are the stable selectors for UI tests — keep them in sync when adding new UI.

### Adding a New Bank Handler

Insert a row into the `bank_handler_configs` Room database with the three regexes. For seeding at install time, add an `INSERT` in `BankHandlerDatabase.SeedCallback.onCreate`. No new Kotlin classes are needed.

## Versioning & Releases

`versionCode`/`versionName` are derived from git tags by the `com.gladed.androidgitversion`
plugin (`codeFormat = "MMNNPP"`; tag `0.0.1` → code 1). Tags are plain semver with no `v`
prefix. Pushing a `X.Y.Z` tag triggers `.github/workflows/release.yml`, which builds the
release APK and creates a GitHub Release — signed if the `KEYSTORE_BASE64`,
`KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` secrets exist, unsigned otherwise.
`release-build.yml` runs `assembleRelease` automatically on master pushes and as an opt-in
on PRs (add the `release-build` label); it can also be triggered manually via
`workflow_dispatch`, which additionally publishes a prerelease GitHub Release (tagged with
the derived version, titled "(manual build)") — re-dispatching at the same commit replaces
the previous manual release, but the job aborts rather than overwriting a real tag-triggered
release.
