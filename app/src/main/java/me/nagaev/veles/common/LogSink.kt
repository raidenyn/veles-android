package me.nagaev.veles.common

import android.util.Log

interface LogSink {
    fun d(tag: String, msg: String)
}

class AndroidLogSink : LogSink {
    override fun d(tag: String, msg: String) {
        Log.d(tag, msg)
    }
}
