package me.nagaev.veles.testing.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
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
    fun `matched result renders status - template name and received text`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = matchedResult),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT)
            .assertIsDisplayed()
            .assertTextContains("Matched", substring = true)
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_TEMPLATE)
            .assertIsDisplayed()
            .assertTextContains("UOB Thailand", substring = true)
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("For purchase THB600.00", substring = true)
    }

    @Test
    fun `filtered result renders status and received text - no template`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = filteredResult),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT)
            .assertIsDisplayed()
            .assertTextContains("No match", substring = true)
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_TEMPLATE)
            .assertDoesNotExist()
        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("some unrelated text", substring = true)
    }

    @Test
    fun `redacted received text is displayed verbatim`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", lastResult = redactedResult),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_RESULT_RECEIVED_TEXT)
            .assertIsDisplayed()
            .assertTextContains("Sensitive notification content hidden", substring = true)
    }

    @Test
    fun `raw content switch is displayed and reflects state when off`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", logRawContent = false),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_LOG_RAW_CONTENT_SWITCH)
            .assertIsDisplayed()
            .assertIsOff()
    }

    @Test
    fun `raw content switch is on when state is true`() {
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", logRawContent = true),
                onTextChanged = {},
                onSend = {},
                logRawContent = true,
                onLogRawContentToggled = {},
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_LOG_RAW_CONTENT_SWITCH)
            .assertIsDisplayed()
            .assertIsOn()
    }

    @Test
    fun `raw content switch click calls onLogRawContentToggled`() {
        var toggled = false
        composeTestRule.setContent {
            TestScreen(
                state = TestState(inputText = "input", logRawContent = false),
                onTextChanged = {},
                onSend = {},
                logRawContent = false,
                onLogRawContentToggled = { toggled = true },
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.TEST_LOG_RAW_CONTENT_SWITCH)
            .performClick()

        assert(toggled) { "onLogRawContentToggled should have been called with true" }
    }
}
