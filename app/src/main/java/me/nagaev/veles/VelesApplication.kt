package me.nagaev.veles

import android.app.Application
import android.util.Log

class VelesApplication : Application() {
    override fun onCreate() {
        super.onCreate()

        // This method fires once as well as constructor
        // & here we have application context

        //Method calls
        Log.d("VelesApplication", "Veles started")
    }
}
