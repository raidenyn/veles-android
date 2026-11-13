@file:Suppress("TooGenericExceptionCaught") // A debug receiver must never crash the app.

package me.nagaev.veles.debugpairing

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import me.nagaev.veles.debugprobe.DebugProbeEntryPoint

/**
 * The scripted-run surface: start/cancel the pairing window with a shell-only broadcast.
 *
 * Both actions are non-echoing: the ordered-broadcast result and the log carry only a
 * constant status line, never the six-digit code. The code is shown on the debug screen.
 */
class PairingDebugReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action ?: return
        val application = context?.applicationContext ?: return
        val logger = EntryPointAccessors.fromApplication(application, DebugProbeEntryPoint::class.java).velesLog()
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                dispatch(action, pending::setResultData) { logger.d(PAIRING_TAG, it) }
            } finally {
                pending.finish()
            }
        }
    }

    internal fun dispatch(action: String, result: (String) -> Unit, log: (String) -> Unit = {}) {
        when (action) {
            ACTION_PAIRING_START -> {
                val line = if (PairingDebugHooks.start()) "pairing window open" else "pairing window unavailable"
                result(line)
                log(line)
            }
            ACTION_PAIRING_CANCEL -> {
                PairingDebugHooks.cancel()
                result("pairing window closed")
                log("pairing window closed")
            }
        }
    }

    companion object {
        // The full action strings are forbidden release strings: they do not match the
        // existing `me.nagaev.veles.debug.` entry, so `releaseHookForbidden` names them.
        const val ACTION_PAIRING_START = "me.nagaev.veles.debugpairing.START"
        const val ACTION_PAIRING_CANCEL = "me.nagaev.veles.debugpairing.CANCEL"
        internal const val PAIRING_TAG = "VelesPairingDebug"
    }
}
