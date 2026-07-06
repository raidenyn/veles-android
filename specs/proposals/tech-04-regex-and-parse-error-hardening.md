# Tech: Harden handler chain against bad regexes and parse failures

**Type:** Technical improvement (robustness)
**Priority:** High — user-supplied configs can crash the listener
**Effort:** Medium (~1 day)

## Problem

Bank configs are now user-editable (CRUD UI, #2), but the pipeline trusts them completely:

1. **Regex compilation.** `RegexMessageHandler`'s constructor does `Regex(otpRegex)` etc. A
   syntactically invalid pattern throws `PatternSyntaxException` inside
   `NotificationListener.onCreate`, crashing the listener process. Once crashed, *no* OTPs are
   intercepted until the service restarts — a single bad row bricks the whole feature.
2. **Group-count assumptions.** The code indexes `groupValues[1]`/`groupValues[2]` blindly. A
   valid regex with fewer capture groups than expected returns `""` for missing groups (or the
   whole pipeline behaves nonsensically) rather than failing clearly.
3. **BigDecimal parsing.** `BigDecimal(it.groupValues[2])` throws `NumberFormatException` if the
   money regex matches but group 2 isn't a clean number (e.g. `1,234.00` with a thousands
   separator). This exception propagates out of `onNotificationPosted` — again crashing the
   listener process on an *incoming notification*, potentially repeatedly.

## Proposal

1. **Validate at write time.** In `BankConfigEditViewModel.save`, compile all three regexes and
   check `otpRegex`/`moneyRegex` contain ≥ 2 capture groups and `merchantRegex` ≥ 1
   (`Pattern.compile(p).matcher("").groupCount()`). Surface field-level errors in the edit screen
   instead of persisting broken configs.
2. **Fail safe at read time.** Make handler construction total: a factory
   (`RegexMessageHandler.fromConfig(config): RegexMessageHandler?`) returns `null` on compile
   failure and the bad config is skipped (and logged non-sensitively). One broken row must never
   take down the chain.
3. **Catch per-message errors.** Wrap the body of `RegexMessageHandler.onMessageReceived` (or the
   dispatch loop in `CompositeMessageHandler`) in a `runCatching`; treat any throwable as
   `FILTERED` so a weird notification can't kill the service. Normalize the amount string
   (strip `,`) before `BigDecimal`.

## Testing

- Unit tests: invalid pattern → handler skipped, chain still dispatches to remaining handlers;
  money group `"1,234.56"` → parsed or filtered, never thrown; regex with 1 group where 2 are
  expected → `FILTERED`.
- Edit-screen ViewModel test: saving an invalid regex sets a validation error and does not call
  the repository.
