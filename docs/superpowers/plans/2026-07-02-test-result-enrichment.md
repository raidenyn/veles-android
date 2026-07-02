# Test Screen Result Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the Test screen result so it shows the matched template name (when matched) and the exact received notification text (including redacted text), not just "Matched"/"No match".

**Architecture:** Convert `MessageHandlingResult` from an enum to a data class carrying `status` + `matchedTemplateName`. Thread it through the handler chain and `NotificationListener` into an enriched `TestResult`, then render the new fields in `TestScreen`.

**Tech Stack:** Kotlin, Jetpack Compose, Room, MockK, JUnit4, Compose UI testing.

## Global Constraints

- `MessageHandlingResult.ACCEPTED` / `MessageHandlingResult.FILTERED` companion constants MUST remain so existing `==` comparisons keep working (data-class equality compares status + name).
- Keep `UserNotifierOtpMessageHandler` and the OTP output notification unchanged — the new fields are only consumed by the Test screen.
- Test screen uses existing `TestTags.TEST_RESULT` tag for the status line; new fields get new tags.
- Unit tests run via `./gradlew testDebugUnitTest`; instrumented tests via `./gradlew connectedDebugAndroidTest` (require device/emulator).
- Follow existing patterns: MockK for mocks, no Android runtime in unit tests, `@Suppress("MaxLineLength")` on long-string test files.

**Reference spec:** `docs/superpowers/specs/2026-07-02-test-result-enrichment-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `app/src/main/java/me/nagaev/veles/otp/handlers/MessageHandler.kt` | Define `MessageHandlingResult` (status + name) and `Message` | Modify |
| `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt` | Regex match + return name on accept | Modify |
| `app/src/main/java/me/nagaev/veles/otp/handlers/CompositeMessageHandler.kt` | Propagate winning handler's name | Modify |
| `app/src/main/java/me/nagaev/veles/common/TestResultFlow.kt` | Enrich `TestResult` with received text/title/source | Modify |
| `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt` | Build enriched `TestResult` | Modify |
| `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt` | Add 2 new test tags | Modify |
| `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt` | Render result card | Modify |
| `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt` | Add name param + template-name assertion | Modify |
| `app/src/test/java/me/nagaev/veles/otp/handlers/UobThaiRegexMessageHandlerTest.kt` | Add name param + template-name assertion | Modify |
| `app/src/test/java/me/nagaev/veles/otp/handlers/CompositeMessageHandlerTest.kt` | Add template-name propagation test | Modify |
| `app/src/test/java/me/nagaev/veles/common/TestResultFlowTest.kt` | Update `TestResult` construction | Modify |
| `app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt` | Update `TestResult` construction | Modify |
| `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt` | Update assertions + add template-name test | Modify |
| `app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt` | New Compose tests for result card | Create |

---

## Task 1: Convert MessageHandlingResult to a data class

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/MessageHandler.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/CompositeMessageHandlerTest.kt` (verify existing tests still compile/pass)

**Interfaces:**
- Produces: `MessageHandlingResult(status: MessageHandlingResult.Status, matchedTemplateName: String?)` with companion constants `ACCEPTED` and `FILTERED`. `Status` is an enum with `ACCEPTED`, `FILTERED`.

- [ ] **Step 1: Convert the enum to a data class**

Replace the entire `MessageHandlingResult` enum definition in `MessageHandler.kt` with:

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

Keep `MessageHandler` interface and `Message` data class exactly as they are.

- [ ] **Step 2: Verify existing handler tests still pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.*"`
Expected: PASS — existing `result == MessageHandlingResult.ACCEPTED` comparisons still work because data-class equality compares status + name (both null for the constants).

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/MessageHandler.kt
git commit -m "refactor: convert MessageHandlingResult enum to data class with matchedTemplateName"
```

---

## Task 2: RegexMessageHandler carries the template name

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/UobThaiRegexMessageHandlerTest.kt`

**Interfaces:**
- Consumes: `MessageHandlingResult` data class from Task 1
- Produces: `RegexMessageHandler(name: String, otpRegex: String, moneyRegex: String, merchantRegex: String, notifier: OtpMessageHandler)` — `name` is the first constructor param

- [ ] **Step 1: Write the failing tests**

In `RegexMessageHandlerTest.kt`, add `name` to the companion object and update the `handler` factory, then add a new test. First, update the companion object:

```kotlin
    private companion object {
        const val HANDLER_NAME = "UOB Thailand"
        const val OTP_REGEX = """ (\w{4})-(\d{6}) """
        const val MONEY_REGEX = """of ([A-Z]{3})(\d{1,15}\.\d{1,4}) at"""
        const val MERCHANT_REGEX = """at (.{1,64}) expiring"""
    }
```

Update the factory to pass the name as the first arg:

```kotlin
    private fun handler(notifier: OtpMessageHandler) =
        RegexMessageHandler(HANDLER_NAME, OTP_REGEX, MONEY_REGEX, MERCHANT_REGEX, notifier)
```

Add a new test at the end of the class:

```kotlin
    @Test
    fun `matched result carries the handler name`() {
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(defaultMessage)

        assert(result.status == MessageHandlingResult.Status.ACCEPTED)
        assert(result.matchedTemplateName == HANDLER_NAME)
    }
```

In `UobThaiRegexMessageHandlerTest.kt`, add `name` to the companion and update the factory the same way:

```kotlin
    private companion object {
        const val HANDLER_NAME = "UOB Thai"
        const val OTP_REGEX = """\((OTP=)(\d{6})\)"""
        const val MONEY_REGEX = """purchase ([A-Z]{3})(\d{1,15}\.\d{1,4})"""
        const val MERCHANT_REGEX = """ at (.+?):"""
    }
```

```kotlin
    private fun handler(notifier: OtpMessageHandler) =
        RegexMessageHandler(HANDLER_NAME, OTP_REGEX, MONEY_REGEX, MERCHANT_REGEX, notifier)
```

Add the same `matched result carries the handler name` test (using `HANDLER_NAME = "UOB Thai"`):

```kotlin
    @Test
    fun `matched result carries the handler name`() {
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(defaultMessage)

        assert(result.status == MessageHandlingResult.Status.ACCEPTED)
        assert(result.matchedTemplateName == HANDLER_NAME)
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest.matched*"`
Expected: FAIL — `RegexMessageHandler` constructor has no `name` param; won't compile.

- [ ] **Step 3: Add the name parameter and return it on match**

In `RegexMessageHandler.kt`, add `name` as the first constructor param and return it on match. Replace the class body:

```kotlin
class RegexMessageHandler(
    private val name: String,
    otpRegex: String,
    moneyRegex: String,
    merchantRegex: String,
    private val notifier: OtpMessageHandler,
) : MessageHandler {
    private val otpPattern = Regex(otpRegex)
    private val moneyPattern = Regex(moneyRegex)
    private val merchantPattern = Regex(merchantRegex)

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        val otp =
            otpPattern.find(message.text)?.let {
                Otp(value = it.groupValues[2], id = it.groupValues[1])
            }
        val money =
            moneyPattern.find(message.text)?.let {
                Money(amount = BigDecimal(it.groupValues[2]), currencyCode = it.groupValues[1])
            }
        val merchant =
            merchantPattern.find(message.text)?.let {
                it.groupValues[1]
            }

        return if (otp != null && money != null && merchant != null) {
            notifier.onOtpMessageReceived(
                OtpMessage(
                    id = message.key.hashCode(),
                    otp = otp,
                    pay = money,
                    merchant = merchant,
                ),
            )
            MessageHandlingResult(MessageHandlingResult.Status.ACCEPTED, name)
        } else {
            MessageHandlingResult.FILTERED
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.*"`
Expected: PASS — all existing tests plus the two new `matched result carries the handler name` tests.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt \
  app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt \
  app/src/test/java/me/nagaev/veles/otp/handlers/UobThaiRegexMessageHandlerTest.kt
git commit -m "feat: RegexMessageHandler carries matched template name"
```

---

## Task 3: CompositeMessageHandler propagates the winning name

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/CompositeMessageHandler.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/handlers/CompositeMessageHandlerTest.kt`

**Interfaces:**
- Consumes: `MessageHandlingResult` data class from Task 1
- Produces: `CompositeMessageHandler.onMessageReceived` returns the winning handler's `MessageHandlingResult` (including its `matchedTemplateName`), or `FILTERED` constant.

- [ ] **Step 1: Write the failing test**

Add to `CompositeMessageHandlerTest.kt`:

```kotlin
    @Test
    fun `first handler matches propagates its matched template name`() {
        val matchedResult = MessageHandlingResult(
            MessageHandlingResult.Status.ACCEPTED,
            "UOB Thailand",
        )
        val first =
            mockk<MessageHandler> {
                every { onMessageReceived(message) } returns matchedResult
            }
        val second = mockk<MessageHandler>()

        val result = CompositeMessageHandler(listOf(first, second)).onMessageReceived(message)

        assert(result == matchedResult)
        assert(result.matchedTemplateName == "UOB Thailand")
        verify(exactly = 0) { second.onMessageReceived(any()) }
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.CompositeMessageHandlerTest.first*"`
Expected: FAIL — current implementation returns the `ACCEPTED` constant (null name), so `result.matchedTemplateName == "UOB Thailand"` fails.

- [ ] **Step 3: Propagate the winning handler's result**

In `CompositeMessageHandler.kt`, return the handler's result directly instead of the constant:

```kotlin
class CompositeMessageHandler(
    private val handlers: List<MessageHandler>,
) : MessageHandler {
    override fun onMessageReceived(message: Message): MessageHandlingResult {
        for (handler in handlers) {
            val result = handler.onMessageReceived(message)
            if (result.status == MessageHandlingResult.Status.ACCEPTED) {
                return result
            }
        }
        return MessageHandlingResult.FILTERED
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.*"`
Expected: PASS — existing tests compare against the `ACCEPTED`/`FILTERED` constants (null name), which mocks return, so equality holds. New test passes.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/CompositeMessageHandler.kt \
  app/src/test/java/me/nagaev/veles/otp/handlers/CompositeMessageHandlerTest.kt
git commit -m "feat: CompositeMessageHandler propagates matched template name"
```

---

## Task 4: Enrich TestResult with received message fields

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/TestResultFlow.kt`
- Test: `app/src/test/java/me/nagaev/veles/common/TestResultFlowTest.kt`
- Test: `app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt`

**Interfaces:**
- Produces: `TestResult(handlingResult: MessageHandlingResult, receivedText: String, receivedTitle: String, sourcePackage: String, timestamp: Long)`. Field `result` is renamed to `handlingResult`.

- [ ] **Step 1: Write the failing tests**

Update `TestResultFlowTest.kt`. The two `TestResult(...)` constructions need the new fields. Replace the test bodies:

```kotlin
    @Test
    fun `emitting ACCEPTED result updates current value`() {
        val result = TestResult(
            handlingResult = MessageHandlingResult.ACCEPTED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 1000L,
        )
        TestResultFlow.current.value = result
        assertEquals(result, TestResultFlow.current.value)
    }

    @Test
    fun `emitting FILTERED result updates current value`() {
        val result = TestResult(
            handlingResult = MessageHandlingResult.FILTERED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 2000L,
        )
        TestResultFlow.current.value = result
        assertEquals(result, TestResultFlow.current.value)
    }
```

Update `TestViewModelTest.kt`. The two `TestResult(MessageHandlingResult.ACCEPTED, 1000L)` constructions (in `TestResultFlow update sets lastResult in state` and `onCleared resets TestResultFlow to null`) become:

```kotlin
        val result = TestResult(
            handlingResult = MessageHandlingResult.ACCEPTED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 1000L,
        )
```

And in `TestResultFlow FILTERED result sets lastResult in state`:

```kotlin
        val result = TestResult(
            handlingResult = MessageHandlingResult.FILTERED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 2000L,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.TestResultFlowTest" --tests "me.nagaev.veles.testing.viewmodel.TestViewModelTest"`
Expected: FAIL — `TestResult` has no `handlingResult`/`receivedText`/etc. fields; won't compile.

- [ ] **Step 3: Enrich TestResult**

Replace the `TestResult` data class in `TestResultFlow.kt`:

```kotlin
data class TestResult(
    val handlingResult: MessageHandlingResult,
    val receivedText: String,
    val receivedTitle: String,
    val sourcePackage: String,
    val timestamp: Long,
)
```

Keep `TestResultFlow` unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.TestResultFlowTest" --tests "me.nagaev.veles.testing.viewmodel.TestViewModelTest"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/TestResultFlow.kt \
  app/src/test/java/me/nagaev/veles/common/TestResultFlowTest.kt \
  app/src/test/java/me/nagaev/veles/testing/viewmodel/TestViewModelTest.kt
git commit -m "feat: enrich TestResult with received text, title, source package"
```

---

## Task 5: NotificationListener builds the enriched TestResult

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt`
- Test: `app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt`

**Interfaces:**
- Consumes: enriched `TestResult` (Task 4), `RegexMessageHandler(name, ...)` (Task 2)
- Produces: `NotificationListener.onCreate` passes `config.name` to each `RegexMessageHandler`. `onNotificationPosted` writes `TestResult(handlingResult, receivedText, receivedTitle, sourcePackage, timestamp)` to `TestResultFlow`.

- [ ] **Step 1: Write the failing test**

Update `NotificationListenerTest.kt`. The existing test `onNotificationPosted writes ACCEPTED to TestResultFlow for self-notifications` (around line 136) currently asserts:

```kotlin
        assertEquals(MessageHandlingResult.ACCEPTED, TestResultFlow.current.value?.result)
```

Replace that assertion with assertions on the enriched `TestResult`:

```kotlin
        val testResult = TestResultFlow.current.value
        assertEquals(MessageHandlingResult.ACCEPTED, testResult?.handlingResult)
        assertEquals("text", testResult?.receivedText)
        assertEquals("title", testResult?.receivedTitle)
        assertEquals(ownPkg, testResult?.sourcePackage)
```

Then add a new test verifying the matched template name is carried through:

```kotlin
    @Test
    fun `onNotificationPosted writes matched template name and received text to TestResultFlow for self-notifications`() {
        val ownPkg = "me.nagaev.veles"
        val expectedText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp."
        val matchedResult = MessageHandlingResult(
            MessageHandlingResult.Status.ACCEPTED,
            "UOB Thai",
        )
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "UOB"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns expectedText
        every { sbn.key } returns "key"
        every { sbn.packageName } returns ownPkg
        every { sbn.notification } returns notification
        notification.extras = bundle
        every { notification.channelId } returns TestNotificationSender.CHANNEL_ID
        every { messageHandler.onMessageReceived(any()) } returns matchedResult

        val service = NotificationListener(state, messageHandler, ownPackageName = ownPkg)
        service.onCreate()
        service.onNotificationPosted(sbn)

        val testResult = TestResultFlow.current.value
        assertEquals(matchedResult, testResult?.handlingResult)
        assertEquals("UOB Thai", testResult?.handlingResult?.matchedTemplateName)
        assertEquals(expectedText, testResult?.receivedText)
        assertEquals("UOB", testResult?.receivedTitle)
        assertEquals(ownPkg, testResult?.sourcePackage)
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.NotificationListenerTest`
Expected: FAIL — `TestResultFlow.current.value?.result` no longer exists (field renamed); also the new test fails because the listener writes the old `TestResult`.

- [ ] **Step 3: Update NotificationListener to build the enriched TestResult and pass config.name**

In `NotificationListener.kt`, update `onCreate` to pass `config.name` to each `RegexMessageHandler`. Replace the `messageHandler = injectedHandler ?: run { ... }` block:

```kotlin
        messageHandler = injectedHandler ?: run {
            val notifier = UserNotifierOtpMessageHandler(this)
            val repository = BankHandlerRepository(this)
            val handlers =
                repository.getAll().map { config ->
                    RegexMessageHandler(
                        name = config.name,
                        otpRegex = config.otpRegex,
                        moneyRegex = config.moneyRegex,
                        merchantRegex = config.merchantRegex,
                        notifier = notifier,
                    )
                }
            CompositeMessageHandler(handlers)
        }
```

Then update the `TestResultFlow` write in `onNotificationPosted`:

```kotlin
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

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.NotificationListenerTest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/NotificationListener.kt \
  app/src/androidTest/java/me/nagaev/veles/otp/NotificationListenerTest.kt
git commit -m "feat: NotificationListener builds enriched TestResult and passes config name"
```

---

## Task 6: TestScreen renders template name and received text

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`
- Modify: `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt`
- Test: `app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt` (new)

**Interfaces:**
- Consumes: enriched `TestResult` (Task 4) with `handlingResult` (carrying `matchedTemplateName` when matched), `receivedText`, `receivedTitle`, `sourcePackage`.
- Produces: `TestScreen` renders the status line under `TEST_RESULT`, template name under `TEST_RESULT_TEMPLATE` (only when matched), received text under `TEST_RESULT_RECEIVED_TEXT`, plus optional title/source lines when non-empty.

- [ ] **Step 1: Write the failing Compose tests**

Create `app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt`:

```kotlin
package me.nagaev.veles.testing.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.assertDoesNotExist
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.testing.viewmodel.TestState
import org.junit.Rule
import org.junit.Test

@Suppress("MaxLineLength")
class TestScreenComposeTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    private val matchedResult = TestResult(
        handlingResult = MessageHandlingResult(
            MessageHandlingResult.Status.ACCEPTED,
            "UOB Thailand",
        ),
        receivedText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp.",
        receivedTitle = "UOB",
        sourcePackage = "me.nagaev.veles",
        timestamp = 1000L,
    )

    private val filteredResult = TestResult(
        handlingResult = MessageHandlingResult.FILTERED,
        receivedText = "some unrelated text",
        receivedTitle = "",
        sourcePackage = "",
        timestamp = 2000L,
    )

    private val redactedResult = TestResult(
        handlingResult = MessageHandlingResult.FILTERED,
        receivedText = "Sensitive notification content hidden",
        receivedTitle = "",
        sourcePackage = "",
        timestamp = 3000L,
    )

    @Test
    fun `matched result renders status, template name, and received text`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = matchedResult),
                onTextChanged = {},
                onSend = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT)
            .assertIsDisplayed()
            .assertTextContains("Matched")
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_TEMPLATE)
            .assertIsDisplayed()
            .assertTextContains("UOB Thailand")
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("For purchase THB600.00")
    }

    @Test
    fun `filtered result renders status and received text, no template`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = filteredResult),
                onTextChanged = {},
                onSend = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT)
            .assertIsDisplayed()
            .assertTextContains("No match")
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_TEMPLATE)
            .assertDoesNotExist()
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("some unrelated text")
    }

    @Test
    fun `redacted received text is displayed verbatim`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = redactedResult),
                onTextChanged = {},
                onSend = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("Sensitive notification content hidden")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.testing.ui.TestScreenComposeTest`
Expected: FAIL — `TEST_RESULT_TEMPLATE` and `TEST_RESULT_RECEIVED_TEXT` tags don't exist yet; the result card doesn't render template/received text.

- [ ] **Step 3: Add the new TestTags**

In `TestTags.kt`, add the two constants after `TEST_RESULT`:

```kotlin
    const val TEST_RESULT = "test_result"
    const val TEST_RESULT_TEMPLATE = "test_result_template"
    const val TEST_RESULT_RECEIVED_TEXT = "test_result_received_text"
```

- [ ] **Step 4: Render the result card in TestScreen**

In `TestScreen.kt`, replace the `ResultBadge` composable and its call site. Update imports to include `Column` (already imported) and add nothing else new. Replace the `state.lastResult?.let { ... }` block:

```kotlin
        state.lastResult?.let { result ->
            Spacer(Modifier.height(16.dp))
            ResultCard(result)
        }
```

Replace the `ResultBadge` composable with:

```kotlin
@Composable
private fun ResultCard(result: TestResult) {
    val (statusLabel, statusColor) = when (result.handlingResult.status) {
        MessageHandlingResult.Status.ACCEPTED -> "Matched ✓" to MATCHED_COLOR
        MessageHandlingResult.Status.FILTERED -> "No match" to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Column {
        Text(
            text = statusLabel,
            color = statusColor,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.testTag(TestTags.TEST_RESULT),
        )
        result.handlingResult.matchedTemplateName?.let { name ->
            Spacer(Modifier.height(4.dp))
            Text(
                text = "Template: $name",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag(TestTags.TEST_RESULT_TEMPLATE),
            )
        }
        Spacer(Modifier.height(4.dp))
        Text(
            text = "Received: ${result.receivedText}",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.testTag(TestTags.TEST_RESULT_RECEIVED_TEXT),
        )
        if (result.receivedTitle.isNotBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = "Title: ${result.receivedTitle}",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (result.sourcePackage.isNotBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = "Source: ${result.sourcePackage}",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
```

Update the `TestScreenPreview` to render a matched result so the preview exercises the template path:

```kotlin
@Preview(showBackground = true)
@Composable
@Suppress("MaxLineLength")
fun TestScreenPreview() {
    TestScreen(
        state = TestState(
            inputText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone.",
            lastResult = TestResult(
                handlingResult = MessageHandlingResult(
                    MessageHandlingResult.Status.ACCEPTED,
                    "UOB Thai",
                ),
                receivedText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp.",
                receivedTitle = "UOB",
                sourcePackage = "com.uob.th",
                timestamp = 1000L,
            ),
        ),
        onTextChanged = {},
        onSend = {},
    )
}
```

No new imports needed — `TestResult`, `MessageHandlingResult`, and `TestTags` are already imported in `TestScreen.kt` (lines 20-22). The `Column` composable is also already imported.

- [ ] **Step 5: Run tests to verify they pass**

Run: `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.testing.ui.TestScreenComposeTest`
Expected: PASS — all three test cases.

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt \
  app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt \
  app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt
git commit -m "feat: TestScreen renders matched template name and received text"
```

---

## Task 7: Full verification

- [ ] **Step 1: Run all unit tests**

Run: `./gradlew testDebugUnitTest`
Expected: PASS — all unit tests green.

- [ ] **Step 2: Run all instrumented tests**

Run: `./gradlew connectedDebugAndroidTest`
Expected: PASS — all instrumented tests green.

- [ ] **Step 3: Build debug APK**

Run: `./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit any remaining fixes (if needed)**

If verification surfaced fixes, commit them. Otherwise no commit needed.