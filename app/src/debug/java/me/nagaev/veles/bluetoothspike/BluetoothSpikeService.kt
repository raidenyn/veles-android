package me.nagaev.veles.bluetoothspike

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.ParcelUuid
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import me.nagaev.veles.R
import java.security.SecureRandom
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

internal class BluetoothSpikeService : Service() {
    companion object {
        const val ACTION_START = "me.nagaev.veles.bluetoothspike.START"
        const val ACTION_PUSH_NOW = "me.nagaev.veles.bluetoothspike.PUSH_NOW"
        const val ACTION_SCHEDULE_PUSH = "me.nagaev.veles.bluetoothspike.SCHEDULE_PUSH"
        const val ACTION_OPEN = "me.nagaev.veles.bluetoothspike.OPEN"
        const val EXTRA_DELAY_MILLIS = "delay_millis"
        const val SMOKE_PUSH_DELAY_MILLIS = 10_000L
        const val LIFECYCLE_PUSH_DELAY_MILLIS = 20 * 60 * 1_000L
        private const val CHANNEL_ID = "VelesBluetoothSpike"
        private const val NOTIFICATION_ID = 7701
        private const val MAINTENANCE_INTERVAL_MILLIS = 5_000L
        private const val MESSAGE_ID_MAX = 0xFFFF
        private const val PHONE_LABEL_SUFFIX_RANGE = 10_000

        fun intent(context: Context, action: String): Intent = Intent(context, BluetoothSpikeService::class.java).setAction(action)
    }

    @Volatile private var started = false

    @Volatile private var gattServer: BluetoothGattServer? = null

    @Volatile private var eventsCharacteristic: BluetoothGattCharacteristic? = null

    @Volatile private var advertiser: BluetoothLeAdvertiser? = null
    private var retriedWithoutName = false

    private val devices = ConcurrentHashMap<String, BluetoothDevice>()
    private val clientLabels = ConcurrentHashMap<String, String>()
    private val messageCounters = ConcurrentHashMap<String, AtomicInteger>()
    private val eventCounter = AtomicInteger(0)

    private val secureRandom = SecureRandom()
    private val phoneLabel = Build.MODEL + "-" + String.format(Locale.US, "%04d", secureRandom.nextInt(PHONE_LABEL_SUFFIX_RANGE))

    private val sessions = SpikeSessionRegistry(
        clockMillis = { System.currentTimeMillis() },
        randomBytes = { n -> ByteArray(n).also { secureRandom.nextBytes(it) } },
    )
    private val reassembler = SpikeFrameReassembler { System.currentTimeMillis() }
    private val queues = SpikeDeliveryQueues()

    private val handler = Handler(Looper.getMainLooper())
    private val scheduledPushRunnables = mutableListOf<Runnable>()
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var maintenanceJob: Job? = null

    private val gattCallback = object : BluetoothGattServerCallback() {
        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) {
            val clientId = device.address
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                val label = deviceNameOrFallback(device, clientId)
                clientLabels[clientId] = label
                devices[clientId] = device
                sessions.onConnected(clientId, label)
                BluetoothSpikeStateStore.client(clientId, label, subscribed = false, authenticated = false)
                BluetoothSpikeStateStore.event("Client connected: $clientId ($label)")
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                cleanupClient(clientId)
            }
        }

        override fun onServiceAdded(status: Int, service: BluetoothGattService) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                BluetoothSpikeStateStore.event("GATT service added")
                startAdvertising(includeDeviceName = true)
            } else {
                BluetoothSpikeStateStore.error("GATT service add failed: $status")
                stopSelf()
            }
        }

        override fun onCharacteristicWriteRequest(
            device: BluetoothDevice,
            requestId: Int,
            characteristic: BluetoothGattCharacteristic,
            preparedWrite: Boolean,
            responseNeeded: Boolean,
            offset: Int,
            value: ByteArray,
        ) {
            val clientId = device.address
            if (characteristic.uuid != SpikeProtocol.COMMAND_UUID) {
                if (responseNeeded) gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_FAILURE, 0, value)
                return
            }
            if (preparedWrite || offset != 0) {
                if (responseNeeded) gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_FAILURE, 0, value)
                return
            }
            if (responseNeeded) gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, 0, value)
            try {
                val complete = reassembler.accept(clientId, value) ?: return
                val message = SpikeProtocol.decode(complete)
                handleWireMessage(clientId, message)
            } catch (e: Exception) {
                BluetoothSpikeStateStore.error("Write handling failed: ${e.message}")
                sendError(clientId, "malformed_message")
            }
        }

        override fun onDescriptorWriteRequest(
            device: BluetoothDevice,
            requestId: Int,
            descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean,
            responseNeeded: Boolean,
            offset: Int,
            value: ByteArray,
        ) {
            val clientId = device.address
            if (descriptor.uuid == SpikeProtocol.CCC_UUID) {
                if (responseNeeded) gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, 0, value)
                val enable = value.isNotEmpty() && value[0].toInt() != 0
                if (enable) {
                    sessions.onSubscribed(clientId)
                    updateClientState(clientId)
                    BluetoothSpikeStateStore.event("Client subscribed: $clientId")
                } else {
                    BluetoothSpikeStateStore.event("Client unsubscribed: $clientId")
                }
            } else {
                if (responseNeeded) gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_FAILURE, 0, value)
            }
        }

        override fun onNotificationSent(device: BluetoothDevice, status: Int) {
            val clientId = device.address
            if (status != BluetoothGatt.GATT_SUCCESS) {
                BluetoothSpikeStateStore.error("Notify failed ($status) for $clientId")
                queues.remove(clientId)
                return
            }
            val next = queues.complete(clientId) ?: return
            val events = eventsCharacteristic ?: return
            sendNotification(device, events, next)
        }
    }

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settings: AdvertiseSettings) {
            BluetoothSpikeStateStore.advertising(true)
            BluetoothSpikeStateStore.event("Advertising started")
        }

        override fun onStartFailure(errorCode: Int) {
            if (errorCode == AdvertiseCallback.ADVERTISE_FAILED_DATA_TOO_LARGE && !retriedWithoutName) {
                retriedWithoutName = true
                BluetoothSpikeStateStore.event("Advertising without device name")
                startAdvertising(includeDeviceName = false)
            } else {
                BluetoothSpikeStateStore.error("Advertising failed: $errorCode")
                BluetoothSpikeStateStore.advertising(false)
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        if (action != ACTION_START && !started) {
            BluetoothSpikeStateStore.event("Ignoring $action before start")
            stopSelf(startId)
            return START_NOT_STICKY
        }
        when (action) {
            ACTION_START -> handleStart()
            ACTION_PUSH_NOW -> pushNow()
            ACTION_SCHEDULE_PUSH -> handleSchedulePush(intent)
            ACTION_OPEN -> { }
        }
        return START_NOT_STICKY
    }

    private fun handleStart() {
        if (started) return
        started = true
        startForegroundNotification()
        BluetoothSpikeStateStore.serviceRunning(true)
        BluetoothSpikeStateStore.event("Service started")
        if (!checkBleAvailability()) {
            BluetoothSpikeStateStore.event("Stopping: BLE unavailable")
            stopSelf()
            return
        }
        setupGattServer()
        maintenanceJob = serviceScope.launch {
            while (isActive) {
                delay(MAINTENANCE_INTERVAL_MILLIS)
                runMaintenance()
            }
        }
    }

    private fun handleSchedulePush(intent: Intent) {
        val delay = if (intent.hasExtra(EXTRA_DELAY_MILLIS)) {
            intent.getLongExtra(EXTRA_DELAY_MILLIS, SMOKE_PUSH_DELAY_MILLIS)
        } else {
            SMOKE_PUSH_DELAY_MILLIS
        }
        val runnable = Runnable { pushNow() }
        synchronized(scheduledPushRunnables) { scheduledPushRunnables.add(runnable) }
        handler.postDelayed(runnable, delay)
        BluetoothSpikeStateStore.event("Scheduled push in ${delay}ms")
    }

    private fun pushNow() {
        val n = eventCounter.incrementAndGet()
        val code = (100000 + n % 900000).toString()
        val targets = sessions.authenticatedTargets()
        if (targets.isEmpty()) {
            BluetoothSpikeStateStore.event("Push requested but no authenticated clients")
            return
        }
        val message = SpikeWireMessage(
            type = SpikeProtocol.TYPE_OTP,
            delivery = SpikeProtocol.DELIVERY_PUSH,
            eventId = n,
            code = code,
            merchant = "Synthetic Shop",
            amount = "10.00",
            currency = "USD",
            phoneLabel = phoneLabel,
        )
        for (clientId in targets) {
            sendMessage(clientId, message)
        }
        BluetoothSpikeStateStore.event("Pushed event $n to ${targets.size} client(s)")
    }

    private fun startForegroundNotification() {
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.bluetooth_spike_notification_channel),
            NotificationManager.IMPORTANCE_LOW,
        )
        manager.createNotificationChannel(channel)
        val openPendingIntent = PendingIntent.getService(
            this,
            0,
            intent(this, ACTION_OPEN),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle(getString(R.string.bluetooth_spike_notification_title))
            .setContentText(getString(R.string.bluetooth_spike_notification_text))
            .setOngoing(true)
            .setContentIntent(openPendingIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        startForeground(NOTIFICATION_ID, notification)
    }

    private fun checkBleAvailability(): Boolean {
        val manager = getSystemService(BLUETOOTH_SERVICE) as? BluetoothManager
        val adapter = manager?.adapter
        if (adapter == null) {
            BluetoothSpikeStateStore.availability(false, "No Bluetooth adapter")
            return false
        }
        if (!adapter.isEnabled) {
            BluetoothSpikeStateStore.availability(false, "Bluetooth disabled")
            return false
        }
        if (!adapter.isMultipleAdvertisementSupported) {
            BluetoothSpikeStateStore.availability(false, "Multiple advertising not supported")
            return false
        }
        if (!hasBluetoothPermissions()) {
            BluetoothSpikeStateStore.availability(false, "Missing BLUETOOTH_ADVERTISE or BLUETOOTH_CONNECT")
            return false
        }
        val leAdvertiser = adapter.bluetoothLeAdvertiser
        if (leAdvertiser == null) {
            BluetoothSpikeStateStore.availability(false, "Advertiser unavailable")
            return false
        }
        advertiser = leAdvertiser
        BluetoothSpikeStateStore.availability(true, "Supported")
        return true
    }

    private fun hasBluetoothPermissions(): Boolean = ContextCompat.checkSelfPermission(this, android.Manifest.permission.BLUETOOTH_ADVERTISE) == PackageManager.PERMISSION_GRANTED &&
        ContextCompat.checkSelfPermission(this, android.Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED

    private fun setupGattServer() {
        val manager = getSystemService(BLUETOOTH_SERVICE) as BluetoothManager
        val command = BluetoothGattCharacteristic(
            SpikeProtocol.COMMAND_UUID,
            BluetoothGattCharacteristic.PROPERTY_WRITE,
            BluetoothGattCharacteristic.PERMISSION_WRITE_ENCRYPTED_MITM,
        )
        val events = BluetoothGattCharacteristic(
            SpikeProtocol.EVENT_UUID,
            BluetoothGattCharacteristic.PROPERTY_NOTIFY,
            BluetoothGattCharacteristic.PERMISSION_READ_ENCRYPTED_MITM,
        )
        events.addDescriptor(
            BluetoothGattDescriptor(
                SpikeProtocol.CCC_UUID,
                BluetoothGattDescriptor.PERMISSION_READ_ENCRYPTED_MITM or
                    BluetoothGattDescriptor.PERMISSION_WRITE_ENCRYPTED_MITM,
            ),
        )
        val service = BluetoothGattService(
            SpikeProtocol.SERVICE_UUID,
            BluetoothGattService.SERVICE_TYPE_PRIMARY,
        ).apply {
            addCharacteristic(command)
            addCharacteristic(events)
        }
        eventsCharacteristic = events
        val server = manager.openGattServer(this, gattCallback)
        if (server == null) {
            BluetoothSpikeStateStore.error("GATT server open failed")
            stopSelf()
            return
        }
        gattServer = server
        server.addService(service)
    }

    private fun startAdvertising(includeDeviceName: Boolean) {
        val leAdvertiser = advertiser ?: return
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setConnectable(true)
            .setTimeout(0)
            .build()
        val primaryData = AdvertiseData.Builder()
            .addServiceUuid(ParcelUuid(SpikeProtocol.SERVICE_UUID))
            .setIncludeDeviceName(false)
            .build()
        if (includeDeviceName) {
            val scanResponse = AdvertiseData.Builder()
                .setIncludeDeviceName(true)
                .build()
            leAdvertiser.startAdvertising(settings, primaryData, scanResponse, advertiseCallback)
        } else {
            leAdvertiser.startAdvertising(settings, primaryData, advertiseCallback)
        }
    }

    private fun handleWireMessage(clientId: String, message: SpikeWireMessage) {
        when (message.type) {
            SpikeProtocol.TYPE_HELLO -> sendMessage(
                clientId,
                sessions.beginAuthentication(clientId, requireNotNull(message.clientNonce)),
            )
            SpikeProtocol.TYPE_AUTHENTICATE -> {
                if (sessions.authenticate(clientId, requireNotNull(message.proof))) {
                    updateClientState(clientId)
                    sendMessage(
                        clientId,
                        SpikeWireMessage(
                            type = SpikeProtocol.TYPE_AUTHENTICATED,
                            phoneLabel = phoneLabel,
                        ),
                    )
                } else {
                    sendError(clientId, "authentication_rejected")
                }
            }
            SpikeProtocol.TYPE_PULL -> {
                if (sessions.isAuthenticated(clientId)) {
                    sendSyntheticOtp(clientId, SpikeProtocol.DELIVERY_CURRENT)
                } else {
                    sendError(clientId, "not_authenticated")
                }
            }
            SpikeProtocol.TYPE_HEARTBEAT -> {
                if (!sessions.heartbeat(clientId)) sendError(clientId, "not_authenticated")
            }
            else -> sendError(clientId, "unsupported_message")
        }
    }

    private fun sendSyntheticOtp(clientId: String, delivery: String) {
        val eventId: Int
        val code: String
        if (delivery == SpikeProtocol.DELIVERY_CURRENT) {
            eventId = 0
            code = "123456"
        } else {
            val n = eventCounter.incrementAndGet()
            eventId = n
            code = (100000 + n % 900000).toString()
        }
        val message = SpikeWireMessage(
            type = SpikeProtocol.TYPE_OTP,
            delivery = delivery,
            eventId = eventId,
            code = code,
            merchant = "Synthetic Shop",
            amount = "10.00",
            currency = "USD",
            phoneLabel = phoneLabel,
        )
        sendMessage(clientId, message)
    }

    private fun sendMessage(clientId: String, message: SpikeWireMessage) {
        val device = devices[clientId] ?: return
        val events = eventsCharacteristic ?: return
        val payload = SpikeProtocol.encode(message)
        val messageId = nextMessageId(clientId)
        val frames = SpikeFrameCodec.split(messageId, payload)
        val wasEmpty = queues.enqueue(clientId, frames)
        if (wasEmpty) {
            val first = queues.peek(clientId) ?: return
            sendNotification(device, events, first)
        }
    }

    private fun sendError(clientId: String, code: String) {
        sendMessage(clientId, SpikeWireMessage(type = SpikeProtocol.TYPE_ERROR, errorCode = code))
    }

    private fun sendNotification(
        device: BluetoothDevice,
        characteristic: BluetoothGattCharacteristic,
        frame: ByteArray,
    ) {
        val server = gattServer ?: return
        val status = server.notifyCharacteristicChanged(device, characteristic, false, frame)
        if (status != BluetoothStatusCodes.SUCCESS) {
            val clientId = device.address
            BluetoothSpikeStateStore.error("Notify rejected ($status) for $clientId")
            queues.remove(clientId)
        }
    }

    private fun nextMessageId(clientId: String): Int {
        val counter = messageCounters.computeIfAbsent(clientId) { AtomicInteger(0) }
        return counter.updateAndGet { if (it >= MESSAGE_ID_MAX) 1 else it + 1 }
    }

    private fun updateClientState(clientId: String) {
        val authenticated = sessions.isAuthenticated(clientId)
        val label = clientLabels[clientId] ?: clientId
        BluetoothSpikeStateStore.client(clientId, label, subscribed = true, authenticated = authenticated)
    }

    private fun cleanupClient(clientId: String) {
        sessions.remove(clientId)
        reassembler.clearClient(clientId)
        queues.remove(clientId)
        devices.remove(clientId)
        messageCounters.remove(clientId)
        clientLabels.remove(clientId)
        BluetoothSpikeStateStore.removeClient(clientId)
        BluetoothSpikeStateStore.event("Client disconnected: $clientId")
    }

    private fun runMaintenance() {
        val expiredFrames = reassembler.expire()
        if (expiredFrames > 0) {
            BluetoothSpikeStateStore.event("Expired $expiredFrames incomplete frame(s)")
        }
        val expired = sessions.expiredAuthenticatedClients()
        for (clientId in expired) {
            val device = devices[clientId]
            if (device != null) gattServer?.cancelConnection(device)
            BluetoothSpikeStateStore.event("Heartbeat expired: $clientId")
        }
    }

    private fun deviceNameOrFallback(device: BluetoothDevice, fallback: String): String = try {
        device.name ?: fallback
    } catch (e: SecurityException) {
        fallback
    }

    override fun onDestroy() {
        super.onDestroy()
        started = false
        maintenanceJob?.cancel()
        maintenanceJob = null
        synchronized(scheduledPushRunnables) {
            scheduledPushRunnables.forEach { handler.removeCallbacks(it) }
            scheduledPushRunnables.clear()
        }
        advertiser?.stopAdvertising(advertiseCallback)
        advertiser = null
        gattServer?.close()
        gattServer = null
        eventsCharacteristic = null
        devices.clear()
        clientLabels.clear()
        messageCounters.clear()
        sessions.clear()
        reassembler.clear()
        queues.clear()
        serviceScope.cancel()
        BluetoothSpikeStateStore.advertising(false)
        BluetoothSpikeStateStore.clearClients()
        BluetoothSpikeStateStore.serviceRunning(false)
        BluetoothSpikeStateStore.event("Service destroyed")
    }
}
