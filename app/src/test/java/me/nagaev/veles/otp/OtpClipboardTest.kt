package me.nagaev.veles.otp

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.PersistableBundle
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.slot
import io.mockk.verify
import me.nagaev.veles.R
import me.nagaev.veles.common.VelesLog
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class OtpClipboardTest {
    private val context = mockk<Context>(relaxed = true)
    private val clipboardManager = mockk<ClipboardManager>(relaxed = true)
    private val clipData = mockk<ClipData>(relaxed = true)
    private val clipDescription = mockk<ClipDescription>(relaxed = true)
    private val logger = mockk<VelesLog>(relaxed = true)

    private val extrasSlot = slot<PersistableBundle>()
    private lateinit var clipboard: OtpClipboard

    @Before
    fun beforeTest() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns clipboardManager
        every { context.getString(R.string.otp_clipboard_label) } returns "OTP"

        mockkStatic(ClipData::class)
        every { ClipData.newPlainText(any<String>(), any<String>()) } returns clipData
        every { clipData.description } returns clipDescription
        every { clipDescription.extras = capture(extrasSlot) } just Runs

        clipboard = OtpClipboard(context, logger)
    }

    @Test
    fun `copy creates a sensitive clip`() {
        val copied = clipboard.copy("123456")

        assertTrue(copied)
        verify { ClipData.newPlainText("OTP", "123456") }
        verify { clipboardManager.setPrimaryClip(clipData) }
        assertTrue(extrasSlot.captured.getBoolean(ClipDescription.EXTRA_IS_SENSITIVE))
        verify { logger.dCopiedOtp("123456") }
    }

    @Test
    fun `missing clipboard service returns false`() {
        every { context.getSystemService(Context.CLIPBOARD_SERVICE) } returns null

        assertFalse(clipboard.copy("123456"))
    }

    @Test
    fun `delayed clear fires when clip is still ours`() {
        every { clipboardManager.primaryClip } returns clipData
        every { clipData.description } returns clipDescription
        every { clipDescription.label } returns "OTP"
        every { clipData.itemCount } returns 1
        every { clipData.getItemAt(0) } returns mockk { every { text } returns "123456" }

        clipboard.copy("123456")

        advanceMainLooperPastClearDelay()

        verify { clipboardManager.clearPrimaryClip() }
    }

    @Test
    fun `delayed clear does not fire when external app replaced the clip`() {
        val externalClip = mockk<ClipData>(relaxed = true)
        val externalDescription = mockk<ClipDescription>(relaxed = true)
        every { externalClip.description } returns externalDescription
        every { externalDescription.label } returns "Note"
        every { externalClip.itemCount } returns 1
        every { externalClip.getItemAt(0) } returns mockk { every { text } returns "user text" }

        clipboard.copy("123456")

        every { clipboardManager.primaryClip } returns externalClip

        advanceMainLooperPastClearDelay()

        verify(exactly = 0) { clipboardManager.clearPrimaryClip() }
    }

    @Test
    fun `delayed clear does not fire when same OTP is re-copied`() {
        every { clipboardManager.primaryClip } returns clipData
        every { clipData.description } returns clipDescription
        every { clipDescription.label } returns "OTP"
        every { clipData.itemCount } returns 1
        every { clipData.getItemAt(0) } returns mockk { every { text } returns "123456" }

        clipboard.copy("123456")
        clipboard.copy("123456")

        advanceMainLooperPastClearDelay()

        verify(exactly = 1) { clipboardManager.clearPrimaryClip() }
    }

    @Test
    fun `delayed clear fails closed when primaryClip is null in background`() {
        every { clipboardManager.primaryClip } returns null

        clipboard.copy("123456")

        advanceMainLooperPastClearDelay()

        verify(exactly = 0) { clipboardManager.clearPrimaryClip() }
    }

    private fun advanceMainLooperPastClearDelay() {
        shadowOf(android.os.Looper.getMainLooper()).idle(2 * 60 * 1000L + 1)
    }
}
