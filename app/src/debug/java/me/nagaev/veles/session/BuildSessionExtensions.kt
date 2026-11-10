package me.nagaev.veles.session

import android.content.Context
import me.nagaev.veles.sharing.SessionHost

/**
 * Debug build type of the session extension point. OTP-09 ships the same stub as release;
 * Task 9 replaces it with the synthetic probe handler and the probe attach hook.
 */
object BuildSessionExtensions {
    @Suppress("UnusedParameter")
    fun applicationHandler(context: Context): ApplicationHandler? = null

    @Suppress("UnusedParameter")
    fun attach(host: SessionHost) = Unit
}
