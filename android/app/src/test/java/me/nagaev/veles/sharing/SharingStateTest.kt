package me.nagaev.veles.sharing

import android.Manifest
import android.app.Application
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import io.mockk.every
import io.mockk.mockk
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SharingStateTest {
    private val context = ApplicationProvider.getApplicationContext<Application>()
    private val adapter: BluetoothAdapter get() = context.getSystemService(BluetoothManager::class.java).adapter

    /** BLE-capable, permitted, enabled, and a controller that reports no multi-advertisement support. */
    private fun ready() {
        shadowOf(context.packageManager).setSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE, true)
        shadowOf(context).grantPermissions(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE)
        shadowOf(adapter).setState(BluetoothAdapter.STATE_ON)
        shadowOf(adapter).setIsMultipleAdvertisementSupported(false)
    }

    @Test fun singleAdvertisementAdapterPasses() {
        ready()
        assertNull(SharingState.prerequisite(context))
    }

    @Test fun missingLeFeatureIsUnsupported() {
        ready()
        shadowOf(context.packageManager).setSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE, false)
        assertEquals(SharingAvailability.Unsupported, SharingState.prerequisite(context))
    }

    @Test fun missingPermissionIsPermissionRequired() {
        ready()
        shadowOf(context).denyPermissions(Manifest.permission.BLUETOOTH_ADVERTISE)
        assertEquals(SharingAvailability.PermissionRequired, SharingState.prerequisite(context))
    }

    @Test fun adapterOffIsBluetoothOff() {
        ready()
        shadowOf(adapter).setState(BluetoothAdapter.STATE_OFF)
        assertEquals(SharingAvailability.BluetoothOff, SharingState.prerequisite(context))
    }

    @Test fun permissionPrecedesBluetoothOff() {
        ready()
        shadowOf(context).denyPermissions(Manifest.permission.BLUETOOTH_CONNECT)
        shadowOf(adapter).setState(BluetoothAdapter.STATE_OFF)
        assertEquals(SharingAvailability.PermissionRequired, SharingState.prerequisite(context))
    }

    @Test fun missingAdapterIsUnsupported() {
        val manager = mockk<BluetoothManager> { every { this@mockk.adapter } returns null }
        assertEquals(SharingAvailability.Unsupported, SharingState.prerequisite(permittedContext(manager)))
    }

    @Test fun refusedAdapterReadIsPermissionRequired() {
        val refusing = mockk<BluetoothAdapter> { every { isEnabled } throws SecurityException("revoked") }
        val manager = mockk<BluetoothManager> { every { this@mockk.adapter } returns refusing }
        assertEquals(SharingAvailability.PermissionRequired, SharingState.prerequisite(permittedContext(manager)))
    }

    /** A context that passes the feature and permission checks and serves [manager]. */
    private fun permittedContext(manager: BluetoothManager): Context {
        val packages = mockk<PackageManager> { every { hasSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE) } returns true }
        return mockk {
            every { packageManager } returns packages
            every { checkPermission(any(), any(), any()) } returns PackageManager.PERMISSION_GRANTED
            every { getSystemService(BluetoothManager::class.java) } returns manager
        }
    }
}
