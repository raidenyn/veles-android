package me.nagaev.veles.sharing

import android.Manifest
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

internal enum class SharingAvailability {
    Disabled,
    Starting,
    Advertising,
    Unsupported,
    PermissionRequired,
    BluetoothOff,
    Failed,
}

internal object SharingState {
    private val mutable = MutableStateFlow(SharingAvailability.Disabled)
    val availability = mutable.asStateFlow()
    private val enabledMutable = MutableStateFlow(false)
    val enabled = enabledMutable.asStateFlow()
    fun load(context: Context) {
        enabledMutable.value = context.getSharedPreferences("sharing", Context.MODE_PRIVATE).getBoolean("enabled", false)
    }
    fun setEnabled(context: Context, value: Boolean) {
        context.getSharedPreferences("sharing", Context.MODE_PRIVATE).edit().putBoolean("enabled", value).apply()
        enabledMutable.value = value
    }
    fun update(value: SharingAvailability) {
        mutable.value = value
    }

    fun prerequisite(context: Context): SharingAvailability? {
        if (!context.packageManager.hasSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE)) {
            return SharingAvailability.Unsupported
        }
        if (listOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE).any {
                ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
            }
        ) {
            return SharingAvailability.PermissionRequired
        }
        val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            ?: return SharingAvailability.Unsupported
        return try {
            when {
                !adapter.isEnabled -> SharingAvailability.BluetoothOff
                !adapter.isMultipleAdvertisementSupported -> SharingAvailability.Unsupported
                else -> null
            }
        } catch (_: SecurityException) {
            SharingAvailability.PermissionRequired
        }
    }
}
