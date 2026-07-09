package me.nagaev.veles.otp

import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import dagger.hilt.android.EntryPointAccessors
import me.nagaev.veles.common.VelesLog

class CopyDataReceiver(
    private val loggerOverride: VelesLog? = null,
) : BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
        const val EXTRA_NOTIFICATION_ID = "NotificationId"
    }

    override fun onReceive(
        context: Context?,
        intent: Intent?,
    ) {
        if (context == null) return
        val logger = loggerOverride ?: resolveLogger(context)
        logger.d("CopyDataReceiver", "Context $context")
        intent?.getStringExtra(EXTRA_COPY_TEXT)?.let {
            val systemService = context.getSystemService(Context.CLIPBOARD_SERVICE)
            (systemService as ClipboardManager?)?.let { clipboardService ->
                val clip = ClipData.newPlainText("OTP", it)
                clipboardService.setPrimaryClip(clip)
                logger.dCopiedOtp(it)
            }
        }
    }

    private fun resolveLogger(context: Context): VelesLog = EntryPointAccessors.fromApplication(
        context.applicationContext,
        NotificationListenerEntryPoint::class.java,
    ).velesLog()
}
