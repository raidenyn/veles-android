package me.nagaev.veles.crypto

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertArrayEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VelesCryptoInstrumentedTest {
    @Test
    fun reversesBinaryBytesThroughJni() {
        val input = byteArrayOf(0x00, 0x80.toByte(), 0xff.toByte(), 0x2a)
        val expected = byteArrayOf(0x2a, 0xff.toByte(), 0x80.toByte(), 0x00)

        assertArrayEquals(expected, VelesCrypto.reverseBytes(input))
    }

    @Test
    fun reversesEmptyBytesThroughJni() {
        assertArrayEquals(byteArrayOf(), VelesCrypto.reverseBytes(byteArrayOf()))
    }
}
