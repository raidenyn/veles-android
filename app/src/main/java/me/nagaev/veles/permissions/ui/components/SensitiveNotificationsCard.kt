package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.viewmodal.SensitiveNotificationsUiState

private const val ADB_COMMAND =
    "adb shell appops set me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow"

@Composable
fun SensitiveNotificationsCard(
    state: SensitiveNotificationsUiState,
    cdmSupported: Boolean,
    settingsLocation: String,
    showOnePlusAdbPreStep: Boolean,
    onEnableViaCompanion: () -> Unit,
    onOpenSettings: () -> Unit,
    onOpenEnhancedSettings: () -> Unit,
    onVerify: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state == SensitiveNotificationsUiState.NotApplicable ||
        state == SensitiveNotificationsUiState.Granted
    ) {
        return
    }
    var fallbacksExpanded by rememberSaveable { mutableStateOf(false) }
    val showFallbacks =
        fallbacksExpanded ||
            state == SensitiveNotificationsUiState.GrantedButRedacted ||
            !cdmSupported
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
        ),
        modifier = modifier
            .fillMaxWidth()
            .testTag(TestTags.SENSITIVE_CARD),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            StatusRow(state)
            Spacer(Modifier.height(12.dp))
            if (state == SensitiveNotificationsUiState.Verifying) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Checking…", fontSize = 13.sp, color = MaterialTheme.colorScheme.onErrorContainer)
                }
            } else {
                if (cdmSupported && state == SensitiveNotificationsUiState.NotGranted) {
                    Text(
                        text = "Android only shares sensitive notifications with companion-device apps, " +
                            "so Veles asks to be registered as one. The system dialog will ask you to pick " +
                            "a nearby Bluetooth device — any device works (headphones, your car, a watch). " +
                            "Turn Bluetooth on first.",
                        fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = onEnableViaCompanion,
                        shape = MaterialTheme.shapes.small,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error,
                            contentColor = MaterialTheme.colorScheme.onError,
                        ),
                        modifier = Modifier.testTag(TestTags.SENSITIVE_ENABLE_BUTTON),
                    ) { Text("Enable (pair as companion)") }
                    Spacer(Modifier.height(8.dp))
                }
                if (showFallbacks) {
                    FallbackSection(
                        settingsLocation = settingsLocation,
                        showOnePlusAdbPreStep = showOnePlusAdbPreStep,
                        onOpenSettings = onOpenSettings,
                        onOpenEnhancedSettings = onOpenEnhancedSettings,
                    )
                } else {
                    TextButton(
                        onClick = { fallbacksExpanded = true },
                        modifier = Modifier.testTag(TestTags.SENSITIVE_FALLBACKS_TOGGLE),
                    ) { Text("More options") }
                }
                OutlinedButton(
                    onClick = onVerify,
                    shape = MaterialTheme.shapes.small,
                    modifier = Modifier.testTag(TestTags.SENSITIVE_VERIFY_BUTTON),
                ) { Text("Check now") }
            }
        }
    }
}

@Composable
private fun StatusRow(state: SensitiveNotificationsUiState) {
    Row(verticalAlignment = Alignment.Top) {
        Icon(
            imageVector = Icons.Filled.Warning,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = when (state) {
                SensitiveNotificationsUiState.NotGranted ->
                    "Android hides OTP content from Veles. Bank codes can't be read until access is granted."
                SensitiveNotificationsUiState.Verifying ->
                    "Checking whether Veles can read sensitive notifications…"
                SensitiveNotificationsUiState.GrantedButRedacted ->
                    "Access is granted, but this device still hides sensitive content. Try the options below."
                SensitiveNotificationsUiState.Unknown ->
                    "Couldn't verify. Check that notification access is enabled, then try again."
                else -> ""
            },
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.testTag(TestTags.SENSITIVE_STATUS),
        )
    }
}

@Composable
private fun FallbackSection(
    settingsLocation: String,
    showOnePlusAdbPreStep: Boolean,
    onOpenSettings: () -> Unit,
    onOpenEnhancedSettings: () -> Unit,
) {
    val clipboard = LocalClipboardManager.current
    Column {
        if (settingsLocation.isNotBlank()) {
            Text(
                text = settingsLocation,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(4.dp))
            OutlinedButton(
                onClick = onOpenSettings,
                shape = MaterialTheme.shapes.small,
                modifier = Modifier.testTag(TestTags.SENSITIVE_OPEN_SETTINGS),
            ) { Text("Open settings") }
            Spacer(Modifier.height(12.dp))
        }
        Text(
            text = "Alternatively, turn off Enhanced notifications. This stops Android from hiding " +
                "sensitive content — but also disables smart replies and actions for all apps.",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        Spacer(Modifier.height(4.dp))
        OutlinedButton(
            onClick = onOpenEnhancedSettings,
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.testTag(TestTags.SENSITIVE_ENHANCED_SETTINGS),
        ) { Text("Enhanced notifications settings") }
        Spacer(Modifier.height(12.dp))
        if (showOnePlusAdbPreStep) {
            Text(
                text = "On OnePlus: first disable 'System notification optimization' in " +
                    "Developer options, then run the command below.",
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(4.dp))
        }
        Text(
            text = "Last resort — grant via adb (see README for full steps):",
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        Text(
            text = ADB_COMMAND,
            fontFamily = FontFamily.Monospace,
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        Spacer(Modifier.height(4.dp))
        OutlinedButton(
            onClick = { clipboard.setText(AnnotatedString(ADB_COMMAND)) },
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.testTag(TestTags.SENSITIVE_ADB_COPY),
        ) { Text("Copy command") }
        Spacer(Modifier.height(8.dp))
    }
}