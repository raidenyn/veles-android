package me.nagaev.veles.otp

import android.content.ComponentName
import android.content.Intent
import android.os.Build
import android.provider.Settings

sealed interface NotificationRedactionPath {
    val settingsLocation: String
    val explainerCopy: String

    fun settingsIntent(componentName: ComponentName): Intent

    object StockAndroid : NotificationRedactionPath {
        override val settingsLocation: String =
            "Settings > Notifications > Notification access > Veles > Sensitive notifications"
        override val explainerCopy: String =
            "Your device hides sensitive notification content from Veles. " +
                "In Settings, open Notifications > Notification access > Veles, " +
                "and turn on 'Sensitive notifications'."

        private fun buildIntent(componentName: ComponentName): Intent = if (Build.VERSION.SDK_INT >= SDK_INT_NOTIFICATION_LISTENER_DETAIL) {
            Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS)
                .putExtra(
                    Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME,
                    componentName.flattenToString(),
                )
        } else {
            Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
        }

        override fun settingsIntent(componentName: ComponentName): Intent = buildIntent(componentName)
    }

    object OxygenOS : NotificationRedactionPath {
        override val settingsLocation: String =
            "Settings > Notifications > Notification access > Veles > Enhanced Notifications"
        override val explainerCopy: String =
            "Your OnePlus device hides sensitive notification content. " +
                "In Settings, open Notifications > Notification access > Veles, " +
                "and turn off 'Enhanced Notifications'."

        override fun settingsIntent(componentName: ComponentName): Intent = StockAndroid.settingsIntent(componentName)
    }

    companion object {
        private const val SDK_INT_NOTIFICATION_LISTENER_DETAIL = 34

        fun from(
            manufacturer: String?,
            componentName: ComponentName,
        ): NotificationRedactionPath = when (manufacturer?.lowercase()) {
            "oneplus" -> OxygenOS
            else -> StockAndroid
        }
    }
}
