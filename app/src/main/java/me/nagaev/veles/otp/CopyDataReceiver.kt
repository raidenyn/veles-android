package me.nagaev.veles.otp

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.EntryPointAccessors
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder

class CopyDataReceiver(
    private val loggerOverride: VelesLog? = null,
    private val notificationBuilderOverride: OtpNotificationBuilder? = null,
    private val otpClipboardOverride: OtpClipboard? = null,
) : BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
        const val EXTRA_NOTIFICATION_ID = "NotificationId"
        const val EXTRA_MERCHANT = "Merchant"
        const val EXTRA_AMOUNT_TEXT = "AmountText"
        const val EXTRA_CURRENCY_CODE = "CurrencyCode"
    }

    @SuppressLint("MissingPermission")
    override fun onReceive(
        context: Context?,
        intent: Intent?,
    ) {
        if (context == null) return
        val logger = loggerOverride ?: resolveLogger(context)
        logger.d("CopyDataReceiver", "Context $context")

        val otp = intent?.getStringExtra(EXTRA_COPY_TEXT) ?: return
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1)
        val merchant = intent.getStringExtra(EXTRA_MERCHANT) ?: ""
        val amountText = intent.getStringExtra(EXTRA_AMOUNT_TEXT) ?: ""
        val currencyCode = intent.getStringExtra(EXTRA_CURRENCY_CODE) ?: ""
        val otpClipboard = otpClipboardOverride ?: resolveOtpClipboard(context)
        otpClipboard.copy(otp)

        if (notificationId != -1) {
            val notificationBuilder =
                notificationBuilderOverride ?: OtpNotificationBuilder(context)
            val notification = notificationBuilder.build(
                notificationId = notificationId,
                merchant = merchant,
                otp = otp,
                amountText = amountText,
                currencyCode = currencyCode,
                copied = true,
            )
            NotificationManagerCompat.from(context).notify(notificationId, notification)
        }
    }

    private fun resolveLogger(context: Context): VelesLog = EntryPointAccessors.fromApplication(
        context.applicationContext,
        NotificationListenerEntryPoint::class.java,
    ).velesLog()

    private fun resolveOtpClipboard(context: Context): OtpClipboard = EntryPointAccessors.fromApplication(
        context.applicationContext,
        NotificationListenerEntryPoint::class.java,
    ).otpClipboard()
}
