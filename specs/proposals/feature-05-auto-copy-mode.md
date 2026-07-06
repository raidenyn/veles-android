# Feature: Auto-copy mode (zero-tap OTP)

**Type:** New feature
**Priority:** Medium-high
**Effort:** Small (~1 day), depends on tech-05 (clipboard hygiene)

## Motivation

The current flow still costs one interaction: pull down the shade, tap "Copy N". When the user is
mid-checkout in a browser or app, even that is friction. An opt-in **auto-copy** mode puts the
OTP on the clipboard the instant it is intercepted, so the user just long-presses → paste in the
OTP field.

## Design

1. **Setting.** Per-app toggle (stored in `NotificationStatePreferences` or, better, a new
   DataStore-backed `SettingsRepository`): "Automatically copy OTP to clipboard". Default **off**
   — silently populating the clipboard is surprising and has security implications the user
   should consciously accept (explain them in the toggle's supporting text).
2. **Behaviour.** In `UserNotifierOtpMessageHandler`, when the mode is on, invoke the same copy
   path `CopyDataReceiver` uses (extract it into a shared `OtpClipboard` helper) before posting
   the notification. The notification still appears (with the Copy action as fallback) but its
   text gains a "· copied" suffix so the user knows the clipboard is ready.
3. **Security guardrails.** Requires `tech-05` first: sensitive-clip flag (suppresses the
   Android 13+ clipboard preview toast content) and auto-clear TTL are mandatory for auto-copy,
   not optional. Note: background clipboard *writes* are allowed for us because the copy happens
   in our own running process in direct response to the notification.
4. **Scope option (phase 2).** Per-bank override in `BankHandlerConfig` (`autoCopy: Boolean?`)
   so users can enable it only for low-risk banks.

## Testing

- Unit test: with mode on, clipboard helper invoked and notification text carries the suffix;
  with mode off, behaviour identical to today.
- Manual: checkout flow in a browser; verify paste offers the OTP with the redacted preview.
