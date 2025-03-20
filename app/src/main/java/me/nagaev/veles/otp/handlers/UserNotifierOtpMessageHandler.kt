package me.nagaev.veles.otp.handlers

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import me.nagaev.veles.R
import me.nagaev.veles.otp.CopyDataReceiver

class UserNotifierOtpMessageHandler(
    private val context: Context
): OtpMessageHandler {
    companion object {
        const val CHANNEL_ID = "HandyOTPMessageChannel"
    }

    override fun onOtpMessageReceived(message: OtpMessage) {
        val text = "OTP: ${message.otp.value}, Pay: ${message.pay.amount} ${message.pay.currencyCode}"
        val title = message.merchant

        val copyIntent = Intent(context, CopyDataReceiver::class.java).apply {
            action = "Copy"
            putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, message.otp.value)
        }
        val copyPendingIntent: PendingIntent =
            PendingIntent.getBroadcast(
                context,
                0,
                copyIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )

        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle(title)
            .setContentText(text)
            .addAction(
                R.drawable.ic_otp_message, "Copy ${message.otp.value}",
                copyPendingIntent)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateNotificationChannel()
                // notificationId is a unique int for each notification that you must define.
                notify(message.hashCode(), builder.build())
            }
        }
    }

    private fun tryCreateNotificationChannel() {
        // Register the channel with the system.
        val notificationManager: NotificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        if (notificationManager.getNotificationChannel(CHANNEL_ID) != null) {
            return
        }

        val importance = NotificationManager.IMPORTANCE_HIGH
        val channel = NotificationChannel(CHANNEL_ID, "Handy OTP", importance).apply {
            description = "Show handy OTP passwords from banks"
        }

        notificationManager.createNotificationChannel(channel)
    }
}