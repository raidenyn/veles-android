package me.nagaev.veles

import android.app.Application
import me.nagaev.veles.common.AndroidLogSink
import me.nagaev.veles.common.SharedPreferencesLogConfig
import me.nagaev.veles.common.VelesLog

class VelesApplication : Application() {
    override fun onCreate() {
        super.onCreate()

        // This method fires once as well as constructor
        // & here we have application context

        val logger = VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(this), BuildConfig.DEBUG)
        logger.d("VelesApplication", "Veles started")
    }
}
