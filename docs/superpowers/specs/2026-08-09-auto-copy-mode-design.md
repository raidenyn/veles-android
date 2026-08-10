# Auto-copy mode

**Issue:** [raidenyn/veles-android#21](https://github.com/raidenyn/veles-android/issues/21)
**Type:** Feature
**Priority:** Medium-high

## Goal

Let a user opt in to immediately copying each intercepted OTP to the system clipboard. The replacement OTP notification remains available as feedback and retains its Copy action as a fallback. Auto-copy is disabled by default.

## Scope

- A global auto-copy toggle on the existing Permissions screen.
- A Proto DataStore-backed settings repository.
- Shared, sensitive clipboard writing with the existing two-minute best-effort TTL.
- Notification feedback that indicates the OTP has already been copied.

## Non-goals

- Per-bank auto-copy overrides or a Room schema migration.
- A user-configurable clipboard TTL.
- Changing notification matching, cancellation, or bank-template behavior.
- Delivering clipboard writes when the application process is killed before asynchronous work starts or before the TTL callback runs.

## Architecture

### Settings storage

Add a Proto DataStore `Settings` message with `bool auto_copy_otp`. The field defaults to `false`, so existing installations and unreadable settings storage do not enable auto-copy.

`SettingsRepository` is an application-scoped singleton. It owns the DataStore and exposes:

- A `Flow<Boolean>` for UI state.
- A suspend read for notification handling.
- A suspend update used by the Permissions screen switch.

The application provides DataStore through Hilt. The repository handles corrupted or unreadable data by emitting and returning `false` rather than enabling automatic clipboard writes.

### Permissions UI

`PermissionsViewModel` observes the repository's Boolean flow and exposes it in `PermissionsState`. `PermissionsActions` gains an action to update the value through the ViewModel coroutine scope.

`PermissionsScreen` renders one global auto-copy setting card. Its switch reflects the current persisted setting. Supporting text explicitly states that intercepted OTPs are copied automatically, are marked sensitive, and are cleared from the clipboard after two minutes. This setting is separate from Android notification permissions.

### Clipboard helper

Extract the clipboard write and delayed ownership-checked clear from `CopyDataReceiver` into an injectable `OtpClipboard` helper. The helper:

1. Obtains `ClipboardManager`.
2. Creates a plain-text clip with the existing OTP label.
3. Sets `ClipDescription.EXTRA_IS_SENSITIVE`.
4. Writes the clip.
5. Schedules the existing two-minute clear only when the current clip still has the expected label and OTP text.

The helper returns whether it successfully wrote a clip. A missing clipboard service is a no-op. `CopyDataReceiver` invokes this helper and continues to update the associated notification to its copied state after an action tap.

### OTP notification handling

`UserNotifierOtpMessageHandler` receives `SettingsRepository`, `OtpClipboard`, and an application-scoped coroutine scope. It keeps its synchronous `OtpMessageHandler` interface, but launches an ordered coroutine for each matched OTP:

1. Read the persisted auto-copy setting.
2. If enabled, invoke `OtpClipboard.copy(otp)`.
3. If application notifications are enabled, post the Veles notification.

When auto-copy is enabled, the notification is built with `copied = true`, so the user sees existing copied feedback and still has the Copy action. When disabled, the notification is built with `copied = false`, preserving current behavior.

The clipboard step is intentionally independent of notification permission. A user who has enabled auto-copy still receives clipboard writes when Veles notifications are disabled; the notification is simply omitted.

## Data flow

### Auto-copy enabled

```
Incoming bank notification
  -> RegexMessageHandler matches OTP
  -> UserNotifierOtpMessageHandler launches application coroutine
  -> SettingsRepository reads auto_copy_otp
  -> OtpClipboard writes sensitive OTP clip and schedules TTL clear
  -> notification permission check
  -> copied-state Veles notification posted, when permitted
```

### Auto-copy disabled

```
Incoming bank notification
  -> RegexMessageHandler matches OTP
  -> UserNotifierOtpMessageHandler launches application coroutine
  -> SettingsRepository reads false
  -> notification permission check
  -> normal Veles notification posted, when permitted
```

### User taps Copy

```
CopyDataReceiver
  -> OtpClipboard writes/replaces sensitive clip and refreshes TTL
  -> replacement copied-state notification posted
```

## Error handling

- DataStore read or corruption failure results in `false`; automatic copying never turns on by accident.
- A missing clipboard service prevents copying but does not prevent the notification from posting.
- Auto-copy invokes the clipboard helper before the notification permission check.
- The delayed clear only clears the OTP it wrote; another OTP or user-copied text is preserved.
- Process death may prevent the asynchronous auto-copy coroutine or delayed cleanup callback from running. This matches the existing best-effort clipboard hygiene guarantee.

## Testing

### SettingsRepository

- The default value is disabled.
- Updating the setting persists and is observable.
- Corrupted or unreadable storage produces disabled behavior.

### OtpClipboard and CopyDataReceiver

- Sensitive clip construction and the ownership predicate retain current coverage.
- A Copy notification action delegates clipboard writing to `OtpClipboard` and rebuilds its notification in copied state.
- Missing clipboard service remains a no-op.

### UserNotifierOtpMessageHandler

- Enabled auto-copy invokes `OtpClipboard` before posting a copied-state notification.
- Disabled auto-copy does not invoke `OtpClipboard` and posts the normal notification state.
- Enabled auto-copy runs even when Veles notifications are disabled.
- Notification permission still suppresses only posting, not clipboard work.

### Permissions UI

- The switch renders from `PermissionsState.autoCopyEnabled`.
- Toggling invokes the new `PermissionsActions` update action.

## Risks

- **Background timing:** the notification listener accepts and cancels the source notification before the asynchronous copy task completes. This is acceptable because the task is launched immediately in an application-scoped coroutine and no synchronous DataStore API exists.
- **Clipboard overwrites:** a second OTP or user action can replace the current clip before the two-minute TTL. The existing label-and-text ownership check prevents stale delayed callbacks from clearing the replacement.
- **Security:** automatic clipboard writes are surprising and potentially sensitive. The feature is disabled by default, uses the platform sensitive-clip marker, clears after two minutes on a best-effort basis, and has explicit supporting text at the opt-in control.
