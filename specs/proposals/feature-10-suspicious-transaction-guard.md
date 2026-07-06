# Feature: Suspicious transaction guard

**Type:** New feature (security)
**Priority:** Medium
**Effort:** Medium (~2–3 days); benefits from feature-01 (history) for merchant memory

## Motivation

OTP phishing works by rushing the victim: the attacker triggers a real transaction and social-
engineers the code out of them before they read the SMS. Veles sits in the perfect position to
slow that down — it already parses **merchant and amount at the moment of authorization**, which
is exactly the context a victim under pressure skips. Instead of making the OTP *easier* to use
in all cases, add friction only when something looks off.

## Design

1. **Rules (all local, all transparent to the user).** A transaction is flagged when any of:
   - amount ≥ user-configured threshold for that currency (e.g. "warn above THB 5,000");
   - first-ever merchant (no prior `OtpEvent` row with this normalized merchant — needs
     `feature-01`; rule disabled until history exists);
   - burst: ≥ N OTPs within M minutes (defaults 3 in 10), a common cardable-site pattern.
2. **Flagged behaviour.** Instead of the normal notification with the one-tap Copy action:
   - post a **warning-styled** notification: "⚠ THB 18,500 at NEWMERCHANT — first time seeing
     this merchant", `BigTextStyle` with the full parsed details, **no Copy action**;
   - the tap action opens a full-screen in-app confirmation showing merchant, amount, bank, and
     a deliberate "I initiated this — copy OTP" button (plus "This isn't me" which just dismisses
     and links to advice text). The OTP is never rendered until confirmation.
3. **Settings.** A "Transaction guard" section: master toggle (default **on** with only the
   burst rule; threshold and new-merchant rules opt-in since they need tuning), per-currency
   thresholds, rule toggles. Stored in the DataStore-backed settings from `feature-05`/`tech-05`
   groundwork.
4. **Explicit non-goal.** No cloud reputation lookups, no ML — deterministic local rules the
   user can read and understand. Veles must never feel like it's second-guessing silently.

## Testing

- Pure rule-engine unit tests: threshold boundaries per currency, first-merchant with/without
  history, burst window sliding.
- Handler test: flagged message → warning notification without copy action; unflagged →
  existing behaviour byte-for-byte.
- Compose test for the confirmation screen (OTP hidden until confirm tapped).
