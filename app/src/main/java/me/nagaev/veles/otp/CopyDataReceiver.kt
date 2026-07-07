package me.nagaev.veles.otp

import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import me.nagaev.veles.BuildConfig
import me.nagaev.veles.common.AndroidLogSink
import me.nagaev.veles.common.SharedPreferencesLogConfig
import me.nagaev.veles.common.VelesLog

class CopyDataReceiver : BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
    }

    override fun onReceive(
        context: Context?,
        intent: Intent?,
    ) {
        val logger = context?.let {
            VelesLog(AndroidLogSink(), SharedPreferencesLogConfig(it), BuildConfig.DEBUG)
        }
        logger?.d("CopyDataReceiver", "Context $context")
        context?.apply {
            intent?.getStringExtra(EXTRA_COPY_TEXT)?.let {
                val systemService = getSystemService(Context.CLIPBOARD_SERVICE)
                (systemService as ClipboardManager?)?.let { clipboardService ->
                    val clip = ClipData.newPlainText("OTP", it)
                    clipboardService.setPrimaryClip(clip)
                    logger?.dCopiedOtp(it)
                }
            }
        }
    }
}
