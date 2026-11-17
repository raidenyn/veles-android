package me.nagaev.veles.sharing

import android.Manifest
import android.app.Application
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustStatus
import me.nagaev.veles.trust.TrustStoreException
import me.nagaev.veles.trust.TrustedPeer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.android.controller.ServiceController
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SharingServiceTest {
    @Test fun storeFailureBeforeTheCoordinatorExistsStillReportsRePairRequired() {
        val statuses = mutableListOf<TrustStatus>()
        val sink = StoreFailureSink { statuses += it }

        // The startup identity read runs before any host or coordinator exists.
        sink()

        assertEquals("the mandated status is reported without a coordinator", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
    }

    @Test fun storeFailureIsDelegatedToTheWiredCoordinatorAndReportedAgainAfterDetach() {
        val statuses = mutableListOf<TrustStatus>()
        val sink = StoreFailureSink { statuses += it }
        var failClosed = 0
        sink.attach { failClosed += 1 }

        sink()

        assertEquals("the wired coordinator owns the status and the termination", 1, failClosed)
        assertTrue("the sink does not report a second status", statuses.isEmpty())

        sink.detach()
        sink()

        assertEquals("after shutdown the sink reports the status on its own", listOf(TrustStatus.RE_PAIR_REQUIRED), statuses)
        assertEquals("a detached coordinator is never called", 1, failClosed)
    }

    @Test fun failedRefreshInvalidatesTheCacheAndPropagates() = runTest {
        val trust = RecoveringTrust()
        val identities = LocalIdentity(trust)
        trust.identityValue = "01".repeat(16)
        identities.warm()
        assertEquals("01".repeat(16), TrustEncoding.hex(identities.current()))

        trust.identityFailure = TrustStoreException()
        val failure = try {
            identities.refresh()
            null
        } catch (thrown: TrustException) {
            thrown
        }

        assertTrue("a failed refresh propagates", failure is TrustStoreException)

        // The deleted id is never served again: the next read reloads from the repository.
        trust.identityFailure = null
        trust.identityValue = "02".repeat(16)
        assertEquals("the reloaded identity is not the stale one", "02".repeat(16), TrustEncoding.hex(identities.current()))
    }

    @Test fun aColdCacheThatCannotReloadFailsClosed() = runTest {
        val trust = RecoveringTrust()
        trust.identityFailure = TrustStoreException()
        val identities = LocalIdentity(trust)

        assertArrayEquals("no identity is invented", ByteArray(0), identities.currentOrEmpty())

        trust.identityFailure = null
        trust.identityValue = "03".repeat(16)
        assertEquals("the next read retries the load", "03".repeat(16), TrustEncoding.hex(identities.currentOrEmpty()))
    }

    @Test fun aResetWinsOverAnOlderInFlightReload() = runTest {
        val trust = GatedIdentityTrust()
        trust.identityValue = "01".repeat(16)
        val identities = LocalIdentity(trust)
        identities.warm()
        assertEquals("01".repeat(16), TrustEncoding.hex(identities.current()))

        // A failed refresh leaves an empty cache; the next generation starts a gated reload.
        trust.identityFailure = TrustStoreException()
        runCatching { identities.refresh() }
        trust.identityFailure = null
        trust.gateNextLoad()
        val inFlight = async(Dispatchers.Default) { identities.current() }
        trust.loadEntered.await()

        // The reset publishes its identity while the older load is still suspended.
        trust.identityValue = "03".repeat(16)
        identities.refresh()
        assertEquals("the reset published its identity", "03".repeat(16), TrustEncoding.hex(identities.current()))

        trust.releaseLoad.complete(Unit)
        val served = inFlight.await()

        assertEquals(
            "the superseded load is discarded and the reset's identity is served",
            "03".repeat(16),
            TrustEncoding.hex(served),
        )
        assertEquals("the cache still holds the reset's identity", "03".repeat(16), TrustEncoding.hex(identities.current()))
        assertEquals("warm, the failed refresh, the gated load, the reset and its retry", 5, trust.loads)
    }

    /** A trust repository whose identity read can be gated, fail and recover. */
    private class GatedIdentityTrust : InstallationTrustRepository {
        var identityValue: String = "01".repeat(16)
        var identityFailure: Exception? = null
        var loads = 0
            private set
        private var gateNext = false
        val loadEntered = CompletableDeferred<Unit>()
        val releaseLoad = CompletableDeferred<Unit>()

        fun gateNextLoad() {
            gateNext = true
        }

        override suspend fun identity(): InstallationIdentity {
            loads += 1
            if (gateNext) {
                gateNext = false
                loadEntered.complete(Unit)
                releaseLoad.await()
            }
            identityFailure?.let { throw it }
            return InstallationIdentity(identityValue, "04".repeat(65))
        }

        override suspend fun peers(): List<TrustedPeer> = emptyList()

        override suspend fun activePeers(): List<TrustedPeer> = emptyList()

        override suspend fun addPending(peer: PendingPeer) = error("not used")

        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("not used")

        override suspend fun revoke(peerId: String) = error("not used")

        override suspend fun beginRevocation(peerId: String) = Unit

        override suspend fun endRevocation(peerId: String) = Unit

        override suspend fun markAuthenticated(peerId: String) = Unit

        override suspend fun resetIdentity() = Unit
    }

    /** A trust repository whose identity read can fail and recover, with one value at a time. */
    private class RecoveringTrust : InstallationTrustRepository {
        var identityValue: String = "01".repeat(16)
        var identityFailure: Exception? = null

        override suspend fun identity(): InstallationIdentity {
            identityFailure?.let { throw it }
            return InstallationIdentity(identityValue, "04".repeat(65))
        }

        override suspend fun peers(): List<TrustedPeer> = emptyList()

        override suspend fun activePeers(): List<TrustedPeer> = emptyList()

        override suspend fun addPending(peer: PendingPeer) = error("not used")

        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("not used")

        override suspend fun revoke(peerId: String) = error("not used")

        override suspend fun beginRevocation(peerId: String) = Unit

        override suspend fun endRevocation(peerId: String) = Unit

        override suspend fun markAuthenticated(peerId: String) = Unit

        override suspend fun resetIdentity() = Unit
    }

    @Test fun enabledIntentSurvivesUnavailableRestorationWithoutClaimingAdvertising() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        shadowOf(context.packageManager).setSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE, false)
        SharingState.setEnabled(context, true)
        val controller = Robolectric.buildService(SharingService::class.java).create()
        assertEquals(android.app.Service.START_NOT_STICKY, controller.get().onStartCommand(null, 0, 1))
        SharingState.load(context)
        assertTrue(SharingState.enabled.value)
        assertEquals(SharingAvailability.Unsupported, SharingState.availability.value)
        controller.destroy()
        SharingState.setEnabled(context, false)
    }

    @Test fun destructionNeverClaimsOldAdvertisingIsStillAvailable() {
        val controller = Robolectric.buildService(SharingService::class.java).create()
        SharingState.update(SharingAvailability.Advertising)
        controller.destroy()
        assertEquals(SharingAvailability.Disabled, SharingState.availability.value)
    }

    @Test fun stopClearsEnabledIntentAndAvailability() {
        val controller = Robolectric.buildService(SharingService::class.java).create()
        val service = controller.get()
        service.getSharedPreferences("sharing", Context.MODE_PRIVATE).edit().putBoolean("enabled", true).commit()
        SharingState.update(SharingAvailability.Advertising)
        service.onStartCommand(Intent(service, SharingService::class.java).setAction(SharingService.STOP), 0, 1)
        assertEquals(SharingAvailability.Disabled, SharingState.availability.value)
        assertFalse(service.getSharedPreferences("sharing", Context.MODE_PRIVATE).getBoolean("enabled", true))
        controller.destroy()
    }

    @Test fun disabledProcessRestorationDoesNotAdvertise() {
        val controller = Robolectric.buildService(SharingService::class.java).create()
        val service = controller.get()
        service.getSharedPreferences("sharing", Context.MODE_PRIVATE).edit().clear().commit()
        SharingState.update(SharingAvailability.Disabled)
        assertEquals(android.app.Service.START_NOT_STICKY, service.onStartCommand(null, 0, 1))
        assertEquals(SharingAvailability.Disabled, SharingState.availability.value)
        controller.destroy()
    }

    @Test fun manifestHasPrivateConnectedDeviceServiceAndNoScanOrLocationPermissions() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val info = context.packageManager.getServiceInfo(android.content.ComponentName(context, SharingService::class.java), 0)
        assertFalse(info.exported)
        assertEquals(android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE, info.foregroundServiceType)
        val permissions = context.packageManager.getPackageInfo(context.packageName, PackageManager.GET_PERMISSIONS)
            .requestedPermissions.orEmpty().toSet()
        assertTrue("android.permission.BLUETOOTH_ADVERTISE" in permissions)
        assertTrue("android.permission.BLUETOOTH_CONNECT" in permissions)
        assertFalse("android.permission.BLUETOOTH_SCAN" in permissions)
        assertFalse("android.permission.ACCESS_FINE_LOCATION" in permissions)
    }

    private val started = mutableListOf<ServiceController<SharingService>>()

    @After fun resetSharingState() {
        started.forEach { it.destroy() }
        started.clear()
        val context = ApplicationProvider.getApplicationContext<Context>()
        SharingState.update(SharingAvailability.Disabled)
        SharingState.setEnabled(context, false)
    }

    /** BLE-capable, permitted, enabled adapter that reports no multi-advertisement support. */
    private fun singleAdvertisementPhone(): Application {
        val context = ApplicationProvider.getApplicationContext<Application>()
        val adapter = context.getSystemService(BluetoothManager::class.java).adapter
        shadowOf(context.packageManager).setSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE, true)
        shadowOf(context).grantPermissions(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE)
        shadowOf(adapter).setState(BluetoothAdapter.STATE_ON)
        shadowOf(adapter).setIsMultipleAdvertisementSupported(false)
        SharingState.update(SharingAvailability.Disabled)
        context.getSharedPreferences("sharing", Context.MODE_PRIVATE).edit().putBoolean("enabled", true).commit()
        return context
    }

    private fun startedService(): SharingService {
        val controller = Robolectric.buildService(SharingService::class.java).create().also { started += it }
        val service = controller.get()
        assertEquals(android.app.Service.START_STICKY, service.onStartCommand(null, 0, 1))
        return service
    }

    @Test fun singleAdvertisementAdapterStartsTheForegroundService() {
        singleAdvertisementPhone()
        val service = startedService()
        assertEquals(SharingAvailability.Starting, SharingState.availability.value)
        assertNotNull(shadowOf(service).lastForegroundNotification)
        assertFalse(shadowOf(service).isStoppedBySelf)
    }

    @Test fun advertisingFailureStopsServiceAndAFreshStartRetries() {
        singleAdvertisementPhone()
        val failed = startedService()

        failed.onGattState(SharingAvailability.Unsupported)

        assertEquals(SharingAvailability.Unsupported, SharingState.availability.value)
        assertTrue(shadowOf(failed).isStoppedBySelf)
        assertTrue(shadowOf(failed).isForegroundStopped)
        assertNull(shadowOf(failed).lastForegroundNotification)

        val retried = startedService()
        assertEquals(SharingAvailability.Starting, SharingState.availability.value)
        assertNotNull(shadowOf(retried).lastForegroundNotification)

        retried.onGattState(SharingAvailability.Advertising)
        assertEquals(SharingAvailability.Advertising, SharingState.availability.value)
    }
}
