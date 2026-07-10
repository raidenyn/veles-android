package me.nagaev.veles.otp.handlers

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import me.nagaev.veles.R
import me.nagaev.veles.otp.CopyDataReceiver

class OtpNotificationBuilder(
    private val context: Context,
) {
    companion object {
        const val CHANNEL_ID = "HandyOTPMessageChannel"
    }

    fun build(
        notificationId: Int,
        merchant: String,
        otp: String,
        amountText: String,
        currencyCode: String,
        copied: Boolean,
    ): Notification {
        tryCreateNotificationChannel()

        val copyIntent =
            Intent(context, CopyDataReceiver::class.java).apply {
                action = "Copy"
                data = Uri.parse("veles://otp/$notificationId")
                putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, otp)
                putExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, notificationId)
                putExtra(CopyDataReceiver.EXTRA_MERCHANT, merchant)
                putExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT, amountText)
                putExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE, currencyCode)
            }
        val copyPendingIntent: PendingIntent =
            PendingIntent.getBroadcast(
                context,
                notificationId,
                copyIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )

        val text = "OTP: $otp, Pay: $amountText $currencyCode"
        val actionLabel = if (copied) "Copy $otp Copied ✓" else "Copy $otp"

        return NotificationCompat
            .Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle(merchant)
            .setContentText(text)
            .addAction(
                R.drawable.ic_otp_message,
                actionLabel,
                copyPendingIntent,
            ).setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
    }

    private fun tryCreateNotificationChannel() {
        val notificationManager =
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
