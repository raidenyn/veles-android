package me.nagaev.veles.testing

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import me.nagaev.veles.R

class TestNotificationSender(private val context: Context) {
    companion object {
        const val CHANNEL_ID = "VelesTestChannel"
        const val SECRET_CHANNEL_ID = "VelesSecretProbeChannel"
        private const val NOTIFICATION_ID = 99999
        private const val SECRET_NOTIFICATION_ID = 99998
        private const val SECRET_PROBE_TEXT = "PROBE_OTP_123456"
    }

    fun post(text: String) {
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle("Veles Test")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateChannel()
                notify(NOTIFICATION_ID, builder.build())
            }
        }
    }

    fun postSecretProbe() {
        val builder = NotificationCompat.Builder(context, SECRET_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle("Veles probe")
            .setContentText(SECRET_PROBE_TEXT)
            .setVisibility(NotificationCompat.VISIBILITY_SECRET)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateSecretChannel()
                notify(SECRET_NOTIFICATION_ID, builder.build())
            }
        }
    }

    private fun tryCreateChannel() {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Veles Test",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Test notifications for verifying handler configs"
        }
        manager.createNotificationChannel(channel)
    }

    private fun tryCreateSecretChannel() {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(SECRET_CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            SECRET_CHANNEL_ID,
            "Veles Secret Probe",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "Sensitive-access probes for verifying redaction toggle state"
        }
        manager.createNotificationChannel(channel)
    }
}