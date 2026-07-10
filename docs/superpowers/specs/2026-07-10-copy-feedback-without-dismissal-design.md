# Copy Feedback Without Dismissal

**Date:** 2026-07-10
**Status:** Approved (pending user spec review)

## Problem

When the user taps the **Copy `<OTP>`** action on the re-presented OTP
notification, `CopyDataReceiver.onReceive` copies the OTP to the clipboard and
then immediately cancels the notification (`CopyDataReceiver.kt:53-55`). The
notification vanishes from the shade, which is inconvenient — the user loses
sight of the OTP, amount, and merchant the instant they copy, with no visual
confirmation that the copy succeeded.

## Goal

Keep the notification visible after a copy tap and give the user clear feedback
that the OTP was copied, while preserving the OTP value in the button label so
the user can still read it.

## Constraints

- Android notification action buttons cannot animate — the notification shade
  does not support animated button states. Realistic feedback is limited to
  re-posting the notification with an updated action label.
- The notification ID is `message.hashCode()` and is reused for re-posting, so
  the update replaces in place (no flicker, no new tray entry).
- The Copy `PendingIntent` identity scheme (request code = notification ID,
  data URI `veles://otp/<id>`, `FLAG_UPDATE_CURRENT | FLAG_IMMUTABLE`) must be
  preserved — it is the fix for the #10 PendingIntent-collision bug and must
  not regress.

## Design

### Behavior

1. User taps **Copy `<OTP>`** on the re-presented OTP notification.
2. The OTP is copied to the clipboard (unchanged — sensitive flag, 2-min clear
   timer).
3. Instead of cancelling the notification, the receiver **re-posts** it with
   the same notification ID and the Copy action label changed to
   **`"Copy <OTP> Copied ✓"`**. The OTP numbers remain visible; "Copied ✓" is
   appended as feedback.
4. The Copy button stays tappable. Each subsequent tap re-copies the OTP,
   re-posts the notification (idempotent), and resets the 2-min clipboard-clear
   timer.
5. The notification is never auto-dismissed by the app. It stays in the shade
   until the user swipes it away.

### Components

#### New: `OtpNotificationBuilder` (`otp/handlers/OtpNotificationBuilder.kt`)

A class taking `@ApplicationContext Context` in its constructor. Single public
method:

```
build(
    notificationId: Int,
    merchant: String,
    otp: String,
    amountText: String,
    currencyCode: String,
    copied: Boolean,
): Notification
```

Responsibilities:
- Builds the notification: small icon `R.drawable.ic_otp_message`, title =
  `merchant`, text = `"OTP: <otp>, Pay: <amountText> <currencyCode>"`,
  `PRIORITY_HIGH`.
- Builds the Copy `PendingIntent` internally, preserving the existing identity
  scheme: request code = `notificationId`, data URI
  `veles://otp/<notificationId>`, `FLAG_UPDATE_CURRENT | FLAG_IMMUTABLE`. All
  fields are stuffed into the intent extras so the receiver can rebuild the
  notification on tap:
  - `EXTRA_COPY_TEXT` = otp
  - `EXTRA_NOTIFICATION_ID` = notificationId
  - `EXTRA_MERCHANT` = merchant
  - `EXTRA_AMOUNT_TEXT` = amountText
  - `EXTRA_CURRENCY_CODE` = currencyCode
- Action label:
  - `copied = false` → `"Copy <otp>"`
  - `copied = true` → `"Copy <otp> Copied ✓"`
- Ensures the notification channel exists (idempotent). The
  `tryCreateNotificationChannel` logic moves here from
  `UserNotifierOtpMessageHandler`, so both the initial post and the re-post are
  covered.

This is the single source of truth for notification layout. Both the handler
and the receiver call it, eliminating duplication.

#### Modify: `UserNotifierOtpMessageHandler` (`otp/handlers/UserNotifierOtpMessageHandler.kt`)

Delegates to `OtpNotificationBuilder.build(..., copied = false)`, formats
`amount` via `BigDecimal.toPlainString()`, then calls
`NotificationManagerCompat.notify(id, notification)` behind the existing
`areNotificationsEnabled()` guard. The PendingIntent/URI/channel logic moves
into the builder.

#### Modify: `CopyDataReceiver` (`otp/CopyDataReceiver.kt`)

- Adds three companion constants:
  - `EXTRA_MERCHANT = "Merchant"`
  - `EXTRA_AMOUNT_TEXT = "AmountText"`
  - `EXTRA_CURRENCY_CODE = "CurrencyCode"`
- After copying the OTP to the clipboard (unchanged), **replaces**
  `cancel(notificationId)` with:
  ```
  OtpNotificationBuilder(context)
      .build(notificationId, merchant, otp, amountText, currencyCode, copied = true)
      .let { NotificationManagerCompat.from(context).notify(notificationId, it) }
  ```
- Reads `merchant`, `amountText`, `currencyCode` from the intent with `?: ""`
  fallbacks.
- The `notificationId != -1` guard (currently around `cancel`) now gates the
  re-post: if the id is missing, the re-post is skipped (degraded but safe —
  clipboard still works).
- The clipboard-clear timer (`Handler.postDelayed` + `shouldClearClip`) is
  unchanged.

### Data Flow

**Initial post (bank OTP arrives):**
```
RegexMessageHandler ACCEPTS OtpMessage
  → UserNotifierOtpMessageHandler.onOtpMessageReceived(message)
      → OtpNotificationBuilder.build(
            id = message.hashCode(),
            merchant = message.merchant,
            otp = message.otp.value,
            amountText = message.pay.amount.toPlainString(),
            currencyCode = message.pay.currencyCode,
            copied = false,
        ) → Notification with action "Copy <otp>"
              └─ PendingIntent extras: otp, notificationId, merchant, amountText, currencyCode
      → NotificationManagerCompat.notify(id, notification)
```

**User taps Copy:**
```
Copy action tapped
  → CopyDataReceiver.onReceive(context, intent)
      1. Read otp, notificationId, merchant, amountText, currencyCode from intent
      2. Build ClipData (sensitive flag) → clipboardManager.setPrimaryClip  [unchanged]
      3. OtpNotificationBuilder.build(..., copied = true)
           → Notification with action "Copy <otp> Copied ✓"
               └─ Same PendingIntent (FLAG_UPDATE_CURRENT keeps identity; extras already present)
         → NotificationManagerCompat.notify(notificationId, …)  [replaces cancel]
      4. Handler.postDelayed → clearPrimaryClip after 2 min  [unchanged]
```

### Testing

#### Unit tests (JVM, Robolectric) — `CopyDataReceiverTest` (modify)

- Replace the "Notification is cancelled with the provided id" assertion:
  instead of `verify { notificationManager.cancel(42) }`, assert
  `notify(42, …)` is called with a notification whose Copy action label
  contains "Copied ✓".
- Add: re-posted notification keeps the same OTP/merchant/amount in its
  content text.
- Add: missing `EXTRA_MERCHANT` / `EXTRA_AMOUNT_TEXT` / `EXTRA_CURRENCY_CODE`
  → re-post still happens with empty-string fallbacks (no crash).
- Keep the "Missing notification id skips cancel" test, re-purposed as
  "Missing notification id skips re-post".

#### Unit tests — `OtpNotificationBuilderTest` (new, Robolectric)

- `copied = false` → action label `"Copy <otp>"`.
- `copied = true` → action label `"Copy <otp> Copied ✓"`.
- PendingIntent request code == `notificationId`; data URI ==
  `veles://otp/<id>`.
- Intent extras carry otp, notificationId, merchant, amountText, currencyCode.
- Channel is created on first build; not recreated on second build
  (idempotent).

#### Unit tests — `UserNotifierOtpMessageHandlerTest` (modify)

- Existing PendingIntent-distinctness and request-code tests still pass
  (behavior unchanged, just moved into the builder).
- Add: notification content text == `"OTP: <otp>, Pay: <amount> <currency>"`;
  title == merchant.

#### Out of scope

Instrumented/UI tests — the Test screen posts on a different channel
(`VelesTestChannel`) and doesn't use `CopyDataReceiver`, so no changes needed
there.

### Edge Cases & Scope Boundaries

- **Empty/missing fields:** `OtpNotificationBuilder` treats `merchant`,
  `amountText`, `currencyCode` as plain strings — empty values render as empty
  in the notification text (no crash). The receiver reads them with `?: ""`
  fallback.
- **`Otp.id` (prefix):** Not shown in the notification today and stays out of
  scope — the Copy label uses `otp.value` only.
- **Amount formatting:** `BigDecimal.toPlainString()` (e.g. `"100"`,
  `"99.99"`) — no locale formatting, no currency symbol. Matches what the
  current handler does implicitly via string interpolation.
- **Re-copy on repeated taps:** Idempotent. Each tap re-copies, re-posts with
  "Copied ✓", and resets the 2-min clipboard-clear timer. No accumulating
  notifications.
- **Clipboard-clear timer:** Unchanged — `shouldClearClip` + 2-min
  `postDelayed` stays exactly as-is.
- **Security:** Merchant/amount/currency are not secrets (they're already
  visible in the notification body). Adding them to the PendingIntent extras
  introduces no new sensitive data exposure beyond what's on screen.
- **Out of scope:** No changes to `NotificationListener`,
  `RegexMessageHandler`, the Test screen, bank config CRUD, or
  clipboard-clear logic.

## Files

| File | Action |
|---|---|
| `app/src/main/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilder.kt` | New |
| `app/src/main/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandler.kt` | Modify |
| `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt` | Modify |
| `app/src/test/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilderTest.kt` | New |
| `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt` | Modify |
| `app/src/test/java/me/nagaev/veles/otp/handlers/UserNotifierOtpMessageHandlerTest.kt` | Modify |
