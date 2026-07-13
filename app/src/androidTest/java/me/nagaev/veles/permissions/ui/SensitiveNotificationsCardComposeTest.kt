package me.nagaev.veles.permissions.ui

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import me.nagaev.veles.common.VelesLinks
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.SensitiveNotificationsCard
import me.nagaev.veles.permissions.viewmodal.SensitiveNotificationsUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class SensitiveNotificationsCardComposeTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    private val openedUris = mutableListOf<String>()
    private val uriHandler = object : UriHandler {
        override fun openUri(uri: String) {
            openedUris += uri
        }
    }

    private fun setCard(
        state: SensitiveNotificationsUiState,
        cdmSupported: Boolean = true,
        showForceStopButton: Boolean = false,
        revealFallbacks: Boolean = false,
        onEnable: () -> Unit = {},
        onVerify: () -> Unit = {},
        onOpenAppInfo: () -> Unit = {},
    ) {
        openedUris.clear()
        composeTestRule.setContent {
            CompositionLocalProvider(LocalUriHandler provides uriHandler) {
                SensitiveNotificationsCard(
                    state = state,
                    cdmSupported = cdmSupported,
                    settingsLocation = "Settings > Notifications",
                    showOnePlusAdbPreStep = false,
                    revealFallbacks = revealFallbacks,
                    showForceStopButton = showForceStopButton,
                    onEnableViaCompanion = onEnable,
                    onOpenSettings = {},
                    onOpenEnhancedSettings = {},
                    onVerify = onVerify,
                    onOpenAppInfo = onOpenAppInfo,
                )
            }
        }
    }

    @Test
    fun pairingGuideOpensPairingAnchor() {
        setCard(SensitiveNotificationsUiState.NotGranted)

        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).performClick()

        assertEquals(listOf(VelesLinks.PAIRING), openedUris)
    }

    @Test
    fun pairingGuideHiddenWithoutCompanionSupport() {
        setCard(SensitiveNotificationsUiState.NotGranted, cdmSupported = false)

        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).assertDoesNotExist()
    }

    @Test
    fun unknownShowsPairingGuide() {
        setCard(SensitiveNotificationsUiState.Unknown)

        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).assertIsDisplayed()
    }

    @Test
    fun adbGuideAppearsWithFallbacksAndOpensAdbAnchor() {
        setCard(SensitiveNotificationsUiState.NotGranted, revealFallbacks = true)

        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).performClick()

        assertEquals(listOf(VelesLinks.ADB), openedUris)
    }

    @Test
    fun adbGuideHiddenWhileFallbacksCollapsed() {
        setCard(SensitiveNotificationsUiState.NotGranted)

        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertDoesNotExist()
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
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_COPY).assertIsDisplayed()
    }

    @Test
    fun grantedButRedactedShowsFallbacksAndVerify() {
        var verified = false
        setCard(SensitiveNotificationsUiState.GrantedButRedacted, onVerify = { verified = true })
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_OPEN_SETTINGS).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_VERIFY_BUTTON).performClick()
        assertTrue(verified)
    }

    @Test
    fun verifyingHidesPairingAndAdbGuides() {
        setCard(SensitiveNotificationsUiState.Verifying)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_VERIFY_BUTTON).assertDoesNotExist()
    }

    @Test
    fun applyingGrantHidesPairingAndAdbGuides() {
        setCard(SensitiveNotificationsUiState.ApplyingGrant)
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertIsDisplayed()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertDoesNotExist()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_VERIFY_BUTTON).assertDoesNotExist()
    }

    @Test
    fun forceStopFallbackOpensAppInfo() {
        var opened = false
        setCard(
            state = SensitiveNotificationsUiState.Unknown,
            showForceStopButton = true,
            onOpenAppInfo = { opened = true },
        )

        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_FORCE_STOP_BUTTON).performClick()

        assertTrue(opened)
    }
}
