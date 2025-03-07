package me.nagaev.veles.services

import android.content.Intent
import android.content.Intent.FLAG_ACTIVITY_NEW_TASK
import android.provider.Settings
import android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS


class AccessNotificationPermissionProvider(
    private val activityProvider: ActivityProvider
): PermissionProvider {
    companion object {
        const val ENABLED_NOTIFICATION_LISTENERS = "enabled_notification_listeners"
    }

    override fun isGranted(): Boolean {
        activityProvider.getActivity().apply {
            return Settings.Secure.getString(
                contentResolver,
                ENABLED_NOTIFICATION_LISTENERS
            ).contains(packageName)
        }
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
