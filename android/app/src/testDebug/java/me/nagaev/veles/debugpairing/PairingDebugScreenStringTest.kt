package me.nagaev.veles.debugpairing

import me.nagaev.veles.R
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * OTP2-REC-011: the debug screen shows the shared pair fingerprint only after both records
 * are authenticated. The string is debug-only and is exercised through the debug variant's
 * resource table.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PairingDebugScreenStringTest {
    @Test
    fun fingerprint_string_renders_the_display_value() {
        val rendered = RuntimeEnvironment.getApplication().getString(R.string.pairing_debug_fingerprint, "ABCD EF01 2345")

        assertEquals("Pairing fingerprint: ABCD EF01 2345", rendered)
    }
}
