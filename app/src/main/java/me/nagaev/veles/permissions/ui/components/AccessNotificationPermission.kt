package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.viewmodal.Permission
import me.nagaev.veles.permissions.viewmodal.RequestPermission
import me.nagaev.veles.permissions.viewmodal.RevokePermission

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
                modifier = Modifier.wrapContentSize().testTag(TestTags.PERMISSION_STATUS(permission.type)),
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