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
        private const val PROBE_NOTIFICATION_ID = 99998
        private const val PROBE_CODE_RANGE_START = 100000
        private const val PROBE_CODE_RANGE_END = 999999
    }

    fun post(text: String) {
        notify(builder(text), NOTIFICATION_ID)
    }

    fun postProbe(): String {
        val code = (PROBE_CODE_RANGE_START..PROBE_CODE_RANGE_END).random()
        val text = context.getString(R.string.test_notification_probe, code)
        notify(builder(text).setVisibility(NotificationCompat.VISIBILITY_SECRET), PROBE_NOTIFICATION_ID)
        return text
    }

    fun cancelProbe() {
        NotificationManagerCompat.from(context).cancel(PROBE_NOTIFICATION_ID)
    }

    private fun builder(text: String): NotificationCompat.Builder = NotificationCompat
        .Builder(context, CHANNEL_ID)
        .setSmallIcon(R.drawable.ic_otp_message)
        .setContentTitle(context.getString(R.string.test_notification_title))
        .setContentText(text)
        .setPriority(NotificationCompat.PRIORITY_HIGH)

    private fun notify(builder: NotificationCompat.Builder, id: Int) {
        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                createOrUpdateNotificationChannel()
                notify(id, builder.build())
            }
        }
    }

    private fun createOrUpdateNotificationChannel() {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel =
            NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.test_notification_channel_name),
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = context.getString(R.string.test_notification_channel_description)
            }
        manager.createNotificationChannel(channel)
    }
}
