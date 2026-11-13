package me.nagaev.veles

import android.app.Application
import dagger.hilt.android.HiltAndroidApp
import me.nagaev.veles.common.VelesLog
import javax.inject.Inject

@HiltAndroidApp
class VelesApplication : Application() {
    @Inject
    lateinit var logger: VelesLog

    override fun onCreate() {
        super.onCreate()
        logger.d("VelesApplication", "Veles started")
    }
}
