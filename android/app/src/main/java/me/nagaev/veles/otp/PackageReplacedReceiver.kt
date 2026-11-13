package me.nagaev.veles.otp

import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.service.notification.NotificationListenerService

/**
 * Re-binds [NotificationListener] after the app is updated.
 *
 * Notification type filters declared via `default_filter_types` in the manifest are applied by the
 * system when the listener binds. When those filters change between app versions, an already-granted
 * listener keeps running with its previous binding, so the new filter would only take effect after
 * the user manually toggled notification access off and on. Requesting a rebind on
 * [Intent.ACTION_MY_PACKAGE_REPLACED] nudges the system to re-establish the connection so the
 * updated filter is picked up automatically.
 */
class PackageReplacedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        if (context == null || intent?.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        NotificationListenerService.requestRebind(
            ComponentName(context, NotificationListener::class.java),
        )
    }
}
