package me.nagaev.veles.services

import kotlinx.coroutines.delay

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

class MockedPermissionsProvider: PermissionsProvider {
    override val providers: Map<PermissionType, PermissionProvider> = mapOf(
        PermissionType.ACCESS_NOTIFICATIONS to MockedPermissionProvider(),
        PermissionType.SEND_NOTIFICATIONS to MockedPermissionProvider()
    )
}

class MockedPermissionProvider: PermissionProvider {
    private var isGranted = false

    override fun isGranted(): Boolean = isGranted

    override suspend fun request() {
        delay(1000)
        isGranted = true
    }

    override suspend fun revoke() {
        isGranted = false
    }
}


