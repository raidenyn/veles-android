package me.nagaev.veles.otp

import android.content.ComponentName
import android.content.Intent
import android.os.Build
import android.provider.Settings
import me.nagaev.veles.R
import me.nagaev.veles.common.UiText

sealed interface NotificationRedactionPath {
    val settingsLocation: UiText.Res

    fun settingsIntent(componentName: ComponentName): Intent

    object StockAndroid : NotificationRedactionPath {
        override val settingsLocation = UiText.Res(R.string.sensitive_card_stock_settings_location)

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
        override val settingsLocation = UiText.Res(R.string.sensitive_card_oneplus_settings_location)

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
