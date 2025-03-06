package me.nagaev.veles.viewmodel

data class PermissionsState (
    val permissions: Map<PermissionType, Permission>
) {
    companion object {
        val Init = PermissionsState(emptyMap())
        val Mocked = PermissionsState(mapOf(
            PermissionType.READ_NOTIFICATION to Permission(PermissionType.READ_NOTIFICATION, true),
            PermissionType.SEND_NOTIFICATION to Permission(PermissionType.SEND_NOTIFICATION, false)
        ))
    }
}

data class Permission (
    val type: PermissionType,
    val granted: Boolean
)

enum class PermissionType {
    READ_NOTIFICATION,
    SEND_NOTIFICATION
}
