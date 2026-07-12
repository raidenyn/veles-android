package me.nagaev.veles.permissions.services

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.justRun
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SensitiveNotificationPermissionProviderTest {
    private val status = mockk<SensitiveNotificationsStatus>()
    private val association = mockk<CompanionAssociationService>()
    private val provider = SensitiveNotificationPermissionProvider(status, association)

    @Test
    fun `isGranted true for Granted and NotApplicable, false for NotGranted`() {
        every { status.check() } returns SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.AppOp)
        assertTrue(provider.isGranted())

        every { status.check() } returns SensitiveNotificationsGrant.NotApplicable
        assertTrue(provider.isGranted())

        every { status.check() } returns SensitiveNotificationsGrant.NotGranted
        assertFalse(provider.isGranted())
    }

    @Test
    fun `request runs the CDM association flow`() = runTest {
        coEvery { association.associate() } returns AssociationOutcome.Associated
        provider.request()
        coVerify { association.associate() }
    }

    @Test
    fun `revoke disassociates`() = runTest {
        justRun { association.disassociate() }
        provider.revoke()
        coVerify { association.disassociate() }
    }

    @Test
    fun `cdmSupported delegates to the association service`() {
        every { association.isSupported() } returns true
        assertTrue(provider.cdmSupported)
        every { association.isSupported() } returns false
        assertFalse(provider.cdmSupported)
    }
}
