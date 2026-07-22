# Harden regex handler capture and amount parsing

**Issue:** [raidenyn/veles-android#13](https://github.com/raidenyn/veles-android/issues/13)
**Type:** Bug fix and robustness improvement
**Priority:** High - user-defined patterns can crash the notification listener
**Effort:** Small

## Problem

`RegexMessageHandler` assumes every matching pattern has the required capture groups and that the
money pattern's second group is directly accepted by `BigDecimal`. The user pattern
`purchase ([A-Z]{3})([\d,]{1,15}\.\d{1,4})` is valid and matches a value such as `1,234.56`, but
`BigDecimal("1,234.56")` throws `NumberFormatException`. That exception escapes
`onMessageReceived` and `NotificationListener.onNotificationPosted`, crashing the listener when a
test or real notification matches.

Valid regexes with too few capture groups have the same failure mode through unchecked
`groupValues[1]` and `groupValues[2]` access. The config editor currently checks only that a regex
compiles, so it permits these patterns.

Issue #13 already tracks both match-time failure modes. Invalid regex construction is now partly
handled by `HandlerChainReloader`; this change addresses the issue's remaining editor and
match-time hardening without broadening into import or chain-reload changes tracked by #58.

## Goals

- Parse comma-grouped amounts such as `1,234.56` by stripping commas before `BigDecimal` parsing.
- Never throw because a matched amount is not a valid decimal.
- Never throw because a user pattern has too few capture groups.
- Reject insufficient capture groups in the bank-config editor before saving.
- Keep malformed or legacy/imported configs from crashing notification handling.

## Non-goals

- Changing the regex configuration schema or Room database.
- Adding locale-dependent currency parsing or enforcing grouping separator rules.
- Redesigning import validation or handler-chain construction; those concerns remain in #58.
- Catching every exception from every message handler. Unrelated programming and notifier failures
  should remain visible rather than being silently converted to a non-match.

## Design

### Save-time validation

`BankConfigEditViewModel` will validate both compilation and capture-group count. OTP and money
patterns require at least two groups; merchant patterns require at least one. A pattern that does
not meet its field's contract receives a field-level validation error and is not persisted.

The validation helper will accept the required group count, compile the pattern, inspect its group
count, and return the existing invalid-pattern field error when either requirement fails. This is
the smallest UI change and avoids introducing a new state or screen behavior.

### Match-time extraction

`RegexMessageHandler.onMessageReceived` will use safe capture-group access. If any required group
is absent, the corresponding extracted value is `null`, and the handler returns `FILTERED` without
notifying the user.

The money capture will be parsed as follows:

1. Strip commas from the captured string.
2. Parse the result with Kotlin's nullable `toBigDecimalOrNull()` extension.
3. If parsing fails, treat the money extraction as absent and return `FILTERED`.

This accepts `1,234.56` (normalized to `1234.56`) and any comma-free decimal. Non-numeric
captures such as `not.a.number` fail parsing and return `FILTERED`. The user's regex decides
which notification text is eligible; the handler only ensures the captured value can safely
become `Money.amount`. No locale-specific grouping validation is performed — the handler is
locale-independent and does not impose grouping rules on the user's capture.

### Error handling

Expected configuration/data errors are handled at their source through safe group access and
nullable numeric parsing. There will be no broad `runCatching` around the handler or composite
chain, because that would also hide failures from the notifier and unrelated defects.

Existing invalid persisted/imported patterns remain protected during chain construction by
`HandlerChainReloader`. Existing persisted patterns with inadequate groups, and patterns whose
captures are malformed for a particular message, become safe non-matches.

## Data Flow

```text
Editor save
  -> compile each pattern
  -> verify required capture-group count
  -> reject invalid field or persist config

Notification/test message
  -> regex match
  -> safely read required groups
  -> validate and normalize captured amount
  -> nullable BigDecimal parse
  -> all values present: notify and ACCEPTED
  -> any value absent/invalid: FILTERED
```

## Testing

`RegexMessageHandlerTest` will add regression coverage for:

- The reported pattern matching `THB1,234.56`, producing `BigDecimal("1234.56")` and `ACCEPTED`.
- A non-numeric money capture returning `FILTERED` without throwing.
- OTP, money, and merchant patterns with insufficient capture groups returning `FILTERED`.

`BankConfigEditViewModelTest` will verify that:

- OTP and money patterns with fewer than two groups are rejected and not persisted.
- A merchant pattern with no capture group is rejected and not persisted.
- Patterns meeting the group-count contract still save normally.

Verification will run the focused handler and editor ViewModel tests, followed by the full debug
unit-test suite.

## Risks

- Strict comma grouping is not enforced; unusual formats such as `12,34,567.89` are stripped to
  `1234567.89` and parsed. The user's regex is the authority on what constitutes a valid capture.
- Reusing the existing invalid-pattern message does not distinguish syntax errors from missing
  groups, but it preserves the current UI and keeps this crash fix narrowly scoped.
