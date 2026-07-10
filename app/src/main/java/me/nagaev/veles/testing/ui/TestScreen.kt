package me.nagaev.veles.testing.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.BuildConfig
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.Money
import me.nagaev.veles.otp.handlers.Otp
import me.nagaev.veles.otp.handlers.OtpMessage
import me.nagaev.veles.testing.viewmodel.TestState
import java.math.BigDecimal

private const val RECEIVED_TEXT_ALPHA = 0.75f

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
            .statusBarsPadding()
            .verticalScroll(rememberScrollState()),
    ) {
        Text(
            text = "Test",
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(top = 20.dp, bottom = 4.dp),
        )
        Text(
            text = "Paste a bank message to check which template matches.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 12.dp),
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
            shape = MaterialTheme.shapes.small,
            modifier = Modifier
                .fillMaxWidth()
                .testTag(TestTags.TEST_SEND_BUTTON),
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
            ResultCard(result = result, typedText = state.inputText)
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
private fun ResultCard(
    result: TestResult,
    typedText: String,
) {
    when (result.handlingResult.status) {
        MessageHandlingResult.Status.ACCEPTED -> MatchedCard(result)
        MessageHandlingResult.Status.FILTERED -> NoMatchCard(result, typedText)
    }
}

@Composable
private fun MatchedCard(result: TestResult) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.tertiaryContainer,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(color = MaterialTheme.colorScheme.tertiary)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "Matched · ${result.handlingResult.matchedTemplateName.orEmpty()}",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onTertiaryContainer,
                    modifier = Modifier.testTag(TestTags.TEST_RESULT),
                )
            }
            result.handlingResult.otpMessage?.let { otpMessage ->
                Spacer(Modifier.height(12.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(
                            color = MaterialTheme.colorScheme.surface,
                            shape = MaterialTheme.shapes.small,
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = otpMessage.otp.value,
                        fontSize = 24.sp,
                        fontFamily = FontFamily.Monospace,
                        letterSpacing = 2.sp,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier
                            .padding(vertical = 12.dp)
                            .testTag(TestTags.TEST_RESULT_OTP),
                    )
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "${otpMessage.pay.currencyCode} ${otpMessage.pay.amount} · ${otpMessage.merchant}",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onTertiaryContainer,
                )
            }
            Spacer(Modifier.height(12.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.onTertiaryContainer.copy(alpha = 0.2f))
            Spacer(Modifier.height(8.dp))
            ReceivedTextLine(
                receivedText = result.receivedText,
                color = MaterialTheme.colorScheme.onTertiaryContainer,
            )
        }
    }
}

@Composable
private fun NoMatchCard(
    result: TestResult,
    typedText: String,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(color = MaterialTheme.colorScheme.outline)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "No match — no bank template recognized this text",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag(TestTags.TEST_RESULT),
                )
            }
            Spacer(Modifier.height(8.dp))
            ReceivedTextLine(
                receivedText = result.receivedText,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (result.receivedText != typedText) {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "This is what the listener actually received — the OS redacted " +
                        "the real content before Veles could read it (see the banner on Home).",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.testTag(TestTags.TEST_RESULT_REDACTION_HINT),
                )
            }
        }
    }
}

@Composable
private fun ReceivedTextLine(
    receivedText: String,
    color: Color,
) {
    Text(
        text = "Received: $receivedText",
        fontSize = 12.sp,
        fontFamily = FontFamily.Monospace,
        color = color.copy(alpha = RECEIVED_TEXT_ALPHA),
        modifier = Modifier.testTag(TestTags.TEST_RESULT_RECEIVED_TEXT),
    )
}

@Composable
private fun StatusDot(color: Color) {
    Box(
        Modifier
            .size(8.dp)
            .background(color = color, shape = CircleShape),
    )
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
                    OtpMessage(
                        otp = Otp(value = "511066", id = "VjKp"),
                        pay = Money(amount = BigDecimal("600.00"), currencyCode = "THB"),
                        merchant = "WWWSFCINEMACITYCOMCORP",
                    ),
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
