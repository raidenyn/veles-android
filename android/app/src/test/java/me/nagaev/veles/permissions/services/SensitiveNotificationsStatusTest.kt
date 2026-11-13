package me.nagaev.veles.permissions.services

import android.app.AppOpsManager
import android.content.Context
import android.content.pm.PackageManager
import io.mockk.every
import io.mockk.mockk
import org.junit.Assert.assertEquals
import org.junit.Test

@Suppress("MaxLineLength")
class SensitiveNotificationsStatusTest {
    private val appOps = mockk<AppOpsManager>()
    private val context = mockk<Context> {
        every { packageName } returns "me.nagaev.veles"
        every { getSystemService(Context.APP_OPS_SERVICE) } returns appOps
    }

    private fun status(sdkInt: Int = 35) = SensitiveNotificationsStatus(context, sdkInt = sdkInt, myUid = { 10001 })

    @Test
    fun `below API 35 is NotApplicable regardless of anything else`() {
        assertEquals(SensitiveNotificationsGrant.NotApplicable, status(sdkInt = 34).check())
    }

    @Test
    fun `permission granted means Granted via Role`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_GRANTED
        assertEquals(
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role),
            status().check(),
        )
    }

    @Test
    fun `appop MODE_ALLOWED means Granted via AppOp`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(SensitiveNotificationsStatus.APP_OP, 10001, "me.nagaev.veles") } returns AppOpsManager.MODE_ALLOWED
        assertEquals(
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.AppOp),
            status().check(),
        )
    }

    @Test
    fun `appop MODE_DEFAULT and permission denied means NotGranted`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(SensitiveNotificationsStatus.APP_OP, 10001, "me.nagaev.veles") } returns AppOpsManager.MODE_DEFAULT
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }

    @Test
    fun `appop MODE_ERRORED means NotGranted`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(SensitiveNotificationsStatus.APP_OP, 10001, "me.nagaev.veles") } returns AppOpsManager.MODE_ERRORED
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }

    @Test
    fun `appop lookup throwing falls back to permission result`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { appOps.unsafeCheckOpNoThrow(any<String>(), any<Int>(), any<String>()) } throws IllegalArgumentException("unknown op")
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }

    @Test
    fun `null AppOpsManager falls back to permission result`() {
        every { context.checkSelfPermission(SensitiveNotificationsStatus.PERMISSION) } returns PackageManager.PERMISSION_DENIED
        every { context.getSystemService(Context.APP_OPS_SERVICE) } returns null
        assertEquals(SensitiveNotificationsGrant.NotGranted, status().check())
    }
}
