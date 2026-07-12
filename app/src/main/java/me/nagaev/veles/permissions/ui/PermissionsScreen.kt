package me.nagaev.veles.permissions.ui

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.ui.components.AccessNotificationPermission
import me.nagaev.veles.permissions.ui.components.SensitiveNotificationsCard
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState

@Composable
fun PermissionsScreen(
    state: PermissionsState,
    actions: PermissionsActions,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .statusBarsPadding(),
    ) {
        item {
            Text(
                text = "Veles",
                fontSize = 28.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, end = 16.dp, top = 22.dp),
            )
        }
        item { Spacer(Modifier.height(16.dp)) }
        item {
            ListenerStatusCard(
                enabled = state.notificationListenerEnabled,
                modifier = Modifier.padding(horizontal = 16.dp),
            )
        }
        item {
            SensitiveNotificationsCard(
                state = state.sensitiveNotifications,
                cdmSupported = state.cdmSupported,
                settingsLocation = state.redactionSettingsLocation,
                showOnePlusAdbPreStep = state.showOnePlusAdbPreStep,
                revealFallbacks = state.revealSensitiveFallbacks,
                onEnableViaCompanion = { actions.requestPermission(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS) },
                onOpenSettings = actions.openRedactionSettings,
                onOpenEnhancedSettings = actions.openEnhancedNotificationsSettings,
                onVerify = actions.verifySensitiveAccess,
                onRestart = actions.restartApp,
                modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 8.dp),
            )
        }
        item {
            Text(
                text = "PERMISSIONS",
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = 0.96.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, top = 16.dp, bottom = 4.dp),
            )
        }
        items(
            items = state.permissions.values.filter { it.type != PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS },
            key = { it.type },
        ) { permission ->
            AccessNotificationPermission(
                permission = permission,
                requestPermission = actions.requestPermission,
                revokePermission = actions.revokePermission,
            )
        }
    }
}

@Composable
private fun ListenerStatusCard(
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
        modifier = modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier
                    .size(10.dp)
                    .background(
                        color = if (enabled) {
                            MaterialTheme.colorScheme.tertiary
                        } else {
                            MaterialTheme.colorScheme.error
                        },
                        shape = CircleShape,
                    ),
            )
            Spacer(Modifier.width(12.dp))
            Column {
                Text(
                    text = if (enabled) {
                        "Notification listener enabled"
                    } else {
                        "Notification listener disabled"
                    },
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.testTag(TestTags.NOTIFICATION_LISTENER_STATUS),
                )
                if (!enabled) {
                    Text(
                        text = "Grant notification access below to turn it on",
                        fontSize = 12.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun PermissionsScreenPreview() {
    PermissionsScreen(
        state = PermissionsState.Mocked,
        actions = PermissionsActions.Mocked,
    )
}
