package me.nagaev.veles.otp

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.PersistableBundle
import dagger.hilt.android.qualifiers.ApplicationContext
import me.nagaev.veles.R
import me.nagaev.veles.common.VelesLog
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class OtpClipboard @Inject constructor(
    @ApplicationContext private val context: Context,
    private val logger: VelesLog,
) {
    companion object {
        private const val CLEAR_DELAY_MILLIS = 2 * 60 * 1000L

        internal fun shouldClearClip(
            clip: ClipData?,
            expectedLabel: String,
            expectedText: String,
        ): Boolean {
            if (clip == null || clip.itemCount == 0) return false
            if (clip.description.label != expectedLabel) return false
            return clip.getItemAt(0).text?.toString() == expectedText
        }
    }

    fun copy(otp: String): Boolean {
        val clipboardManager =
            context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return false
        val clipLabel = context.getString(R.string.otp_clipboard_label)

        val clip = ClipData.newPlainText(clipLabel, otp).apply {
            description.extras = PersistableBundle().apply {
                putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
            }
        }
        clipboardManager.setPrimaryClip(clip)
        logger.dCopiedOtp(otp)

        Handler(Looper.getMainLooper()).postDelayed({
            if (shouldClearClip(clipboardManager.primaryClip, clipLabel, otp)) {
                clipboardManager.clearPrimaryClip()
            }
        }, CLEAR_DELAY_MILLIS)
        return true
    }
}
