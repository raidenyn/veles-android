# android/AGENTS.md

The Android app node. `android/` is an independent Gradle build (`settings.gradle.kts` includes `:app`
and the `../rust` build); the repository-root `./gradlew` forwards to it for the common commands.

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
| `pairing/` | OTP-10 SPAKE2 pairing window, admission, `PairingExecution`, enrollment session and activation (new; lands with the pairing tasks) |
| `permissions/` | `PermissionsActivity` launcher |
| `permissions/services/` | `PermissionProvider` per type (`ACCESS_NOTIFICATIONS`, `SEND_NOTIFICATIONS`, `RECEIVE_SENSITIVE_NOTIFICATIONS`), `SensitiveNotificationsStatus`, `CompanionAssociationService`, `SensitiveNotificationPermissionProvider`, `IntentSenderLauncher` |
| `permissions/viewmodal/` | `PermissionsViewModel` (StateFlow MVVM), `PermissionsState`, `PermissionsActions`, `SensitiveNotificationsUiState` |
| `permissions/ui/` | `VelesPermissionsApp` (NavHost), `PermissionsScreen`, `SensitiveNotificationsCard`, sub-components |
| `testing/` | `TestNotificationSender`, `TestScreen`, `TestViewModel`, `TestState` |
| `common/` | `NotificationStatePreferences`, `TestResultFlow`, `TestInputPreferences`, `VelesLinks`, UI theme, `TestTags` |

### Navigation

`VelesPermissionsApp` wraps a Compose `NavHost` in a `Scaffold` with a persistent bottom `NavigationBar` (`VelesBottomBar` in `common/ui/`) with three destinations: `permissions` (Home, start), `bank-configs` (Templates), and `test` (Test). `bank-config-edit?id={id}` is a full-screen route that hides the bottom bar. The theme is a static emerald/gold brand palette (`Colors.kt`/`Theme.kt`) derived from the launcher icon; Material You dynamic color is intentionally not used. The gold "success" accent is mapped onto `colorScheme.tertiary`.

### Testing Approach

- **Unit tests** (`src/test/`) use MockK; no Android runtime. Focus on `RegexMessageHandler` regex logic and `CompositeMessageHandler` dispatch.
- **Instrumented tests** (`src/androidTest/`) include Compose UI tests (`VelesPermissionsAppTests`) and `NotificationListener` service tests using `mockkStatic` to mock Android framework classes.
- `TestTags` in `common/ui/TestTags.kt` are the stable selectors for UI tests — keep them in sync when adding new UI.

### Adding a New Bank Handler

Insert a row into the `bank_handler_configs` Room database with the three regexes. For seeding at install time, add an `INSERT` in `BankHandlerDatabase.SeedCallback.onCreate`. No new Kotlin classes are needed.

## Signing identities (Android)

Always reuse the **same existing signing identity within each platform/app identity** for subsequent app and fixture builds. This is one stable key per app identity, not one cross-platform key: the Android and macOS identities are separate (the macOS identity lives in `native-bridge/installer/macos/AGENTS.md`).

- **Android debug fixture:** keep the approved fixture keystore backed up securely across containers (never in git; never commit keys or passwords). Compare the signing certificate fingerprints of the new APK and the installed one before `adb install -r`. Do not regenerate the key, uninstall the app, or clear data as a routine workaround; if the original key is unavailable, stop and ask.
- **OTP-23 fixture migration (user-authorized 2026-11-02):** the replacement debug fixture key is `android/config/android_keys/otp23-fixture.keystore`, with local Gradle init script `android/config/android_keys/fixture-signing.gradle`. The directory is git-ignored and must be retained and backed up securely. Build the existing isolated fixture and its test APK from the independent Android build:
  `cd android && ./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug assembleDebugAndroidTest`
  (keep the directory git-ignored; never commit key material).
  This debug-only identity does not authorize changing production signing; keep comparing APK certificates before future updates and never commit the key or its credentials.
- Different production signing is a separately planned migration with revalidation. Do not borrow debug credentials. Never record fingerprints that were not read from real output.

Details: `docs/windows-ble-poc.md`.

Version name and code are injected at build time by the `androidGitVersion` plugin (`prefix = "android-v"`, `codeFormat = "MMNNPP"`).

## Build & Test

Run from `android/`. The root `./gradlew` forwards the common commands; use the `android/` form whenever a command needs a task option the root alias cannot carry, notably `--tests`.

```bash
# Build debug APK
./gradlew assembleDebug

# Build release APK (R8-minified; signed only if VELES_KEYSTORE_* env vars are set)
./gradlew assembleRelease

# Run unit tests (JVM, no device needed)
./gradlew testDebugUnitTest

# Run a single unit test class; the root `testDebugUnitTest` alias cannot accept `--tests`
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"

# Run instrumented tests (requires connected device or emulator)
./gradlew connectedDebugAndroidTest

# Run a single instrumented test class
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests
```

Clean APK assembly now requires rustup/cargo and NDK r29 (`rustJni` is a dependency of `:app:assembleDebug`/`assembleRelease`). Node >= 22 is not needed to build the APK on its own.

The Android consumer of the OTP-02 contract is `:app:protocolJvmTest`, which the root `./gradlew protocolCheck` runs (see `protocol/AGENTS.md`).

## Rules

- `.agents/rules/android-lint-before-push.md` — before pushing, run the Android lint gate from `android/`: `./gradlew spotlessCheck detekt lintDebug`, plus the raw `android.util.Log` guard from the `android / lint` job in `.github/workflows/check-android.yml`. If formatting fails, run `./gradlew spotlessApply`, inspect its changes, and rerun the checks; fix every failure before pushing and rerun after later changes. Never rely on CI to discover formatting or lint failures, and report a blocker instead of claiming the PR is ready.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
