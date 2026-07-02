package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsBottomHeight
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.MaterialTheme
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
                    revokePermission = actions.revokePermission,
                )
                Spacer(Modifier.windowInsetsBottomHeight(WindowInsets.systemBars))
            }
        }
    }
}
