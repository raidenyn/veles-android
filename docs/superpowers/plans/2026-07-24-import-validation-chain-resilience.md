# Import Validation and Handler-Chain Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject invalid bank-template imports atomically and ensure invalid persisted rows cannot freeze or suppress the valid OTP handler chain.

**Architecture:** A pure `BankConfigValidator` defines the name, regex syntax, and capture-group contract shared by the editor, importer, and runtime reloader. Import analysis validates the last-entry-wins effective set before producing either a normal diff or structured rejection details; the reloader independently skips invalid rows and publishes a composite from every valid row.

**Tech Stack:** Kotlin, Android SDK 33-35, Jetpack Compose Material 3, StateFlow ViewModels, Room, kotlinx.serialization, coroutines, JUnit 4, MockK, Compose UI tests, Gradle Kotlin DSL.

## Global Constraints

- Add no runtime dependency and do not change the Room schema or import JSON schema.
- Preserve last-entry-wins deduplication for imported duplicate template names.
- Reject the complete effective import set when any template is invalid; never partially write it.
- Apply one contract: non-blank name, two OTP groups, two amount groups, and one merchant group.
- Keep raw regex values and `PatternSyntaxException` diagnostics out of user-visible copy and logs.
- Do not delete, repair, or quarantine existing invalid database rows.
- Preserve flow-level retry/backoff and coroutine cancellation behavior in `HandlerChainReloader`.
- Keep valid import review, overwrite, confirmation, and success behavior unchanged.
- Follow existing resource-backed copy and stable `TestTags` conventions.

---

### Task 1: Add the shared validator and migrate editor validation

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/BankConfigValidator.kt`
- Create: `app/src/test/java/me/nagaev/veles/otp/config/BankConfigValidatorTest.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt:53-116`
- Test: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt`

**Interfaces:**
- Consumes: Four raw strings: `name`, `otpRegex`, `moneyRegex`, and `merchantRegex`.
- Produces: `enum class BankConfigField` and `BankConfigValidator.invalidFields(...): Set<BankConfigField>` for editor, importer, and reloader use.

- [ ] **Step 1: Write focused tests for the complete validation contract**

Create `BankConfigValidatorTest.kt`:

```kotlin
package me.nagaev.veles.otp.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BankConfigValidatorTest {
    private fun invalidFields(
        name: String = "Bank",
        otp: String = """(\w+)-(\d{6})""",
        amount: String = """([A-Z]{3})(\d+)""",
        merchant: String = """at (.+)""",
    ) = BankConfigValidator.invalidFields(name, otp, amount, merchant)

    @Test
    fun `valid template has no invalid fields`() {
        assertTrue(invalidFields().isEmpty())
    }

    @Test
    fun `blank name and regexes report every field`() {
        assertEquals(
            setOf(
                BankConfigField.NAME,
                BankConfigField.OTP_REGEX,
                BankConfigField.MONEY_REGEX,
                BankConfigField.MERCHANT_REGEX,
            ),
            invalidFields(name = " ", otp = "", amount = " ", merchant = ""),
        )
    }

    @Test
    fun `syntax errors report their regex fields`() {
        assertEquals(
            setOf(
                BankConfigField.OTP_REGEX,
                BankConfigField.MONEY_REGEX,
                BankConfigField.MERCHANT_REGEX,
            ),
            invalidFields(otp = "[", amount = "(", merchant = "*"),
        )
    }

    @Test
    fun `insufficient capture groups report their regex fields`() {
        assertEquals(
            setOf(
                BankConfigField.OTP_REGEX,
                BankConfigField.MONEY_REGEX,
                BankConfigField.MERCHANT_REGEX,
            ),
            invalidFields(
                otp = """(\d{6})""",
                amount = """([A-Z]{3})\d+""",
                merchant = """at .+""",
            ),
        )
    }
}
```

- [ ] **Step 2: Run the validator test and verify the missing-symbol failure**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.BankConfigValidatorTest"
```

Expected: Kotlin test compilation fails with unresolved references to `BankConfigValidator` and `BankConfigField`.

- [ ] **Step 3: Implement the pure validator**

Create `BankConfigValidator.kt`:

```kotlin
package me.nagaev.veles.otp.config

import java.util.regex.PatternSyntaxException

enum class BankConfigField {
    NAME,
    OTP_REGEX,
    MONEY_REGEX,
    MERCHANT_REGEX,
}

object BankConfigValidator {
    fun invalidFields(
        name: String,
        otpRegex: String,
        moneyRegex: String,
        merchantRegex: String,
    ): Set<BankConfigField> = buildSet {
        if (name.isBlank()) add(BankConfigField.NAME)
        if (otpRegex.isInvalidRegex(requiredGroupCount = 2)) add(BankConfigField.OTP_REGEX)
        if (moneyRegex.isInvalidRegex(requiredGroupCount = 2)) add(BankConfigField.MONEY_REGEX)
        if (merchantRegex.isInvalidRegex(requiredGroupCount = 1)) add(BankConfigField.MERCHANT_REGEX)
    }

    private fun String.isInvalidRegex(requiredGroupCount: Int): Boolean {
        if (isBlank()) return true
        return try {
            Regex(this).toPattern().matcher("").groupCount() < requiredGroupCount
        } catch (e: PatternSyntaxException) {
            true
        }
    }
}
```

- [ ] **Step 4: Run the validator tests and verify they pass**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.BankConfigValidatorTest"
```

Expected: all four `BankConfigValidatorTest` tests pass.

- [ ] **Step 5: Replace editor-local compilation checks with the shared result**

In `BankConfigEditViewModel.kt`, remove the `PatternSyntaxException` import. Add imports for `BankConfigField` and `BankConfigValidator`. Replace the validation portion of `save()` with:

```kotlin
val invalidFields = BankConfigValidator.invalidFields(
    name = s.name,
    otpRegex = s.otpRegex,
    moneyRegex = s.moneyRegex,
    merchantRegex = s.merchantRegex,
)
val nameError = if (BankConfigField.NAME in invalidFields) {
    UiText.Res(R.string.bank_config_edit_name_required)
} else {
    null
}
val otpRegexError = regexError(s.otpRegex, BankConfigField.OTP_REGEX in invalidFields)
val moneyRegexError = regexError(s.moneyRegex, BankConfigField.MONEY_REGEX in invalidFields)
val merchantRegexError = regexError(s.merchantRegex, BankConfigField.MERCHANT_REGEX in invalidFields)
```

Delete `validateRegex` and add this helper at the end of the class:

```kotlin
private fun regexError(pattern: String, invalid: Boolean): UiText? = when {
    !invalid -> null
    pattern.isBlank() -> UiText.Res(R.string.bank_config_edit_required)
    else -> UiText.Res(R.string.bank_config_edit_invalid_regex)
}
```

Keep the existing state update, early return, and repository write code unchanged.

- [ ] **Step 6: Run validator and editor tests**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.BankConfigValidatorTest" --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModelTest"
```

Expected: all focused tests pass, including blank-field, syntax, group-count, insert, and update cases.

- [ ] **Step 7: Commit the shared contract**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/BankConfigValidator.kt app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt app/src/test/java/me/nagaev/veles/otp/config/BankConfigValidatorTest.kt app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt
git commit -m "refactor: share bank config validation"
```

### Task 2: Reject invalid imports before creating a writable review

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/io/ConfigImporter.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/config/io/ConfigImporterTest.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/RejectedImportReview.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt:138-205`
- Modify: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt`

**Interfaces:**
- Consumes: `BankConfigValidator.invalidFields(...)` and the existing last-entry-wins imported list.
- Produces: `ConfigImporter.Analysis.Valid`, `ConfigImporter.Analysis.Invalid`, `ConfigImporter.InvalidEntry`, and `BankConfigsState.rejectedImportReview: RejectedImportReview?`.

- [ ] **Step 1: Add importer tests for validation after deduplication**

In `ConfigImporterTest.kt`, change the helper defaults so every ordinary fixture is valid:

```kotlin
private fun json(
    name: String,
    otp: String = """(\w+)-(\d{6})""",
    amount: String = """([A-Z]{3})(\d+)""",
    merchant: String = """at (.+)""",
) = BankConfigJson(name, RegexJson(otp, amount, merchant))
```

Add these tests:

```kotlin
@Test
fun `analyze returns every invalid effective entry and field`() {
    val analysis = ConfigImporter.analyze(
        parsed = listOf(
            json("Broken", otp = "[", merchant = "no group"),
            json("", amount = """([A-Z]{3})\d+"""),
        ),
        existing = emptyList(),
    )

    val invalid = analysis as ConfigImporter.Analysis.Invalid
    assertEquals(2, invalid.entries.size)
    assertEquals(
        setOf(BankConfigField.OTP_REGEX, BankConfigField.MERCHANT_REGEX),
        invalid.entries[0].invalidFields,
    )
    assertEquals(
        setOf(BankConfigField.NAME, BankConfigField.MONEY_REGEX),
        invalid.entries[1].invalidFields,
    )
}

@Test
fun `analyze validates only last duplicate value`() {
    val analysis = ConfigImporter.analyze(
        parsed = listOf(json("Dup", otp = "["), json("Dup")),
        existing = emptyList(),
    )

    val valid = analysis as ConfigImporter.Analysis.Valid
    assertEquals(1, valid.diff.toInsert.size)
    assertEquals("""(\w+)-(\d{6})""", valid.diff.toInsert.single().regex.otp)
}

@Test
fun `analyze returns existing diff when every effective entry is valid`() {
    val analysis = ConfigImporter.analyze(
        parsed = listOf(json("New Bank"), json("UOB Thailand")),
        existing = listOf(existing),
    )

    val valid = analysis as ConfigImporter.Analysis.Valid
    assertEquals(1, valid.diff.toInsert.size)
    assertEquals(1, valid.diff.toOverwrite.size)
}
```

Add `import me.nagaev.veles.otp.config.BankConfigField`.

- [ ] **Step 2: Run importer tests and verify `analyze` is unresolved**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.io.ConfigImporterTest"
```

Expected: test compilation fails because `ConfigImporter.analyze` and `ConfigImporter.Analysis` do not exist.

- [ ] **Step 3: Add structured importer analysis while preserving `diff`**

Replace `ConfigImporter.kt` with:

```kotlin
package me.nagaev.veles.otp.config.io

import me.nagaev.veles.otp.config.BankConfigField
import me.nagaev.veles.otp.config.BankConfigValidator
import me.nagaev.veles.otp.config.BankHandlerConfig

object ConfigImporter {
    data class Diff(
        val toInsert: List<BankConfigJson>,
        val toOverwrite: List<Pair<BankHandlerConfig, BankConfigJson>>,
    )

    data class InvalidEntry(
        val config: BankConfigJson,
        val invalidFields: Set<BankConfigField>,
    )

    sealed interface Analysis {
        data class Valid(val diff: Diff) : Analysis
        data class Invalid(val entries: List<InvalidEntry>) : Analysis
    }

    fun analyze(parsed: List<BankConfigJson>, existing: List<BankHandlerConfig>): Analysis {
        val effective = deduplicate(parsed)
        val invalidEntries = effective.mapNotNull { config ->
            val invalidFields = BankConfigValidator.invalidFields(
                name = config.name,
                otpRegex = config.regex.otp,
                moneyRegex = config.regex.amount,
                merchantRegex = config.regex.merchant,
            )
            if (invalidFields.isEmpty()) null else InvalidEntry(config, invalidFields)
        }
        return if (invalidEntries.isEmpty()) {
            Analysis.Valid(classify(effective, existing))
        } else {
            Analysis.Invalid(invalidEntries)
        }
    }

    fun diff(parsed: List<BankConfigJson>, existing: List<BankHandlerConfig>): Diff =
        classify(deduplicate(parsed), existing)

    private fun deduplicate(parsed: List<BankConfigJson>): List<BankConfigJson> {
        val deduped = LinkedHashMap<String, BankConfigJson>()
        for (entry in parsed) deduped[entry.name] = entry
        return deduped.values.toList()
    }

    private fun classify(
        effective: List<BankConfigJson>,
        existing: List<BankHandlerConfig>,
    ): Diff {
        val toInsert = mutableListOf<BankConfigJson>()
        val toOverwrite = mutableListOf<Pair<BankHandlerConfig, BankConfigJson>>()
        for (incoming in effective) {
            val match = existing.firstOrNull { it.name == incoming.name }
            if (match == null) {
                toInsert.add(incoming)
            } else {
                toOverwrite.add(match to incoming)
            }
        }
        return Diff(toInsert, toOverwrite)
    }
}
```

- [ ] **Step 4: Run importer tests and verify old diff behavior plus new analysis**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.io.ConfigImporterTest"
```

Expected: all existing classification/deduplication tests and the three new analysis tests pass.

- [ ] **Step 5: Add failing ViewModel tests for atomic rejection state**

In `BankConfigsViewModelTest.kt`, first make the class-level `config` valid by changing:

```kotlin
otpRegex = """(\w+)-(\d{6})""",
```

Add an import for `BankConfigField`, then add:

```kotlin
@Test
fun `onImportUri rejects all effective entries when any template is invalid`() {
    coJustRun { repository.insert(any()) }
    coEvery { repository.update(any()) } returns Unit
    val context = mockk<Context>()
    val json = ConfigSerializer.toJson(
        listOf(
            config.copy(name = "Valid Bank"),
            config.copy(name = "Broken Bank", otpRegex = "[", merchantRegex = "no group"),
        ),
    )
    every { context.contentResolver.openInputStream(any()) }
        .returns(ByteArrayInputStream(json.toByteArray()))
    val vm = BankConfigsViewModel(repository, testDispatcher)

    vm.onImportUri(context, Uri.parse("content://x/y"))
    vm.confirmImport()

    assertNull(vm.state.value.importReview)
    val rejected = vm.state.value.rejectedImportReview
    assertNotNull(rejected)
    assertEquals("Broken Bank", rejected!!.entries.single().name)
    assertEquals(
        setOf(BankConfigField.OTP_REGEX, BankConfigField.MERCHANT_REGEX),
        rejected.entries.single().invalidFields,
    )
    coVerify(exactly = 0) { repository.insert(any()) }
    coVerify(exactly = 0) { repository.update(any()) }
}

@Test
fun `onImportUri reports blank name and all invalid fields together`() {
    val context = mockk<Context>()
    val json = ConfigSerializer.toJson(
        listOf(
            config.copy(
                name = "",
                otpRegex = "",
                moneyRegex = "",
                merchantRegex = "",
            ),
        ),
    )
    every { context.contentResolver.openInputStream(any()) }
        .returns(ByteArrayInputStream(json.toByteArray()))
    val vm = BankConfigsViewModel(repository, testDispatcher)

    vm.onImportUri(context, Uri.parse("content://x/y"))

    assertEquals(
        BankConfigField.entries.toSet(),
        vm.state.value.rejectedImportReview!!.entries.single().invalidFields,
    )
}

@Test
fun `cancelImport clears rejected review`() {
    val context = mockk<Context>()
    val json = ConfigSerializer.toJson(listOf(config.copy(name = "Broken", otpRegex = "[")))
    every { context.contentResolver.openInputStream(any()) }
        .returns(ByteArrayInputStream(json.toByteArray()))
    val vm = BankConfigsViewModel(repository, testDispatcher)

    vm.onImportUri(context, Uri.parse("content://x/y"))
    vm.cancelImport()

    assertNull(vm.state.value.rejectedImportReview)
}
```

- [ ] **Step 6: Run ViewModel tests and verify rejected-state symbols are unresolved**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelTest"
```

Expected: test compilation fails because `BankConfigsState.rejectedImportReview` and its entry type do not exist.

- [ ] **Step 7: Add rejected-review state and route importer analysis into it**

Create `RejectedImportReview.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankConfigField
import me.nagaev.veles.otp.config.io.ConfigImporter

data class RejectedImportReview(
    val entries: List<Entry>,
) {
    data class Entry(
        val name: String,
        val invalidFields: Set<BankConfigField>,
    )

    companion object {
        fun from(entries: List<ConfigImporter.InvalidEntry>) = RejectedImportReview(
            entries.map { Entry(it.config.name, it.invalidFields) },
        )
    }
}
```

Add this property to `BankConfigsState` after `importReview`:

```kotlin
val rejectedImportReview: RejectedImportReview? = null,
```

In `BankConfigsViewModel.onImportUri`, clear stale import/message state before reading:

```kotlin
_state.update {
    it.copy(importReview = null, rejectedImportReview = null, message = null)
}
```

Replace the direct `diff` update after the empty-list check with:

```kotlin
when (val analysis = ConfigImporter.analyze(parsed, _state.value.configs)) {
    is ConfigImporter.Analysis.Valid -> _state.update {
        it.copy(
            importReview = ImportReview.from(analysis.diff),
            rejectedImportReview = null,
        )
    }
    is ConfigImporter.Analysis.Invalid -> _state.update {
        it.copy(
            importReview = null,
            rejectedImportReview = RejectedImportReview.from(analysis.entries),
        )
    }
}
```

Replace `cancelImport()` with:

```kotlin
fun cancelImport() {
    _state.update { it.copy(importReview = null, rejectedImportReview = null) }
}
```

- [ ] **Step 8: Run importer and ViewModel tests**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.io.ConfigImporterTest" --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelTest"
```

Expected: all focused tests pass. Invalid imports expose only rejected state and `confirmImport()` performs zero writes; valid imports retain their normal review and write behavior.

- [ ] **Step 9: Commit atomic import analysis**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/io/ConfigImporter.kt app/src/main/java/me/nagaev/veles/otp/config/viewmodel/RejectedImportReview.kt app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt app/src/test/java/me/nagaev/veles/otp/config/io/ConfigImporterTest.kt app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt
git commit -m "fix: reject invalid template imports"
```

### Task 3: Render structured import rejection without an Import action

**Files:**
- Modify: `app/src/main/res/values/strings.xml:15-46`
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt:27-34`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt`
- Modify: `app/src/androidTest/java/me/nagaev/veles/otp/config/ExportImportFlowTest.kt`

**Interfaces:**
- Consumes: `BankConfigsState.rejectedImportReview` and `RejectedImportReview.Entry.invalidFields` from Task 2.
- Produces: A localized `RejectedImportDialog` selected by `TestTags.BANK_CONFIG_IMPORT_REJECTED_DIALOG`, dismissed through the existing `onCancelImport` callback.

- [ ] **Step 1: Add a failing instrumented UI test for names, fields, fallback, and absent confirmation**

In `ExportImportFlowTest.kt`, add imports:

```kotlin
import androidx.compose.ui.test.assertDoesNotExist
import androidx.compose.ui.test.onNodeWithText
import me.nagaev.veles.R
```

Add this test:

```kotlin
@Test
fun `invalid import shows every invalid entry and no import action`() {
    val context = androidx.test.core.app.ApplicationProvider
        .getApplicationContext<android.content.Context>()
    val json = me.nagaev.veles.otp.config.io.ConfigSerializer.toJson(
        listOf(
            BankHandlerConfig(
                name = "Broken Bank",
                otpRegex = "[",
                moneyRegex = """([A-Z]{3})\d+""",
                merchantRegex = "no group",
                createdAt = 0L,
                updatedAt = 0L,
            ),
            BankHandlerConfig(
                name = "",
                otpRegex = """(\w+)-(\d{6})""",
                moneyRegex = """([A-Z]{3})(\d+)""",
                merchantRegex = """at (.+)""",
                createdAt = 0L,
                updatedAt = 0L,
            ),
        ),
    )
    val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, json)

    composeRule.runOnIdle { vm.onImportUri(context, uri) }
    composeRule.waitUntil(5000) { vm.state.value.rejectedImportReview != null }

    composeRule.onNodeWithTag(TestTags.BANK_CONFIG_IMPORT_REJECTED_DIALOG).assertIsDisplayed()
    composeRule.onNodeWithText("Broken Bank").assertIsDisplayed()
    composeRule.onNodeWithText(context.getString(R.string.bank_configs_import_unnamed)).assertIsDisplayed()
    composeRule.onNodeWithText(
        context.getString(R.string.bank_configs_import_field_name),
        substring = true,
    ).assertIsDisplayed()
    composeRule.onNodeWithText(
        context.getString(R.string.bank_configs_import_field_otp),
        substring = true,
    ).assertIsDisplayed()
    composeRule.onNodeWithText(
        context.getString(R.string.bank_configs_import_field_amount),
        substring = true,
    ).assertIsDisplayed()
    composeRule.onNodeWithText(
        context.getString(R.string.bank_configs_import_field_merchant),
        substring = true,
    ).assertIsDisplayed()
    composeRule.onNodeWithTag(TestTags.BANK_CONFIG_IMPORT_CONFIRM).assertDoesNotExist()
}
```

- [ ] **Step 2: Compile instrumented tests and verify missing resource/tag failures**

Run:

```bash
./gradlew compileDebugAndroidTestKotlin
```

Expected: compilation fails because the rejected-dialog resource IDs and test tag do not exist.

- [ ] **Step 3: Add localized rejection copy and a stable test tag**

Add these resources after the existing import strings in `strings.xml`:

```xml
<string name="bank_configs_import_rejected_title">Can\'t import templates</string>
<string name="bank_configs_import_rejected_body">Nothing was imported. Fix these templates and try again:</string>
<string name="bank_configs_import_unnamed">Unnamed template</string>
<string name="bank_configs_import_field_name">Name</string>
<string name="bank_configs_import_field_otp">OTP regex</string>
<string name="bank_configs_import_field_amount">Amount regex</string>
<string name="bank_configs_import_field_merchant">Merchant regex</string>
<string name="bank_configs_import_invalid_field">- %1$s</string>
```

Add to `TestTags` beside the existing import dialog tag:

```kotlin
const val BANK_CONFIG_IMPORT_REJECTED_DIALOG = "bank_config_import_rejected_dialog"
```

- [ ] **Step 4: Render the informational rejection dialog**

In `BankConfigsScreen.kt`, add imports:

```kotlin
import androidx.annotation.StringRes
import me.nagaev.veles.otp.config.BankConfigField
import me.nagaev.veles.otp.config.viewmodel.RejectedImportReview
```

After the existing normal import-review block, add:

```kotlin
if (state.rejectedImportReview != null) {
    RejectedImportDialog(
        review = state.rejectedImportReview,
        onDismiss = onCancelImport,
    )
}
```

Add these functions after `ImportReviewDialog`:

```kotlin
@Composable
private fun RejectedImportDialog(
    review: RejectedImportReview,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(8.dp),
        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_REJECTED_DIALOG),
        title = { Text(stringResource(R.string.bank_configs_import_rejected_title)) },
        text = {
            Column {
                Text(stringResource(R.string.bank_configs_import_rejected_body))
                review.entries.forEach { entry ->
                    val displayName = if (entry.name.isBlank()) {
                        stringResource(R.string.bank_configs_import_unnamed)
                    } else {
                        entry.name
                    }
                    Text(
                        text = displayName,
                        style = MaterialTheme.typography.titleSmall,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                    entry.invalidFields.forEach { field ->
                        Text(
                            stringResource(
                                R.string.bank_configs_import_invalid_field,
                                stringResource(field.labelResource()),
                            ),
                        )
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.action_ok))
            }
        },
    )
}

@StringRes
private fun BankConfigField.labelResource(): Int = when (this) {
    BankConfigField.NAME -> R.string.bank_configs_import_field_name
    BankConfigField.OTP_REGEX -> R.string.bank_configs_import_field_otp
    BankConfigField.MONEY_REGEX -> R.string.bank_configs_import_field_amount
    BankConfigField.MERCHANT_REGEX -> R.string.bank_configs_import_field_merchant
}
```

The dialog deliberately has no `dismissButton` and no node tagged `BANK_CONFIG_IMPORT_CONFIRM`.

- [ ] **Step 5: Update valid instrumented import fixtures to satisfy the shared contract**

In the three existing import tests in `ExportImportFlowTest.kt`, replace imported placeholder regex values such as `"new"`, `"new-otp"`, `"x"`, and `"new-mer"` with these valid values:

```kotlin
otpRegex = """(\w+)-(\d{6})""",
moneyRegex = """([A-Z]{3})(\d+)""",
merchantRegex = """at (.+)""",
```

In `confirm import writes new and overwrite rows to the database`, update the expected overwrite assertions to:

```kotlin
assertEquals("""(\w+)-(\d{6})""", existing.otpRegex)
assertEquals("""([A-Z]{3})(\d+)""", existing.moneyRegex)
assertEquals("""at (.+)""", existing.merchantRegex)
assertEquals("""(\w+)-(\d{6})""", newBank.otpRegex)
```

- [ ] **Step 6: Compile Android tests and run all JVM tests**

Run:

```bash
./gradlew testDebugUnitTest compileDebugAndroidTestKotlin
```

Expected: JVM tests pass and all instrumented test sources compile.

- [ ] **Step 7: Run the focused connected test when an Android target is available**

Check:

```bash
adb devices
```

If a device or emulator is listed as `device`, run:

```bash
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.config.ExportImportFlowTest
```

Expected: every `ExportImportFlowTest` test passes, including the rejected-import dialog test. If no target is available, record that connected execution was unavailable; Step 6 still proves the test source compiles.

- [ ] **Step 8: Commit the rejection UI**

```bash
git add app/src/main/res/values/strings.xml app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt app/src/androidTest/java/me/nagaev/veles/otp/config/ExportImportFlowTest.kt
git commit -m "feat: explain rejected template imports"
```

### Task 4: Rebuild the runtime chain from each valid persisted row

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/HandlerChainReloader.kt:35-55`
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/HandlerChainReloaderTest.kt:110-135`

**Interfaces:**
- Consumes: `BankConfigValidator.invalidFields(...)` and `BankConfigField` from Task 1.
- Produces: Every config-flow emission atomically publishes `CompositeMessageHandler(validHandlers)` while invalid rows are skipped and safely logged.

- [ ] **Step 1: Replace the stale-chain regression test with per-row resilience tests**

In `HandlerChainReloaderTest.kt`, replace `malformed config does not kill the collector and a later fix takes effect` with:

```kotlin
@Test
fun `mixed initial emission activates valid handler and skips malformed row`() =
    runTest(UnconfinedTestDispatcher()) {
        val malformed = config(
            "Bad",
            "[",
            """ pay ([A-Z]{3})(\d+\.\d{2}) """,
            """ at (\w+)""",
        )
        val flow = MutableStateFlow(listOf(malformed, bankA))
        val r = reloader(flow)

        r.start(this)
        try {
            assertEquals(
                MessageHandlingResult.Status.ACCEPTED,
                r.messageHandler.onMessageReceived(msgA).status,
            )
            assertEquals("BankA", r.messageHandler.onMessageReceived(msgA).matchedTemplateName)
        } finally {
            r.stop()
        }
    }

@Test
fun `invalid only initial emission stays collectible and later correction activates`() =
    runTest(UnconfinedTestDispatcher()) {
        val malformed = config("Bad", "[", "x", "y")
        val flow = MutableStateFlow(listOf(malformed))
        val r = reloader(flow)

        r.start(this)
        try {
            assertEquals(MessageHandlingResult.FILTERED, r.messageHandler.onMessageReceived(msgA))

            flow.value = listOf(bankA)

            assertEquals(
                MessageHandlingResult.Status.ACCEPTED,
                r.messageHandler.onMessageReceived(msgA).status,
            )
        } finally {
            r.stop()
        }
    }

@Test
fun `invalid only emission replaces previous valid chain and later correction activates`() =
    runTest(UnconfinedTestDispatcher()) {
        val malformed = config("Bad", "[", "x", "y")
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)

        r.start(this)
        try {
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgA).status)

            flow.value = listOf(malformed)
            assertEquals(MessageHandlingResult.FILTERED, r.messageHandler.onMessageReceived(msgA))

            flow.value = listOf(bankB)
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgB).status)
        } finally {
            r.stop()
        }
    }

@Test
fun `valid edits and deletions apply while malformed row remains`() = runTest(UnconfinedTestDispatcher()) {
    val malformed = config("Bad", "[", "x", "y")
    val flow = MutableStateFlow(listOf(malformed, bankA))
    val r = reloader(flow)

    r.start(this)
    try {
        flow.value = listOf(malformed, bankB)

        assertEquals(MessageHandlingResult.Status.FILTERED, r.messageHandler.onMessageReceived(msgA).status)
        assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgB).status)

        flow.value = listOf(malformed)
        assertEquals(MessageHandlingResult.Status.FILTERED, r.messageHandler.onMessageReceived(msgB).status)
    } finally {
        r.stop()
    }
}
```

- [ ] **Step 2: Run the reloader tests and verify mixed input fails**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.HandlerChainReloaderTest"
```

Expected: `mixed initial emission activates valid handler and skips malformed row` fails because the malformed row aborts the whole rebuild and leaves the empty default chain. The invalid-only recovery test may pass only after the bad row is removed, but it does not satisfy the mixed-list requirement.

- [ ] **Step 3: Validate and construct every row independently**

In `HandlerChainReloader.kt`, import `BankConfigValidator`. Replace the inner emission-level `try/catch` in `configs.collect` with:

```kotlin
configs.collect { list ->
    val handlers = list.mapNotNull { config ->
        val invalidFields = BankConfigValidator.invalidFields(
            name = config.name,
            otpRegex = config.otpRegex,
            moneyRegex = config.moneyRegex,
            merchantRegex = config.merchantRegex,
        )
        if (invalidFields.isNotEmpty()) {
            logger().log(
                Level.WARNING,
                "Skipping invalid handler config {0}: {1}",
                arrayOf(config.name, invalidFields.joinToString()),
            )
            null
        } else {
            try {
                RegexMessageHandler(
                    name = config.name,
                    otpRegex = config.otpRegex,
                    moneyRegex = config.moneyRegex,
                    merchantRegex = config.merchantRegex,
                    notifier = notifier,
                )
            } catch (e: PatternSyntaxException) {
                logger().log(
                    Level.WARNING,
                    "Skipping handler config {0}: construction failed",
                    config.name,
                )
                null
            }
        }
    }
    messageHandler = CompositeMessageHandler(handlers)
}
```

Do not log `e`: `PatternSyntaxException` messages include the pattern text. Keep the outer flow-level `CancellationException` and `Exception` catches, retry flag, delay, and restart loop unchanged.

- [ ] **Step 4: Run the focused validator and reloader tests**

Run:

```bash
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.BankConfigValidatorTest" --tests "me.nagaev.veles.otp.handlers.HandlerChainReloaderTest"
```

Expected: all focused tests pass, including mixed rows, invalid-only recovery, valid edits with a persistent malformed row, flow restart, cancellation, double-start, and stop behavior.

- [ ] **Step 5: Run the complete verification suite**

Run:

```bash
./gradlew testDebugUnitTest compileDebugAndroidTestKotlin assembleDebug
```

Expected: all unit tests pass, instrumented test sources compile, and the debug APK builds successfully.

If an Android target is available and Task 3 did not already run connected tests, run:

```bash
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.otp.config.ExportImportFlowTest
```

Expected: all selected connected tests pass. Otherwise record that connected execution was unavailable.

- [ ] **Step 6: Review the final diff for validation consistency and privacy**

Run:

```bash
git diff --check
git diff -- app/src/main app/src/test app/src/androidTest
git status --short
```

Expected: no whitespace errors; one validator supplies editor, importer, and reloader; rejected imports cannot create `ImportReview`; logs and UI contain no regex values or parser diagnostics; only issue #58 code/tests plus the implementation plan are changed. Leave unrelated pre-existing worktree files untouched.

- [ ] **Step 7: Commit runtime resilience**

```bash
git add app/src/main/java/me/nagaev/veles/otp/handlers/HandlerChainReloader.kt app/src/test/java/me/nagaev/veles/otp/handlers/HandlerChainReloaderTest.kt
git commit -m "fix: isolate invalid handler configs"
```
