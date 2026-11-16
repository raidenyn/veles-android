package me.nagaev.veles.debugpairing

import me.nagaev.veles.debugprobe.PowerManagerWakeResource
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * OTP-11 D9 / "Release exclusion guards": every new peer-management string must be named in
 * `releaseHookForbidden`, so `:app:releaseHookGuard` scans the R8 output for it. The list is
 * the contract; a string added to the debug screen without its needle fails here.
 */
class ReleaseGuardStringsTest {
    /** The strings this task adds to the debug screen. */
    private val newStrings = listOf(
        "pairing_debug_peers_title",
        "pairing_debug_peer_fingerprint",
        "pairing_debug_peer_connected",
        "pairing_debug_peer_disconnected",
        "pairing_debug_revoke",
        "pairing_debug_mutual_remove",
        "pairing_debug_peer_revoked",
        "pairing_debug_revoke_failed",
        "pairing_debug_mutual_removed",
        "pairing_debug_mutual_local_only",
        "pairing_debug_mutual_no_response",
        "pairing_debug_mutual_failed",
        "pairing_debug_reset",
        "pairing_debug_reset_warning",
        "pairing_debug_reset_confirm",
        "pairing_debug_reset_cancel",
        "pairing_debug_reset_done",
        "pairing_debug_reset_failed",
        "pairing_debug_confirmation_required",
        "pairing_debug_trust_status",
        "pairing_debug_trust_ok",
        "pairing_debug_trust_re_pair",
        "pairing_debug_peers_failed",
        "pairing_debug_no_peers",
        "pairing_debug_unavailable",
    )

    @Test
    fun every_new_debug_string_is_forbidden_in_release() {
        val strings = debugStrings()
        val forbidden = releaseHookForbidden()

        val missing = newStrings.filter { name ->
            val value = strings[name]
            // A `$` in a format argument is escaped in the Kotlin source (`%1\$s`).
            value == null || "\"${value.replace("$", "\\$")}\"" !in forbidden
        }

        assertTrue(
            "missing from pairing_debug strings or releaseHookForbidden: $missing",
            missing.isEmpty(),
        )
    }

    /**
     * OTP-12: `lintDebug` requires the platform `prefix:tag` wake-lock form, which puts the Gate B
     * wake tag outside the `me.nagaev.veles.debug.` prefix. The resource-name loop above cannot see
     * a tag, so the exact string is asserted here next to the list it must stay in.
     */
    @Test
    fun gate_b_wake_lock_tag_is_forbidden_in_release() {
        val tag = PowerManagerWakeResource.TAG
        assertTrue("$tag is missing from releaseHookForbidden", "\"$tag\"" in releaseHookForbidden())
    }

    private fun debugStrings(): Map<String, String> {
        val file = firstExisting("src/debug/res/values/strings.xml", "app/src/debug/res/values/strings.xml")
        val pattern = Regex("""<string name="([^"]+)">(.*?)</string>""")
        return pattern.findAll(file.readText()).associate { it.groupValues[1] to it.groupValues[2] }
    }

    private fun releaseHookForbidden(): String {
        val file = firstExisting("build.gradle.kts", "app/build.gradle.kts")
        val text = file.readText()
        val start = text.indexOf("val releaseHookForbidden = listOf(")
        assertTrue("releaseHookForbidden is declared in ${file.path}", start >= 0)
        val end = text.indexOf("\n)\n", start)
        assertTrue("releaseHookForbidden is a closed list", end > start)
        return text.substring(start, end)
    }

    private fun firstExisting(vararg candidates: String): File = candidates
        .map(::File)
        .firstOrNull { it.isFile }
        ?: throw AssertionError("none of the expected files exists: ${candidates.joinToString()}")
}
