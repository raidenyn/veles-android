# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Build debug APK
./gradlew assembleDebug

# Run unit tests (JVM, no device needed)
./gradlew testDebugUnitTest

# Run a single unit test class
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.UobOtpMessageHandlerTest"

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
      → UobOtpMessageHandler (MessageHandler)   — regex-parses UOB SMS format
          → UserNotifierOtpMessageHandler (OtpMessageHandler) — posts simplified notification
              → CopyDataReceiver (BroadcastReceiver) — copies OTP to clipboard on tap
```

The handler chain is wired in `NotificationListener`'s constructor via constructor injection, enabling easy mocking in tests. If the message matches (`ACCEPTED`), the original notification is cancelled; otherwise it passes through (`FILTERED`).

### Package Structure

| Package | Responsibility |
|---|---|
| `otp/` | Notification listening, OTP parsing, user notification |
| `otp/handlers/` | `MessageHandler` chain-of-responsibility interfaces and implementations |
| `permissions/` | The launcher `Activity`, permission management UI and ViewModel |
| `permissions/services/` | `PermissionProvider` per permission type (ACCESS_NOTIFICATIONS, SEND_NOTIFICATIONS) |
| `permissions/viewmodal/` | `PermissionsViewModel` (StateFlow-based MVVM), `PermissionsState` |
| `common/` | `NotificationStatePreferences` (SharedPreferences wrapper), UI theme, `TestTags` |

### Adding a New Bank Handler

1. Implement `MessageHandler` (raw notification → parsed `OtpMessage`).
2. Wrap the existing `UserNotifierOtpMessageHandler` (or another `OtpMessageHandler`) for output.
3. Wire the new handler in `NotificationListener`'s default-parameter block.

### Testing Approach

- **Unit tests** (`src/test/`) use MockK; no Android runtime needed. Focus on `MessageHandler` regex logic.
- **Instrumented tests** (`src/androidTest/`) include Compose UI tests (`VelesPermissionsAppTests`) and `NotificationListener` service tests that mock Android framework classes via `mockkStatic`.
- `TestTags` in `common/ui/TestTags.kt` are the stable selectors used in UI tests — keep them in sync when adding new UI.