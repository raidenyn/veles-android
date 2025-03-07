package me.nagaev.veles.permissions.services

interface PermissionsProvider {
    val providers: Map<PermissionType, PermissionProvider>
}

enum class PermissionType {
    ACCESS_NOTIFICATIONS,
    SEND_NOTIFICATIONS
}

class PermissionsProviderImpl(
    override val providers: Map<PermissionType, PermissionProvider>
): PermissionsProvider

interface PermissionProvider {
    fun isGranted(): Boolean

    suspend fun request()

    suspend fun revoke()
}
