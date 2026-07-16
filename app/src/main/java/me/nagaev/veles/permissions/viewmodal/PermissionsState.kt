package me.nagaev.veles.permissions.viewmodal

import me.nagaev.veles.R
import me.nagaev.veles.common.UiText
import me.nagaev.veles.permissions.services.PermissionType

enum class SensitiveNotificationsUiState {
    NotApplicable,
    NotGranted,
    Verifying,
    ApplyingGrant,
    Granted,
    GrantedButRedacted,
    Unknown,
}

data class PermissionsState(
    val permissions: Map<PermissionType, Permission>,
    val notificationListenerEnabled: Boolean,
    val sensitiveNotifications: SensitiveNotificationsUiState = SensitiveNotificationsUiState.NotApplicable,
    val cdmSupported: Boolean = false,
    val showOnePlusAdbPreStep: Boolean = false,
    val redactionSettingsLocation: UiText = UiText.Res(R.string.sensitive_card_stock_settings_location),
    val revealSensitiveFallbacks: Boolean = false,
    val showForceStopButton: Boolean = false,
) {
    companion object {
        val Init = PermissionsState(emptyMap(), notificationListenerEnabled = false)
        val Mocked =
            PermissionsState(
                mapOf(
                    PermissionType.ACCESS_NOTIFICATIONS to
                        Permission(
                            PermissionType.ACCESS_NOTIFICATIONS,
                            true,
                        ),
                    PermissionType.SEND_NOTIFICATIONS to
                        Permission(
                            PermissionType.SEND_NOTIFICATIONS,
                            false,
                        ),
                ),
                notificationListenerEnabled = false,
            )
    }
}

data class Permission(
    val type: PermissionType,
    val granted: Boolean?,
)
