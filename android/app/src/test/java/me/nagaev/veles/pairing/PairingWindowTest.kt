package me.nagaev.veles.pairing

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.SecureRandom

class PairingWindowTest : PairingTestBase() {
    private var now = 0L

    @Test
    fun window_expires_at_300s() {
        now = 1_000L
        val window = PairingWindow({ now }, ScriptedRandom(PAIRING_ID, draw(7)))
        val opened = window.open()

        now += 299_999L
        assertTrue(window.isOpen())

        now += 1L
        assertFalse("the window closes at exactly 300 000 ms", window.isOpen())
        assertArrayEquals("the code is erased on expiry", ByteArray(6), opened.code.copyBytes())
        assertThrows(IllegalStateException::class.java) { window.codeBytes() }
        assertTrue(window.pairingId.isEmpty())
    }

    @Test
    fun second_open_refused() {
        now = 0L
        val random = ScriptedRandom(PAIRING_ID, draw(42))
        val window = PairingWindow({ now }, random)
        val opened = window.open()
        val codeBefore = window.codeBytes()

        assertThrows(IllegalStateException::class.java) { window.open() }

        assertTrue("the open window is unchanged", window.isOpen())
        assertArrayEquals(PAIRING_ID, window.pairingId)
        assertArrayEquals(opened.pairingId, window.pairingId)
        assertArrayEquals(codeBefore, window.codeBytes())
        assertEquals("a refused open draws nothing", 2, random.calls)
    }

    @Test
    fun window_state_absent_after_restart() {
        now = 0L
        val window = PairingWindow({ now }, ScriptedRandom(PAIRING_ID, draw(42)))
        window.open()

        // Process death loses every in-memory object. Nothing is persisted, so the
        // restarted process starts closed with no id and no code.
        val restarted = PairingWindow({ now }, SecureRandom())
        assertFalse(restarted.isOpen())
        assertTrue(restarted.pairingId.isEmpty())
        assertThrows(IllegalStateException::class.java) { restarted.codeBytes() }
    }

    @Test
    fun code_is_six_digits_with_leading_zeros_uniform() {
        now = 0L
        val random = ScriptedRandom(
            PAIRING_ID,
            draw(0xFFFF_FFFFL),
            draw(42L),
        )
        val window = PairingWindow({ now }, random)

        val opened = window.open()

        assertArrayEquals(
            "drew ${String(opened.code.copyBytes(), Charsets.US_ASCII)}",
            "000042".toByteArray(Charsets.US_ASCII),
            opened.code.copyBytes(),
        )
        assertArrayEquals("000042".toByteArray(Charsets.US_ASCII), window.codeBytes())
        assertEquals("the out-of-range draw must be rejected, not wrapped", 3, random.calls)
    }

    @Test
    fun code_toString_redacts() {
        now = 0L
        val window = PairingWindow({ now }, ScriptedRandom(PAIRING_ID, draw(42)))
        val opened = window.open()

        val text = opened.code.toString()
        assertFalse("toString must not contain the code", text.contains("000042"))
        assertFalse("toString must not contain the digits", text.contains("42"))
        assertTrue(text.contains("REDACTED"))

        val refusal = assertThrows(IllegalStateException::class.java) { window.open() }
        assertFalse(refusal.message.orEmpty().contains("000042"))
        assertFalse(refusal.message.orEmpty().contains("42"))
    }

    private fun draw(value: Long): ByteArray = byteArrayOf(
        (value ushr 24).toByte(),
        (value ushr 16).toByte(),
        (value ushr 8).toByte(),
        value.toByte(),
    )

    private class ScriptedRandom(private vararg val draws: ByteArray) : SecureRandom() {
        private var index = 0

        val calls: Int get() = index

        override fun nextBytes(bytes: ByteArray) {
            val draw = draws[index]
            assertEquals("scripted draw size", bytes.size, draw.size)
            draw.copyInto(bytes)
            index++
        }
    }

    private companion object {
        val PAIRING_ID = ByteArray(16) { (it + 1).toByte() }
    }
}
