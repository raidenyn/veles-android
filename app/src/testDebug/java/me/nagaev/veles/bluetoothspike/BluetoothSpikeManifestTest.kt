package me.nagaev.veles.bluetoothspike

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import androidx.test.core.app.ApplicationProvider
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class BluetoothSpikeManifestTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun `debug manifest declares connected device foreground service`() {
        val info = context.packageManager.getServiceInfo(
            ComponentName(context, BluetoothSpikeService::class.java),
            PackageManager.ComponentInfoFlags.of(0),
        )
        assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE, info.foregroundServiceType)
        assertTrue(!info.exported)
    }

    @Test
    fun `debug manifest exposes spike launcher and Bluetooth permissions`() {
        val launchers = context.packageManager.queryIntentActivities(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setPackage(context.packageName),
            PackageManager.ResolveInfoFlags.of(0),
        )
        val permissions = context.packageManager.getPackageInfo(
            context.packageName,
            PackageManager.PackageInfoFlags.of(PackageManager.GET_PERMISSIONS.toLong()),
        ).requestedPermissions?.toSet() ?: emptySet()

        assertTrue(launchers.any { it.activityInfo.name == BluetoothSpikeActivity::class.java.name })
        assertTrue(Manifest.permission.BLUETOOTH_ADVERTISE in permissions)
        assertTrue(Manifest.permission.BLUETOOTH_CONNECT in permissions)
        assertTrue(Manifest.permission.POST_NOTIFICATIONS in permissions)
        assertTrue(Manifest.permission.FOREGROUND_SERVICE_CONNECTED_DEVICE in permissions)
    }
}
