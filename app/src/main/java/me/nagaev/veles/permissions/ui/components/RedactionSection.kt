package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.ui.TestTags

@Composable
fun RedactionSection(
    state: RedactionState,
    settingsLocation: String,
    onOpenSettings: () -> Unit,
    onTestSensitiveReading: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth().padding(horizontal = 10.dp)) {
        when (state) {
            RedactionState.Unknown -> {
                Text(
                    modifier = Modifier.testTag(TestTags.REDACTION_STATUS),
                    text = "Sensitive notification access: Not yet checked",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.secondary,
                )
                Spacer(Modifier.height(4.dp))
                TextButton(
                    onClick = onTestSensitiveReading,
                    modifier = Modifier.testTag(TestTags.REDACTION_TEST_BUTTON),
                ) { Text("Run a test") }
            }
            RedactionState.Readable -> {
                Text(
                    modifier = Modifier.testTag(TestTags.REDACTION_STATUS),
                    text = "Sensitive notification access: \u2713 Readable",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.secondary,
                )
            }
            RedactionState.Hidden -> {
                Text(
                    modifier = Modifier.testTag(TestTags.REDACTION_STATUS),
                    text = "\u26A0 Sensitive notification access is off",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
                Spacer(Modifier.height(4.dp))
                if (settingsLocation.isNotBlank()) {
                    Text(
                        text = "Open: $settingsLocation",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(8.dp))
                }
                Row {
                    Button(
                        onClick = onOpenSettings,
                        modifier = Modifier.testTag(TestTags.REDACTION_OPEN_SETTINGS),
                    ) { Text("Open settings") }
                    Spacer(Modifier.padding(horizontal = 4.dp))
                    TextButton(
                        onClick = onTestSensitiveReading,
                        modifier = Modifier.testTag(TestTags.REDACTION_TEST_BUTTON),
                    ) { Text("Test sensitive reading") }
                }
            }
        }
    }
}
