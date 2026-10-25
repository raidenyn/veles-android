package me.nagaev.veles.sharing

import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import me.nagaev.veles.permissions.PermissionsActivity
import org.junit.Assert.assertNull
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/** Opt-in physical fixture. A controller creates `files/otp23-stop` to finish and release BLE. */
@RunWith(AndroidJUnit4::class)
class SharingFeasibilityTest {
    @Test fun holdSyntheticPeripheralForWindowsCentral() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue(InstrumentationRegistry.getArguments().getString("otp23") == "true")
        val context = instrumentation.targetContext
        val ready = File(context.filesDir, "otp23-ready")
        val stop = File(context.filesDir, "otp23-stop")
        ready.delete()
        stop.delete()
        assertNull("Requires Bluetooth advertising and nearby-device grants", SharingState.prerequisite(context))
        ActivityScenario.launch(PermissionsActivity::class.java).use { scenario ->
            try {
                scenario.onActivity { it.startService(Intent(it, SharingService::class.java)) }
                withTimeout(10000) { SharingState.availability.first { it == SharingAvailability.Advertising } }
                ready.writeText("advertising")
                withTimeout(1800000) { while (!stop.exists()) delay(250) }
            } finally {
                context.stopService(Intent(context, SharingService::class.java))
                SharingState.setEnabled(context, false)
                ready.delete()
                stop.delete()
            }
        }
    }
}
