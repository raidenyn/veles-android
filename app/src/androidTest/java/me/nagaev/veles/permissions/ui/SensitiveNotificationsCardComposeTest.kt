package me.nagaev.veles.permissions.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.SensitiveNotificationsCard
import me.nagaev.veles.permissions.viewmodal.SensitiveNotificationsUiState
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class SensitiveNotificationsCardComposeTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    private fun setCard(
        state: SensitiveNotificationsUiState,
        cdmSupported: Boolean = true,
        onEnable: () -> Unit = {},
        onVerify: () -> Unit = {},
    ) {
        composeTestRule.setContent {
            SensitiveNotificationsCard(
                state = state,
                cdmSupported = cdmSupported,
                settingsLocation = "Settings > Notifications",
                showOnePlusAdbPreStep = false,
                onEnableViaCompanion = onEnable,
                onOpenSettings = {},
                onOpenEnhancedSettings = {},
                onVerify = onVerify,
                onRestart = {},
            )
        }
    }

    @Test
    fun cardHiddenWhenNotApplicable() {
        setCard(SensitiveNotificationsUiState.NotApplicable)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertDoesNotExist()
    }

    @Test
    fun cardHiddenWhenGranted() {
        setCard(SensitiveNotificationsUiState.Granted)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertDoesNotExist()
    }

    @Test
    fun notGrantedShowsEnableButtonAndTriggersCallback() {
        var enabled = false
        setCard(SensitiveNotificationsUiState.NotGranted, onEnable = { enabled = true })
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).performClick()
        assertTrue(enabled)
    }

    @Test
    fun fallbacksHiddenBehindToggleWhenCdmSupported() {
        setCard(SensitiveNotificationsUiState.NotGranted)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_COPY).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_FALLBACKS_TOGGLE).performClick()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_COPY).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENHANCED_SETTINGS).assertIsDisplayed()
    }

    @Test
    fun fallbacksShownImmediatelyWhenCdmUnsupported() {
        setCard(SensitiveNotificationsUiState.NotGranted, cdmSupported = false)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_COPY).assertIsDisplayed()
    }

    @Test
    fun grantedButRedactedShowsFallbacksAndVerify() {
        var verified = false
        setCard(SensitiveNotificationsUiState.GrantedButRedacted, onVerify = { verified = true })
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_OPEN_SETTINGS).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_VERIFY_BUTTON).performClick()
        assertTrue(verified)
    }

    @Test
    fun verifyingShowsProgressWithoutButtons() {
        setCard(SensitiveNotificationsUiState.Verifying)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_VERIFY_BUTTON).assertDoesNotExist()
    }
}
