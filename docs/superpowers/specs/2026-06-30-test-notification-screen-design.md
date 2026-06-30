# Test Notification Screen — Design Spec

**Date:** 2026-06-30

## Overview

Add a dedicated Test screen to the app that lets users type arbitrary notification text, post it as a real system notification, and see whether the regex handlers matched it. This lets developers verify handler configs without needing a real bank SMS.

---

## Navigation

- Adopt single-activity `NavHost` (navigation-compose already in dependencies).
- Two routes: `"permissions"` (existing) and `"test"` (new).
- `VelesPermissionsApp` becomes the `NavHost` host.
- `PermissionsScreen` gains an `onNavigateToTest: () -> Unit` action, rendered as a "Test" button.
- `TestScreen` navigates back via standard system back / toolbar back arrow.

---

## Service → UI Communication

**`common/TestResultFlow.kt`** — singleton `object` with a `MutableStateFlow<TestResult?>`:

```kotlin
data class TestResult(val result: MessageHandlingResult, val timestamp: Long)

object TestResultFlow {
    val current = MutableStateFlow<TestResult?>(null)
}
```

**`NotificationListener`** — after calling `messageHandler.onMessageReceived(message)`, checks `message.source == packageName`. If true (self-notification from our app), writes the result and current timestamp to `TestResultFlow.current`. No threading concerns: StateFlow is thread-safe and the service runs in-process with the Activity.

**`TestViewModel`** — collects `TestResultFlow.current` in `viewModelScope`, merging updates into `_uiState`.

---

## Test Notification Sending

**`testing/TestNotificationSender.kt`** — posts a system notification on a new channel `"VelesTestChannel"`:

- Channel name: "Veles Test" (separate from the OTP output channel `HandyOTPMessageChannel`).
- Notification title: `"Veles Test"` (fixed).
- Notification text: the user's input (becomes `EXTRA_TEXT`, which the `RegexMessageHandler` reads).
- Lazy channel creation on first send (same pattern as `UserNotifierOtpMessageHandler`).

---

## Input Persistence

**`common/TestInputPreferences.kt`** — SharedPreferences wrapper (same pattern as `NotificationStatePreferences`):

- Stores one key: `"test_input_text"`.
- Read on `TestViewModel` init; written on every `onTextChanged` call.

---

## ViewModel & State

**`testing/viewmodel/TestState.kt`**:

```kotlin
data class TestState(
    val inputText: String,
    val lastResult: TestResult?
)
```

**`testing/viewmodel/TestViewModel.kt`**:

- Init: loads `inputText` from `TestInputPreferences`, starts collecting `TestResultFlow.current`.
- `onTextChanged(text: String)`: updates `_uiState.inputText` + persists to `TestInputPreferences`.
- `send()`: calls `TestNotificationSender.post(inputText)`.

---

## Test Screen UI

**`testing/ui/TestScreen.kt`**:

| Element | Detail |
|---|---|
| Title | "Test" |
| `OutlinedTextField` | Multi-line, label "Notification text", pre-filled from saved text |
| "Send" button | Disabled when `inputText` is blank |
| Result badge | Shown only when `lastResult != null`; green "Matched ✓" for `ACCEPTED`, muted "No match" for `FILTERED` |

---

## Package Structure (new files)

```
common/
  TestInputPreferences.kt     — SharedPreferences wrapper for saved input text
  TestResultFlow.kt           — Singleton StateFlow for listener → UI communication

testing/
  TestNotificationSender.kt   — Posts system notification on VelesTestChannel
  ui/
    TestScreen.kt             — Compose screen
  viewmodel/
    TestState.kt              — UI state data class
    TestViewModel.kt          — ViewModel
    TestViewModelFactory.kt   — ViewModelProvider.Factory
```

## Modified files

```
otp/NotificationListenerService.kt     — Write to TestResultFlow for self-notifications
permissions/ui/VelesPermissionsApp.kt  — Become NavHost with two routes
permissions/ui/PermissionsScreen.kt    — Add onNavigateToTest action + "Test" button
permissions/viewmodal/PermissionsViewModel.kt  — Pass onNavigateToTest through actions (or via NavController directly)
```

---

## Data Flow (end-to-end)

1. User opens app → Permissions screen with "Test" button.
2. User taps "Test" → navigates to Test screen (pre-filled with last saved text).
3. User edits text → saved to SharedPreferences on each change.
4. User taps "Send" → `TestNotificationSender` posts notification with user text as `EXTRA_TEXT`.
5. System delivers notification → `NotificationListener.onNotificationPosted` fires.
6. `RegexMessageHandler` runs → returns `ACCEPTED` or `FILTERED`.
7. `NotificationListener` detects `source == packageName` → writes `TestResult` to `TestResultFlow`.
8. `TestViewModel` collects update → sets `lastResult` in UI state.
9. Test screen shows green "Matched ✓" or muted "No match".
10. If `ACCEPTED`: original test notification is cancelled + OTP notification appears in tray.
