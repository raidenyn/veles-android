package me.nagaev.veles.permissions.services

import android.content.Intent
import android.content.Intent.FLAG_ACTIVITY_NEW_TASK
import android.provider.Settings
import android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS
import me.nagaev.veles.otp.NotificationListener


class AccessNotificationPermissionProvider(
    private val activityProvider: ActivityProvider
): PermissionProvider {
    companion object {
        const val ENABLED_NOTIFICATION_LISTENERS = "enabled_notification_listeners"
    }

    private val _listenerName = NotificationListener::class.java.let {
        it.canonicalName
    }

    override fun isGranted(): Boolean {
        val activity = activityProvider.getActivity()
        val str = Settings.Secure.getString(
            activity.contentResolver,
            ENABLED_NOTIFICATION_LISTENERS
        )
        val permissionClassName = activity.packageName + "/" + _listenerName
        return str.contains(permissionClassName)
    }

    override suspend fun request() {
        activityProvider.getActivity().apply {
            val settingsIntent = Intent(ACTION_NOTIFICATION_LISTENER_SETTINGS)
            settingsIntent.addFlags(FLAG_ACTIVITY_NEW_TASK)
            startActivity(settingsIntent)
        }
    }

    override suspend fun revoke() {
        request()
    }
}
