package me.nagaev.veles.permissions.viewmodal

import me.nagaev.veles.permissions.services.PermissionType

data class PermissionsState (
    val permissions: Map<PermissionType, Permission>,
    val notificationListenerEnabled: Boolean,
) {
    companion object {
        val Init = PermissionsState(emptyMap(), notificationListenerEnabled = false)
        val Mocked = PermissionsState(
            mapOf(
                PermissionType.ACCESS_NOTIFICATIONS to Permission(
                    PermissionType.ACCESS_NOTIFICATIONS,
                    true
                ),
                PermissionType.SEND_NOTIFICATIONS to Permission(
                    PermissionType.SEND_NOTIFICATIONS,
                    false
                )
            ),
            notificationListenerEnabled = false
        )
    }
}

data class Permission (
    val type: PermissionType,
    val granted: Boolean?
)
