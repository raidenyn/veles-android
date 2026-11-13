package me.nagaev.veles.otp

import android.app.Notification
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat

object RedactionDetector {
    private val DIGIT_RUN = Regex("\\d{4,}")

    fun isRedacted(sbn: StatusBarNotification): Boolean {
        val notification = sbn.notification ?: return false
        val text =
            notification.extras
                ?.getCharSequence(NotificationCompat.EXTRA_TEXT)
                ?.toString()
                .orEmpty()
        return notification.visibility == Notification.VISIBILITY_SECRET &&
            text.isNotBlank() &&
            DIGIT_RUN.find(text) == null
    }
}
