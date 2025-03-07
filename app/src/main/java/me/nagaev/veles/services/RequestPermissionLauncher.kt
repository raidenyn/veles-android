package me.nagaev.veles.services

import androidx.activity.ComponentActivity
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.ActivityOptionsCompat

class RequestPermissionLauncher(
    val launch: (permission: String, callback: (Boolean) -> Unit) -> Unit
) {
    companion object {
        fun create(
            activity: ComponentActivity
        ): RequestPermissionLauncher {
            var closerCallback = { _: Boolean -> }
            val resultLauncher = activity.registerForActivityResult(
                ActivityResultContracts.RequestPermission()
            ) { granted ->
                closerCallback(granted)
            }
            val launch = { permission: String, callback: (Boolean) -> Unit ->
                closerCallback = callback
                resultLauncher.launch(permission)
            }
            return RequestPermissionLauncher(launch)
        }
    }
}