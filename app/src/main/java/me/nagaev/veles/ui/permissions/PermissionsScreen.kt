package me.nagaev.veles.ui.permissions

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsBottomHeight
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.windowsizeclass.WindowWidthSizeClass
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.services.PermissionType
import me.nagaev.veles.R
import me.nagaev.veles.ui.VelesApp
import me.nagaev.veles.viewmodel.Permission
import me.nagaev.veles.viewmodel.PermissionsActions
import me.nagaev.veles.viewmodel.PermissionsState
import me.nagaev.veles.viewmodel.RequestPermission
import me.nagaev.veles.viewmodel.RevokePermission
import me.nagaev.veles.viewmodel.UiState

@Composable
fun PermissionsScreen(
    uiState: PermissionsState,
    actions: PermissionsActions,
    modifier: Modifier = Modifier
) {
    Box(modifier = modifier.fillMaxSize()) {
        PermissionsList(
            permissions = uiState.permissions,
            actions = actions,
            modifier = Modifier.fillMaxSize(),
        )
    }
}

@Composable
fun PermissionsList(
    permissions: Map<PermissionType, Permission>,
    actions: PermissionsActions,
    modifier: Modifier = Modifier
) {
    Column {
        Text(
            modifier = Modifier.padding(10.dp),
            text = stringResource(id = R.string.permissions),
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.primary
        )

        Box(modifier = modifier.windowInsetsPadding(WindowInsets.statusBars)) {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .background(MaterialTheme.colorScheme.surface),
                state = rememberLazyListState(),
            ) {
                items(items = permissions.values.toList(), key = { it.type }) { provider ->
                    AccessNotificationPermission(
                        permission = provider,
                        requestPermission = actions.requestPermission,
                        revokePermission = actions.revokePermission
                    )
                    Spacer(Modifier.windowInsetsBottomHeight(WindowInsets.systemBars))
                }
            }
        }
    }
}

fun getPermissionDescription(type: PermissionType): Int {
    return when (type) {
        PermissionType.ACCESS_NOTIFICATIONS -> R.string.access_notification_permission_description
        PermissionType.SEND_NOTIFICATIONS -> R.string.send_notification_permission_description
    }
}

@Composable
fun AccessNotificationPermission(
    permission: Permission,
    requestPermission: RequestPermission,
    revokePermission: RevokePermission
) {
    Card(
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh
        )
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                modifier = Modifier
                    .weight(1f),
                text = stringResource(id = getPermissionDescription(permission.type)),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary
            )
            SwitchWithLoader(
                modifier = Modifier.wrapContentSize(),
                checked = permission.granted,
                onCheckedChange = {
                    if (it) {
                        requestPermission(permission.type)
                    } else {
                        revokePermission(permission.type)
                    }
                },
            )
        }
    }
}

@Composable
fun SwitchWithLoader(
    checked: Boolean?,
    onCheckedChange: ((Boolean) -> Unit)?,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier.padding(10.dp).wrapContentSize()
    ) {
        if (checked == null) {
            CircularProgressIndicator(
                modifier = Modifier.wrapContentSize()
            )
        } else {
            Switch(
                modifier = Modifier.wrapContentSize(),
                checked = checked,
                onCheckedChange = onCheckedChange,
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
fun PermissionsScreenPreview() {
    PermissionsScreen(
        uiState = PermissionsState.Mocked,
        actions = PermissionsActions.Mocked,
    )
}
