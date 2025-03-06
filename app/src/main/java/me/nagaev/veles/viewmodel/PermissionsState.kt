package me.nagaev.veles.viewmodel

import me.nagaev.veles.services.PermissionType

data class PermissionsState (
    val permissions: Map<PermissionType, Permission>
) {
    companion object {
        val Init = PermissionsState(emptyMap())
        val Mocked = PermissionsState(mapOf(
            PermissionType.ACCESS_NOTIFICATIONS to Permission(PermissionType.ACCESS_NOTIFICATIONS, true),
            PermissionType.SEND_NOTIFICATIONS to Permission(PermissionType.SEND_NOTIFICATIONS, false)
        ))
    }
}

data class Permission (
    val type: PermissionType,
    val granted: Boolean?
)
