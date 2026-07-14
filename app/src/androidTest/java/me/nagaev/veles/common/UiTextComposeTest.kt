package me.nagaev.veles.common

import android.content.Context
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.test.core.app.ApplicationProvider
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
                    UiText.Plural(
                        R.plurals.ui_text_test_plural_with_label,
                        2,
                        listOf(2, "selected"),
                    ).asString(),
                ).joinToString("|"),
                Modifier.testTag("resolved"),
            )
        }
        composeRule.onNodeWithTag("resolved")
            .assertTextEquals(
                listOf(
                    context.getString(R.string.bank_configs_title),
                    context.getString(R.string.bank_configs_delete_title, "Bank"),
                    context.resources.getQuantityString(
                        R.plurals.ui_text_test_plural_with_label,
                        2,
                        2,
                        "selected",
                    ),
                ).joinToString("|"),
            )
    }
}
