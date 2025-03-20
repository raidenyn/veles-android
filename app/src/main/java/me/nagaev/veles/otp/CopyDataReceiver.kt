package me.nagaev.veles.otp

import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.util.Log

class CopyDataReceiver: BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
    }

    override fun onReceive(context: Context?, intent: Intent?) {
        Log.d("CopyDataReceiver", "Context $context")
        context?.apply {
            intent?.getStringExtra(EXTRA_COPY_TEXT)?.let {
                val systemService = getSystemService(Context.CLIPBOARD_SERVICE)
                (systemService as ClipboardManager?)?.let { clipboardService ->
                    val clip = ClipData.newPlainText("OTP", it)
                    clipboardService.setPrimaryClip(clip)
                    Log.d("CopyDataReceiver", "Copied $it")
                }
            }
        }
    }
}
