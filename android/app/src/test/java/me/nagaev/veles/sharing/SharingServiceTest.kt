package me.nagaev.veles.sharing

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SharingServiceTest {
    @Test fun enabledIntentSurvivesUnavailableRestorationWithoutClaimingAdvertising() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        shadowOf(context.packageManager).setSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE, false)
        SharingState.setEnabled(context, true)
        val controller = Robolectric.buildService(SharingService::class.java).create()
        assertEquals(android.app.Service.START_NOT_STICKY, controller.get().onStartCommand(null, 0, 1))
        SharingState.load(context)
        assertTrue(SharingState.enabled.value)
        assertEquals(SharingAvailability.Unsupported, SharingState.availability.value)
        controller.destroy()
        SharingState.setEnabled(context, false)
    }

    @Test fun destructionNeverClaimsOldAdvertisingIsStillAvailable() {
        val controller = Robolectric.buildService(SharingService::class.java).create()
        SharingState.update(SharingAvailability.Advertising)
        controller.destroy()
        assertEquals(SharingAvailability.Disabled, SharingState.availability.value)
    }

    @Test fun stopClearsEnabledIntentAndAvailability() {
        val controller = Robolectric.buildService(SharingService::class.java).create()
        val service = controller.get()
        service.getSharedPreferences("sharing", Context.MODE_PRIVATE).edit().putBoolean("enabled", true).commit()
        SharingState.update(SharingAvailability.Advertising)
        service.onStartCommand(Intent(service, SharingService::class.java).setAction(SharingService.STOP), 0, 1)
        assertEquals(SharingAvailability.Disabled, SharingState.availability.value)
        assertFalse(service.getSharedPreferences("sharing", Context.MODE_PRIVATE).getBoolean("enabled", true))
        controller.destroy()
    }

    @Test fun disabledProcessRestorationDoesNotAdvertise() {
        val controller = Robolectric.buildService(SharingService::class.java).create()
        val service = controller.get()
        service.getSharedPreferences("sharing", Context.MODE_PRIVATE).edit().clear().commit()
        SharingState.update(SharingAvailability.Disabled)
        assertEquals(android.app.Service.START_NOT_STICKY, service.onStartCommand(null, 0, 1))
        assertEquals(SharingAvailability.Disabled, SharingState.availability.value)
        controller.destroy()
    }

    @Test fun manifestHasPrivateConnectedDeviceServiceAndNoScanOrLocationPermissions() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val info = context.packageManager.getServiceInfo(android.content.ComponentName(context, SharingService::class.java), 0)
        assertFalse(info.exported)
        assertEquals(android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE, info.foregroundServiceType)
        val permissions = context.packageManager.getPackageInfo(context.packageName, PackageManager.GET_PERMISSIONS)
            .requestedPermissions.orEmpty().toSet()
        assertTrue("android.permission.BLUETOOTH_ADVERTISE" in permissions)
        assertTrue("android.permission.BLUETOOTH_CONNECT" in permissions)
        assertFalse("android.permission.BLUETOOTH_SCAN" in permissions)
        assertFalse("android.permission.ACCESS_FINE_LOCATION" in permissions)
    }
}
