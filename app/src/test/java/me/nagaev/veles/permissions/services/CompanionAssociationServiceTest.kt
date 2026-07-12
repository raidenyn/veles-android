package me.nagaev.veles.permissions.services

import android.companion.AssociationInfo
import android.companion.AssociationRequest
import android.companion.CompanionDeviceManager
import android.content.Context
import android.content.IntentSender
import android.content.pm.PackageManager
import io.mockk.every
import io.mockk.justRun
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.Executor

class CompanionAssociationServiceTest {
    private val cdm = mockk<CompanionDeviceManager>()
    private val packageManager = mockk<PackageManager> {
        every { hasSystemFeature(PackageManager.FEATURE_COMPANION_DEVICE_SETUP) } returns true
    }
    private val context = mockk<Context> {
        every { getSystemService(Context.COMPANION_DEVICE_SERVICE) } returns cdm
    }
    private val directExecutor = Executor { it.run() }
    private val watchRequest = mockk<AssociationRequest>()

    init {
        every { context.getPackageManager() } returns packageManager
    }

    private fun service(launcher: IntentSenderLauncher = IntentSenderLauncher { _, _ -> }) =
        CompanionAssociationService(context, launcher, executor = directExecutor, buildRequest = { watchRequest })

    private fun watchAssociation(): AssociationInfo = mockk {
        every { deviceProfile } returns AssociationRequest.DEVICE_PROFILE_WATCH
        every { id } returns 7
    }

    @Test
    fun `unsupported when companion feature missing`() = runTest {
        every { packageManager.hasSystemFeature(PackageManager.FEATURE_COMPANION_DEVICE_SETUP) } returns false
        assertFalse(service().isSupported())
        assertEquals(AssociationOutcome.Unsupported, service().associate())
    }

    @Test
    fun `unsupported when CompanionDeviceManager is null`() = runTest {
        every { context.getSystemService(Context.COMPANION_DEVICE_SERVICE) } returns null
        assertEquals(AssociationOutcome.Unsupported, service().associate())
    }

    @Test
    fun `hasAssociation true only for watch-profile associations`() {
        every { cdm.myAssociations } returns listOf(watchAssociation())
        assertTrue(service().hasAssociation())

        every { cdm.myAssociations } returns emptyList()
        assertFalse(service().hasAssociation())
    }

    @Test
    fun `associate resolves Associated when callback reports creation`() = runTest {
        val callbackSlot = slot<CompanionDeviceManager.Callback>()
        every { cdm.associate(watchRequest, any(), capture(callbackSlot)) } answers {
            callbackSlot.captured.onAssociationCreated(watchAssociation())
        }
        assertEquals(AssociationOutcome.Associated, service().associate())
    }

    @Test
    fun `associate resolves via launcher result when dialog finishes`() = runTest {
        val callbackSlot = slot<CompanionDeviceManager.Callback>()
        val sender = mockk<IntentSender>()
        every { cdm.associate(watchRequest, any(), capture(callbackSlot)) } answers {
            callbackSlot.captured.onAssociationPending(sender)
        }
        val cancellingLauncher = IntentSenderLauncher { _, callback -> callback(false) }
        assertEquals(AssociationOutcome.Cancelled, service(cancellingLauncher).associate())

        val okLauncher = IntentSenderLauncher { _, callback -> callback(true) }
        assertEquals(AssociationOutcome.Associated, service(okLauncher).associate())
    }

    @Test
    fun `associate resolves Failed on callback failure`() = runTest {
        val callbackSlot = slot<CompanionDeviceManager.Callback>()
        every { cdm.associate(watchRequest, any(), capture(callbackSlot)) } answers {
            callbackSlot.captured.onFailure("boom")
        }
        assertEquals(AssociationOutcome.Failed("boom"), service().associate())
    }

    @Test
    fun `associate resolves Failed when cdm associate throws`() = runTest {
        every { cdm.associate(watchRequest, any<Executor>(), any<CompanionDeviceManager.Callback>()) } throws SecurityException("nope")
        assertEquals(AssociationOutcome.Failed("nope"), service().associate())
    }

    @Test
    fun `disassociate removes all watch-profile associations`() {
        val association = watchAssociation()
        every { cdm.myAssociations } returns listOf(association)
        justRun { cdm.disassociate(7) }

        service().disassociate()

        verify { cdm.disassociate(7) }
    }
}