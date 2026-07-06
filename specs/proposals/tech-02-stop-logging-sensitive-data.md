# Tech: Stop logging OTPs and notification content

**Type:** Technical improvement (security/privacy)
**Priority:** High — OTPs are secrets
**Effort:** Small (~2 hours)

## Problem

Sensitive data is written to logcat in production builds:

- `NotificationListener.onNotificationPosted` logs the full title and text of **every**
  notification on the device (`Log.d("NotificationListener", "Title: $title, Text: $text, ...")`).
- `CopyDataReceiver.onReceive` logs the copied OTP verbatim (`Log.d("CopyDataReceiver", "Copied '$it'")`).

Logcat is readable by ADB, bug reports, and (pre-redaction) other debugging tooling. For an app
whose entire premise is handling bank OTPs, leaking them (plus the content of every other app's
notifications) into the system log is the single biggest privacy gap.

## Proposal

1. Introduce a tiny logging wrapper (e.g. `VelesLog`) that:
   - no-ops (or logs only non-sensitive metadata) when `!BuildConfig.DEBUG`;
   - even in debug, redacts message bodies by default (`text.take(0)` / hash / length only),
     behind an explicit opt-in flag for local development.
2. Replace all direct `Log.d` calls in `NotificationListener`, `CopyDataReceiver`, and handlers.
3. Add a detekt rule or a forbidden-API lint check (`ForbiddenMethodCall` on `android.util.Log`)
   so raw logging can't creep back in.

## Testing

- Unit test that `VelesLog` produces no output when debug flag is false (inject a fake sink).
- Grep-level CI guard: detekt `ForbiddenMethodCall` fails the build on direct `android.util.Log`
  usage outside `VelesLog`.
