package me.nagaev.veles.session

import android.content.Context
import me.nagaev.veles.sharing.SessionHost

/**
 * Release build type of the session extension point. OTP-09 ships the stub so that no
 * application record has a handler: every authorized application record fails closed with
 * `app_error` `protocol_violation` until OTP-10 adds the production handler.
 */
object BuildSessionExtensions {
    @Suppress("UnusedParameter")
    fun applicationHandler(context: Context): ApplicationHandler? = null

    @Suppress("UnusedParameter")
    fun attach(host: SessionHost) = Unit
}
