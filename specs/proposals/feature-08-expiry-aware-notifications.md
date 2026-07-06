# Feature: Expiry-aware OTP notifications (countdown + auto-dismiss)

**Type:** New feature
**Priority:** Medium-high
**Effort:** Small-medium (~1–2 days)

## Motivation

Bank OTPs expire quickly (the seeded UOB format even says so: "… at MERCHANT **expiring** …").
Today the Veles notification lives until manually dismissed, so the shade accumulates dead codes
that no longer work — worse than useless, since a user can confidently paste an expired OTP and
get a confusing decline. The message usually *tells us* the validity window; we just throw that
information away.

## Design

1. **Config.** Add two optional columns to `BankHandlerConfig`:
   - `expiryRegex: String?` — group 1 captures a duration or timestamp fragment from the message
     (e.g. `expiring (\d{1,2}) min` or `within (\d+) seconds`);
   - `defaultTtlSeconds: Int?` — fallback when the message doesn't state one (many banks use a
     fixed 3 or 5 minutes). Both surfaced in `BankConfigEditScreen` and covered by the live
     preview from `feature-02`.
2. **Extraction.** `RegexMessageHandler` computes `expiresAt: Long?` (message-parsed value wins
   over the default TTL) and adds it to `OtpMessage`. Absent both, behaviour is unchanged.
3. **Notification.** When `expiresAt` is known, `UserNotifierOtpMessageHandler`:
   - shows a live countdown using `setUsesChronometer(true)` + `setChronometerCountDown(true)` +
     `setWhen(expiresAt)` — no polling, the system renders the ticking timer;
   - sets `setTimeoutAfter(expiresAt - now)` so the notification auto-cancels at expiry — this is
     a plain notification API, no alarms/WorkManager needed.
4. **Downstream consumers.** `feature-01` (history) stores `expiresAt` to grey out expired codes;
   `feature-07` (widget) uses it instead of its own fixed TTL.

## Edge cases

Clock changes (chronometer uses elapsed-realtime-anchored `when`; acceptable drift), messages
stating an absolute time ("valid until 14:05") — out of scope for v1, `expiryRegex` targets
durations only.

## Testing

- Unit tests: duration parsing (minutes/seconds variants), fallback TTL, neither present.
- `UserNotifierOtpMessageHandlerTest`: builder receives chronometer + timeout values when
  `expiresAt` set, and none otherwise.
