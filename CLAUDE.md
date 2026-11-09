# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mandatory Local Checks Before Creating a PR

Before creating any pull request, run the local lint checks on the exact changes
being submitted and confirm that every command succeeds:

```bash
./gradlew spotlessCheck detekt lintDebug
```

Also run the raw `android.util.Log` guard from the `lint-check` job in
`.github/workflows/ci.yml`. These are all parts of the CI lint gate;
`detekt` and `lintDebug` alone are not sufficient because they do not check
Spotless formatting.

If formatting fails, run `./gradlew spotlessApply`, inspect its changes, and rerun
the checks. Fix all failures before pushing the initial PR branch and running
`gh pr create`. Do not rely on CI to discover formatting or lint failures.
Rerun the checks after subsequent code changes before pushing PR updates.
If a check cannot run, report the blocker instead of claiming the PR is ready.

## Stable Signing Identities (Android and macOS builds)

Always reuse the **same existing signing identity within each platform/app identity** for
subsequent app and fixture builds. This is one stable key per app identity, not one
cross-platform key: Android and macOS identities are separate.

- **Android debug fixture:** keep the approved fixture keystore backed up securely across
  containers (never in git; never commit keys or passwords). Compare the signing certificate
  fingerprints of the new APK and the installed one before `adb install -r`. Do not
  regenerate the key, uninstall the app, or clear data as a routine workaround; if the
  original key is unavailable, stop and ask.
- **OTP-23 fixture migration (user-authorized 2026-11-02):** the replacement debug fixture key
  is `config/android_keys/otp23-fixture.keystore`, with local Gradle init script
  `config/android_keys/fixture-signing.gradle`. The directory is git-ignored and must be
  retained and backed up securely. Build the existing isolated fixture and its test APK with
  `./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug assembleDebugAndroidTest`.
  This debug-only identity does not authorize changing production signing; keep comparing APK
  certificates before future updates and never commit the key or its credentials.
- **macOS:** reuse the exact approved signing identity/team and bundle ID. No ad-hoc
  substitution, key export, or TCC reset; use the Aqua keychain context when required.
- Different production signing is a separately planned migration with revalidation. Do not
  borrow debug credentials. Never record fingerprints that were not read from real output.

Details: `docs/windows-ble-poc.md` (Android) and `docs/desktop-ble-poc.md` (macOS).

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

# Native-bridge (OTP-01 sub-project 1c) — Tauri 2.x packaging/build scaffold for the Native Messaging host.
# NOTE: these are existing TOOLING commands, not the approved production architecture. Per the RFC
# amendment of 2026-10-26 the production host is a plain Rust executable that never starts the Tauri
# event loop; retaining Tauri solely for packaging is NOT approved/NOT justified (docs/otp-23-requirements-matrix.md D-01);
# OTP-25 picks another packaging pipeline or brings a separately reviewed justification.
# Install npm deps, check format/lint/typecheck, run npm+cargo tests
./gradlew bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeTest

# Build the unsigned host (cargo tauri build --no-bundle -- --locked)
./gradlew bridgeBuild

# Emit Chrome native-messaging host manifests
./gradlew bridgeManifests

# Platform-specific deterministic packaging into build/native-bridge/
./gradlew bridgePackage

# OTP-24 production Native Messaging host (native-bridge/host, crate veles-ble-host) — plain Rust,
# NO Tauri/Node/npm; distinct from the scaffold tasks above. Locked cargo, toolchain pinned to
# 1.98.0 by native-bridge/host/rust-toolchain.toml, cargo target under build/native-bridge/host-target.
./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest
# Build ONLY veles-native-host (locked, release, no features; the test-driver bin is feature-gated and
# never built). Emits build/native-bridge/host/veles-native-host[.exe] + SHA256SUMS. Windows and macOS
# runners compile the real adapters; Linux only runs the portable checks.
./gradlew bridgeHostBuild
python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py'   # static build-contract checks

# OTP-01 sub-project 1d — supply chain
# Supply-chain policy verifier (CI policy job in build-supply-chain.yml + local).
verify/verify-supply-chain.sh                        # SBOM + license + install-script + remote-code (CI policy job + local)
verify/aggregate-checksums.sh <commit>               # build/verification/SHA256SUMS.toolchains (local only)

# Supply-chain verifier local prerequisites (CI provisions these in
# build-supply-chain.yml; locally you must run them first):
#   npm ci --ignore-scripts --prefix verify   # cyclonedx-npm + license-checker -> verify/node_modules/.bin/
#   ./gradlew installCargoDeny                 # cargo-deny -> build/verify-tools/ (cargo-cyclonedx is installed by generate-sboms.sh)
# `verify/verify-supply-chain.sh` asserts Node 26.8.1 / npm 11.19.0 and the
# active Rust toolchain (generate-sboms.sh shells out to ./gradlew verifyCargoCyclonedx).

# OTP-25 standalone installers (native-bridge/installer: engine crate, NSIS and SwiftPM frontends) — plain Rust,
# NO Tauri/Node. Checks (run on Linux, Windows and macOS CI) and the UNSIGNED input producers (Windows/macOS only):
./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest
./gradlew bridgeInstallerScriptsTest          # Python suites incl. test_build_contract.py (not on Windows)
./gradlew bridgeInstallerUnsigned             # PRODUCTION channel; needs a tracked, approved release-<platform>.json
./gradlew bridgeInstallerPackageFixture       # FIXTURE channel from the tracked fixture-<platform>.json
./gradlew bridgeInstallerSupplyChain          # verify/verify-supply-chain.sh incl. native-installer.cdx.json
# Output lands under build/native-bridge/installer/{target,slots,out,tools}. Producers never sign; signing,
# notarization and the release gate are the protected release-native-installers.yml (Task 13).

# Assert every declared generated output was removed by `./gradlew clean`.
rust/scripts/assert-clean.sh
```

Clean APK assembly now requires rustup/cargo and NDK r29 (`rustJni` is a dependency of
`:app:assembleDebug`/`assembleRelease`). Node >= 22 is required for `rustTest` (which
runs a WASM smoke test), the WASM/extension commands, and the native-bridge commands
above — it is not needed to build the APK on its own.

**Verification Node (OTP-01 1d).** Supply-chain environments
require an **exact** Node pin — Node **26.8.1** with bundled npm **11.19.0** —
asserted by `verify/verify-supply-chain.sh`. The developer-facing package contract remains a
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
| `build-native-windows.yml` | OTP-25 Windows installer inputs (UNSIGNED): `bridgeInstallerUnsigned` (approved `release-windows.json`) or `bridgeInstallerPackageFixture`; each builds the tree twice (slots a/b) and publishes `tree.tar` + `provenance.json` + `report-ab.json` + `SHA256SUMS` as `native-unsigned-windows` / `native-fixture-windows`; provisions the pinned NSIS under `build/native-bridge/installer/tools/nsis`. No Tauri, no signing |
| `build-native-macos.yml` | OTP-25 macOS installer inputs (UNSIGNED), same shape under the pinned Xcode: `native-unsigned-macos` / `native-fixture-macos`. No Tauri, no signing |
| `build-supply-chain.yml` | SBOM/license/audit reports (`supply-chain-reports`); report validation + policy enforcement (six SBOMs incl. `native-host.cdx.json` and `native-installer.cdx.json`; vendored `bluest` patch provenance via `verify/check-vendored-patches.mjs`) |
| `build-native-host.yml` | OTP-24 production host (called from `ci.yml`, not `release-build.yml`): Linux portable checks, `windows-2025`/`macos-26` compile + test + `bridgeHostBuild`; artifacts `native-host-windows` (exe + `SHA256SUMS`) / `native-host-macos` (mode-preserving tar of the executable + `SHA256SUMS`, plus archive sidecar; upload drops bare exec bits) (no installer, no Tauri). Not the scaffold's `native-windows`/`native-macos` archives |

`release-build.yml` composes these with the neutral names, and its manual
prerelease publisher depends on the guard plus every producer/policy job;
`release.yml` (tag releases) builds once, publishes the draft's assets after a
digest check, and attests provenance — no rebuild gate. The former
`build-toolchain-manifest.yml` aggregate workflow is retired.

Windows runs on `windows-2025`; macOS runs on `macos-26` with job-level
`DEVELOPER_DIR=/Applications/Xcode_26.6.app` (Xcode build `17F113`, SDK
`macosx26.5`). Runner labels are versioned, not immutable image SHAs — the
project makes no image-SHA claim.

**Clean contract (OTP-01 1d).** Root `./gradlew clean` removes every declared
generated product, verification, SBOM, checksum, and tool output under `build/`
plus the two named source-tree exceptions (`web-extension/dist` and
`web-extension/rust-wasm/pkg`) and the native-bridge source-tree outputs.
`rust/scripts/assert-clean.sh` asserts none survive.

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
