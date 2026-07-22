# Regex Handler Parse Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent user-defined regex captures from crashing notification handling while correctly parsing conventionally comma-grouped amounts.

**Architecture:** Make `RegexMessageHandler` explicitly validate required match groups and convert amount captures through a strict, nullable normalization path. Add capture-group-count validation to `BankConfigEditViewModel` so new invalid configs are rejected at save time, while runtime checks protect legacy and imported configs.

**Tech Stack:** Kotlin, Android ViewModel/StateFlow, Java `BigDecimal` and regex APIs, JUnit 4, MockK, Gradle

## Global Constraints

- Do not change the regex configuration schema or Room database.
- Do not add locale-dependent currency parsing or arbitrary grouping separators.
- Do not change import validation or handler-chain construction; issue #58 owns that work.
- Do not add broad exception handling around handlers or the composite chain.
- Reuse `R.string.bank_config_edit_invalid_regex` for insufficient capture groups.
- OTP and money patterns require at least two capture groups; merchant patterns require at least one.
- Conventionally comma-grouped amounts use an optional sign, one to three leading digits, one or more groups of exactly three digits, and an optional decimal fraction.
- Use `/home/oc-shadow/.local/jdk` as `JAVA_HOME` for every Gradle command.

---

## File Structure

- Modify `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt` to safely extract required groups, validate comma grouping, normalize amounts, and parse without throwing.
- Modify `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt` with regression coverage for comma amounts, malformed captures, and missing groups.
- Modify `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt` to validate field-specific capture-group counts before persistence.
- Modify `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt` with save-time group-count validation coverage and valid two-group OTP fixtures.

### Task 1: Make Runtime Match Extraction Total

**Files:**
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt:11-26,262`
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt:5-43`

**Interfaces:**
- Consumes: Existing `RegexMessageHandler(name, otpRegex, moneyRegex, merchantRegex, notifier)` constructor and `MessageHandler.onMessageReceived(message): MessageHandlingResult` contract.
- Produces: The same public constructor and method contract; malformed required captures return `MessageHandlingResult.FILTERED`, and valid grouped amounts produce normalized `Money.amount` values.

- [ ] **Step 1: Add failing runtime regression tests**

Replace the test helper with a version that allows each regex to be overridden:

```kotlin
private fun handler(
    notifier: OtpMessageHandler,
    otpRegex: String = OTP_REGEX,
    moneyRegex: String = MONEY_REGEX,
    merchantRegex: String = MERCHANT_REGEX,
) = RegexMessageHandler(HANDLER_NAME, otpRegex, moneyRegex, merchantRegex, notifier)
```

Append these tests to `RegexMessageHandlerTest`:

```kotlin
@Test
fun `Comma grouped amount is normalized and accepted`() {
    val message =
        defaultMessage.copy(
            text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase THB1,234.56 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.",
        )
    val otpMessageHandler = mockk<OtpMessageHandler>()
    every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

    val result =
        handler(
            notifier = otpMessageHandler,
            moneyRegex = """purchase ([A-Z]{3})([\d,]{1,15}\.\d{1,4})""",
        ).onMessageReceived(message)

    assert(result.status == MessageHandlingResult.Status.ACCEPTED)
    verify {
        otpMessageHandler.onOtpMessageReceived(
            OtpMessage(
                otp = Otp(value = "079853", id = "HStX"),
                pay = Money(amount = BigDecimal("1234.56"), currencyCode = "THB"),
                merchant = "AMP*AIS SERVICES",
            ),
        )
    }
}

@Test
fun `Malformed comma grouping is filtered`() {
    val message =
        defaultMessage.copy(
            text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase THB12,34.56 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.",
        )
    val otpMessageHandler = mockk<OtpMessageHandler>()

    val result =
        handler(
            notifier = otpMessageHandler,
            moneyRegex = """purchase ([A-Z]{3})([\d,]{1,15}\.\d{1,4})""",
        ).onMessageReceived(message)

    assert(result == MessageHandlingResult.FILTERED)
    verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
}

@Test
fun `Non-numeric money capture is filtered`() {
    val message =
        defaultMessage.copy(
            text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase THBnot.a.number at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.",
        )
    val otpMessageHandler = mockk<OtpMessageHandler>()

    val result =
        handler(
            notifier = otpMessageHandler,
            moneyRegex = """purchase ([A-Z]{3})([a-z.]+)""",
        ).onMessageReceived(message)

    assert(result == MessageHandlingResult.FILTERED)
    verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
}

@Test
fun `OTP pattern with insufficient groups is filtered`() {
    val otpMessageHandler = mockk<OtpMessageHandler>()

    val result =
        handler(
            notifier = otpMessageHandler,
            otpRegex = """SMS-OTP \w{4}-\d{6}""",
        ).onMessageReceived(defaultMessage)

    assert(result == MessageHandlingResult.FILTERED)
    verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
}

@Test
fun `Money pattern with insufficient groups is filtered`() {
    val otpMessageHandler = mockk<OtpMessageHandler>()

    val result =
        handler(
            notifier = otpMessageHandler,
            moneyRegex = """of [A-Z]{3}\d+\.\d+ at""",
        ).onMessageReceived(defaultMessage)

    assert(result == MessageHandlingResult.FILTERED)
    verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
}

@Test
fun `Merchant pattern with insufficient groups is filtered`() {
    val otpMessageHandler = mockk<OtpMessageHandler>()

    val result =
        handler(
            notifier = otpMessageHandler,
            merchantRegex = """at .+ expiring""",
        ).onMessageReceived(defaultMessage)

    assert(result == MessageHandlingResult.FILTERED)
    verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
}
```

- [ ] **Step 2: Run the handler tests and confirm the unsafe behavior**

Run:

```bash
JAVA_HOME="/home/oc-shadow/.local/jdk" PATH="/home/oc-shadow/.local/jdk/bin:$PATH" ./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"
```

Expected: FAIL. The comma and malformed amount tests encounter `NumberFormatException`; insufficient-group tests encounter `IndexOutOfBoundsException` instead of returning `FILTERED`.

- [ ] **Step 3: Implement safe group extraction and amount parsing**

Replace `RegexMessageHandler` with:

```kotlin
package me.nagaev.veles.otp.handlers

class RegexMessageHandler(
    private val name: String,
    otpRegex: String,
    moneyRegex: String,
    merchantRegex: String,
    private val notifier: OtpMessageHandler,
) : MessageHandler {
    private companion object {
        val GROUPED_AMOUNT_PATTERN = Regex("""[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?""")
    }

    private val otpPattern = Regex(otpRegex)
    private val moneyPattern = Regex(moneyRegex)
    private val merchantPattern = Regex(merchantRegex)

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        val otp =
            otpPattern.find(message.text)?.let { match ->
                val id = match.groupValues.getOrNull(1)?.takeIf { it.isNotEmpty() }
                val value = match.groupValues.getOrNull(2)?.takeIf { it.isNotEmpty() }
                if (id != null && value != null) Otp(value = value, id = id) else null
            }
        val money =
            moneyPattern.find(message.text)?.let { match ->
                val currencyCode = match.groupValues.getOrNull(1)?.takeIf { it.isNotEmpty() }
                val capturedAmount = match.groupValues.getOrNull(2)?.takeIf { it.isNotEmpty() }
                val normalizedAmount =
                    capturedAmount?.let { amount ->
                        if (',' !in amount) {
                            amount
                        } else {
                            amount.takeIf { GROUPED_AMOUNT_PATTERN.matches(it) }?.replace(",", "")
                        }
                    }
                val amount = normalizedAmount?.toBigDecimalOrNull()
                if (currencyCode != null && amount != null) Money(amount, currencyCode) else null
            }
        val merchant =
            merchantPattern.find(message.text)?.groupValues?.getOrNull(1)?.takeIf { it.isNotEmpty() }

        return if (otp != null && money != null && merchant != null) {
            val otpMessage =
                OtpMessage(
                    otp = otp,
                    pay = money,
                    merchant = merchant,
                )
            notifier.onOtpMessageReceived(otpMessage)
            MessageHandlingResult(MessageHandlingResult.Status.ACCEPTED, name, otpMessage)
        } else {
            MessageHandlingResult.FILTERED
        }
    }
}
```

- [ ] **Step 4: Run the focused handler tests**

Run:

```bash
JAVA_HOME="/home/oc-shadow/.local/jdk" PATH="/home/oc-shadow/.local/jdk/bin:$PATH" ./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"
```

Expected: `BUILD SUCCESSFUL`; all existing and new `RegexMessageHandlerTest` cases pass.

- [ ] **Step 5: Commit the runtime hardening**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt app/src/test/java/me/nagaev/veles/otp/handlers/RegexMessageHandlerTest.kt
git commit -m "fix: safely parse regex handler captures"
```

### Task 2: Reject Invalid Capture Contracts on Save

**Files:**
- Modify: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt:25-33,56-122,132`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt:53-58,103-112`

**Interfaces:**
- Consumes: Existing `BankConfigEditViewModel.save()` and `BankConfigEditState` field-error properties.
- Produces: Private `validateRegex(pattern: String, requiredGroupCount: Int): UiText?`; save-time validation calls it with `2`, `2`, and `1` for OTP, money, and merchant patterns respectively.

- [ ] **Step 1: Make valid OTP fixtures satisfy the documented contract**

In `BankConfigEditViewModelTest`, change `existingConfig.otpRegex` and its load assertion from `"""\d{6}"""` to `"""(\w+)-(\d{6})"""`. Make the same replacement in the blank-name test and the valid-new-config test so those tests isolate name validation and successful persistence rather than accidentally supplying an invalid OTP contract.

The affected snippets become:

```kotlin
private val existingConfig = BankHandlerConfig(
    id = 42L,
    name = "Test Bank",
    otpRegex = """(\w+)-(\d{6})""",
    moneyRegex = """([A-Z]{3})(\d+)""",
    merchantRegex = """at (.+)""",
    createdAt = 1000L,
    updatedAt = 2000L,
)
```

```kotlin
assert(state.otpRegex == """(\w+)-(\d{6})""")
```

Use this call in both the blank-name and valid-new-config tests:

```kotlin
vm.onOtpRegexChanged("""(\w+)-(\d{6})""")
```

- [ ] **Step 2: Add failing save-time group-count tests**

Append these tests to `BankConfigEditViewModelTest`:

```kotlin
@Test
fun `save rejects OTP regex with fewer than two groups`() {
    val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
    vm.onNameChanged("My Bank")
    vm.onOtpRegexChanged("""(\d{6})""")
    vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
    vm.onMerchantRegexChanged("""at (.+)""")

    vm.save()

    assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.otpRegexError)
    coVerify(exactly = 0) { repository.insert(any()) }
}

@Test
fun `save rejects money regex with fewer than two groups`() {
    val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
    vm.onNameChanged("My Bank")
    vm.onOtpRegexChanged("""(\w+)-(\d{6})""")
    vm.onMoneyRegexChanged("""([A-Z]{3})\d+""")
    vm.onMerchantRegexChanged("""at (.+)""")

    vm.save()

    assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.moneyRegexError)
    coVerify(exactly = 0) { repository.insert(any()) }
}

@Test
fun `save rejects merchant regex without a group`() {
    val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
    vm.onNameChanged("My Bank")
    vm.onOtpRegexChanged("""(\w+)-(\d{6})""")
    vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
    vm.onMerchantRegexChanged("""at .+""")

    vm.save()

    assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.merchantRegexError)
    coVerify(exactly = 0) { repository.insert(any()) }
}
```

The existing `save valid new config calls insert and sets savedSuccessfully` test, now using a two-group OTP pattern, remains the regression test that valid capture contracts save normally.

- [ ] **Step 3: Run the ViewModel tests and confirm missing validation**

Run:

```bash
JAVA_HOME="/home/oc-shadow/.local/jdk" PATH="/home/oc-shadow/.local/jdk/bin:$PATH" ./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModelTest"
```

Expected: FAIL. The three new tests reach an unstubbed repository insert or fail because the expected field error remains `null`.

- [ ] **Step 4: Add required capture-group counts to editor validation**

Change the three calls in `save()` to:

```kotlin
val otpRegexError = validateRegex(s.otpRegex, requiredGroupCount = 2)
val moneyRegexError = validateRegex(s.moneyRegex, requiredGroupCount = 2)
val merchantRegexError = validateRegex(s.merchantRegex, requiredGroupCount = 1)
```

Replace `validateRegex` with:

```kotlin
private fun validateRegex(pattern: String, requiredGroupCount: Int): UiText? = if (pattern.isBlank()) {
    UiText.Res(R.string.bank_config_edit_required)
} else {
    try {
        val groupCount = Regex(pattern).toPattern().matcher("").groupCount()
        if (groupCount < requiredGroupCount) {
            UiText.Res(R.string.bank_config_edit_invalid_regex)
        } else {
            null
        }
    } catch (e: PatternSyntaxException) {
        UiText.Res(R.string.bank_config_edit_invalid_regex)
    }
}
```

- [ ] **Step 5: Run the focused ViewModel tests**

Run:

```bash
JAVA_HOME="/home/oc-shadow/.local/jdk" PATH="/home/oc-shadow/.local/jdk/bin:$PATH" ./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModelTest"
```

Expected: `BUILD SUCCESSFUL`; invalid group counts set field errors without repository writes, and valid configs still save.

- [ ] **Step 6: Run the complete debug unit-test suite**

Run:

```bash
JAVA_HOME="/home/oc-shadow/.local/jdk" PATH="/home/oc-shadow/.local/jdk/bin:$PATH" ./gradlew testDebugUnitTest
```

Expected: `BUILD SUCCESSFUL` with no unit-test regressions.

- [ ] **Step 7: Commit editor validation**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt
git commit -m "fix: validate regex capture group counts"
```
