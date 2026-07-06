# Feature: OTP & transaction history screen

**Type:** New feature
**Priority:** High — highest user value for lowest novelty risk
**Effort:** Medium (~2–3 days)

## Motivation

Once the original bank notification is cancelled and the Veles notification is dismissed, the OTP
and the transaction context (merchant, amount) are gone. Users who dismiss too fast, or who want
to double-check "what was that 1,200 THB charge yesterday?", have nothing. The app already
extracts structured data (`OtpMessage`: otp, amount, currency, merchant) — persisting it is cheap
and unlocks the app's second act (see spending insights, `feature-06`).

## Design

1. **Storage.** New Room entity `OtpEvent(id, bankName, otpValue, currencyCode, amount, merchant,
   receivedAt)` in the existing `BankHandlerDatabase` (version bump + migration). Written by a new
   decorator around `UserNotifierOtpMessageHandler` (or directly in it) whenever a message is
   `ACCEPTED`.
2. **Retention & privacy.** OTP values are short-lived secrets: store the OTP column only for a
   configurable window (default 24 h) and null it out with a periodic `WorkManager` job; keep the
   transaction rows (merchant/amount) long-term. Optionally an "incognito" toggle that disables
   history entirely.
3. **UI.** New route `history` in `VelesPermissionsApp`'s NavHost, entry point next to the
   existing Test / Bank Configs navigation. List grouped by day: merchant, amount + currency,
   time, bank name; recent entries (still within TTL) show a copy-OTP affordance.
4. **Empty/edge states.** Empty list explains that history fills as OTPs arrive; deletion via
   swipe or long-press with the same confirm-dialog pattern as `BankConfigsScreen`.

## Non-goals

Cloud sync, export (separate proposal), editing entries.

## Testing

- DAO tests for insert/query/prune.
- ViewModel test: events grouped by day, TTL filtering of OTP visibility.
- Compose UI test with `TestTags` additions for list items.
