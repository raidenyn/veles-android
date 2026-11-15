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
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
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
import me.nagaev.veles.session.RemovalHandler
import me.nagaev.veles.session.RemovalOperations
import me.nagaev.veles.session.composeApplicationHandlers
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.AndroidIdentityKeyStore
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PeerRevocationCoordinator
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStatus
import me.nagaev.veles.trust.createAndroidTrustRepository
import java.security.SecureRandom

internal class SharingService : Service() {
    private var gatt: SharingGattServer? = null
    private var host: SessionHost? = null
    private var pairing: PairingEndpoint? = null
    private var sessionScope: CoroutineScope? = null
    private var registered = false

    /** The OTP-11 coordinator; the repository's store-failure callback reaches it. */
    @Volatile
    private var coordinator: PeerRevocationCoordinator? = null

    /**
     * The store-failure sink the repository is created with. It reports the mandated status on
     * its own until the coordinator exists, so a startup failure (the identity read that runs
     * before any host or coordinator) is never silent.
     */
    private val storeFailures = StoreFailureSink(SharingState::updateTrustStatus)
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
     * (OTP-10 plan 9d).
     *
     * The activation handler, the removal handler and the build-type extension handler are
     * composed together in production (design D4): a release build with a null extension still
     * activates and still handles mutual removal.
     *
     * The installation identity is a volatile cache read by the synchronous engine factory. It
     * is warmed before the first link is admitted and refreshed from the repository after an
     * explicit reset deleted the row and the key (design D6). A failed refresh invalidates it,
     * and the next generation reloads; a generation that cannot load an identity fails closed
     * with the generic pre-auth reject instead of authenticating with a stale id.
     */
    private fun sessionHost(transport: SessionTransport): SessionHost {
        val proofs = ProofRegistry()
        val repository = createAndroidTrustRepository(
            this,
            ActivationVerifier { peer, step, proof -> proofs.consume(peer, step, proof) },
            onStoreFailure = storeFailures::invoke,
        )
        val identities = LocalIdentity(repository).also { it.warm() }
        val trust = ResettingTrust(repository, identities)
        val keyStore = AndroidIdentityKeyStore()
        val activation = ActivationEngine(trust, proofs)
        val removal = LateRemovalOperations()
        val handler = composeApplicationHandlers(
            RemovalHandler(removal),
            activation,
            BuildSessionExtensions.applicationHandler(this),
        )
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        sessionScope = scope
        // One window and one admission for the service; the native core is resolved only
        // when a pairing_request actually starts an execution.
        val pairing = PairingEndpoint(
            trust = trust,
            proofs = proofs,
            onPairFingerprint = BuildSessionExtensions::publishPairFingerprint,
        )
        val host = SessionHost(
            scope,
            transport,
            { _ ->
                PhoneSessionEngine(
                    localInstallationId = identities.currentOrEmpty(),
                    signer = IdentitySigner { message -> keyStore.sign(message) },
                    lookup = trust.peerLookup(),
                    clock = SystemClock::elapsedRealtime,
                    nonce = { ByteArray(NONCE_BYTES).also(SecureRandom()::nextBytes) },
                    handler = handler,
                )
            },
            trust,
            SystemClock::elapsedRealtime,
            pairingFactory = { gen -> pairing.connection(gen.serial) },
            onSessionClosed = { sessionId -> proofs.revoke(sessionId) },
        )
        val coordinator = PeerRevocationCoordinator(
            trust,
            host,
            scope,
            onStatus = SharingState::updateTrustStatus,
        )
        this.coordinator = coordinator
        removal.coordinator = coordinator
        storeFailures.attach(coordinator::failClosed)
        this.pairing = pairing
        this.host = host
        BuildSessionExtensions.attach(host)
        BuildSessionExtensions.attachPairing(pairing) {
            scope.launch { host.cancelPairings() }
        }
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
        BuildSessionExtensions.detachPairing()
        pairing?.shutdown()
        pairing = null
        sessionScope?.cancel()
        sessionScope = null
        // No coordinator survives its host: a store failure after shutdown has nothing to close
        // and must not reach a dead one.
        storeFailures.detach()
        coordinator = null
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
 * The composition root's store-failure sink (OTP-11 design D6). Before the host and the
 * coordinator exist — the startup identity read runs first — it reports the one mandated
 * "re-pair required" status on its own; once the coordinator is wired, its fail-closed action
 * owns both the status and the termination. Exactly one status is emitted per episode.
 */
internal class StoreFailureSink(private val onStatus: (TrustStatus) -> Unit) {
    @Volatile private var action: (() -> Unit)? = null

    fun attach(action: () -> Unit) {
        this.action = action
    }

    fun detach() {
        action = null
    }

    operator fun invoke() {
        val wired = action
        if (wired == null) onStatus(TrustStatus.RE_PAIR_REQUIRED) else wired()
    }
}

/**
 * The service's installation identity cache (OTP-11 design D6).
 *
 * The engine factory is not suspending, so it reads [current] from this cache. [warm] performs
 * the startup read exactly once, before the first link is admitted, and fails closed the same
 * way the once-captured identity did. An explicit reset refreshes the cache from the repository
 * after `resetIdentity()` deleted the row and the key. A failed refresh invalidates the cache and
 * propagates: the deleted installation id is never served again, and the next generation reloads
 * from the repository (or, when that also fails, is refused by [currentOrEmpty]).
 *
 * Reload and reset publication are serialized by an epoch: a reset always wins over a load that
 * started before it, so a suspended reload can never overwrite the identity a later reset
 * published (which would resurrect a deleted installation id for every later generation).
 */
internal class LocalIdentity(private val trust: InstallationTrustRepository) {
    private val lock = Any()
    private var epoch = 0L

    @Volatile private var cached: ByteArray? = null

    fun warm() {
        cached = runBlocking { load() }
    }

    /** The identity for a new generation, reloading after an invalidation; may throw TrustException. */
    fun current(): ByteArray = cached ?: reload()

    /**
     * The identity for a new generation, or an empty value when the store cannot supply one. The
     * engine factory cannot propagate: an exception there would leave the admitted link
     * unmanaged. An empty installation id makes every handshake fail closed with the generic
     * pre-auth reject, and the next generation retries the reload.
     */
    fun currentOrEmpty(): ByteArray = try {
        current()
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (_: TrustException) {
        ByteArray(0)
    }

    /**
     * Called after `resetIdentity()` deleted the row and the key. The cache is invalidated and the
     * epoch advanced in one critical section, so no load that started before this reset can publish
     * afterwards; a failure propagates because the store cannot confirm the new identity.
     */
    suspend fun refresh() {
        val started = synchronized(lock) {
            epoch += 1
            cached = null
            epoch
        }
        val loaded = load()
        synchronized(lock) {
            // A later reset owns the cache; this one must not overwrite its identity.
            if (epoch == started) cached = loaded
        }
    }

    /**
     * Loads an identity for a generation and publishes it only under the epoch it started with. A
     * load superseded by a reset is discarded and retried, so the caller receives the reset's
     * identity and the deleted one is never served.
     */
    private fun reload(): ByteArray {
        while (true) {
            val started = synchronized(lock) { epoch }
            val loaded = runBlocking { load() }
            synchronized(lock) {
                if (epoch == started) {
                    cached = loaded
                    return loaded
                }
            }
        }
    }

    private suspend fun load(): ByteArray = TrustEncoding.bytes(trust.identity().installationId)
}

/**
 * The trust repository as the coordinator sees it: an explicit identity reset also refreshes the
 * identity cache, after the repository's own row-then-key deletions (ruling R3: the coordinator
 * calls `resetIdentity()` and never duplicates its body).
 */
internal class ResettingTrust(
    private val delegate: InstallationTrustRepository,
    private val identities: LocalIdentity,
) : InstallationTrustRepository by delegate {
    override suspend fun resetIdentity() {
        delegate.resetIdentity()
        identities.refresh()
    }
}

/**
 * The removal handler is composed into every engine before the coordinator that owns the fence
 * and the detached finish exists (the coordinator needs the host, the host needs the handler).
 * This indirection is the only late binding; the handler itself never sees it.
 */
private class LateRemovalOperations : RemovalOperations {
    @Volatile var coordinator: PeerRevocationCoordinator? = null

    override suspend fun deleteFenced(peerId: String) {
        checkNotNull(coordinator) { "removal before the coordinator is wired" }.deleteFenced(peerId)
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
    private val proofs: ProofRegistry,
    coreFactory: () -> PairingCore = { VelesPairingCore() },
    private val onPairFingerprint: (String) -> Unit = {},
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
        proofs = proofs,
        onPairFingerprint = onPairFingerprint,
    )

    /** Erases the pairing code and the pairing id; live executions are closed by the host. */
    fun shutdown() = window.cancel()
}
