package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
    modifier: Modifier = Modifier,
) {
    if (state != RedactionState.Hidden) return
    Column(modifier = modifier.fillMaxWidth().padding(horizontal = 10.dp)) {
        if (settingsLocation.isNotBlank()) {
            Text(
                text = settingsLocation,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(8.dp))
        }
        Button(
            onClick = onOpenSettings,
            modifier = Modifier.testTag(TestTags.REDACTION_OPEN_SETTINGS),
        ) { Text("Open settings") }
    }
}
