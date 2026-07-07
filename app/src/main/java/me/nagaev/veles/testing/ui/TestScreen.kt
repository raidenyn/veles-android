package me.nagaev.veles.testing.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.BuildConfig
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.testing.viewmodel.TestState

private val MATCHED_COLOR = Color(0xFF4CAF50)

@Composable
fun TestScreen(
    state: TestState,
    onTextChanged: (String) -> Unit,
    onSend: () -> Unit,
    logRawContent: Boolean,
    onLogRawContentToggled: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp)
            .statusBarsPadding(),
    ) {
        Text(
            text = "Test",
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(vertical = 10.dp),
        )
        OutlinedTextField(
            value = state.inputText,
            onValueChange = onTextChanged,
            label = { Text("Notification text") },
            minLines = 3,
            modifier = Modifier
                .fillMaxWidth()
                .testTag(TestTags.TEST_INPUT),
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSend,
            enabled = state.inputText.isNotBlank(),
            modifier = Modifier.testTag(TestTags.TEST_SEND_BUTTON),
        ) {
            Text("Send")
        }
        Spacer(Modifier.height(16.dp))
        if (BuildConfig.DEBUG) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Switch(
                    checked = logRawContent,
                    onCheckedChange = onLogRawContentToggled,
                    modifier = Modifier.testTag(TestTags.TEST_LOG_RAW_CONTENT_SWITCH),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "Show raw notification content in logs (debug only)",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        state.lastResult?.let { result ->
            Spacer(Modifier.height(16.dp))
            ResultCard(result)
        }
    }
}

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
        logRawContent = false,
        onLogRawContentToggled = {},
    )
}
