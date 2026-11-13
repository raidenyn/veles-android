package me.nagaev.veles.sharing

import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import me.nagaev.veles.permissions.PermissionsActivity
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Physical smoke: grant CONNECT/ADVERTISE and enable Bluetooth before selecting this class. */
@RunWith(AndroidJUnit4::class)
class SharingLifecycleTest {
    @Test fun visibleStartAdvertisesAndExplicitStopCleansUp() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assumeTrue("Requires BLE advertising, Bluetooth enabled and nearby-device grants", SharingState.prerequisite(context) == null)
        ActivityScenario.launch(PermissionsActivity::class.java).use { scenario ->
            try {
                scenario.onActivity { it.startService(Intent(it, SharingService::class.java)) }
                withTimeout(10000) { SharingState.availability.first { it == SharingAvailability.Advertising } }
                scenario.onActivity {
                    it.startService(Intent(it, SharingService::class.java).setAction(SharingService.STOP))
                }
                withTimeout(5000) { SharingState.availability.first { it == SharingAvailability.Disabled } }
            } finally {
                context.stopService(Intent(context, SharingService::class.java))
                SharingState.setEnabled(context, false)
            }
        }
        Unit
    }
}
