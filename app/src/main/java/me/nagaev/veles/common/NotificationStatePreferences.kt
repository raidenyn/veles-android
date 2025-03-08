package me.nagaev.veles.common

import android.content.Context
import me.nagaev.veles.R

class NotificationStatePreferences(private val context: Context) {
     fun saveConnectionState(state: Boolean) {
        with(context) {
            val sharedPref = getSharedPreferences(
                getString(R.string.notification_listener_preferences), Context.MODE_PRIVATE)

            with (sharedPref.edit()) {
                putBoolean(getString(R.string.notification_listener_connected), state)
                apply()
            }
        }
    }

    fun getConnectionState(): Boolean {
        with(context) {
            val sharedPref = getSharedPreferences(
                getString(R.string.notification_listener_preferences), Context.MODE_PRIVATE)

            return sharedPref.getBoolean(getString(R.string.notification_listener_connected), false)
        }
    }
}
