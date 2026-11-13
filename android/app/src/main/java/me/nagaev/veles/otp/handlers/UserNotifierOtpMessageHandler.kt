package me.nagaev.veles.otp.handlers

import android.annotation.SuppressLint
import android.content.Context
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import me.nagaev.veles.common.di.ApplicationScope
import me.nagaev.veles.otp.OtpClipboard
import me.nagaev.veles.settings.SettingsRepository
import javax.inject.Inject

class UserNotifierOtpMessageHandler @Inject constructor(
    @ApplicationContext private val context: Context,
    private val settingsRepository: SettingsRepository,
    private val otpClipboard: OtpClipboard,
    @ApplicationScope private val applicationScope: CoroutineScope,
) : OtpMessageHandler {

    override fun onOtpMessageReceived(message: OtpMessage) {
        applicationScope.launch {
            val copied = if (settingsRepository.isAutoCopyEnabled()) {
                otpClipboard.copy(message.otp.value)
            } else {
                false
            }
            postNotification(message, copied)
        }
    }

    @SuppressLint("MissingPermission")
    private fun postNotification(message: OtpMessage, copied: Boolean) {
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
                    copied = copied,
                )
            notify(notificationId, notification)
        }
    }
}
