package me.nagaev.veles

import android.app.Activity
import android.content.Intent
import android.content.Intent.FLAG_ACTIVITY_NEW_TASK
import android.provider.Settings
import android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS

class PermissionChecker {
    fun ensurePermissions(activity: Activity) {
        hasNotificationAccess(activity).not().let {
            if (it) {
                openPermissions(activity)
                activity.finishAndRemoveTask()
            }
        }
    }

    private fun hasNotificationAccess(activity: Activity): Boolean {
        return Settings.Secure.getString(
            activity.contentResolver,
            "enabled_notification_listeners"
        ).contains(activity.packageName)
    }

    private fun openPermissions(activity: Activity) {
        val settingsIntent = Intent(ACTION_NOTIFICATION_LISTENER_SETTINGS)
        settingsIntent.addFlags(FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(settingsIntent)
    }
}