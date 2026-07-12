package me.nagaev.veles.testing

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import me.nagaev.veles.R
import javax.inject.Inject

class TestNotificationSender @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    companion object {
        const val CHANNEL_ID = "VelesTestChannel"
        private const val NOTIFICATION_ID = 99999
        private const val PROBE_CODE_RANGE_START = 100000
        private const val PROBE_CODE_RANGE_END = 999999
    }

    fun post(text: String) {
        notify(builder(text))
    }

    fun postProbe(): String {
        val text = "Veles check: code ${(PROBE_CODE_RANGE_START..PROBE_CODE_RANGE_END).random()}"
        notify(builder(text).setVisibility(NotificationCompat.VISIBILITY_SECRET))
        return text
    }

    fun cancelProbe() {
        NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID)
    }

    private fun builder(text: String): NotificationCompat.Builder =
        NotificationCompat
            .Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle("Veles Test")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

    private fun notify(builder: NotificationCompat.Builder) {
        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateChannel()
                notify(NOTIFICATION_ID, builder.build())
            }
        }
    }

    private fun tryCreateChannel() {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel =
            NotificationChannel(
                CHANNEL_ID,
                "Veles Test",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Test notifications for verifying handler configs"
            }
        manager.createNotificationChannel(channel)
    }
}
