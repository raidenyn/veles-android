package me.nagaev.veles.viewmodel

import android.app.Activity
import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.services.AccessNotificationPermissionProvider
import me.nagaev.veles.services.ActivityProvider
import me.nagaev.veles.services.ActivityProviderImpl
import me.nagaev.veles.services.PermissionType
import me.nagaev.veles.services.PermissionsProvider
import me.nagaev.veles.services.PermissionsProviderImpl
import me.nagaev.veles.services.RequestPermissionLauncher
import me.nagaev.veles.services.SendNotificationPermissionProvider

class UiViewModelFactory(
    private val activity: ComponentActivity,
    private val requestPermissionLauncher: RequestPermissionLauncher
): ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        return if (modelClass.isAssignableFrom(UiViewModel::class.java)) {
            create() as T
        } else {
            throw IllegalArgumentException("ViewModel Not Found")
        }
    }

    private fun create(): UiViewModel {
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

        return UiViewModel(permissionsProvider)
    }
}