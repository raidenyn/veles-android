# Test Screen Result Enrichment — Design Spec

**Date:** 2026-07-02

## Overview

The Test screen currently shows only a single "Matched ✓" or "No match" badge after a test
notification is processed. This makes debugging handler configs harder than it needs to be:
the developer cannot see which template matched, and cannot see the exact text the
`NotificationListener` actually received — which may differ from what was typed if the system
redacts it ("Sensitive notification content hidden").

This change enriches the test result reporting so the Test screen also shows:

- The exact received notification text (as delivered to the handler chain), and
- The matched template (bank handler config) name, when the result is `ACCEPTED`.

---

## Approach

Change `MessageHandler.onMessageReceived` to return a result object (instead of a bare enum)
that carries the matched template name. Propagate the received message text, title, and source
package through `TestResult` to the Test screen, which renders them in a result card.

---

## Handler Result Object

### `otp/handlers/MessageHandler.kt`

`MessageHandlingResult` becomes a data class with an embedded `Status` enum and an optional
matched template name:

```kotlin
data class MessageHandlingResult(
    val status: Status,
    val matchedTemplateName: String?,
) {
    enum class Status { ACCEPTED, FILTERED }

    companion object {
        val ACCEPTED = MessageHandlingResult(Status.ACCEPTED, null)
        val FILTERED = MessageHandlingResult(Status.FILTERED, null)
    }
}
```

- Keeping `ACCEPTED` / `FILTERED` as companion constants means existing call sites that compare
  with `==` against `MessageHandlingResult.ACCEPTED` continue to work (data-class equality
  compares `status` + `matchedTemplateName`, both null for the constants).
- `MessageHandler.onMessageReceived` return type is unchanged in signature; it now returns the
  data class instead of the enum.

### `otp/handlers/RegexMessageHandler.kt`

- Gains a `name: String` constructor parameter, sourced from `BankHandlerConfig.name` by
  `NotificationListener.onCreate`.
- On a match: returns `MessageHandlingResult(Status.ACCEPTED, name)`.
- On no match: returns `MessageHandlingResult.FILTERED` (the constant).

### `otp/handlers/CompositeMessageHandler.kt`

- Returns the first `ACCEPTED` result it receives (carrying that handler's `matchedTemplateName`).
- If no handler accepts, returns `MessageHandlingResult.FILTERED`.
- Existing behavior (stop-on-first-accept) is preserved.

### `otp/handlers/UserNotifierOtpMessageHandler.kt`

- Unchanged.

---

## TestResult / TestResultFlow

### `common/TestResultFlow.kt`

`TestResult` is enriched to carry the full received notification payload, not just the result
status:

```kotlin
data class TestResult(
    val handlingResult: MessageHandlingResult,
    val receivedText: String,
    val receivedTitle: String,
    val sourcePackage: String,
    val timestamp: Long,
)

object TestResultFlow {
    val current: MutableStateFlow<TestResult?> = MutableStateFlow(null)
}
```

- `receivedText`: the exact `Message.text` seen by the handler chain. This is what makes
  redaction visible — when the system replaces the body with "Sensitive notification content
  hidden", that string is what flows through and what gets displayed.
- `receivedTitle` / `sourcePackage`: notification metadata not currently surfaced in the UI.
- `handlingResult`: carries both status and (when matched) the template name.

### `otp/NotificationListener.kt`

`onNotificationPosted` builds the enriched `TestResult` from the `Message` it already constructs
and the handler result:

```kotlin
val handlingResult = messageHandler.onMessageReceived(message)

val effectiveOwnPackage = ownPackageName ?: getPackageName()
val channelId = it.notification?.channelId
if (message.source == effectiveOwnPackage && channelId == TestNotificationSender.CHANNEL_ID) {
    TestResultFlow.current.value = TestResult(
        handlingResult = handlingResult,
        receivedText = message.text,
        receivedTitle = message.title,
        sourcePackage = message.source,
        timestamp = System.currentTimeMillis(),
    )
}
```

No other `NotificationListener` behavior changes.

---

## Test Screen UI

### `testing/ui/TestScreen.kt`

Replace the single-line `ResultBadge` with a result card rendered when `lastResult != null`:

| Line | Content | Tag |
|---|---|---|
| Status | "Matched ✓" (green) or "No match" (muted) | `TEST_RESULT` (existing) |
| Template | "Template: <name>" — shown only when `handlingResult.status == ACCEPTED` | `TEST_RESULT_TEMPLATE` (new) |
| Received text | "Received: <receivedText>" — always shown (including redacted text) | `TEST_RESULT_RECEIVED_TEXT` (new) |
| Title/source | "Title: <receivedTitle>" / "Source: <sourcePackage>" — shown only when non-empty | (no dedicated tags; part of the result card) |

All lines live inside a `Column` under the existing `TEST_RESULT` tag, with consistent spacing.
The green/muted color treatment for the status line matches the current badge.

### `common/ui/TestTags.kt`

Add:

```kotlin
const val TEST_RESULT_TEMPLATE = "test_result_template"
const val TEST_RESULT_RECEIVED_TEXT = "test_result_received_text"
```

### `TestScreenPreview`

Update the preview to render a matched `TestResult` (with a template name and sample text) so
the matched-with-template path is visible in the preview.

---

## Testing

### Unit tests

**`otp/handlers/RegexMessageHandlerTest.kt`** (existing — update + add):
- Existing assertions comparing against `MessageHandlingResult.ACCEPTED` / `FILTERED` continue
  to pass via the companion constants (filtered results carry null name).
- Update the `handler(...)` factory to pass a `name` argument.
- Add: `Valid OTP message processing returns matched template name` — assert
  `result.matchedTemplateName == "UOB Thailand"` (or the test's chosen name).

**`otp/handlers/CompositeMessageHandlerTest.kt`** (existing — add):
- Existing tests pass through (mocks return `ACCEPTED` / `FILTERED` constants).
- Add: `first handler matches propagates its matched template name` — first handler returns
  `MessageHandlingResult(Status.ACCEPTED, "UOB Thailand")`, assert the composite result carries
  the same name.

**`common/TestResultFlowTest.kt`** (existing — update):
- `TestResult` construction changes; update the two `TestResult(...)` calls to include the new
  fields (`receivedText`, `receivedTitle`, `sourcePackage`). Existing flow semantics unchanged.

**`testing/viewmodel/TestViewModelTest.kt`** (existing — update):
- Update `TestResult(...)` construction in the two tests that emit results to include the new
  fields. `lastResult` equality assertions continue to work.

### Instrumented tests

**`otp/NotificationListenerTest.kt`** (existing — update + add):
- Line ~157: `TestResultFlow.current.value?.result` becomes `?.handlingResult`; assert
  `handlingResult`, `receivedText`, `receivedTitle`, `sourcePackage` match the posted
  notification.
- Existing tests that return `MessageHandlingResult.ACCEPTED` / `FILTERED` from mocked handlers
  keep working via the companion constants.
- Add: `onNotificationPosted writes matched template name and received text to TestResultFlow for
  self-notifications` — mock handler returns
  `MessageHandlingResult(Status.ACCEPTED, "UOB Thailand")`, assert the full `TestResult` is
  populated (template name, received text, title, source).

**`testing/ui/TestScreenComposeTest.kt`** (new — instrumented Compose test):
Direct `setContent { TestScreen(...) }` tests with canned `TestState` / `TestResult` values,
avoiding the NavHost/permissions plumbing. Cases:

1. `matched result renders status, template name, and received text` — `TestResult` with
   `Status.ACCEPTED`, `matchedTemplateName = "UOB Thailand"`,
   `receivedText = "For purchase THB600.00..."`. Assert `TEST_RESULT` contains "Matched ✓",
   `TEST_RESULT_TEMPLATE` contains "UOB Thailand", `TEST_RESULT_RECEIVED_TEXT` contains the
   received text.
2. `filtered result renders status and received text, no template` — `TestResult` with
   `Status.FILTERED`, `matchedTemplateName = null`. Assert "No match", template node absent,
   received text shown.
3. `redacted received text is displayed verbatim` — `receivedText =
   "Sensitive notification content hidden"`. Assert `TEST_RESULT_RECEIVED_TEXT` contains that
   string, surfacing the redaction for debugging.

**`permissions/ui/VelesPermissionsAppTests.kt`** (existing — no changes required):
These tests exercise the permissions screen and do not render the Test screen, so no updates are
needed there.

---

## Files Touched

### Modified

```
otp/handlers/MessageHandler.kt           — MessageHandlingResult enum → data class + Status enum
otp/handlers/RegexMessageHandler.kt      — add name param, return name on match
otp/handlers/CompositeMessageHandler.kt  — propagate matchedTemplateName from winning handler
common/TestResultFlow.kt                 — enrich TestResult with receivedText/title/source
otp/NotificationListener.kt              — build enriched TestResult for self-notifications
testing/ui/TestScreen.kt                 — render result card with template + received text
common/ui/TestTags.kt                    — add TEST_RESULT_TEMPLATE, TEST_RESULT_RECEIVED_TEXT
```

### Tests modified

```
test/.../RegexMessageHandlerTest.kt       — update factory, add template-name assertion
test/.../CompositeMessageHandlerTest.kt   — add template-name propagation test
test/.../common/TestResultFlowTest.kt     — update TestResult construction
test/.../testing/viewmodel/TestViewModelTest.kt — update TestResult construction
androidTest/.../otp/NotificationListenerTest.kt — update assertions, add template-name test
```

### Tests added

```
androidTest/.../testing/ui/TestScreenComposeTest.kt — Compose tests for result card rendering
```

---

## Out of Scope

- No change to `UserNotifierOtpMessageHandler` or the OTP output notification.
- No change to the production OTP flow — the new fields are only consumed by the Test screen.
- No persistence of test results beyond the current session (`TestResultFlow` stays in-memory).
- No change to the NavHost routes or navigation plumbing.