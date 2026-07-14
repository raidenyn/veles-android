package me.nagaev.veles.otp

import android.app.Notification
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.PersistableBundle
import androidx.core.app.NotificationManagerCompat
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.slot
import io.mockk.verify
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.R
import me.nagaev.veles.otp.CopyDataReceiver.Companion.EXTRA_COPY_TEXT
import me.nagaev.veles.otp.CopyDataReceiver.Companion.EXTRA_NOTIFICATION_ID
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder
import org.junit.Assert.assertTrue
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
    private val clipData = mockk<ClipData>(relaxed = true)
    private val clipDescription = mockk<ClipDescription>(relaxed = true)
    private val notificationManager = mockk<NotificationManagerCompat>(relaxed = true)
    private val logger = mockk<VelesLog>(relaxed = true)
    private val notificationBuilder = mockk<OtpNotificationBuilder>(relaxed = true)
    private val mockNotification = mockk<Notification>(relaxed = true)

    private val testText = "Test text"
    private val extrasSlot = slot<PersistableBundle>()

    @Before
    fun beforeTest() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns clipboardManager
        every { context.getString(R.string.otp_clipboard_label) } returns "OTP"
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns testText
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns 42
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT) } returns "Test Merchant"
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT) } returns "100"
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE) } returns "USD"

        mockkStatic(ClipData::class)
        every { ClipData.newPlainText(any<String>(), any<String>()) } returns clipData
        every { clipData.description } returns clipDescription
        every { clipDescription.extras = capture(extrasSlot) } just Runs

        mockkStatic(NotificationManagerCompat::class)
        every { NotificationManagerCompat.from(context) } returns notificationManager

        every {
            notificationBuilder.build(any(), any(), any(), any(), any(), any())
        } returns mockNotification
    }

    @Test
    fun `Valid Context and Intent with text`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify { ClipData.newPlainText("OTP", testText) }
        verify { clipboardManager.setPrimaryClip(clipData) }
    }

    @Test
    fun `Clip is marked sensitive`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        assertTrue(
            "Copied OTP clip must be flagged EXTRA_IS_SENSITIVE",
            extrasSlot.captured.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE),
        )
    }

    @Test
    fun `Notification is re-posted with copied state instead of cancelled`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify { notificationManager.notify(42, mockNotification) }
        verify(exactly = 0) { notificationManager.cancel(any<Int>()) }
    }

    @Test
    fun `Missing notification id skips re-post`() {
        every { intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1) } returns -1

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 0) { notificationManager.notify(any(), any()) }
    }

    @Test
    fun `Null Context`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(null, intent)
    }

    @Test
    fun `Null Intent`() {
        CopyDataReceiver(logger, notificationBuilder).onReceive(mockk(relaxed = true), null)
    }

    @Test
    fun `Missing EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns null

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Empty EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns ""

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 1) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Clipboard Service unavailable`() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns null

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Missing merchant amount and currency extras fall back to empty strings`() {
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT) } returns null
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT) } returns null
        every { intent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE) } returns null

        CopyDataReceiver(logger, notificationBuilder).onReceive(context, intent)

        verify { notificationManager.notify(42, mockNotification) }
    }
}
