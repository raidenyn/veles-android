package me.nagaev.veles.debugpairing

import me.nagaev.veles.R
import me.nagaev.veles.trust.TrustStatus
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

    /** OTP-11 D6: the one status a surface may show, including the fail-closed value. */
    @Test
    fun trust_status_labels_render_both_values() {
        val context = RuntimeEnvironment.getApplication()

        assertEquals(
            "Trust status: Trust OK",
            context.getString(R.string.pairing_debug_trust_status, context.getString(trustStatusLabel(TrustStatus.OK))),
        )
        assertEquals(
            "Trust status: Re-pair required",
            context.getString(
                R.string.pairing_debug_trust_status,
                context.getString(trustStatusLabel(TrustStatus.RE_PAIR_REQUIRED)),
            ),
        )
    }

    /** OTP-11 D9: the reset control is labelled destructive and asks for confirmation. */
    @Test
    fun reset_strings_are_destructive_and_confirmed() {
        val context = RuntimeEnvironment.getApplication()

        assertEquals("Reset identity", context.getString(R.string.pairing_debug_reset))
        assertEquals("Confirm reset", context.getString(R.string.pairing_debug_reset_confirm))
        assertEquals("Cancel reset", context.getString(R.string.pairing_debug_reset_cancel))
        assertEquals(
            "Reset is destructive: it deletes every trust record and the phone identity.",
            context.getString(R.string.pairing_debug_reset_warning),
        )
    }

    /** The spec's mutual-removal copy: local only, and nothing deleted. */
    @Test
    fun mutual_remove_without_a_session_says_nothing_was_deleted() {
        val context = RuntimeEnvironment.getApplication()

        val rendered = context.getString(R.string.pairing_debug_mutual_local_only)

        assertEquals(
            "No open full session: only local removal is possible. Nothing was deleted.",
            rendered,
        )
    }
}
