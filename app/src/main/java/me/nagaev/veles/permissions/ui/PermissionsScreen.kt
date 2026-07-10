package me.nagaev.veles.permissions.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.PermissionsList
import me.nagaev.veles.permissions.ui.components.RedactionSection
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState

@Composable
fun PermissionsScreen(
    state: PermissionsState,
    actions: PermissionsActions,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column {
            Text(
                modifier = Modifier.padding(10.dp).statusBarsPadding(),
                text = stringResource(id = R.string.permissions),
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                modifier = Modifier.padding(10.dp).testTag(TestTags.NOTIFICATION_LISTENER_STATUS),
                text =
                if (state.notificationListenerEnabled) {
                    "Notification listener enabled"
                } else {
                    "Notification listener disabled"
                },
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.secondary,
            )
            RedactionSection(
                state = state.redactionState,
                settingsLocation = state.redactionSettingsLocation,
                onOpenSettings = actions.openRedactionSettings,
            )
            PermissionsList(
                permissions = state.permissions,
                actions = actions,
                modifier = Modifier.fillMaxSize(),
            )
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
