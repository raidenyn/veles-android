# Fix Copy PendingIntent identity collision (reopens #10)

**Issue:** [raidenyn/veles-android#10](https://github.com/raidenyn/veles-android/issues/10)
**Prior fix (insufficient):** [raidenyn/veles-android#27](https://github.com/raidenyn/veles-android/pull/27)
**Type:** Technical improvement (bug fix)
**Priority:** High — user-visible correctness bug
**Effort:** Small (~1-2 hours)

## Problem

PR #27 fixed issue #10 ("tapping Copy on an older OTP notification copies the newest OTP") by
giving the Copy `PendingIntent` a unique request code and a unique `Uri`, both derived from
`OtpMessage.id`:

```kotlin
// UserNotifierOtpMessageHandler.kt
data = Uri.parse("veles://otp/${message.id}")
...
PendingIntent.getBroadcast(context, message.id, copyIntent, FLAG_UPDATE_CURRENT or FLAG_IMMUTABLE)
```

`OtpMessage.id` is populated in `RegexMessageHandler.kt` as `message.key.hashCode()`, where
`message.key` is the **source** Android notification's `StatusBarNotification.key`
(`pkg|id|tag|uid`). That key is only unique across messages if the *source* app's notification
id/tag changes between posts — which is not guaranteed:

- `TestNotificationSender` (used by the in-app Test Screen) always posts under a hard-coded
  `NOTIFICATION_ID = 99999`, so two Test Screen sends in a row produce an identical `sbn.key`.
- Many banking apps update-in-place (reuse the same notification id/tag) for sequential OTP
  notifications.

When the source key repeats, `OtpMessage.id` repeats, so **both** of PR #27's defenses collapse
back to the same request code and the same `Uri` — reproducing the original bug: tapping Copy on
the older notification copies the newest OTP, silently.

Meanwhile, `UserNotifierOtpMessageHandler` already computes `notificationId = message.hashCode()`
to call `notify(notificationId, ...)`. This hashes the actual OTP content (`otp`, `pay`,
`merchant`), not the source key, so it's reliably distinct whenever two notifications are
genuinely different — which is exactly why two simultaneous OTP notifications already show up as
separate tray entries today. The Copy action was just never wired to this same identity. (Issue
#10's own proposal even suggested this: *"Use `message.id`, or `message.hashCode()`, matching
`notify()`"* — PR #27 took the first option, which turned out to be the wrong one.)

`OtpMessage.id` is not referenced anywhere outside these two lines in production code.

## Goal

The Copy action's `PendingIntent` and `Uri` are keyed off the same identity Android already uses
to decide whether two OTP notifications are distinct in the tray (`notificationId =
message.hashCode()`), so the two can never disagree again — regardless of what the source
notification's id/tag/key happen to be.

## Non-goals

- Changing `TestNotificationSender`'s fixed notification id. The fix must be correct regardless
  of source-notification-id reuse, so this isn't required for correctness, and is out of scope.
- Any change to clipboard clearing, redaction, or notification content/text (unrelated, already
  covered by prior work).

## Architecture

No architectural change — this narrows an existing identity computation to a single source of
truth:

```
UserNotifierOtpMessageHandler.onOtpMessageReceived(message)
  notificationId = message.hashCode()          // unchanged: already used for notify()
  copyIntent.data = "veles://otp/$notificationId"   // was: message.id
  PendingIntent.getBroadcast(context, notificationId, copyIntent, ...)  // was: message.id
  notify(notificationId, ...)                  // unchanged
```

## Components

### `OtpMessageHandler.kt` (modify — `otp/handlers/OtpMessageHandler.kt`)

Remove the now-unused `id: Int` field from `OtpMessage`:

```kotlin
data class OtpMessage(
    val otp: Otp,
    val pay: Money,
    val merchant: String,
)
```

### `RegexMessageHandler.kt` (modify — `otp/handlers/RegexMessageHandler.kt`)

Stop populating the removed field — drop `id = message.key.hashCode(),` from the `OtpMessage(...)`
construction. No other change; `message.key` is otherwise unused by this handler and is not
touched further.

### `UserNotifierOtpMessageHandler.kt` (modify — `otp/handlers/UserNotifierOtpMessageHandler.kt`)

Replace both `message.id` usages with the already-computed `notificationId`:

```kotlin
val notificationId = message.hashCode()

val copyIntent =
    Intent(context, CopyDataReceiver::class.java).apply {
        action = "Copy"
        // A unique data URI makes Intent.filterEquals differ per message, so even
        // if two request codes ever collide the PendingIntents stay distinct and
        // each keeps its own extras (FLAG_UPDATE_CURRENT would otherwise overwrite
        // them, making the older notification's Copy action copy the newest OTP).
        // Tied to notificationId (not the source notification's key) because the
        // source key is not guaranteed unique across messages.
        data = Uri.parse("veles://otp/$notificationId")
        putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, message.otp.value)
        putExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, notificationId)
    }
val copyPendingIntent: PendingIntent =
    PendingIntent.getBroadcast(
        context,
        // Same identity used for notify() below, so two notifications that are
        // distinct in the tray always have distinct Copy PendingIntents, and vice
        // versa. Do not go back to a value derived from the source notification's
        // key/id/tag — those can repeat across distinct OTP messages (see #10).
        notificationId,
        copyIntent,
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
```

`notificationId` is computed once (hoisted above the intent, as it already effectively is) and
reused for both the copy intent and the later `notify(notificationId, builder.build())` call — no
behavior change to the `notify()` call itself, just reuse of the same local.

## Data flow

### Two distinct OTPs visible at once (the bug scenario)
```
Message A (OTP 111111) arrives → notificationId_A = hash(A) → notify(notificationId_A, ...)
                                → Copy_A PendingIntent(requestCode = notificationId_A, uri = .../notificationId_A)
Message B (OTP 222222) arrives, same source key as A → notificationId_B = hash(B) ≠ notificationId_A
                                → notify(notificationId_B, ...)  // separate tray entry, as today
                                → Copy_B PendingIntent(requestCode = notificationId_B, uri = .../notificationId_B)
                                → distinct request code AND distinct Uri → distinct PendingIntent
Tap Copy on A → CopyDataReceiver receives EXTRA_COPY_TEXT = "111111" (A's own extras, untouched by B)
```

### Two messages that hash identically (content-identical OtpMessage)
```
notificationId_A == notificationId_B → notify() replaces the tray entry (same as today's behavior
for identical content) → Copy PendingIntents also collapse into one, consistently. Not a
regression: this is the same collapse Android already performs for the notification itself.
```

## Error handling

No new error paths. Removing `OtpMessage.id` is a compile-time-checked change — any remaining
reference outside the files listed above will fail the build, not fail silently.

## Testing

### `RegexMessageHandlerTest.kt` / `UobThaiRegexMessageHandlerTest.kt` (modify — `src/test/`)

Remove `id = defaultMessage.key.hashCode(),` from the expected `OtpMessage(...)` fixtures (2 call
sites in `RegexMessageHandlerTest.kt`, 1 in `UobThaiRegexMessageHandlerTest.kt`).

### `UserNotifierOtpMessageHandlerTest.kt` (modify — `src/test/`, Robolectric)

- Remove `id = ...` from all `OtpMessage(...)` fixtures in this file.
- `Copy PendingIntent is distinct per notification and keeps its own OTP` (existing): keep as-is
  otherwise — still proves two differently-content'd messages get distinct request codes and
  each keeps its own `EXTRA_COPY_TEXT`. This continues to pass, now via `notificationId` instead
  of the removed `id`.
- **New test:** `Copy PendingIntent request code matches the posted notification id` — asserts
  `shadowPendingIntent.requestCode == message.hashCode()` (i.e. the same id passed to
  `notify()`). This is the regression guard: it pins the invariant that Copy-button identity and
  tray-notification identity can never be computed from different sources again.
- **New test:** `Copy intent data URI encodes the notification id` — asserts the copy intent's
  `data` (`Uri`) path segment equals `message.hashCode().toString()`.

### `UserNotifierOtpMessageHandlerTest.kt` (modify — `src/androidTest/`)

Remove `id = 1` from `defaultMessage`. No other change; `testNotificationCreation` already keys
off `defaultMessage.hashCode()`.

### Manual (per issue #10's own testing scope, still valid)

Use the Test Screen to fire two different matching messages in quick succession (this already
reproduces the source-key collision via `TestNotificationSender`'s fixed id), tap Copy on the
first notification while the second is still visible, verify the clipboard holds the first
notification's OTP.

## Out of scope

- `TestNotificationSender`'s fixed notification id (see Non-goals).
- Clipboard clearing/redaction behavior (unrelated, already covered by prior work).

## Risks

- **Content-identical `OtpMessage`s (same otp/pay/merchant) arriving close together:** their
  `notificationId`s collide by design (same as today), so the second `notify()` call replaces the
  first tray entry and their Copy PendingIntents collapse into one. This matches Android's own
  notification-identity semantics and is not a new edge case introduced by this fix.
- **Removing `OtpMessage.id` is a public-ish data class field:** grep confirms it is not
  referenced anywhere outside `UserNotifierOtpMessageHandler.kt`, `RegexMessageHandler.kt`, and
  their direct test fixtures, so removal is safe and mechanical.
