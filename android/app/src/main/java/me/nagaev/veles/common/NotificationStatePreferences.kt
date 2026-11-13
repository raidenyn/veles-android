package me.nagaev.veles.common

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import me.nagaev.veles.R
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class NotificationStatePreferences @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val _currentConnectionState = MutableStateFlow(readConnectionState())
    val currentConnectionState: StateFlow<Boolean> = _currentConnectionState.asStateFlow()

    fun saveConnectionState(state: Boolean) {
        with(context) {
            val sharedPref =
                getSharedPreferences(
                    getString(R.string.notification_listener_preferences),
                    Context.MODE_PRIVATE,
                )

            with(sharedPref.edit()) {
                putBoolean(getString(R.string.notification_listener_connected), state)
                apply()
            }
        }
        _currentConnectionState.value = state
    }

    fun getConnectionState(): Boolean = currentConnectionState.value

    private fun readConnectionState(): Boolean {
        with(context) {
            val sharedPref =
                getSharedPreferences(
                    getString(R.string.notification_listener_preferences),
                    Context.MODE_PRIVATE,
                )

            return sharedPref.getBoolean(getString(R.string.notification_listener_connected), false)
        }
    }
}
