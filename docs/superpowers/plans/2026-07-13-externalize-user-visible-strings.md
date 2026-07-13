# Externalize User-Visible Strings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every user-visible Android string into resources and establish English-only native per-app locale support without changing product behavior.

**Architecture:** Compose resolves static resources at render time, while Context-free ViewModels carry immutable `UiText` resource references. Notification and clipboard components resolve resources from their existing `Context`; explicit locale configuration declares and packages only English.

**Tech Stack:** Kotlin, Android SDK 33-35, Jetpack Compose Material 3, Android resources, StateFlow ViewModels, JUnit 4, MockK, Robolectric, Compose UI tests, Gradle Kotlin DSL.

## Global Constraints

- Work only on `feature/54-externalize-user-visible-strings`.
- Keep minimum SDK 33 and target/compile SDK 35 unchanged.
- Add no runtime dependencies and no non-English translations.
- Use feature- or screen-prefixed resource names and positional placeholders (`%1$s`, `%2$d`).
- Use `<plurals>` for count-dependent copy and pass the quantity again when the rendered text displays it.
- Keep routes, IDs, preference keys, intent actions/extras, database names, URLs, adb commands, regexes, `TestTags`, and dynamic domain data untranslated.
- Keep preference file/key values byte-for-byte unchanged.
- Mark the default matching sample and clipboard label `translatable="false"`.
- Correct only clearly awkward English; do not broadly rewrite copy or behavior.
- Do not localize the landing-page site or add an in-app language picker.

---

### Task 1: Add the immutable UiText contract

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/common/UiText.kt`
- Create: `app/src/test/java/me/nagaev/veles/common/UiTextTest.kt`
- Modify: `app/src/main/res/values/strings.xml`

**Interfaces:**
- Consumes: Android `@StringRes` and `@PluralsRes`; Compose `stringResource` and `pluralStringResource`.
- Produces: `UiText.Res(id: Int, args: List<Any>)`, `UiText.Plural(id: Int, quantity: Int, args: List<Any>)`, and `@Composable UiText.asString(): String`.

- [ ] **Step 1: Write equality tests that fail because UiText does not exist**

```kotlin
package me.nagaev.veles.common

import me.nagaev.veles.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class UiTextTest {
    @Test
    fun `formatted resources compare by argument value`() {
        assertEquals(
            UiText.Res(R.string.app_name, listOf("same")),
            UiText.Res(R.string.app_name, listOf("same")),
        )
    }

    @Test
    fun `plural resources include quantity and arguments in equality`() {
        assertEquals(
            UiText.Plural(R.plurals.bank_configs_exported, 2, listOf(2)),
            UiText.Plural(R.plurals.bank_configs_exported, 2, listOf(2)),
        )
        assertNotEquals(
            UiText.Plural(R.plurals.bank_configs_exported, 1, listOf(1)),
            UiText.Plural(R.plurals.bank_configs_exported, 2, listOf(2)),
        )
    }
}
```

Temporarily add this real resource so the test compiles before Task 2 expands the config resources:

```xml
<plurals name="bank_configs_exported">
    <item quantity="one">Exported %1$d template</item>
    <item quantity="other">Exported %1$d templates</item>
</plurals>
```

- [ ] **Step 2: Run the focused test and verify the missing type failure**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.UiTextTest"`

Expected: compilation fails with `Unresolved reference: UiText`.

- [ ] **Step 3: Implement UiText and its Compose resolver**

```kotlin
package me.nagaev.veles.common

import androidx.annotation.PluralsRes
import androidx.annotation.StringRes
import androidx.compose.runtime.Composable
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource

sealed interface UiText {
    data class Res(
        @StringRes val id: Int,
        val args: List<Any> = emptyList(),
    ) : UiText

    data class Plural(
        @PluralsRes val id: Int,
        val quantity: Int,
        val args: List<Any> = emptyList(),
    ) : UiText
}

@Composable
fun UiText.asString(): String = when (this) {
    is UiText.Res -> if (args.isEmpty()) {
        stringResource(id)
    } else {
        stringResource(id, *args.toTypedArray())
    }
    is UiText.Plural -> if (args.isEmpty()) {
        pluralStringResource(id, quantity)
    } else {
        pluralStringResource(id, quantity, *args.toTypedArray())
    }
}
```

- [ ] **Step 4: Run the focused test and static checks**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.common.UiTextTest" detekt`

Expected: `UiTextTest` passes and detekt reports no new issue.

- [ ] **Step 5: Commit the foundation**

```bash
git add app/src/main/java/me/nagaev/veles/common/UiText.kt app/src/test/java/me/nagaev/veles/common/UiTextTest.kt app/src/main/res/values/strings.xml
git commit -m "feat: add resource-backed UI text"
```

### Task 2: Convert bank-config ViewModel messages

**Files:**
- Modify: `app/src/main/res/values/strings.xml`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt`

**Interfaces:**
- Consumes: `UiText.Res`, `UiText.Plural`, and generated `R.string`/`R.plurals` IDs.
- Produces: `BankConfigsState.message: UiText?` and `BankConfigEditState` error fields typed as `UiText?`.

- [ ] **Step 1: Change ViewModel tests to require exact resource-backed values**

Replace English assertions and weak null checks with exact values:

```kotlin
assertEquals(
    UiText.Res(R.string.bank_configs_select_at_least_one),
    vm.state.value.message,
)
assertEquals(UiText.Res(R.string.bank_configs_export_failed), vm.state.value.message)
assertEquals(
    UiText.Res(R.string.bank_configs_import_invalid_file),
    vm.state.value.message,
)
assertEquals(
    UiText.Plural(R.plurals.bank_configs_imported, 2, listOf(2)),
    vm.state.value.message,
)
```

Add exact edit-validation assertions:

```kotlin
assertEquals(UiText.Res(R.string.bank_config_edit_name_required), vm.state.value.nameError)
assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.otpRegexError)
assertEquals(UiText.Res(R.string.bank_config_edit_required), vm.state.value.moneyRegexError)
assertEquals(UiText.Res(R.string.bank_config_edit_required), vm.state.value.merchantRegexError)
```

Import `me.nagaev.veles.R` and `me.nagaev.veles.common.UiText` in both test files. Extend the export-success test to assert `UiText.Plural(R.plurals.bank_configs_exported, 1, listOf(1))`, and the import-read-failure and empty-import tests to assert their exact resource IDs.

- [ ] **Step 2: Run the ViewModel tests and verify type/assertion failures**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.*"`

Expected: tests fail because state still contains raw `String` values and the new IDs are absent.

- [ ] **Step 3: Complete the bank-config outcome and validation resources**

Keep the `bank_configs_exported` plural added in Task 1 and add the remaining entries below; the complete set must contain each resource exactly once.

```xml
<string name="bank_configs_select_at_least_one">Select at least one template</string>
<string name="bank_configs_export_failed">Export failed</string>
<string name="bank_configs_import_cannot_read">Import failed: cannot read file</string>
<string name="bank_configs_nothing_to_import">Nothing to import</string>
<string name="bank_configs_import_invalid_file">Import failed: invalid file</string>
<plurals name="bank_configs_exported">
    <item quantity="one">Exported %1$d template</item>
    <item quantity="other">Exported %1$d templates</item>
</plurals>
<plurals name="bank_configs_imported">
    <item quantity="one">Imported %1$d template</item>
    <item quantity="other">Imported %1$d templates</item>
</plurals>
<string name="bank_config_edit_name_required">Name is required</string>
<string name="bank_config_edit_required">Required</string>
<string name="bank_config_edit_invalid_regex">Invalid regex</string>
```

- [ ] **Step 4: Change state and ViewModels to emit UiText**

Use these exact state types:

```kotlin
val message: UiText? = null
val nameError: UiText? = null
val otpRegexError: UiText? = null
val moneyRegexError: UiText? = null
val merchantRegexError: UiText? = null
```

Replace every message assignment with the corresponding value:

```kotlin
UiText.Res(R.string.bank_configs_select_at_least_one)
UiText.Plural(R.plurals.bank_configs_exported, count, listOf(count))
UiText.Res(R.string.bank_configs_export_failed)
UiText.Res(R.string.bank_configs_import_cannot_read)
UiText.Res(R.string.bank_configs_nothing_to_import)
UiText.Res(R.string.bank_configs_import_invalid_file)
UiText.Plural(R.plurals.bank_configs_imported, review.totalConfigs, listOf(review.totalConfigs))
```

Make regex validation return `UiText?`:

```kotlin
private fun validateRegex(pattern: String): UiText? = if (pattern.isBlank()) {
    UiText.Res(R.string.bank_config_edit_required)
} else {
    try {
        Regex(pattern)
        null
    } catch (e: PatternSyntaxException) {
        UiText.Res(R.string.bank_config_edit_invalid_regex)
    }
}
```

Set the blank-name error to `UiText.Res(R.string.bank_config_edit_name_required)`.

- [ ] **Step 5: Resolve state-backed UiText so the existing screens continue to compile**

In `BankConfigsScreen`, resolve the message only at the dialog boundary:

```kotlin
text = { Text(state.message.asString()) }
```

In `BankConfigEditScreen`, resolve the name error and change only the error parameter of `RegexField`:

```kotlin
supportingText = state.nameError?.let { error -> { Text(error.asString()) } }

@Composable
private fun RegexField(
    caption: String,
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    error: UiText?,
) {
    Column {
        Text(
            text = caption,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 4.dp),
        )
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            label = { Text(label) },
            textStyle = LocalTextStyle.current.copy(
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
            ),
            isError = error != null,
            supportingText = error?.let { message -> { Text(message.asString()) } },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
```

Import `me.nagaev.veles.common.UiText` and `me.nagaev.veles.common.asString` where needed.

- [ ] **Step 6: Run all ViewModel tests**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.*"`

Expected: all config ViewModel tests pass.

- [ ] **Step 7: Commit resource-backed config state**

```bash
git add app/src/main/res/values/strings.xml app/src/main/java/me/nagaev/veles/otp/config/viewmodel app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt app/src/test/java/me/nagaev/veles/otp/config/viewmodel
git commit -m "refactor: localize bank config state messages"
```

### Task 3: Externalize bank-config Compose copy

**Files:**
- Modify: `app/src/main/res/values/strings.xml`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt`
- Create: `app/src/androidTest/java/me/nagaev/veles/common/UiTextComposeTest.kt`

**Interfaces:**
- Consumes: `UiText.asString()`, `stringResource`, `pluralStringResource`, config state from Task 2.
- Produces: bank-config screens with no hardcoded product copy and an instrumented resolver test.

- [ ] **Step 1: Add an instrumented test for plain, formatted, and plural resolution**

```kotlin
package me.nagaev.veles.common

import androidx.compose.material3.Text
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.test.core.app.ApplicationProvider
import android.content.Context
import me.nagaev.veles.R
import org.junit.Rule
import org.junit.Test

class UiTextComposeTest {
    @get:Rule val composeRule = createComposeRule()
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun resolvesPlainFormattedAndPluralResources() {
        composeRule.setContent {
            Text(
                listOf(
                    UiText.Res(R.string.bank_configs_title).asString(),
                    UiText.Res(R.string.bank_configs_delete_title, listOf("Bank")).asString(),
                    UiText.Plural(R.plurals.bank_configs_import_title, 2, listOf(2)).asString(),
                ).joinToString("|"),
                Modifier.testTag("resolved"),
            )
        }
        composeRule.onNodeWithTag("resolved")
            .assertTextEquals(
                listOf(
                    context.getString(R.string.bank_configs_title),
                    context.getString(R.string.bank_configs_delete_title, "Bank"),
                    context.resources.getQuantityString(R.plurals.bank_configs_import_title, 2, 2),
                ).joinToString("|"),
            )
    }
}
```

- [ ] **Step 2: Run compilation and verify missing resource IDs/UI type failures**

Run: `./gradlew compileDebugAndroidTestKotlin`

Expected: compilation fails because the new screen resource IDs do not exist.

- [ ] **Step 3: Add the complete bank-config screen resource set**

```xml
<string name="bank_configs_title">Bank Templates</string>
<string name="bank_configs_import_templates">Import templates</string>
<string name="bank_configs_export_templates">Export templates</string>
<string name="bank_configs_add_template">Add template</string>
<string name="bank_configs_delete_title">Delete \"%1$s\"?</string>
<string name="bank_configs_delete_body">This template will be permanently removed.</string>
<string name="bank_configs_delete">Delete</string>
<string name="action_cancel">Cancel</string>
<string name="action_ok">OK</string>
<string name="bank_configs_export">Export</string>
<string name="bank_configs_import">Import</string>
<string name="bank_configs_import_new">New:</string>
<string name="bank_configs_import_replace">Will replace:</string>
<string name="bank_configs_list_item">- %1$s</string>
<string name="bank_configs_edit_template">Edit %1$s</string>
<string name="bank_configs_delete_template">Delete %1$s</string>
<plurals name="bank_configs_import_title">
    <item quantity="one">Import %1$d template?</item>
    <item quantity="other">Import %1$d templates?</item>
</plurals>
<string name="bank_config_edit_back">Back</string>
<string name="bank_config_edit_new_title">New Template</string>
<string name="bank_config_edit_title">Edit Template</string>
<string name="bank_config_edit_name">Name</string>
<string name="bank_config_edit_otp_caption">OTP regex - group 1: id, group 2: code</string>
<string name="bank_config_edit_otp_label">OTP Regex</string>
<string name="bank_config_edit_money_caption">Money regex - group 1: currency, group 2: amount</string>
<string name="bank_config_edit_money_label">Money Regex</string>
<string name="bank_config_edit_merchant_caption">Merchant regex - group 1: merchant name</string>
<string name="bank_config_edit_merchant_label">Merchant Regex</string>
<string name="bank_config_edit_save">Save</string>
```

- [ ] **Step 4: Resolve all BankConfigsScreen copy at render time**

Import `stringResource`, `pluralStringResource`, and `UiText.asString`. Replace each literal with its resource. Preserve dynamic values in whole resources:

```kotlin
title = { Text(stringResource(R.string.bank_configs_delete_title, state.deleteTarget.name)) }
title = {
    Text(
        pluralStringResource(
            R.plurals.bank_configs_import_title,
            review.totalConfigs,
            review.totalConfigs,
        ),
    )
}
review.toInsert.forEach { Text(stringResource(R.string.bank_configs_list_item, it.name)) }
contentDescription = stringResource(R.string.bank_configs_edit_template, config.name)
text = { Text(state.message.asString()) }
```

All remaining buttons, headings, dialog text, and content descriptions use the IDs from Step 3.

- [ ] **Step 5: Make RegexField resource-aware and resolve UiText errors**

Use resource IDs for static field metadata and `UiText?` for errors:

```kotlin
@Composable
private fun RegexField(
    @StringRes caption: Int,
    value: String,
    onValueChange: (String) -> Unit,
    @StringRes label: Int,
    error: UiText?,
) {
    Column {
        Text(
            text = stringResource(caption),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 4.dp),
        )
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            label = { Text(stringResource(label)) },
            textStyle = LocalTextStyle.current.copy(
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
            ),
            isError = error != null,
            supportingText = error?.let { message -> { Text(message.asString()) } },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
```

Call it with these exact caption/label pairs: `bank_config_edit_otp_caption`/`bank_config_edit_otp_label`, `bank_config_edit_money_caption`/`bank_config_edit_money_label`, and `bank_config_edit_merchant_caption`/`bank_config_edit_merchant_label`. The name error remains `state.nameError?.let { error -> { Text(error.asString()) } }` from Task 2.

- [ ] **Step 6: Compile and run config/UI text tests**

Run: `./gradlew testDebugUnitTest compileDebugAndroidTestKotlin`

Expected: unit tests pass and all Android test sources compile. If an emulator is available, also run `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.common.UiTextComposeTest` and expect one passing test.

- [ ] **Step 7: Commit localized bank-config screens**

```bash
git add app/src/main/res/values/strings.xml app/src/main/java/me/nagaev/veles/otp/config/ui app/src/androidTest/java/me/nagaev/veles/common/UiTextComposeTest.kt
git commit -m "refactor: externalize bank config screen copy"
```

### Task 4: Externalize permissions and sensitive-notification copy

**Files:**
- Modify: `app/src/main/res/values/strings.xml`
- Modify: `app/src/main/java/me/nagaev/veles/otp/NotificationRedactionPath.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModel.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/components/SensitiveNotificationsCard.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt`
- Modify: `app/src/test/java/me/nagaev/veles/permissions/viewmodal/PermissionsViewModelSensitiveTest.kt`
- Modify: `app/src/androidTest/java/me/nagaev/veles/permissions/ui/SensitiveNotificationsCardComposeTest.kt`
- Modify: `app/src/androidTest/java/me/nagaev/permissions/ui/VelesPermissionsAppTests.kt`

**Interfaces:**
- Consumes: `UiText.Res` and `UiText.asString()`.
- Produces: `NotificationRedactionPath.settingsLocation: UiText.Res` and `PermissionsState.redactionSettingsLocation: UiText`.

- [ ] **Step 1: Change redaction and permissions tests to assert resource identity**

Replace raw-copy tests with:

```kotlin
assertEquals(
    UiText.Res(R.string.sensitive_card_stock_settings_location),
    NotificationRedactionPath.StockAndroid.settingsLocation,
)
assertEquals(
    UiText.Res(R.string.sensitive_card_oneplus_settings_location),
    NotificationRedactionPath.OxygenOS.settingsLocation,
)
```

Delete the `explainerCopy` test. In `PermissionsViewModelSensitiveTest`, add:

```kotlin
assertEquals(
    UiText.Res(R.string.sensitive_card_stock_settings_location),
    viewModel().uiState.value.redactionSettingsLocation,
)
```

Change `SensitiveNotificationsCardComposeTest.setCard` to pass `UiText.Res(R.string.sensitive_card_stock_settings_location)`. Resolve app-test text expectations from instrumentation context:

```kotlin
private val targetContext = InstrumentationRegistry.getInstrumentation().targetContext

.assertTextContains(
    targetContext.getString(R.string.permissions_listener_enabled),
    substring = true,
)
```

- [ ] **Step 2: Run focused tests and verify the expected type failures**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.NotificationRedactionPathTest" --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelSensitiveTest" compileDebugAndroidTestKotlin`

Expected: compilation fails because the state/path still expose `String` and resource IDs are missing.

- [ ] **Step 3: Add permissions and sensitive-card resources**

Add these IDs, preserving current meaning and using corrected permission wording:

```xml
<string name="permissions_title">Veles</string>
<string name="permissions_section">PERMISSIONS</string>
<string name="permissions_listener_enabled">Notification listener enabled</string>
<string name="permissions_listener_disabled">Notification listener disabled</string>
<string name="permissions_listener_grant_hint">Grant notification access below to turn it on</string>
<string name="access_notification_permission_description">Allow Veles to read bank notifications and present their OTP details clearly.</string>
<string name="send_notification_permission_description">Allow Veles to post its own OTP notifications.</string>
<string name="sensitive_card_checking">Checking…</string>
<string name="sensitive_card_applying">Finishing setup - Android is applying the permission…</string>
<string name="sensitive_card_force_stop_help">Setup is taking longer than expected. Open App info, tap Force stop, then reopen Veles.</string>
<string name="sensitive_card_open_app_info">Open App info</string>
<string name="sensitive_card_pairing_explanation">Android only shares sensitive notifications with companion-device apps, so Veles asks to be registered as one. The system dialog will ask you to pick a nearby Bluetooth device - any device works (headphones, your car, a watch). Turn Bluetooth on first.</string>
<string name="sensitive_card_pairing_guide">Why is pairing needed?</string>
<string name="sensitive_card_enable">Enable (pair as companion)</string>
<string name="sensitive_card_more_options">More options</string>
<string name="sensitive_card_check_now">Check now</string>
<string name="sensitive_card_status_not_granted">Android hides OTP content from Veles. Bank codes can\'t be read until access is granted.</string>
<string name="sensitive_card_status_verifying">Checking whether Veles can read sensitive notifications…</string>
<string name="sensitive_card_status_granted_but_redacted">Access is granted, but this device still hides sensitive content. Try the options below.</string>
<string name="sensitive_card_status_unknown">Couldn\'t verify. Check that notification access is enabled, then try again.</string>
<string name="sensitive_card_stock_settings_location">Settings &gt; Notifications &gt; Notification access &gt; Veles &gt; Sensitive notifications</string>
<string name="sensitive_card_oneplus_settings_location">Settings &gt; Notifications &gt; Notification access &gt; Veles &gt; Enhanced Notifications</string>
<string name="sensitive_card_open_settings">Open settings</string>
<string name="sensitive_card_enhanced_explanation">Alternatively, turn off Enhanced notifications. This stops Android from hiding sensitive content - but also disables smart replies and actions for all apps.</string>
<string name="sensitive_card_enhanced_settings">Enhanced notifications settings</string>
<string name="sensitive_card_oneplus_adb_prestep">On OnePlus: first disable \'System notification optimization\' in Developer options, then run the command below.</string>
<string name="sensitive_card_adb_intro">Last resort - grant via adb:</string>
<string name="sensitive_card_full_guide">Full guide</string>
<string name="sensitive_card_copy_command">Copy command</string>
```

Keep `ADB_COMMAND` as a Kotlin constant.

- [ ] **Step 4: Make redaction settings location resource-backed**

```kotlin
sealed interface NotificationRedactionPath {
    val settingsLocation: UiText.Res
    fun settingsIntent(componentName: ComponentName): Intent

    object StockAndroid : NotificationRedactionPath {
        override val settingsLocation = UiText.Res(R.string.sensitive_card_stock_settings_location)
        // retain settingsIntent implementation
    }

    object OxygenOS : NotificationRedactionPath {
        override val settingsLocation = UiText.Res(R.string.sensitive_card_oneplus_settings_location)
        // retain settingsIntent implementation
    }
}
```

Delete `explainerCopy`. Change `PermissionsState.redactionSettingsLocation` to `UiText` with a default of `UiText.Res(R.string.sensitive_card_stock_settings_location)`; `PermissionsViewModel` continues copying `redactionPath.settingsLocation` directly.

- [ ] **Step 5: Resolve every permissions-screen literal**

Use `stringResource` in `PermissionsScreen`. Change `SensitiveNotificationsCard.settingsLocation` and `FallbackSection.settingsLocation` to `UiText`, resolve once with `val settingsLocationText = settingsLocation.asString()`, and use resource IDs for all status, button, explanation, and link text. Map status explicitly:

```kotlin
val statusText = when (state) {
    SensitiveNotificationsUiState.NotGranted -> R.string.sensitive_card_status_not_granted
    SensitiveNotificationsUiState.Verifying -> R.string.sensitive_card_status_verifying
    SensitiveNotificationsUiState.ApplyingGrant -> R.string.sensitive_card_applying
    SensitiveNotificationsUiState.GrantedButRedacted -> R.string.sensitive_card_status_granted_but_redacted
    SensitiveNotificationsUiState.Unknown -> R.string.sensitive_card_status_unknown
    SensitiveNotificationsUiState.NotApplicable,
    SensitiveNotificationsUiState.Granted -> return
}
Text(
    text = stringResource(statusText),
    fontSize = 13.sp,
    color = MaterialTheme.colorScheme.onErrorContainer,
    modifier = Modifier.testTag(TestTags.SENSITIVE_STATUS),
)
```

- [ ] **Step 6: Run focused unit tests and compile Compose tests**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.NotificationRedactionPathTest" --tests "me.nagaev.veles.permissions.viewmodal.PermissionsViewModelSensitiveTest" compileDebugAndroidTestKotlin`

Expected: focused tests pass and instrumented test sources compile. If a target is available, run `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.permissions.ui.SensitiveNotificationsCardComposeTest,me.nagaev.permissions.ui.VelesPermissionsAppTests` and expect all selected tests to pass.

- [ ] **Step 7: Commit localized permissions flows**

```bash
git add app/src/main/res/values/strings.xml app/src/main/java/me/nagaev/veles/otp/NotificationRedactionPath.kt app/src/main/java/me/nagaev/veles/permissions app/src/test/java/me/nagaev/veles/otp/NotificationRedactionPathTest.kt app/src/test/java/me/nagaev/veles/permissions app/src/androidTest/java/me/nagaev/veles/permissions app/src/androidTest/java/me/nagaev/permissions
git commit -m "refactor: externalize permissions copy"
```

### Task 5: Externalize Test screen, bottom navigation, and default sample

**Files:**
- Modify: `app/src/main/res/values/strings.xml`
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/VelesBottomBar.kt`
- Modify: `app/src/main/java/me/nagaev/veles/common/TestInputPreferences.kt`
- Modify: `app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt`
- Modify: `app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt`

**Interfaces:**
- Consumes: Compose resource APIs and `Context.getString`.
- Produces: `BottomNavDestination.labelRes: Int` and resource-backed Test-screen copy/sample.

- [ ] **Step 1: Make Compose tests derive expected static copy from resources**

Add `targetContext` through `InstrumentationRegistry` and replace embedded status words:

```kotlin
private val targetContext = InstrumentationRegistry.getInstrumentation().targetContext

.assertTextContains(
    targetContext.getString(R.string.test_screen_matched, "UOB Thailand"),
    substring = true,
)
.assertTextContains(targetContext.getString(R.string.test_screen_no_match), substring = true)
```

Keep assertions for domain data (`UOB Thailand`, OTP, received bank text) as raw test fixtures.

- [ ] **Step 2: Add navigation, Test-screen, and non-translatable sample resources**

```xml
<string name="bottom_nav_home">Home</string>
<string name="bottom_nav_templates">Templates</string>
<string name="bottom_nav_test">Test</string>
<string name="test_screen_title">Test</string>
<string name="test_screen_description">Paste a bank message to check which template matches.</string>
<string name="test_screen_notification_text">Notification text</string>
<string name="test_screen_send">Send</string>
<string name="test_screen_log_raw">Show raw notification content in logs (debug only)</string>
<string name="test_screen_matched">Matched · %1$s</string>
<string name="test_screen_payment_summary">%1$s %2$s · %3$s</string>
<string name="test_screen_no_match">No match - no bank template recognized this text</string>
<string name="test_screen_redaction_hint">This is what the listener actually received - the OS redacted the real content before Veles could read it (see the banner on Home).</string>
<string name="test_screen_received">Received: %1$s</string>
<string name="test_default_notification" translatable="false">For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone. If you didn\'t make it, call 02-285-1573.</string>
```

- [ ] **Step 3: Replace navigation labels with resource IDs**

```kotlin
data class BottomNavDestination(
    val route: String,
    @StringRes val labelRes: Int,
    val icon: ImageVector,
)

val bottomNavDestinations = listOf(
    BottomNavDestination(Routes.PERMISSIONS, R.string.bottom_nav_home, Icons.Filled.Home),
    BottomNavDestination(Routes.BANK_CONFIGS, R.string.bottom_nav_templates, Icons.AutoMirrored.Filled.ListAlt),
    BottomNavDestination(Routes.TEST, R.string.bottom_nav_test, Icons.Filled.Science),
)
```

Inside the loop, resolve once with `val label = stringResource(destination.labelRes)` and use it for both icon content description and `Text`.

- [ ] **Step 4: Replace Test-screen literals and preference default**

Use `stringResource` for all Test-screen text and complete formatted resources for matched status, payment summary, and received text:

```kotlin
text = stringResource(R.string.test_screen_matched, result.handlingResult.matchedTemplateName.orEmpty())
text = stringResource(
    R.string.test_screen_payment_summary,
    otpMessage.pay.currencyCode,
    otpMessage.pay.amount,
    otpMessage.merchant,
)
text = stringResource(R.string.test_screen_received, receivedText)
```

Keep preview fixtures as test/domain data. Load the real default sample with:

```kotlin
fun load(): String = prefs.getString(
    "test_input_text",
    context.getString(R.string.test_default_notification),
) ?: ""
```

The preference file/key constants stay exactly `test_input_preferences` and `test_input_text`.

- [ ] **Step 5: Compile and run tests**

Run: `./gradlew testDebugUnitTest compileDebugAndroidTestKotlin`

Expected: unit tests pass and Android test sources compile. If a target is available, run `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.veles.testing.ui.TestScreenComposeTest` and expect all selected tests to pass.

- [ ] **Step 6: Commit Test and navigation extraction**

```bash
git add app/src/main/res/values/strings.xml app/src/main/java/me/nagaev/veles/common/ui/VelesBottomBar.kt app/src/main/java/me/nagaev/veles/common/TestInputPreferences.kt app/src/main/java/me/nagaev/veles/testing/ui/TestScreen.kt app/src/androidTest/java/me/nagaev/veles/testing/ui/TestScreenComposeTest.kt
git commit -m "refactor: externalize test and navigation copy"
```

### Task 6: Localize notifications, channels, clipboard metadata, and system labels

**Files:**
- Modify: `app/src/main/res/values/strings.xml`
- Modify: `app/src/main/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilder.kt`
- Modify: `app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilderTest.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt`
- Modify: `app/src/test/java/me/nagaev/veles/otp/ShouldClearClipTest.kt`

**Interfaces:**
- Consumes: generated resource IDs and existing component `Context` values.
- Produces: localized posted notifications and resubmitted channel metadata with unchanged channel IDs.

- [ ] **Step 1: Change notification and clipboard tests to resolve expected resources**

Use the Robolectric application context in assertions:

```kotlin
assertEquals(context.getString(R.string.otp_notification_copy, testOtp), action.title)
assertEquals(context.getString(R.string.otp_notification_copied, testOtp), action.title)
assertEquals(
    context.getString(
        R.string.otp_notification_content,
        testOtp,
        testAmount,
        testCurrency,
    ),
    notification.extras.get(NotificationCompat.EXTRA_TEXT),
)
assertEquals(
    context.getString(R.string.otp_notification_channel_name),
    notificationManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID).name,
)
```

Rename the second-build channel test to `Channel metadata is resubmitted without changing id` and assert both the unchanged ID and resource-backed name/description. In `CopyDataReceiverTest`, stub `context.getString(R.string.otp_clipboard_label)` to return `"OTP"` and verify that value. Change `shouldClearClip` to accept `expectedLabel`; tests call `shouldClearClip(clip("OTP", ...), "OTP", expectedText)`.

- [ ] **Step 2: Run focused tests and verify failures**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.OtpNotificationBuilderTest" --tests "me.nagaev.veles.otp.CopyDataReceiverTest" --tests "me.nagaev.veles.otp.ShouldClearClipTest"`

Expected: tests fail because resource IDs and the label-aware signature do not exist.

- [ ] **Step 3: Add notification, channel, probe, clipboard, and service resources**

```xml
<string name="otp_notification_content">OTP: %1$s, Pay: %2$s %3$s</string>
<string name="otp_notification_copy">Copy %1$s</string>
<string name="otp_notification_copied">Copy %1$s Copied ✓</string>
<string name="otp_notification_channel_name">Handy OTP</string>
<string name="otp_notification_channel_description">Convenient OTP notifications from banks</string>
<string name="test_notification_probe">Veles check: code %1$d</string>
<string name="test_notification_title">Veles Test</string>
<string name="test_notification_channel_name">Veles Test</string>
<string name="test_notification_channel_description">Test notifications for verifying handler templates</string>
<string name="otp_clipboard_label" translatable="false">OTP</string>
<string name="notification_listener_service_name">Veles OTP listener</string>
```

- [ ] **Step 4: Resolve notification copy at posting time and always resubmit channels**

In `OtpNotificationBuilder`:

```kotlin
val text = context.getString(R.string.otp_notification_content, otp, amountText, currencyCode)
val actionLabel = context.getString(
    if (copied) R.string.otp_notification_copied else R.string.otp_notification_copy,
    otp,
)

private fun createOrUpdateNotificationChannel() {
    val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    val channel = NotificationChannel(
        CHANNEL_ID,
        context.getString(R.string.otp_notification_channel_name),
        NotificationManager.IMPORTANCE_HIGH,
    ).apply {
        description = context.getString(R.string.otp_notification_channel_description)
    }
    manager.createNotificationChannel(channel)
}
```

Call `createOrUpdateNotificationChannel()` on every build; remove the existing-channel early return. Apply the same pattern in `TestNotificationSender`, and build the probe with:

```kotlin
val code = (PROBE_CODE_RANGE_START..PROBE_CODE_RANGE_END).random()
val text = context.getString(R.string.test_notification_probe, code)
```

- [ ] **Step 5: Resolve and compare the clipboard label consistently**

```kotlin
internal fun shouldClearClip(
    clip: ClipData?,
    expectedLabel: String,
    expectedText: String,
): Boolean {
    if (clip == null || clip.itemCount == 0) return false
    if (clip.description.label != expectedLabel) return false
    return clip.getItemAt(0).text?.toString() == expectedText
}
```

In `onReceive`, resolve `val clipLabel = context.getString(R.string.otp_clipboard_label)`, use it in `ClipData.newPlainText`, and capture it in the delayed callback to call `shouldClearClip(clipboardManager.primaryClip, clipLabel, otp)`.

- [ ] **Step 6: Run notification/clipboard tests**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.OtpNotificationBuilderTest" --tests "me.nagaev.veles.otp.CopyDataReceiverTest" --tests "me.nagaev.veles.otp.ShouldClearClipTest"`

Expected: all selected tests pass, including resource-backed text and stable channel IDs.

- [ ] **Step 7: Commit system-surface localization**

```bash
git add app/src/main/res/values/strings.xml app/src/main/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilder.kt app/src/main/java/me/nagaev/veles/testing/TestNotificationSender.kt app/src/main/java/me/nagaev/veles/otp/CopyDataReceiver.kt app/src/test/java/me/nagaev/veles/otp/handlers/OtpNotificationBuilderTest.kt app/src/test/java/me/nagaev/veles/otp/CopyDataReceiverTest.kt app/src/test/java/me/nagaev/veles/otp/ShouldClearClipTest.kt
git commit -m "refactor: localize notification system surfaces"
```

### Task 7: Declare locales, remove dead resources, and audit the result

**Files:**
- Create: `app/src/main/res/xml/locales_config.xml`
- Modify: `app/src/main/AndroidManifest.xml`
- Modify: `app/build.gradle.kts`
- Modify: `app/src/main/res/values/strings.xml`
- Modify: `app/lint-baseline.xml` only if running lint removes obsolete `UnusedResources` entries automatically or the now-fixed hardcoded-string entries are still baselined.

**Interfaces:**
- Consumes: the complete resource set from Tasks 1-6.
- Produces: English-only app locale metadata, English-only packaged resources, and a clean production-source audit.

- [ ] **Step 1: Add a failing manifest/resource test through the build**

Add `android:localeConfig="@xml/locales_config"` to `<application>` before creating the XML file, and add this Gradle block inside `android`:

```kotlin
androidResources {
    localeFilters += listOf("en")
}
```

- [ ] **Step 2: Run resource processing and verify the missing locale config failure**

Run: `./gradlew processDebugResources`

Expected: resource linking fails because `@xml/locales_config` does not exist.

- [ ] **Step 3: Create the explicit locale configuration**

```xml
<?xml version="1.0" encoding="utf-8"?>
<locale-config xmlns:android="http://schemas.android.com/apk/res/android">
    <locale android:name="en" />
</locale-config>
```

Do not enable AGP automatic locale-config generation.

- [ ] **Step 4: Remove dead templates and stabilize technical resource values**

Delete unused Android Studio template IDs: `navigation_drawer_open`, `navigation_drawer_close`, `nav_header_title`, `nav_header_subtitle`, `nav_header_desc`, `action_settings`, `menu_home`, `menu_gallery`, `menu_slideshow`, and unused `permissions` if `rg` confirms no references.

Keep these exact persisted values but mark them non-translatable if they remain resources:

```xml
<string name="notification_listener_connected" translatable="false">notification_listener_connected</string>
<string name="notification_listener_preferences" translatable="false">notification_listener_preferences</string>
```

- [ ] **Step 5: Run a source audit and classify every remaining literal**

Run:

```bash
rg -n '"[^"\n]*[A-Za-z][^"\n]*"' app/src/main/java --glob '*.kt'
```

Expected remaining matches are limited to:

- Routes, channel IDs, URI/MIME values, intent actions/extras, preference keys, adb command, logs, and package/API identifiers.
- Regexes, dynamic defaults such as empty strings, and domain/test data in previews.
- Exception messages intended only for developers, such as unsupported enum branches.

For each other match, add a named resource and replace the literal before continuing. Specifically verify no unclassified literals remain in `VelesBottomBar.kt`, `PermissionsScreen.kt`, `SensitiveNotificationsCard.kt`, `BankConfigsScreen.kt`, `BankConfigEditScreen.kt`, `TestScreen.kt`, `OtpNotificationBuilder.kt`, `TestNotificationSender.kt`, `CopyDataReceiver.kt`, either config ViewModel, or `NotificationRedactionPath.kt`.

- [ ] **Step 6: Verify XML and locale consistency**

Run:

```bash
rg -n '<string|<plurals|<item' app/src/main/res/values/strings.xml
rg -n 'localeConfig|localeFilters|android:name="en"' app/src/main/AndroidManifest.xml app/build.gradle.kts app/src/main/res/xml/locales_config.xml
```

Expected: all product copy is in `strings.xml`; `en` appears in both locale configuration locations; the manifest references `@xml/locales_config`; no dead template resources remain.

- [ ] **Step 7: Run the complete verification suite**

Run: `./gradlew testDebugUnitTest assembleDebug lintDebug compileDebugAndroidTestKotlin`

Expected: all tasks complete successfully with no test, compilation, resource, lint, or detekt regression. If an Android target is connected, additionally run `./gradlew connectedDebugAndroidTest` and expect all instrumented tests to pass; otherwise record that only instrumented execution was unavailable, while instrumented sources compiled.

- [ ] **Step 8: Review the final diff for scope and persistence safety**

Run:

```bash
git diff master...HEAD -- app/src/main app/src/test app/src/androidTest app/build.gradle.kts app/lint-baseline.xml
git status --short
```

Expected: only localization, related tests, explicit locale plumbing, approved English corrections, and documentation are changed. Confirm channel IDs and preference values are unchanged.

- [ ] **Step 9: Commit locale plumbing and cleanup**

```bash
git add app/src/main/res/xml/locales_config.xml app/src/main/AndroidManifest.xml app/build.gradle.kts app/src/main/res/values/strings.xml app/lint-baseline.xml
git commit -m "build: declare supported app locales"
```

If `app/lint-baseline.xml` is unchanged, omit it from `git add`.
