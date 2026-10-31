package me.nagaev.veles.sharing

import android.content.Context
import android.content.Intent
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import me.nagaev.veles.permissions.PermissionsActivity
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertNull
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * Opt-in physical fixture. A controller creates `files/otp23-stop` to finish and release BLE.
 *
 * `-e otp23_unsolicited_after_s N` additionally makes the phone push one deterministic, unsolicited
 * 4096-byte synthetic record to each central N seconds after that central subscribes (strict
 * background-delivery validation: the desktop sends nothing during the idle interval). Hash-only
 * evidence is written to `files/otp23-unsolicited.json` and survives the run for retrieval afterwards.
 *
 * `-e otp23_transport_evidence true -e otp23_run_id ID` records the actual notify API return codes and
 * `onNotificationSent` statuses per peer into `files/otp23-transport-evidence.json` (see docs/otp-23-phone-telemetry.md).
 */
@RunWith(AndroidJUnit4::class)
class SharingFeasibilityTest {
    @Test fun holdSyntheticPeripheralForWindowsCentral() = runBlocking {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.getString("otp23") == "true")
        val context = instrumentation.targetContext
        val ready = File(context.filesDir, "otp23-ready")
        val stop = File(context.filesDir, "otp23-stop")
        val evidence = File(context.filesDir, "otp23-unsolicited.json")
        val transport = File(context.filesDir, "otp23-transport-evidence.json")
        val runId = args.getString("otp23_run_id")
        val telemetryOn = args.getString("otp23_transport_evidence") == "true"
        require(!telemetryOn || (runId != null && RUN_ID.matches(runId))) { "otp23_transport_evidence requires a valid otp23_run_id" }
        val startedAt = System.currentTimeMillis()
        // Everything that mutates fixture state sits inside try/finally, so a failed prerequisite cannot leak it.
        try {
            ready.delete()
            stop.delete()
            evidence.delete()
            transport.delete()
            SyntheticTransportFixture.unsolicitedEvidence.clear()
            SyntheticTransportFixture.telemetry = if (telemetryOn) TransportTelemetry(runId!!) else null
            SyntheticTransportFixture.unsolicitedAfterMillis = args.getString("otp23_unsolicited_after_s")?.toLong()?.times(1000)
            assertNull("Requires Bluetooth advertising and nearby-device grants", SharingState.prerequisite(context))
            ActivityScenario.launch(PermissionsActivity::class.java).use { scenario ->
                scenario.onActivity { it.startService(Intent(it, SharingService::class.java)) }
                withTimeout(10000) { SharingState.availability.first { it == SharingAvailability.Advertising } }
                ready.writeText("advertising")
                var written = 0
                var changes = -1L
                withTimeout(1800000) {
                    while (!stop.exists()) {
                        val seen = SyntheticTransportFixture.unsolicitedEvidence.toList()
                        if (seen.size != written) {
                            evidence.writeText(unsolicitedJson(seen))
                            written = seen.size
                        }
                        SyntheticTransportFixture.telemetry?.snapshot()?.takeIf { it.changes != changes }?.let {
                            changes = it.changes
                            writeAtomically(transport, transportJson(context, it, startedAt, null, false))
                        }
                        delay(250)
                    }
                }
            }
        } finally {
            val telemetry = SyntheticTransportFixture.telemetry
            SyntheticTransportFixture.unsolicitedAfterMillis = null
            var stopped = false
            try {
                context.stopService(Intent(context, SharingService::class.java))
                SharingState.setEnabled(context, false)
            } finally {
                // stopService returns at once; wait (bounded) for onDestroy to release BLE, then let late callbacks land.
                withContext(NonCancellable) {
                    stopped = withTimeoutOrNull(5000) {
                        SharingState.availability.first { it != SharingAvailability.Starting && it != SharingAvailability.Advertising }
                    } != null
                    delay(750)
                }
                try {
                    if (telemetry != null) {
                        writeAtomically(transport, transportJson(context, telemetry.snapshot(), startedAt, System.currentTimeMillis(), stopped))
                    }
                } finally {
                    SyntheticTransportFixture.telemetry = null
                    ready.delete()
                    stop.delete()
                }
            }
        }
    }

    private fun writeAtomically(target: File, text: String) {
        val tmp = File(target.parentFile, target.name + ".tmp")
        tmp.writeText(text)
        Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }

    /** Hash-only: peers are SHA-256 prefixes, events carry sizes and codes, never bytes or addresses. */
    private fun transportJson(context: Context, snap: TransportTelemetry.Snapshot, startedAt: Long, finishedAt: Long?, stopped: Boolean): String {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        return JSONObject()
            .put("kind", "otp23-transport-evidence")
            .put("run_id", snap.runId)
            .put("started_at_epoch_ms", startedAt)
            .put("finished_at_epoch_ms", finishedAt ?: JSONObject.NULL)
            .put("final", finishedAt != null)
            .put("final_after_service_stopped", finishedAt != null && stopped)
            .put("clean", snap.clean && finishedAt != null && stopped)
            .put("source_package", context.packageName)
            .put("source_version_name", info.versionName)
            .put("source_version_code", info.longVersionCode)
            .put("truncated", snap.truncated)
            .put("peers_dropped", snap.peersDropped)
            .put(
                "peers",
                JSONArray(
                    snap.peers.map { p ->
                        JSONObject()
                            .put("peer_sha256_prefix", p.peer)
                            .put("notify_calls", p.notifyCalls)
                            .put("api_success", p.apiSuccess)
                            .put("api_nonzero", p.apiNonZero)
                            .put("attempted_bytes", p.attemptedBytes)
                            .put("callbacks", p.callbacks)
                            .put("callback_success", p.callbackSuccess)
                            .put("callback_nonzero", p.callbackNonZero)
                            .put("unbound_callbacks", p.unboundCallbacks)
                            .put("pending_unresolved", p.pendingUnresolved)
                            .put("pending_dropped", p.pendingDropped)
                            .put("callbacks_seen", p.callbacksSeen)
                            .put("callbacks_seen_nonzero", p.callbacksSeenNonZero)
                            .put("callbacks_after_close", p.callbacksAfterClose)
                            .put("connects", p.connects)
                            .put("disconnects", p.disconnects)
                            .put("subscribes", p.subscribes)
                            .put("mtu", p.mtu ?: JSONObject.NULL)
                            .put("truncated", p.truncated)
                            .put(
                                "events",
                                JSONArray(
                                    p.events.map { e ->
                                        JSONObject()
                                            .put("seq", e.seq)
                                            .put("kind", e.kind)
                                            .put("epoch_ms", e.epochMs)
                                            .put("elapsed_realtime_ms", e.elapsedMs)
                                            .put("code", e.code ?: JSONObject.NULL)
                                            .put("label", e.label ?: JSONObject.NULL)
                                            .put("bytes", e.bytes)
                                            .put("bound_op", e.boundOp ?: JSONObject.NULL)
                                    },
                                ),
                            )
                    },
                ),
            ).toString(2)
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

    private companion object {
        val RUN_ID = Regex("[A-Za-z0-9._-]{1,64}")
    }
}
