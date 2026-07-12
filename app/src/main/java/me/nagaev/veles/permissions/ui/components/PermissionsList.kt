package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.viewmodal.Permission
import me.nagaev.veles.permissions.viewmodal.PermissionsActions

@Composable
fun PermissionsList(
    permissions: Map<PermissionType, Permission>,
    actions: PermissionsActions,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        state = rememberLazyListState(),
    ) {
        items(items = permissions.values.toList(), key = { it.type }) { provider ->
            AccessNotificationPermission(
                permission = provider,
                requestPermission = actions.requestPermission,
                revokePermission = actions.revokePermission,
            )
        }
    }
}
