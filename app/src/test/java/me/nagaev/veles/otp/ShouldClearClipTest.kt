package me.nagaev.veles.otp

import android.content.ClipData
import android.content.ClipDescription
import io.mockk.every
import io.mockk.mockk
import me.nagaev.veles.otp.CopyDataReceiver.Companion.shouldClearClip
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ShouldClearClipTest {
    private fun clip(label: String?, text: String?, itemCount: Int = 1): ClipData {
        val description = mockk<ClipDescription>()
        every { description.label } returns label
        val item = mockk<ClipData.Item>()
        every { item.text } returns text
        return mockk<ClipData>().apply {
            every { this@apply.description } returns description
            every { this@apply.itemCount } returns itemCount
            every { getItemAt(0) } returns item
        }
    }

    @Test
    fun `matching label and text clears`() {
        assertTrue(shouldClearClip(clip("OTP", "123456"), "123456"))
    }

    @Test
    fun `matching label but different text does not clear`() {
        assertFalse(shouldClearClip(clip("OTP", "999999"), "123456"))
    }

    @Test
    fun `different label does not clear`() {
        assertFalse(shouldClearClip(clip("Note", "123456"), "123456"))
    }

    @Test
    fun `null clip does not clear`() {
        assertFalse(shouldClearClip(null, "123456"))
    }

    @Test
    fun `empty clip does not clear`() {
        assertFalse(shouldClearClip(clip("OTP", null, itemCount = 0), "123456"))
    }
}
