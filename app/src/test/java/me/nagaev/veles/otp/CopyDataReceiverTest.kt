package me.nagaev.veles.otp

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.verify
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.CopyDataReceiver.Companion.EXTRA_COPY_TEXT
import org.junit.Before
import org.junit.Test

class CopyDataReceiverTest {
    private val context = mockk<Context>(relaxed = true)
    private val clipboardManager = mockk<ClipboardManager>(relaxed = true)
    private val intent = mockk<Intent>(relaxed = true)
    private val clipData = mockk<ClipData>(relaxed = true)
    private val logger = mockk<VelesLog>(relaxed = true)

    private val testText = "Test text"

    @Before
    fun beforeTest() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns clipboardManager
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns testText

        mockkStatic(ClipData::class)
        every { ClipData.newPlainText(any<String>(), any<String>()) } returns clipData
    }

    @Test
    fun `Valid Context and Intent with text`() {
        val receiver = CopyDataReceiver(logger)
        receiver.onReceive(context, intent)

        verify { ClipData.newPlainText("OTP", testText) }
        verify { clipboardManager.setPrimaryClip(clipData) }
    }

    @Test
    fun `Null Context`() {
        val receiver = CopyDataReceiver(logger)
        receiver.onReceive(null, intent)
    }

    @Test
    fun `Null Intent`() {
        val context = mockk<Context>(relaxed = true)

        val receiver = CopyDataReceiver(logger)
        receiver.onReceive(context, null)
    }

    @Test
    fun `Missing EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns null

        val receiver = CopyDataReceiver(logger)
        receiver.onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Empty EXTRA COPY TEXT`() {
        every { intent.getStringExtra(EXTRA_COPY_TEXT) } returns ""

        val receiver = CopyDataReceiver(logger)
        receiver.onReceive(context, intent)

        verify(exactly = 1) { clipboardManager.setPrimaryClip(any()) }
    }

    @Test
    fun `Clipboard Service unavailable`() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns null

        val receiver = CopyDataReceiver(logger)
        receiver.onReceive(context, intent)

        verify(exactly = 0) { clipboardManager.setPrimaryClip(any()) }
    }
}
