package me.nagaev.veles.testing.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
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
        state.lastResult?.let { result ->
            Spacer(Modifier.height(16.dp))
            ResultBadge(result)
        }
    }
}

@Composable
private fun ResultBadge(result: TestResult) {
    val (label, color) = when (result.result) {
        MessageHandlingResult.ACCEPTED -> "Matched ✓" to MATCHED_COLOR
        MessageHandlingResult.FILTERED -> "No match" to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text(
        text = label,
        color = color,
        style = MaterialTheme.typography.bodyLarge,
        modifier = Modifier.testTag(TestTags.TEST_RESULT),
    )
}

@Preview(showBackground = true)
@Composable
@Suppress("MaxLineLength")
fun TestScreenPreview() {
    TestScreen(
        state = TestState(inputText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone. If you didn't make it, call 02-285-1573."),
        onTextChanged = {},
        onSend = {},
    )
}
