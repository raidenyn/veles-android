package me.nagaev.veles.permissions.services

import android.app.Activity
import android.content.IntentSender
import androidx.activity.ComponentActivity
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts

class IntentSenderLauncher(
    val launch: (intentSender: IntentSender, callback: (resultOk: Boolean) -> Unit) -> Unit,
) {
    companion object {
        fun create(activity: ComponentActivity): IntentSenderLauncher {
            var closerCallback = { _: Boolean -> }
            val resultLauncher =
                activity.registerForActivityResult(
                    ActivityResultContracts.StartIntentSenderForResult(),
                ) { result ->
                    closerCallback(result.resultCode == Activity.RESULT_OK)
                }
            val launch = { intentSender: IntentSender, callback: (Boolean) -> Unit ->
                closerCallback(false)
                closerCallback = callback
                resultLauncher.launch(IntentSenderRequest.Builder(intentSender).build())
            }
            return IntentSenderLauncher(launch)
        }
    }
}
