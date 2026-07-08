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

# Run a single unit test class
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"

# Run instrumented tests (requires connected device or emulator)
./gradlew connectedDebugAndroidTest

# Run a single instrumented test class
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests
```

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

### Package Structure

| Package | Responsibility |
|---|---|
| `otp/` | `NotificationListener` service, `CopyDataReceiver` |
| `otp/handlers/` | `MessageHandler` / `OtpMessageHandler` interfaces, `RegexMessageHandler`, `CompositeMessageHandler`, `UserNotifierOtpMessageHandler` |
| `otp/config/` | `BankHandlerConfig` (Room entity), `BankHandlerConfigDao`, `BankHandlerDatabase` (with seed), `BankHandlerRepository` |
| `permissions/` | `PermissionsActivity` launcher |
| `permissions/services/` | `PermissionProvider` per type (`ACCESS_NOTIFICATIONS`, `SEND_NOTIFICATIONS`) |
| `permissions/viewmodal/` | `PermissionsViewModel` (StateFlow MVVM), `PermissionsState`, `PermissionsActions` |
| `permissions/ui/` | `VelesPermissionsApp` (NavHost), `PermissionsScreen`, sub-components |
| `testing/` | `TestNotificationSender`, `TestScreen`, `TestViewModel`, `TestState` |
| `common/` | `NotificationStatePreferences`, `TestResultFlow`, `TestInputPreferences`, UI theme, `TestTags` |

### Navigation

`VelesPermissionsApp` hosts a Compose `NavHost` with two routes: `permissions` (start) → `test`.

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
