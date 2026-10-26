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
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertNull
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * Opt-in physical fixture. A controller creates `files/otp23-stop` to finish and release BLE.
 *
 * `-e otp23_unsolicited_after_s N` additionally makes the phone push one deterministic, unsolicited
 * 4096-byte synthetic record to each central N seconds after that central subscribes (strict
 * background-delivery validation: the desktop sends nothing during the idle interval). Hash-only
 * evidence is written to `files/otp23-unsolicited.json` and survives the run for retrieval afterwards.
 */
@RunWith(AndroidJUnit4::class)
class SharingFeasibilityTest {
    @Test fun holdSyntheticPeripheralForWindowsCentral() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue(InstrumentationRegistry.getArguments().getString("otp23") == "true")
        val context = instrumentation.targetContext
        val ready = File(context.filesDir, "otp23-ready")
        val stop = File(context.filesDir, "otp23-stop")
        val evidence = File(context.filesDir, "otp23-unsolicited.json")
        ready.delete()
        stop.delete()
        evidence.delete()
        SyntheticTransportFixture.unsolicitedEvidence.clear()
        SyntheticTransportFixture.unsolicitedAfterMillis = InstrumentationRegistry.getArguments()
            .getString("otp23_unsolicited_after_s")?.toLong()?.times(1000)
        assertNull("Requires Bluetooth advertising and nearby-device grants", SharingState.prerequisite(context))
        ActivityScenario.launch(PermissionsActivity::class.java).use { scenario ->
            try {
                scenario.onActivity { it.startService(Intent(it, SharingService::class.java)) }
                withTimeout(10000) { SharingState.availability.first { it == SharingAvailability.Advertising } }
                ready.writeText("advertising")
                var written = 0
                withTimeout(1800000) {
                    while (!stop.exists()) {
                        val seen = SyntheticTransportFixture.unsolicitedEvidence.toList()
                        if (seen.size != written) {
                            evidence.writeText(unsolicitedJson(seen))
                            written = seen.size
                        }
                        delay(250)
                    }
                }
            } finally {
                SyntheticTransportFixture.unsolicitedAfterMillis = null
                context.stopService(Intent(context, SharingService::class.java))
                SharingState.setEnabled(context, false)
                ready.delete()
                stop.delete()
            }
        }
    }

    private fun unsolicitedJson(seen: List<SyntheticTransportFixture.Unsolicited>): String = JSONObject()
        .put("kind", "otp23-unsolicited-evidence")
        .put(
            "pushes",
            JSONArray(
                seen.map {
                    JSONObject()
                        .put("peer_sha256_prefix", it.peer)
                        .put("subscribed_at_epoch_ms", it.subscribedAtMillis)
                        .put("fired_at_epoch_ms", it.firedAtMillis)
                        .put("configured_delay_ms", it.delayMillis)
                        .put("queued", it.queued)
                        .put("bytes", it.bytes)
                        .put("sha256", it.sha256)
                },
            ),
        ).toString(2)
}
