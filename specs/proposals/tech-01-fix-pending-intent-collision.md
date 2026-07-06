# Tech: Fix PendingIntent collision in Copy action (bug)

**Type:** Technical improvement (bug fix)
**Priority:** High — user-visible correctness bug
**Effort:** Small (~1 hour)

## Problem

`UserNotifierOtpMessageHandler.onOtpMessageReceived` creates the "Copy" `PendingIntent` with a
hard-coded request code `0` and `FLAG_UPDATE_CURRENT`:

```kotlin
PendingIntent.getBroadcast(
    context,
    0,                     // same request code for every notification
    copyIntent,
    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
)
```

Because the `Intent` differs only in its *extras* (which are not part of `Intent.filterEquals`),
every OTP notification resolves to the **same** `PendingIntent`. `FLAG_UPDATE_CURRENT` overwrites
the extras each time, so when two OTP notifications are visible simultaneously, tapping "Copy" on
the older one copies the **newest** OTP — the wrong code, silently.

## Proposal

Use a unique request code per notification, derived from the same value already used as the
notification id:

```kotlin
PendingIntent.getBroadcast(
    context,
    message.id,            // or message.hashCode(), matching notify()
    copyIntent,
    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
)
```

Alternatively (belt and braces), also make the intents distinct with
`copyIntent.data = Uri.parse("veles://otp/${message.id}")` so `filterEquals` differs even if
request codes ever collide.

## Testing

- Unit test in `UserNotifierOtpMessageHandlerTest`: post two OTP messages, capture both
  `PendingIntent`s (or the request codes via `mockkStatic(PendingIntent::class)`), assert they are
  distinct and each carries its own `EXTRA_COPY_TEXT`.
- Manual: use the Test Screen to fire two different matching messages, tap Copy on the first
  notification, verify the clipboard holds the first OTP.
