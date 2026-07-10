package me.nagaev.veles.otp.handlers

import android.annotation.SuppressLint
import android.content.Context
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject

class UserNotifierOtpMessageHandler @Inject constructor(
    @ApplicationContext private val context: Context,
) : OtpMessageHandler {

    @SuppressLint("MissingPermission")
    override fun onOtpMessageReceived(message: OtpMessage) {
        val notificationId = message.hashCode()

        with(NotificationManagerCompat.from(context)) {
            if (!areNotificationsEnabled()) return

            val notification =
                OtpNotificationBuilder(context).build(
                    notificationId = notificationId,
                    merchant = message.merchant,
                    otp = message.otp.value,
                    amountText = message.pay.amount.toPlainString(),
                    currencyCode = message.pay.currencyCode,
                    copied = false,
                )
            notify(notificationId, notification)
        }
    }
}
