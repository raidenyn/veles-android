package me.nagaev.veles.common

import android.util.Log
import javax.inject.Inject

interface LogSink {
    fun d(tag: String, msg: String)
}

class AndroidLogSink @Inject constructor() : LogSink {
    override fun d(tag: String, msg: String) {
        Log.d(tag, msg)
    }
}
