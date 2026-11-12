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
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import me.nagaev.veles.BuildConfig
import me.nagaev.veles.R
import me.nagaev.veles.pairing.ActivationEngine
import me.nagaev.veles.pairing.PairingAdmission
import me.nagaev.veles.pairing.PairingConnection
import me.nagaev.veles.pairing.PairingCore
import me.nagaev.veles.pairing.PairingLimits
import me.nagaev.veles.pairing.PairingWindow
import me.nagaev.veles.pairing.PhonePairingMachine
import me.nagaev.veles.pairing.ProofRegistry
import me.nagaev.veles.pairing.VelesPairingCore
import me.nagaev.veles.permissions.PermissionsActivity
import me.nagaev.veles.session.BuildSessionExtensions
import me.nagaev.veles.session.IdentitySigner
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.PeerLookup
import me.nagaev.veles.session.PeerRecord
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.composeApplicationHandlers
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.AndroidIdentityKeyStore
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.createAndroidTrustRepository
import java.security.SecureRandom

internal class SharingService : Service() {
    private var gatt: SharingGattServer? = null
    private var host: SessionHost? = null
    private var pairing: PairingEndpoint? = null
    private var sessionScope: CoroutineScope? = null
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
                val transport = BoundTransport()
                // Exactly one listener: the OTP-06 plaintext echo is instrumentation-only and can
                // never run beside the session engine (design D4 "the plaintext echo is isolated").
                val listener: ConnectionListener = if (BuildConfig.DEBUG && TransportMode.instrumentationEcho) {
                    InstrumentationEchoListener(transport)
                } else {
                    sessionHost(transport)
                }
                gatt = SharingGattServer(this, { state ->
                    if (state == SharingAvailability.Advertising) SharingState.update(state) else fail(state)
                }, listener).also {
                    transport.server = it
                    it.start()
                }
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

    /**
     * The production session path. The repository's verifier is the session-bound proof
     * registry the pairing machine and the recovery activation engine fill; the debug
     * `BuildSessionExtensions` and `ProbeReceiver` repositories deliberately stay reject-all
     * (OTP-10 plan 9d). The installation id is read once, before the first link is admitted,
     * because the engine factory is not suspending.
     *
     * The activation handler is always built and composed with the build-type extension handler
     * (the debug probe), so a release build with a null extension still activates.
     */
    private fun sessionHost(transport: SessionTransport): SessionHost {
        val proofs = ProofRegistry()
        val repository = createAndroidTrustRepository(
            this,
            ActivationVerifier { peer, step, proof -> proofs.consume(peer, step, proof) },
        )
        val localInstallationId = runBlocking { TrustEncoding.bytes(repository.identity().installationId) }
        val keyStore = AndroidIdentityKeyStore()
        val activation = ActivationEngine(repository, proofs)
        val handler = composeApplicationHandlers(activation, BuildSessionExtensions.applicationHandler(this))
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        sessionScope = scope
        // One window and one admission for the service; the native core is resolved only
        // when a pairing_request actually starts an execution.
        val pairing = PairingEndpoint(trust = repository)
        val host = SessionHost(
            scope,
            transport,
            { _ ->
                PhoneSessionEngine(
                    localInstallationId = localInstallationId,
                    signer = IdentitySigner { message -> keyStore.sign(message) },
                    lookup = repository.peerLookup(),
                    clock = SystemClock::elapsedRealtime,
                    nonce = { ByteArray(NONCE_BYTES).also(SecureRandom()::nextBytes) },
                    handler = handler,
                )
            },
            repository,
            SystemClock::elapsedRealtime,
            pairingFactory = { gen -> pairing.connection(gen.serial) },
            onSessionClosed = { sessionId -> proofs.revoke(sessionId) },
        )
        this.pairing = pairing
        this.host = host
        BuildSessionExtensions.attach(host)
        return host
    }

    private fun fail(state: SharingAvailability) {
        gatt?.close()
        gatt = null
        shutdownSessions()
        SharingState.update(state)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /**
     * The engine and every owned record are erased before the scope is cancelled. `shutdown`
     * does not depend on a coroutine being scheduled: it waits for each generation's mutex and
     * falls back to a forced erasure, so a suspended Keystore call cannot leave keys behind.
     */
    private fun shutdownSessions() {
        // The host closes every live pairing execution; the window then erases the code.
        host?.let { runBlocking { it.shutdown() } }
        host = null
        pairing?.shutdown()
        pairing = null
        sessionScope?.cancel()
        sessionScope = null
    }

    override fun onDestroy() {
        gatt?.close()
        gatt = null
        shutdownSessions()
        if (registered) unregisterReceiver(bluetoothChanges)
        if (SharingState.availability.value in listOf(SharingAvailability.Starting, SharingAvailability.Advertising)) {
            SharingState.update(SharingAvailability.Disabled)
        }
        super.onDestroy()
    }

    companion object {
        const val STOP = "me.nagaev.veles.sharing.STOP"
        private const val CHANNEL = "VelesSharing"
        private const val NONCE_BYTES = 16

        /** The engine reads trust records; it never writes them (the host owns `lastSeen`). */
        private fun InstallationTrustRepository.peerLookup(): PeerLookup = PeerLookup { installationId ->
            peers().firstOrNull { TrustEncoding.bytes(it.installationId).contentEquals(installationId) }?.let { peer ->
                PeerRecord(
                    installationId = TrustEncoding.bytes(peer.installationId),
                    identityPublicKey = TrustEncoding.bytes(peer.publicKey),
                    state = when (peer.state) {
                        TrustState.ACTIVE -> LocalRecordState.ACTIVE
                        TrustState.PENDING -> LocalRecordState.PENDING
                        TrustState.COMMITTED_WAITING_ACK -> LocalRecordState.COMMITTED_WAITING_ACK
                    },
                    // OTP2-CRY-019f: the public `ET` persisted with the pending/committed record
                    // (and kept on `active`) is what binds the session's recovery activation keys.
                    et = TrustEncoding.bytes(peer.enrollmentTranscript),
                )
            }
        }
    }

    /** Breaks the server→listener→transport cycle; assigned before the server starts. */
    private class BoundTransport : SessionTransport {
        lateinit var server: SharingGattServer

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean = server.send(gen, bytes, token)

        override fun close(gen: ConnectionGeneration) = server.close(gen)
    }
}

/**
 * The production pairing endpoint: one window and one admission for the whole service,
 * and one core resolved lazily on the first `pairing_request` that survives admission.
 *
 * Constructing this object never touches `VelesCrypto`: the window and the admission are
 * pure state, and the core is only reached when a machine calls `start`. That keeps a
 * Robolectric-hosted service start free of the JNI library while still giving every
 * connection the same phone-wide budgets and window.
 */
internal class PairingEndpoint(
    private val clock: () -> Long = SystemClock::elapsedRealtime,
    private val trust: InstallationTrustRepository,
    coreFactory: () -> PairingCore = { VelesPairingCore() },
) {
    private val limits = PairingLimits()
    private val core: PairingCore by lazy(LazyThreadSafetyMode.SYNCHRONIZED) { coreFactory() }
    private val admission = PairingAdmission(limits, clock)

    internal val window = PairingWindow(clock)

    /** One connection's machine; [conn] is the process-unique generation serial. */
    fun connection(conn: Long): PairingConnection = PhonePairingMachine(
        window = window,
        admission = admission,
        conn = conn,
        trust = trust,
        coreProvider = { core },
        clock = clock,
        limits = limits,
    )

    /** Erases the pairing code and the pairing id; live executions are closed by the host. */
    fun shutdown() = window.cancel()
}
