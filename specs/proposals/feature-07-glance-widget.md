# Feature: Home-screen widget for the latest OTP (Glance)

**Type:** New feature
**Priority:** Low-medium
**Effort:** Medium (~2 days)

## Motivation

The Glance dependencies (`androidx.glance`, `glance-appwidget`, `glance-material3`) are already
declared in `app/build.gradle.kts` but unused — the intent for a widget clearly existed. A
home-screen widget showing the most recent OTP (with merchant + amount for context and a tap-to-
copy action) is a natural fit: during a checkout the user flips to the launcher and the code is
already visible, no shade interaction needed.

## Design

1. **Widget.** `GlanceAppWidget` + `GlanceAppWidgetReceiver` showing:
   - active state: merchant, amount, large OTP, relative time ("42 s ago"), tap → the existing
     `CopyDataReceiver` via `actionSendBroadcast`;
   - idle state: "No recent OTP" with tap opening the app.
2. **Data flow.** On `ACCEPTED`, alongside posting the notification, write the latest
   `OtpMessage` to a small DataStore (`latest_otp`) and call `MyWidget.updateAll(context)`.
   If `feature-01` (history) lands first, read the newest row from Room instead.
3. **Expiry.** OTPs go stale fast: the widget auto-reverts to idle after a TTL (default 5 min)
   via `WorkManager` one-shot, and immediately when the OTP is copied. Never show codes older
   than the TTL after reboot (persist the timestamp, compare on render).
4. **Lock-screen caution.** Widgets can be visible on some launchers/lock screens; document that
   the widget shows OTPs in plain sight and default the TTL short. Optional setting: "show only
   merchant until tapped".

## Testing

- Unit tests for the TTL/state reduction logic (pure function: `(latestOtp, now) → WidgetState`).
- Manual matrix: widget added/removed, reboot with fresh vs stale OTP, copy-then-look.
