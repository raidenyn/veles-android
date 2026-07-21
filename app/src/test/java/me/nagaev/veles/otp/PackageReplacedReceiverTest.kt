package me.nagaev.veles.otp

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.service.notification.NotificationListenerService
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.verify
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PackageReplacedReceiverTest {
    private val context = mockk<Context>(relaxed = true)

    @Test
    fun `MY_PACKAGE_REPLACED requests a listener rebind`() {
        mockkStatic(NotificationListenerService::class)
        every { NotificationListenerService.requestRebind(any()) } returns Unit
        val intent = mockk<Intent>(relaxed = true)
        every { intent.action } returns Intent.ACTION_MY_PACKAGE_REPLACED

        PackageReplacedReceiver().onReceive(context, intent)

        verify {
            NotificationListenerService.requestRebind(
                ComponentName(context, NotificationListener::class.java),
            )
        }
    }

    @Test
    fun `Unrelated action does not request a rebind`() {
        mockkStatic(NotificationListenerService::class)
        every { NotificationListenerService.requestRebind(any()) } returns Unit
        val intent = mockk<Intent>(relaxed = true)
        every { intent.action } returns Intent.ACTION_BOOT_COMPLETED

        PackageReplacedReceiver().onReceive(context, intent)

        verify(exactly = 0) { NotificationListenerService.requestRebind(any()) }
    }

    @Test
    fun `Null intent does not request a rebind`() {
        mockkStatic(NotificationListenerService::class)
        every { NotificationListenerService.requestRebind(any()) } returns Unit

        PackageReplacedReceiver().onReceive(context, null)

        verify(exactly = 0) { NotificationListenerService.requestRebind(any()) }
    }
}
