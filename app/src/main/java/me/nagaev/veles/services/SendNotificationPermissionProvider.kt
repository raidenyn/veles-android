package me.nagaev.veles.services

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

class SendNotificationPermissionProvider(
    private val activityProvider: ActivityProvider,
    private val requestPermissionLauncher: RequestPermissionLauncher
): PermissionProvider {
    override fun isGranted(): Boolean {
        with(NotificationManagerCompat.from(activityProvider.getActivity())) {
            return areNotificationsEnabled()
        }
    }

    override suspend fun request() = suspendCancellableCoroutine { cont ->
        with(activityProvider.getActivity()) {
            if (shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS)) {
                when {
                    ContextCompat.checkSelfPermission(
                        this,
                        Manifest.permission.POST_NOTIFICATIONS
                    ) == PackageManager.PERMISSION_GRANTED
                        -> cont.resume(Unit)
                    else -> requestPermissionLauncher.launch(
                        Manifest.permission.POST_NOTIFICATIONS
                    ) { granted ->
                        if (!granted) {
                            showAppSettings()
                        }
                        cont.resume(Unit)
                    }
                }
            } else {
                showAppSettings()
                cont.resume(Unit)
            }
        }
    }

    override suspend fun revoke() {
        showAppSettings()
    }

    private fun showAppSettings() {
        with(activityProvider.getActivity()) {
            val settingsIntent: Intent = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
            //.putExtra(Settings.EXTRA_CHANNEL_ID, MY_CHANNEL_ID)
            startActivity(settingsIntent)
        }
    }
}
