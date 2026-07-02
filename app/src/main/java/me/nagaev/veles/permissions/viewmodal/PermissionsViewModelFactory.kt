package me.nagaev.veles.permissions.viewmodal

import android.app.Activity
import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.otp.NotificationRedactionPath
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
    private val requestPermissionLauncher: RequestPermissionLauncher,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T {
        require(modelClass.isAssignableFrom(PermissionsViewModel::class.java)) { "ViewModel Not Found" }
        return create() as T
    }

    private fun create(): PermissionsViewModel {
        val activityProvider: ActivityProvider = ActivityProviderImpl(activity)
        val accessNotificationPermissionProvider =
            AccessNotificationPermissionProvider(activityProvider)
        val sendNotificationPermissionProvider =
            SendNotificationPermissionProvider(activityProvider, requestPermissionLauncher)

        val permissionsProvider: PermissionsProvider =
            PermissionsProviderImpl(
                providers =
                mapOf(
                    PermissionType.ACCESS_NOTIFICATIONS to accessNotificationPermissionProvider,
                    PermissionType.SEND_NOTIFICATIONS to sendNotificationPermissionProvider,
                ),
            )

        val notificationStatePreferences = NotificationStatePreferences(activity)
        val componentName =
            android.content.ComponentName(
                activity.packageName,
                "me.nagaev.veles.otp.NotificationListener",
            )
        val redactionPath = NotificationRedactionPath.from(Build.MANUFACTURER, componentName)
        val openSettings: (android.content.Intent) -> Unit = { intent -> activity.startActivity(intent) }

        return PermissionsViewModel(
            permissionsProvider,
            notificationStatePreferences,
            redactionPath,
            componentName,
            openSettings,
        )
    }
}
