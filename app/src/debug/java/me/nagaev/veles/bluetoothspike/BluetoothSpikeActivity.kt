package me.nagaev.veles.bluetoothspike

import android.Manifest
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import me.nagaev.veles.common.ui.theme.VelesTheme

internal class BluetoothSpikeActivity : ComponentActivity() {
    private var pendingStart = false

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        val denied = grants.filterValues { !it }.keys
        if (denied.isNotEmpty()) {
            BluetoothSpikeStateStore.error("Permissions denied: ${denied.joinToString()}")
        } else if (pendingStart) {
            sendServiceAction(BluetoothSpikeService.ACTION_START)
        }
        pendingStart = false
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        setContent {
            val state by BluetoothSpikeStateStore.state.collectAsStateWithLifecycle()
            VelesTheme {
                BluetoothSpikeScreen(
                    state = state,
                    onStart = ::startServiceWithPermissions,
                    onStop = ::stopService,
                    onOpenBluetoothSettings = ::openBluetoothSettings,
                    onPushNow = { sendServiceAction(BluetoothSpikeService.ACTION_PUSH_NOW) },
                    onScheduleSmokePush = {
                        sendServiceAction(
                            BluetoothSpikeService.ACTION_SCHEDULE_PUSH,
                            BluetoothSpikeService.SMOKE_PUSH_DELAY_MILLIS,
                        )
                    },
                    onScheduleLifecyclePush = {
                        sendServiceAction(
                            BluetoothSpikeService.ACTION_SCHEDULE_PUSH,
                            BluetoothSpikeService.LIFECYCLE_PUSH_DELAY_MILLIS,
                        )
                    },
                )
            }
        }
    }

    private fun startServiceWithPermissions() {
        val needed = listOf(
            Manifest.permission.BLUETOOTH_ADVERTISE,
            Manifest.permission.BLUETOOTH_CONNECT,
            Manifest.permission.POST_NOTIFICATIONS,
        ).filter {
            ContextCompat.checkSelfPermission(this, it) != android.content.pm.PackageManager.PERMISSION_GRANTED
        }
        if (needed.isEmpty()) {
            sendServiceAction(BluetoothSpikeService.ACTION_START)
        } else {
            pendingStart = true
            permissionLauncher.launch(needed.toTypedArray())
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        pendingStart = false
    }

    private fun stopService() {
        stopService(Intent(this, BluetoothSpikeService::class.java))
    }

    private fun openBluetoothSettings() {
        startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS))
    }

    private fun sendServiceAction(action: String, delayMillis: Long? = null) {
        val intent = BluetoothSpikeService.intent(this, action).apply {
            delayMillis?.let { putExtra(BluetoothSpikeService.EXTRA_DELAY_MILLIS, it) }
        }
        if (action == BluetoothSpikeService.ACTION_START) {
            ContextCompat.startForegroundService(this, intent)
        } else {
            startService(intent)
        }
    }
}
