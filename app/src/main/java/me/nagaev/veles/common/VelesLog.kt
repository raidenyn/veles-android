package me.nagaev.veles.common

class VelesLog(
    private val sink: LogSink,
    private val logConfig: LogConfig,
    private val debugEnabled: Boolean,
) {
    fun d(tag: String, msg: String) {
        if (debugEnabled) sink.d(tag, msg)
    }

    fun dNotificationLogged(pkg: String, title: String, text: String, key: String, postTime: Long) {
        if (!debugEnabled) return
        if (logConfig.rawContentEnabled) {
            sink.d(
                "NotificationListener",
                "Title: $title, Text: $text, Package: $pkg, Timestamp: $postTime, Key: $key",
            )
        } else {
            sink.d(
                "NotificationListener",
                "Posted: pkg=$pkg, titleLen=${title.length}, textLen=${text.length}, Timestamp: $postTime, Key: $key",
            )
        }
    }

    fun dCopiedOtp(value: String) {
        if (!debugEnabled) return
        if (logConfig.rawContentEnabled) {
            sink.d("CopyDataReceiver", "Copied '$value'")
        } else {
            sink.d("CopyDataReceiver", "Copied OTP (len=${value.length})")
        }
    }
}
