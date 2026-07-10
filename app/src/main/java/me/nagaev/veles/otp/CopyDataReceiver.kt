package me.nagaev.veles.otp

import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.PersistableBundle
import androidx.core.app.NotificationManagerCompat
import dagger.hilt.android.EntryPointAccessors
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder

class CopyDataReceiver(
    private val loggerOverride: VelesLog? = null,
    private val notificationBuilderOverride: OtpNotificationBuilder? = null,
) : BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
        const val EXTRA_NOTIFICATION_ID = "NotificationId"
        const val EXTRA_MERCHANT = "Merchant"
        const val EXTRA_AMOUNT_TEXT = "AmountText"
        const val EXTRA_CURRENCY_CODE = "CurrencyCode"
        internal const val CLIP_LABEL = "OTP"
        private const val CLEAR_DELAY_MILLIS = 2 * 60 * 1000L

        internal fun shouldClearClip(clip: ClipData?, expectedText: String): Boolean {
            if (clip == null || clip.itemCount == 0) return false
            if (clip.description.label != CLIP_LABEL) return false
            return clip.getItemAt(0).text?.toString() == expectedText
        }
    }

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
        val clipboardManager =
            context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return

        val clip = ClipData.newPlainText(CLIP_LABEL, otp).apply {
            description.extras = PersistableBundle().apply {
                putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
            }
        }
        clipboardManager.setPrimaryClip(clip)
        logger.dCopiedOtp(otp)

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

        Handler(Looper.getMainLooper()).postDelayed({
            if (shouldClearClip(clipboardManager.primaryClip, otp)) {
                clipboardManager.clearPrimaryClip()
            }
        }, CLEAR_DELAY_MILLIS)
    }

    private fun resolveLogger(context: Context): VelesLog = EntryPointAccessors.fromApplication(
        context.applicationContext,
        NotificationListenerEntryPoint::class.java,
    ).velesLog()
}
