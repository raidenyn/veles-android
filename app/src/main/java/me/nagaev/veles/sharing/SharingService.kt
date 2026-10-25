@file:Suppress("MagicNumber") // Notification ID and fixed synthetic fixture bytes.

package me.nagaev.veles.sharing

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.IBinder
import androidx.core.app.NotificationCompat
import me.nagaev.veles.BuildConfig
import me.nagaev.veles.R
import me.nagaev.veles.permissions.PermissionsActivity

internal class SharingService : Service() {
    private var gatt: SharingGattServer? = null
    private var registered = false
    private val preferences get() = getSharedPreferences("sharing", MODE_PRIVATE)
    private val bluetoothChanges = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            SharingState.prerequisite(context)?.let(::fail)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    @Suppress("ReturnCount") // Lifecycle prerequisites fail before foreground setup.
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) {
            SharingState.setEnabled(this, false)
            fail(SharingAvailability.Disabled)
            return START_NOT_STICKY
        }
        if (intent == null && !preferences.getBoolean("enabled", false)) {
            stopSelf()
            return START_NOT_STICKY
        }
        SharingState.prerequisite(this)?.let {
            fail(it)
            return START_NOT_STICKY
        }
        try {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.sharing_title), NotificationManager.IMPORTANCE_LOW),
            )
            val open = PendingIntent.getActivity(this, 0, Intent(this, PermissionsActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
            val stop = PendingIntent.getService(
                this,
                1,
                Intent(this, SharingService::class.java).setAction(STOP),
                PendingIntent.FLAG_IMMUTABLE,
            )
            val notification = NotificationCompat.Builder(this, CHANNEL)
                .setSmallIcon(R.mipmap.ic_launcher)
                .setContentTitle(getString(R.string.sharing_title))
                .setContentText(getString(R.string.sharing_notification))
                .setContentIntent(open).setOngoing(true)
                .addAction(0, getString(R.string.sharing_stop), stop).build()
            startForeground(204, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            SharingState.setEnabled(this, true)
            if (gatt == null) {
                SharingState.update(SharingAvailability.Starting)
                registerReceiver(bluetoothChanges, IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED), RECEIVER_EXPORTED)
                registered = true
                gatt = SharingGattServer(this, { state ->
                    if (state == SharingAvailability.Advertising) SharingState.update(state) else fail(state)
                }, { _, record ->
                    // Fixed debug fixture only; no production message handler or OTP data dependency.
                    if (BuildConfig.DEBUG) SyntheticTransportFixture.reply(record) else null
                })
                gatt?.start()
            }
        } catch (_: SecurityException) {
            fail(SharingAvailability.PermissionRequired)
            return START_NOT_STICKY
        } catch (_: IllegalStateException) {
            fail(SharingAvailability.Failed)
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    private fun fail(state: SharingAvailability) {
        gatt?.close()
        gatt = null
        SharingState.update(state)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        gatt?.close()
        gatt = null
        if (registered) unregisterReceiver(bluetoothChanges)
        if (SharingState.availability.value in listOf(SharingAvailability.Starting, SharingAvailability.Advertising)) {
            SharingState.update(SharingAvailability.Disabled)
        }
        super.onDestroy()
    }

    companion object {
        const val STOP = "me.nagaev.veles.sharing.STOP"
        private const val CHANNEL = "VelesSharing"
    }
}
