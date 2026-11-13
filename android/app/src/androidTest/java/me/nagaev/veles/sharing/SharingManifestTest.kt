package me.nagaev.veles.sharing

import android.content.ComponentName
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SharingManifestTest {
    @Test fun installedManifestIsPeripheralOnlyAndServiceIsPrivate() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val service = context.packageManager.getServiceInfo(ComponentName(context, SharingService::class.java), 0)
        assertFalse(service.exported)
        assertEquals(ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE, service.foregroundServiceType)
        val permissions = context.packageManager.getPackageInfo(context.packageName, PackageManager.GET_PERMISSIONS)
            .requestedPermissions.orEmpty().toSet()
        assertTrue("android.permission.BLUETOOTH_CONNECT" in permissions)
        assertTrue("android.permission.BLUETOOTH_ADVERTISE" in permissions)
        assertFalse("android.permission.BLUETOOTH_SCAN" in permissions)
        assertFalse("android.permission.ACCESS_FINE_LOCATION" in permissions)
    }
}
