# Clipboard hygiene for copied OTPs

**Issue:** [raidenyn/veles-android#14](https://github.com/raidenyn/veles-android/issues/14)
**Type:** Technical improvement (security)
**Priority:** Medium-high
**Effort:** Small (~half a day)

## Problem

`CopyDataReceiver` puts the OTP on the clipboard as ordinary plain text (`app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt:28`):

- Any app the user switches to can read it (Android 10+ focus restrictions limit this somewhat, but the next focused app — e.g. the browser page asking for the OTP — legitimately can, and so can a malicious keyboard/IME at any time).
- The clip is shown in the Android 13+ clipboard preview overlay and stored in clipboard-history keyboards (Gboard, Samsung clipboard) indefinitely.
- It stays on the clipboard forever; OTPs are typically valid for 1–5 minutes but the clip survives far longer.
- The Veles notification itself stays in the shade after the OTP has been copied and used, so a consumed OTP lingers visibly.

## Goal

After a Copy tap: the clip is marked sensitive (no system preview/history), it self-clears from the clipboard after 2 minutes unless overwritten sooner, and the originating notification is dismissed.

## Non-goals

- User-configurable TTL — the issue's "configurable" means "easy to change in code," not a settings UI. There is no settings screen in the app today (only `permissions/` and `testing/`); adding one is out of scope. TTL is a hardcoded constant.
- `WorkManager` / `AlarmManager` — the app has no `WorkManager` dependency today. `NotificationListenerService` is bound by the system while notification access is granted, keeping the process alive for the life of a 2-minute delay in the overwhelming majority of cases. Adding a new scheduling dependency for a best-effort hygiene measure is more than this task needs. If the process is killed before the delay fires, the clip is simply never cleared — acceptable, since it's a defense-in-depth improvement over the current "never clears" baseline, not a guarantee.
- Any change to `RegexMessageHandler`, bank config, or the notification content/text itself.

## Architecture

```
Copy notification action tapped
  → CopyDataReceiver.onReceive(context, intent)
      → build sensitive ClipData ("OTP" label, EXTRA_IS_SENSITIVE = true)
      → clipboardManager.setPrimaryClip(clip)
      → NotificationManagerCompat.from(context).cancel(notificationId)   // dismiss the shade entry
      → Handler(Looper.getMainLooper()).postDelayed(CLEAR_DELAY_MILLIS) {
            if (shouldClearClip(clipboardManager.primaryClip, "OTP", otp)) {
                clipboardManager.clearPrimaryClip()
            }
        }
```

`shouldClearClip` is a small pure function so the "did the clipboard change under us" check is unit-testable without touching `Handler`/`Looper`.

## Components

### `CopyDataReceiver` (modify — `otp/CopyDataReceiver.kt`)

```kotlin
class CopyDataReceiver(
    private val loggerOverride: VelesLog? = null,
) : BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
        const val EXTRA_NOTIFICATION_ID = "NotificationId"
        private const val CLIP_LABEL = "OTP"
        private const val CLEAR_DELAY_MILLIS = 2 * 60 * 1000L

        internal fun shouldClearClip(clip: ClipData?, expectedText: String): Boolean {
            if (clip == null || clip.itemCount == 0) return false
            if (clip.description.label != CLIP_LABEL) return false
            return clip.getItemAt(0).text?.toString() == expectedText
        }
    }

    override fun onReceive(context: Context?, intent: Intent?) {
        if (context == null) return
        val logger = loggerOverride ?: resolveLogger(context)
        logger.d("CopyDataReceiver", "Context $context")

        val otp = intent?.getStringExtra(EXTRA_COPY_TEXT) ?: return
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1)

        val clipboardManager = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return

        val clip = ClipData.newPlainText(CLIP_LABEL, otp).apply {
            description.extras = PersistableBundle().apply {
                putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
            }
        }
        clipboardManager.setPrimaryClip(clip)
        logger.dCopiedOtp(otp)

        if (notificationId != -1) {
            NotificationManagerCompat.from(context).cancel(notificationId)
        }

        Handler(Looper.getMainLooper()).postDelayed({
            if (shouldClearClip(clipboardManager.primaryClip, otp)) {
                clipboardManager.clearPrimaryClip()
            }
        }, CLEAR_DELAY_MILLIS)
    }

    private fun resolveLogger(context: Context): VelesLog = EntryPointAccessors.fromApplication(
        context.applicationContext,
        NotificationListenerEntryPoint::class.java,
    ).velesLog()
}
```

Behavior changes from today:
- `EXTRA_NOTIFICATION_ID` is a new intent extra; if absent (defensive default `-1`, e.g. a stale `PendingIntent` from before this change), the cancel step is skipped rather than crashing.
- Empty-string OTP: the existing test `Empty EXTRA COPY TEXT` expects `setPrimaryClip` still called once — preserved, since `getStringExtra` returns `""` (non-null), not null.
- Missing `EXTRA_COPY_TEXT` (null): existing early-return behavior preserved (`Missing EXTRA COPY TEXT` test).

### `UserNotifierOtpMessageHandler` (modify — `otp/handlers/UserNotifierOtpMessageHandler.kt`)

The notification id is already computed as `message.hashCode()` for `notify(...)`. Hoist it to a local val and pass it into the copy `Intent`:

```kotlin
val notificationId = message.hashCode()
val copyIntent =
    Intent(context, CopyDataReceiver::class.java).apply {
        action = "Copy"
        data = Uri.parse("veles://otp/${message.id}")
        putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, message.otp.value)
        putExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, notificationId)
    }
// ...
notify(notificationId, builder.build())
```

No other changes to this file.

## Data flow

### Copy tapped, nothing else happens for 2 minutes
```
onReceive → sensitive ClipData set → notification cancelled → delayed check fires
  → primaryClip label == "OTP" && text == otp → clearPrimaryClip()
```

### Copy tapped, then a second OTP arrives and is copied within 2 minutes
```
First onReceive schedules a delayed check for OTP-A.
Second onReceive (new instance) sets clip to OTP-B, cancels its own notification, schedules its own delayed check.
First delayed check fires → primaryClip is now OTP-B → shouldClearClip returns false → clipboard untouched.
Second delayed check fires later → clears OTP-B (if still unchanged).
```

### Copy tapped, user copies unrelated text (e.g. from another app) before TTL
```
Delayed check fires → primaryClip label != "OTP" (or text differs) → shouldClearClip returns false → clipboard untouched.
```

## Error handling

- `context == null` → return, same as today.
- `EXTRA_COPY_TEXT` missing → return before touching the clipboard, same as today.
- Clipboard service unavailable (`as? ClipboardManager` is null) → return, same as today (preserves the `Clipboard Service unavailable` test).
- `EXTRA_NOTIFICATION_ID` missing/`-1` → skip `cancel`, no crash.
- The scheduled `Handler` callback captures `clipboardManager` and `otp` by closure; no null-safety concerns since both are already validated non-null before scheduling.
- No new logging of raw OTP content beyond the existing `logger.dCopiedOtp(otp)` call, which already respects `LogConfig.rawContentEnabled` (length-only by default, per the prior "stop logging OTPs" fix). The delayed clear path logs nothing new.

## Testing

### `CopyDataReceiverTest` (modify — `src/test/`, MockK, JVM, no Robolectric)

New/changed cases:
1. `ClipData.newPlainText("OTP", testText)` still asserted (mocked to return `clipData`, per existing setup). Capture the sensitive-flag write with a `slot<PersistableBundle>()`: `every { clipData.description.extras = capture(extrasSlot) } just Runs`, then assert `extrasSlot.captured.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE)` is `true` after `onReceive`.
2. Valid `EXTRA_NOTIFICATION_ID` → `mockkStatic(NotificationManagerCompat::class)`, `every { NotificationManagerCompat.from(context) } returns mockk<NotificationManagerCompat>(relaxed = true)` (mockable via the existing `mockk-agent` dependency, which already supports final classes), then `verify { notificationManagerCompat.cancel(notificationId) }`.
3. Missing `EXTRA_NOTIFICATION_ID` (defaults to `-1`) → assert `cancel` is never called.
4. Existing tests (`Null Context`, `Null Intent`, `Missing EXTRA COPY TEXT`, `Empty EXTRA COPY TEXT`, `Clipboard Service unavailable`) continue to pass unchanged in behavior.

### New pure-function test: `shouldClearClip`

Direct JVM test, no mocking of `Handler`/`Looper` needed:
1. Matching label (`"OTP"`) and matching text → `true`.
2. Matching label, different text (user/another OTP overwrote it) → `false`.
3. Different label → `false`.
4. `null` clip → `false`.
5. Clip with zero items → `false`.

### Not unit tested (manual, per issue's own testing scope)

- On an Android 13+ device/emulator: copy an OTP, confirm the system clipboard preview shows the redacted "content copied" UI instead of the code.
- Confirm the clip is gone from the clipboard ~2 minutes after copy (e.g. paste into another app before/after the delay).
- Confirm the Veles notification disappears from the shade immediately after tapping Copy.

## Out of scope

- `WorkManager`/`AlarmManager`-based scheduling (see Non-goals).
- User-configurable TTL / settings screen (see Non-goals).
- Redacting the notification's own visible text (`OTP: ..., Pay: ...`) — already covered by the existing sensitive-notification-redaction work; unrelated to the clipboard.

## Risks

- **Process death before the 2-minute delay fires:** the clip is never auto-cleared in that case. Accepted per Non-goals — this is a best-effort improvement over the current "never clears" behavior, not a hard guarantee, and adding `WorkManager` solely to close this gap is disproportionate to the issue's scope.
- **Two Copy taps close together (< 2 minutes apart):** handled correctly by the label+text match check — the earlier scheduled clear becomes a no-op once the clip has changed, per the Data Flow section above. No explicit cancellation of the earlier `Handler` callback is needed.
- **`PersistableBundle` availability:** minSdk is 33, so `ClipDescription.EXTRA_IS_SENSITIVE` and `PersistableBundle` are available unconditionally — no version gating required.
