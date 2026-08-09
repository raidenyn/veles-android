package me.nagaev.veles.otp

import android.app.Notification
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.verify
import me.nagaev.veles.R
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.CopyDataReceiver.Companion.EXTRA_COPY_TEXT
import me.nagaev.veles.otp.CopyDataReceiver.Companion.EXTRA_NOTIFICATION_ID
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CopyDataReceiverTest {
    private val context = mockk<Context>(relaxed = true)
    private val clipboardManager = mockk<ClipboardManager>(relaxed = true)
    private val intent = mockk<Intent>(relaxed = true)
    private val notificationManager = mockk<NotificationManagerCompat>(relaxed = true)
    private val logger = mockk<VelesLog>(relaxed = true)
    private val notificationBuilder = mockk<OtpNotificationBuilder>(relaxed = true)
    private val otpClipboard = mockk<OtpClipboard>(relaxed = true)
    private val mockNotification = mockk<Notification>(relaxed = true)

    private val testText = "Test text"

    @Before
    fun beforeTest() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns clipboardManager
        every { context.getString(R.string.otp_clipboard_label) } returns "OTP"
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns testText
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns 42
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT) } returns "Test Merchant"
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT) } returns "100"
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE) } returns "USD"
        every { otpClipboard.copy(any()) } returns true

        mockkStatic(NotificationManagerCompat::class)
        every { NotificationManagerCompat.from(context) } returns notificationManager

        every {
            notificationBuilder.build(any(), any(), any(), any(), any(), any())
        } returns mockNotification
    }

    @Test
    fun `receiver delegates OTP copy to shared helper`() {
        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

        verify { otpClipboard.copy(testText) }
    }

    @Test
    fun `Notification is re-posted with copied state instead of cancelled`() {
        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

        verify { notificationManager.notify(42, mockNotification) }
        verify(exactly = 0) { notificationManager.cancel(any<Int>()) }
    }

    @Test
    fun `Missing notification id skips re-post`() {
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns -1

        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

        verify(exactly = 0) { notificationManager.notify(any(), any()) }
    }

    @Test
    fun `Null Context`() {
        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(null, intent)
    }

    @Test
    fun `Null Intent`() {
        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(mockk(relaxed = true), null)
    }

    @Test
    fun `Missing EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns null

        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

        verify(exactly = 0) { otpClipboard.copy(any()) }
    }

    @Test
    fun `Empty EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns ""

        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

        verify(exactly = 1) { otpClipboard.copy("") }
    }

    @Test
    fun `Clipboard Service unavailable`() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns null
        every { otpClipboard.copy(any()) } returns false

        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Missing merchant amount and currency extras fall back to empty strings`() {
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT) } returns null
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT) } returns null
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE) } returns null

        CopyDataReceiver(logger, notificationBuilder, otpClipboard).onReceive(context, intent)

        verify { notificationManager.notify(42, mockNotification) }
    }
}
