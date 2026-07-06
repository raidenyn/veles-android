# Tech: Clipboard hygiene for copied OTPs

**Type:** Technical improvement (security)
**Priority:** Medium-high
**Effort:** Small (~half a day)

## Problem

`CopyDataReceiver` puts the OTP on the clipboard as ordinary plain text:

- Any app the user switches to can read it (subject to Android 10+ focus restrictions, but the
  next focused app — e.g. the browser page asking for the OTP — legitimately can, and so can a
  malicious keyboard/IME at any time).
- The OTP is shown in the Android 13+ clipboard preview overlay and stored in clipboard-history
  keyboards (Gboard clipboard, Samsung clipboard) indefinitely.
- It stays on the clipboard forever; OTPs are typically valid for 1–5 minutes but the clip
  survives far longer.

## Proposal

1. **Mark the clip sensitive** so the system suppresses the content preview and history:
   ```kotlin
   val clip = ClipData.newPlainText("OTP", otp).apply {
       description.extras = PersistableBundle().apply {
           putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
       }
   }
   ```
   (minSdk is 33, so `EXTRA_IS_SENSITIVE` is available unconditionally.)
2. **Auto-clear.** After a configurable TTL (default 2 minutes), clear the clipboard if it still
   holds our clip: schedule via `WorkManager` (survives process death) or a simple
   `Handler.postDelayed` in an already-running component; call `clipboardManager.clearPrimaryClip()`
   only when `primaryClip`'s label is `"OTP"` and the text matches, to avoid nuking user data.
3. **Auto-dismiss the Veles notification** once Copy is tapped (`NotificationManagerCompat.cancel`
   with the notification id passed alongside `EXTRA_COPY_TEXT`), so consumed OTPs don't linger in
   the shade either.

## Testing

- Unit test `CopyDataReceiver` with a mocked `ClipboardManager`: assert `EXTRA_IS_SENSITIVE` set,
  assert cancel called with the right id.
- Manual: on Android 13+, copy an OTP and confirm the preview shows the redacted "content copied"
  UI instead of the code.
