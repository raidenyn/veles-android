package me.nagaev.veles.otp.handlers

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import me.nagaev.veles.R
import me.nagaev.veles.otp.CopyDataReceiver
import javax.inject.Inject

class UserNotifierOtpMessageHandler @Inject constructor(
    @ApplicationContext private val context: Context,
) : OtpMessageHandler {
    companion object {
        const val CHANNEL_ID = "HandyOTPMessageChannel"
    }

    override fun onOtpMessageReceived(message: OtpMessage) {
        val text = "OTP: ${message.otp.value}, Pay: ${message.pay.amount} ${message.pay.currencyCode}"
        val title = message.merchant
        val notificationId = message.hashCode()

        val copyIntent =
            Intent(context, CopyDataReceiver::class.java).apply {
                action = "Copy"
                // A unique data URI makes Intent.filterEquals differ per message, so even
                // if two request codes ever collide the PendingIntents stay distinct and
                // each keeps its own extras (FLAG_UPDATE_CURRENT would otherwise overwrite
                // them, making the older notification's Copy action copy the newest OTP).
                data = Uri.parse("veles://otp/${message.id}")
                putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, message.otp.value)
                putExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, notificationId)
            }
        val copyPendingIntent: PendingIntent =
            PendingIntent.getBroadcast(
                context,
                // Unique request code per notification so each notification owns its own
                // PendingIntent; otherwise FLAG_UPDATE_CURRENT would overwrite the older
                // notification's extras and "Copy" would copy the newest OTP.
                message.id,
                copyIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )

        val builder =
            NotificationCompat
                .Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_otp_message)
                .setContentTitle(title)
                .setContentText(text)
                .addAction(
                    R.drawable.ic_otp_message,
                    "Copy ${message.otp.value}",
                    copyPendingIntent,
                ).setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateNotificationChannel()
                notify(notificationId, builder.build())
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
        val channel =
            NotificationChannel(CHANNEL_ID, "Handy OTP", importance).apply {
                description = "Show handy OTP passwords from banks"
            }

        notificationManager.createNotificationChannel(channel)
    }
}
