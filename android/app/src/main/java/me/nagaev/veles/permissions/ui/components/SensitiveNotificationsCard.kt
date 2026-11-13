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
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.R
import me.nagaev.veles.common.UiText
import me.nagaev.veles.common.VelesLinks
import me.nagaev.veles.common.asString
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.viewmodal.SensitiveNotificationsUiState

private const val ADB_COMMAND =
    "adb shell appops set me.nagaev.veles RECEIVE_SENSITIVE_NOTIFICATIONS allow"

@Suppress("LongParameterList")
@Composable
fun SensitiveNotificationsCard(
    state: SensitiveNotificationsUiState,
    cdmSupported: Boolean,
    settingsLocation: UiText,
    showOnePlusAdbPreStep: Boolean,
    revealFallbacks: Boolean = false,
    showForceStopButton: Boolean,
    onEnableViaCompanion: () -> Unit,
    onOpenSettings: () -> Unit,
    onOpenEnhancedSettings: () -> Unit,
    onVerify: () -> Unit,
    onOpenAppInfo: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state == SensitiveNotificationsUiState.NotApplicable ||
        state == SensitiveNotificationsUiState.Granted
    ) {
        return
    }
    val uriHandler = LocalUriHandler.current
    var fallbacksExpanded by rememberSaveable { mutableStateOf(false) }
    val showFallbacks =
        fallbacksExpanded ||
            revealFallbacks ||
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
            if (state == SensitiveNotificationsUiState.Verifying ||
                state == SensitiveNotificationsUiState.ApplyingGrant
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = if (state == SensitiveNotificationsUiState.Verifying) {
                            stringResource(R.string.sensitive_card_checking)
                        } else {
                            stringResource(R.string.sensitive_card_applying)
                        },
                        fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
            } else {
                if (showForceStopButton) {
                    Text(
                        text = stringResource(R.string.sensitive_card_force_stop_help),
                        fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                    Spacer(Modifier.height(4.dp))
                    OutlinedButton(
                        onClick = onOpenAppInfo,
                        shape = MaterialTheme.shapes.small,
                        modifier = Modifier.testTag(TestTags.SENSITIVE_FORCE_STOP_BUTTON),
                    ) { Text(stringResource(R.string.sensitive_card_open_app_info)) }
                    Spacer(Modifier.height(8.dp))
                }
                if (cdmSupported && (state == SensitiveNotificationsUiState.NotGranted || state == SensitiveNotificationsUiState.Unknown)) {
                    Text(
                        text = stringResource(R.string.sensitive_card_pairing_explanation),
                        fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                    TextButton(
                        onClick = { uriHandler.openUri(VelesLinks.PAIRING) },
                        modifier = Modifier.testTag(TestTags.SENSITIVE_PAIRING_GUIDE),
                    ) { Text(stringResource(R.string.sensitive_card_pairing_guide)) }
                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = onEnableViaCompanion,
                        shape = MaterialTheme.shapes.small,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error,
                            contentColor = MaterialTheme.colorScheme.onError,
                        ),
                        modifier = Modifier.testTag(TestTags.SENSITIVE_ENABLE_BUTTON),
                    ) { Text(stringResource(R.string.sensitive_card_enable)) }
                    Spacer(Modifier.height(8.dp))
                }
                if (showFallbacks) {
                    FallbackSection(
                        settingsLocation = settingsLocation,
                        showOnePlusAdbPreStep = showOnePlusAdbPreStep,
                        onOpenSettings = onOpenSettings,
                        onOpenEnhancedSettings = onOpenEnhancedSettings,
                        onOpenAdbGuide = { uriHandler.openUri(VelesLinks.ADB) },
                    )
                } else {
                    TextButton(
                        onClick = { fallbacksExpanded = true },
                        modifier = Modifier.testTag(TestTags.SENSITIVE_FALLBACKS_TOGGLE),
                    ) { Text(stringResource(R.string.sensitive_card_more_options)) }
                }
                OutlinedButton(
                    onClick = onVerify,
                    shape = MaterialTheme.shapes.small,
                    modifier = Modifier.testTag(TestTags.SENSITIVE_VERIFY_BUTTON),
                ) { Text(stringResource(R.string.sensitive_card_check_now)) }
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
            text = stringResource(
                when (state) {
                    SensitiveNotificationsUiState.NotGranted -> R.string.sensitive_card_status_not_granted
                    SensitiveNotificationsUiState.Verifying -> R.string.sensitive_card_status_verifying
                    SensitiveNotificationsUiState.ApplyingGrant -> R.string.sensitive_card_applying
                    SensitiveNotificationsUiState.GrantedButRedacted -> R.string.sensitive_card_status_granted_but_redacted
                    SensitiveNotificationsUiState.Unknown -> R.string.sensitive_card_status_unknown
                    SensitiveNotificationsUiState.NotApplicable,
                    SensitiveNotificationsUiState.Granted,
                    -> return
                },
            ),
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.testTag(TestTags.SENSITIVE_STATUS),
        )
    }
}

@Composable
private fun FallbackSection(
    settingsLocation: UiText,
    showOnePlusAdbPreStep: Boolean,
    onOpenSettings: () -> Unit,
    onOpenEnhancedSettings: () -> Unit,
    onOpenAdbGuide: () -> Unit,
) {
    val clipboard = LocalClipboardManager.current
    val settingsLocationText = settingsLocation.asString()
    Column {
        if (settingsLocationText.isNotBlank()) {
            Text(
                text = settingsLocationText,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(4.dp))
            OutlinedButton(
                onClick = onOpenSettings,
                shape = MaterialTheme.shapes.small,
                modifier = Modifier.testTag(TestTags.SENSITIVE_OPEN_SETTINGS),
            ) { Text(stringResource(R.string.sensitive_card_open_settings)) }
            Spacer(Modifier.height(12.dp))
        }
        Text(
            text = stringResource(R.string.sensitive_card_enhanced_explanation),
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        Spacer(Modifier.height(4.dp))
        OutlinedButton(
            onClick = onOpenEnhancedSettings,
            shape = MaterialTheme.shapes.small,
            modifier = Modifier.testTag(TestTags.SENSITIVE_ENHANCED_SETTINGS),
        ) { Text(stringResource(R.string.sensitive_card_enhanced_settings)) }
        Spacer(Modifier.height(12.dp))
        if (showOnePlusAdbPreStep) {
            Text(
                text = stringResource(R.string.sensitive_card_oneplus_adb_prestep),
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(4.dp))
        }
        Text(
            text = stringResource(R.string.sensitive_card_adb_intro),
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        TextButton(
            onClick = onOpenAdbGuide,
            modifier = Modifier.testTag(TestTags.SENSITIVE_ADB_GUIDE),
        ) { Text(stringResource(R.string.sensitive_card_full_guide)) }
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
        ) { Text(stringResource(R.string.sensitive_card_copy_command)) }
        Spacer(Modifier.height(8.dp))
    }
}
