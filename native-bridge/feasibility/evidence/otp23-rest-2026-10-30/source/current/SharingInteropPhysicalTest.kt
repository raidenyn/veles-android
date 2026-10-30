@file:Suppress("MissingPermission", "MagicNumber")

package me.nagaev.veles.sharing

import android.Manifest
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.pm.PackageManager
import android.os.ParcelUuid
import android.os.SystemClock
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import me.nagaev.veles.permissions.PermissionsActivity
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** Opt-in real-radio evidence. Callback injection is explicitly labelled, never an OS observation. */
@RunWith(AndroidJUnit4::class)
class SharingInteropPhysicalTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context get() = instrumentation.targetContext

    private fun runId(): String {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.getString("otp23_a01") == "true")
        return requireNotNull(args.getString("otp23_run_id")).also {
            require(it.matches(Regex("[A-Za-z0-9._-]{1,64}")))
        }
    }

    private fun write(file: File, data: JSONObject) {
        val temporary = File(file.parentFile, file.name + ".tmp")
        temporary.writeText(data.toString(2))
        Files.move(temporary.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }

    private fun field(server: SharingGattServer, name: String): Any = requireNotNull(server.javaClass.getDeclaredField(name).apply { isAccessible = true }.get(server))

    @Test fun holdObservedPeripheral() {
        val id = runId()
        val output = File(context.filesDir, "otp23-a01-server.json")
        val readyFile = File(context.filesDir, "otp23-a01-ready")
        val stop = File(context.filesDir, "otp23-a01-stop")
        val command = File(context.filesDir, "otp23-a01-command.json")
        val ack = File(context.filesDir, "otp23-a01-ack.json")
        val states = JSONArray()
        val records = JSONArray()
        val injections = JSONArray()
        val connected = CountDownLatch(1)
        var server: SharingGattServer? = null
        var previousState = ""
        var handledNonce: String? = null
        var failure: Throwable? = null
        listOf(readyFile, stop, command, ack).forEach { it.delete() }
        check(SyntheticTransportFixture.telemetry == null) { "Another fixture is running" }
        SyntheticTransportFixture.telemetry = TransportTelemetry(id)

        fun snapshot(): JSONObject {
            val own = requireNotNull(server)
            val peers = (field(own, "devices") as Map<*, *>).values.filterIsInstance<BluetoothDevice>()
            val telemetry = requireNotNull(SyntheticTransportFixture.telemetry).snapshot()
            return JSONObject()
                .put("epoch_ms", System.currentTimeMillis()).put("elapsed_ms", SystemClock.elapsedRealtime())
                .put("peers", JSONArray(peers.map { SyntheticTransportFixture.sha256Hex(it.address.toByteArray()).take(16) }))
                .put("ready_count", (field(own, "ready") as Set<*>).size)
                .put("subscribed_count", (field(own, "subscribed") as Set<*>).size)
                .put("connected_at_elapsed_ms", JSONArray((field(own, "connectedAt") as Map<*, *>).values.toList()))
                .put("records", records.length())
                .put(
                    "transport_events",
                    JSONArray(
                        telemetry.peers.flatMap { p ->
                            p.events.map { e ->
                                JSONObject().put("peer", p.peer).put("kind", e.kind)
                                    .put("epoch_ms", e.epochMs).put("elapsed_ms", e.elapsedMs)
                                    .put("code", e.code ?: JSONObject.NULL).put("bytes", e.bytes)
                            }
                        },
                    ),
                )
        }

        try {
            ActivityScenario.launch(PermissionsActivity::class.java).use {
                instrumentation.runOnMainSync {
                    server = SharingGattServer(context, { state ->
                        if (state == SharingAvailability.Advertising) connected.countDown()
                    }, { _, record ->
                        records.put(
                            JSONObject().put("epoch_ms", System.currentTimeMillis())
                                .put("elapsed_ms", SystemClock.elapsedRealtime()).put("bytes", record.size)
                                .put("sha256", SyntheticTransportFixture.sha256Hex(record)),
                        )
                        SyntheticTransportFixture.reply(record)
                    }).also { it.start() }
                }
                assertTrue("Advertising did not start", connected.await(10, TimeUnit.SECONDS))
                readyFile.writeText(id)
                val deadline = SystemClock.elapsedRealtime() + 600000
                while (!stop.exists()) {
                    check(SystemClock.elapsedRealtime() < deadline) { "Physical fixture deadline expired" }
                    if (command.exists()) {
                        val request = JSONObject(command.readText())
                        val nonce = request.getString("nonce")
                        if (request.getString("run_id") == id && nonce != handledNonce) {
                            require(request.getString("cmd") == "inject_duplicate_connected")
                            lateinit var before: JSONObject
                            instrumentation.runOnMainSync {
                                before = snapshot()
                                val own = requireNotNull(server)
                                val devices = (field(own, "devices") as Map<*, *>).values.filterIsInstance<BluetoothDevice>()
                                check(devices.size == 1 && before.getInt("ready_count") == 1 && before.getInt("subscribed_count") == 1)
                                (field(own, "callbacks") as BluetoothGattServerCallback)
                                    .onConnectionStateChange(devices.single(), BluetoothGatt.GATT_SUCCESS, BluetoothProfile.STATE_CONNECTED)
                            }
                            instrumentation.waitForIdleSync()
                            instrumentation.runOnMainSync {
                                val after = snapshot()
                                assertEquals(before.getJSONArray("peers").toString(), after.getJSONArray("peers").toString())
                                assertEquals(before.getJSONArray("connected_at_elapsed_ms").toString(), after.getJSONArray("connected_at_elapsed_ms").toString())
                                assertEquals(1, after.getInt("ready_count"))
                                assertEquals(1, after.getInt("subscribed_count"))
                                val event = JSONObject().put("run_id", id).put("nonce", nonce)
                                    .put("origin", "instrumentation injection into the real callback on a live physical link; not a naturally repeated OS callback")
                                    .put("before", before).put("after", after)
                                injections.put(event)
                                write(ack, event)
                            }
                            handledNonce = nonce
                        }
                    }
                    instrumentation.runOnMainSync {
                        val state = snapshot()
                        // Keep only state/event changes, with the actual sample time retained.
                        val key = state.toString().replace(Regex("\"(?:epoch_ms|elapsed_ms)\":\\d+,?"), "")
                        if (key != previousState) {
                            check(states.length() < 2000) { "State evidence exceeded its bound" }
                            states.put(state)
                            previousState = key
                        }
                        write(
                            output,
                            JSONObject().put("run_id", id).put("status", "RUNNING")
                                .put("states", states).put("records", records).put("injections", injections),
                        )
                    }
                    Thread.sleep(20)
                }
            }
        } catch (error: Throwable) {
            failure = error
            throw error
        } finally {
            instrumentation.runOnMainSync { server?.close() }
            Thread.sleep(750)
            write(
                output,
                JSONObject().put("run_id", id).put("status", if (failure == null) "CAPTURED" else "ERROR")
                    .put("error", failure?.javaClass?.simpleName ?: JSONObject.NULL)
                    .put("states", states).put("records", records).put("injections", injections).put("server_closed", true),
            )
            SyntheticTransportFixture.telemetry = null
            readyFile.delete()
        }
    }

    private data class Event(val kind: String, val code: Int, val extra: Int = 0, val value: ByteArray? = null) {
        val epochMs = System.currentTimeMillis()
        val elapsedMs = SystemClock.elapsedRealtime()
        fun json(): JSONObject = JSONObject().put("kind", kind).put("code", code).put("extra", extra)
            .put("epoch_ms", epochMs).put("elapsed_ms", elapsedMs)
            .put("bytes", value?.size ?: 0).put("sha256", value?.let(SyntheticTransportFixture::sha256Hex) ?: JSONObject.NULL)
    }

    @Test fun cccdBeforeMtuIsRejectedOnRealRadio() {
        val id = runId()
        val events = CopyOnWriteArrayList<Event>()
        val queue = LinkedBlockingQueue<Event>()
        val notifications = LinkedBlockingQueue<ByteArray>()
        val output = File(context.filesDir, "otp23-a01-client.json")
        val discovery = CountDownLatch(1)
        var peripheral: BluetoothDevice? = null
        var gatt: BluetoothGatt? = null
        var error: Throwable? = null
        val manager = context.getSystemService(BluetoothManager::class.java)
        val scanner = manager.adapter.bluetoothLeScanner
        fun emit(event: Event) {
            events.add(event)
            queue.offer(event)
        }
        fun await(kind: String): Event {
            val deadline = SystemClock.elapsedRealtime() + 10000
            while (SystemClock.elapsedRealtime() < deadline) {
                val event = queue.poll(100, TimeUnit.MILLISECONDS) ?: continue
                if (event.kind == kind) return event
                check(event.kind != "connection" || event.extra != BluetoothProfile.STATE_DISCONNECTED) { "Link disconnected before $kind" }
            }
            error("No $kind callback")
        }
        val scanCallback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                if (peripheral == null) {
                    peripheral = result.device
                    discovery.countDown()
                }
            }
        }
        val callback = object : BluetoothGattCallback() {
            override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) = emit(Event("connection", status, newState))
            override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) = emit(Event("services", status))
            override fun onMtuChanged(gatt: BluetoothGatt, mtu: Int, status: Int) = emit(Event("mtu", status, mtu))
            override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) = emit(Event("cccd", status))
            override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) = emit(Event("write", status))
            override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, value: ByteArray) {
                emit(Event("notification", BluetoothGatt.GATT_SUCCESS, value = value.copyOf()))
                notifications.offer(value.copyOf())
            }
        }
        var activity: ActivityScenario<PermissionsActivity>? = null
        try {
            val scenario = ActivityScenario.launch(PermissionsActivity::class.java)
            activity = scenario
            scenario.onActivity {
                if (it.checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) != PackageManager.PERMISSION_GRANTED) {
                    it.requestPermissions(arrayOf(Manifest.permission.BLUETOOTH_SCAN), 2301)
                }
            }
            val permissionDeadline = SystemClock.elapsedRealtime() + 60000
            while (context.checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) != PackageManager.PERMISSION_GRANTED) {
                check(SystemClock.elapsedRealtime() < permissionDeadline) { "Fixture SCAN permission not granted" }
                Thread.sleep(100)
            }
            scanner.startScan(
                listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(SERVICE)).build()),
                ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(),
                scanCallback,
            )
            assertTrue("No own-service advertiser", discovery.await(20, TimeUnit.SECONDS))
            scanner.stopScan(scanCallback)
            val link = requireNotNull(requireNotNull(peripheral).connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE))
            gatt = link
            val connected = await("connection")
            assertEquals(BluetoothGatt.GATT_SUCCESS, connected.code)
            assertEquals(BluetoothProfile.STATE_CONNECTED, connected.extra)
            assertTrue(link.discoverServices())
            assertEquals(BluetoothGatt.GATT_SUCCESS, await("services").code)
            val service = requireNotNull(link.getService(SERVICE))
            val notify = requireNotNull(service.getCharacteristic(NOTIFY))
            val writer = requireNotNull(service.getCharacteristic(WRITE))
            val cccd = requireNotNull(notify.getDescriptor(CCCD))
            assertTrue(link.setCharacteristicNotification(notify, true))
            assertFalse("MTU callback already occurred; negative setup is invalid", events.any { it.kind == "mtu" })
            assertEquals(BluetoothStatusCodes.SUCCESS, link.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE))
            assertEquals(BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, await("cccd").code)
            val before = byteArrayOf(0x80.toByte(), 1, 0, 4, 0x56, 0x45, 0x4c, 1)
            assertEquals(BluetoothStatusCodes.SUCCESS, link.writeCharacteristic(writer, before, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE))
            assertEquals(BluetoothGatt.GATT_SUCCESS, await("write").code)
            assertEquals(null, notifications.poll(300, TimeUnit.MILLISECONDS))
            emit(Event("negative_phase_complete", BluetoothGatt.GATT_SUCCESS))
            assertTrue(link.requestMtu(517))
            val mtu = await("mtu")
            assertEquals(BluetoothGatt.GATT_SUCCESS, mtu.code)
            assertTrue("MTU below protocol floor", mtu.extra >= 185)
            assertEquals(BluetoothStatusCodes.SUCCESS, link.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE))
            assertEquals(BluetoothGatt.GATT_SUCCESS, await("cccd").code)
            val valid = before.copyOf().also { it[1] = 2 }
            assertEquals(BluetoothStatusCodes.SUCCESS, link.writeCharacteristic(writer, valid, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE))
            val reply = requireNotNull(notifications.poll(5, TimeUnit.SECONDS)) { "No post-MTU reply" }
            assertEquals(8, reply.size)
            assertTrue(reply.copyOfRange(4, 8).contentEquals(byteArrayOf(0x56, 0x45, 0x4c, 2)))
            emit(Event("positive_phase_complete", BluetoothGatt.GATT_SUCCESS))
        } catch (failure: Throwable) {
            error = failure
            throw failure
        } finally {
            scanner.stopScan(scanCallback)
            gatt?.disconnect()
            gatt?.close()
            activity?.close()
            write(
                output,
                JSONObject().put("run_id", id).put("status", if (error == null) "PASS" else "FAIL")
                    .put("error", error?.javaClass?.simpleName ?: JSONObject.NULL)
                    .put("events", JSONArray(events.map { it.json() }))
                    .put("permission_scope", "SCAN declared only by opt-in fixture manifest; ordinary runtime permission request, no shell identity adoption"),
            )
        }
    }

    private companion object {
        val SERVICE: UUID = UUID.fromString("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val WRITE: UUID = UUID.fromString("8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val NOTIFY: UUID = UUID.fromString("8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }
}
