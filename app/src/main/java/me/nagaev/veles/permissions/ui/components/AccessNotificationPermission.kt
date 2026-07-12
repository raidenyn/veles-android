package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Column
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.viewmodal.Permission
import me.nagaev.veles.permissions.viewmodal.RequestPermission
import me.nagaev.veles.permissions.viewmodal.RevokePermission

fun getPermissionTitle(type: PermissionType): Int = when (type) {
    PermissionType.ACCESS_NOTIFICATIONS -> R.string.access_notification_permission_title
    PermissionType.SEND_NOTIFICATIONS -> R.string.send_notification_permission_title
    PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS ->
        throw IllegalArgumentException("RECEIVE_SENSITIVE_NOTIFICATIONS has no list-row title")
}

fun getPermissionDescription(type: PermissionType): Int = when (type) {
    PermissionType.ACCESS_NOTIFICATIONS -> R.string.access_notification_permission_description
    PermissionType.SEND_NOTIFICATIONS -> R.string.send_notification_permission_description
    PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS ->
        throw IllegalArgumentException("RECEIVE_SENSITIVE_NOTIFICATIONS has no list-row description")
}

@Composable
fun AccessNotificationPermission(
    permission: Permission,
    requestPermission: RequestPermission,
    revokePermission: RevokePermission,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = stringResource(id = getPermissionTitle(permission.type)),
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = stringResource(id = getPermissionDescription(permission.type)),
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
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
