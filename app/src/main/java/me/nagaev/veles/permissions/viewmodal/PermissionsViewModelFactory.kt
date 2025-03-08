package me.nagaev.veles.permissions.viewmodal

import android.app.Activity
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.permissions.services.AccessNotificationPermissionProvider
import me.nagaev.veles.permissions.services.ActivityProvider
import me.nagaev.veles.permissions.services.ActivityProviderImpl
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.permissions.services.PermissionsProviderImpl
import me.nagaev.veles.permissions.services.RequestPermissionLauncher
import me.nagaev.veles.permissions.services.SendNotificationPermissionProvider

class PermissionsViewModelFactory(
    private val activity: Activity,
    private val requestPermissionLauncher: RequestPermissionLauncher
): ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        return if (modelClass.isAssignableFrom(PermissionsViewModel::class.java)) {
            create() as T
        } else {
            throw IllegalArgumentException("ViewModel Not Found")
        }
    }

    private fun create(): PermissionsViewModel {
        // TODO: change to DI on growing
        val activityProvider: ActivityProvider = ActivityProviderImpl(activity)
        val accessNotificationPermissionProvider =
            AccessNotificationPermissionProvider(activityProvider)
        val sendNotificationPermissionProvider =
            SendNotificationPermissionProvider(activityProvider, requestPermissionLauncher)

        val permissionsProvider: PermissionsProvider = PermissionsProviderImpl(
            providers = mapOf(
                PermissionType.ACCESS_NOTIFICATIONS to accessNotificationPermissionProvider,
                PermissionType.SEND_NOTIFICATIONS to sendNotificationPermissionProvider
            )
        )

        val notificationStatePreferences = NotificationStatePreferences(activity)

        return PermissionsViewModel(
            permissionsProvider,
            notificationStatePreferences
        )
    }
}