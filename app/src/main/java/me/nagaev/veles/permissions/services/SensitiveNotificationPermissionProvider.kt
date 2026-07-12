package me.nagaev.veles.permissions.services

class SensitiveNotificationPermissionProvider(
    private val status: SensitiveNotificationsStatus,
    private val association: CompanionAssociationService,
) : PermissionProvider {
    val cdmSupported: Boolean
        get() = association.isSupported()

    override fun isGranted(): Boolean = status.check() != SensitiveNotificationsGrant.NotGranted

    override suspend fun request() {
        association.associate()
    }

    override suspend fun revoke() {
        association.disassociate()
    }
}