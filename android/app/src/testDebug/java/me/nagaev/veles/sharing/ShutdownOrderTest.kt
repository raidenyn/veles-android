package me.nagaev.veles.sharing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * OTP-12 design D5 / spec "Cancellation and detach": the debug build's `detach()` must run as the
 * first statement of `SharingService.shutdownSessions()`, before the host is shut down, so a
 * scheduled push cannot fire against a host that is already closing.
 */
class ShutdownOrderTest {
    @Test
    fun detach_precedes_host_shutdown() {
        val text = source().readText()
        val marker = "private fun shutdownSessions() {"
        val start = text.indexOf(marker)
        assertTrue("shutdownSessions() is declared", start >= 0)

        val firstStatement = text.substring(start + marker.length)
            .lineSequence()
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .first { line -> !line.startsWith("//") && !line.startsWith("*") && !line.startsWith("/*") }

        assertEquals(
            "detach is the first statement of shutdownSessions()",
            "BuildSessionExtensions.detach()",
            firstStatement,
        )
        val detach = text.indexOf("BuildSessionExtensions.detach()")
        val shutdown = text.indexOf("it.shutdown()")
        assertTrue("the host shutdown call is present", shutdown >= 0)
        assertTrue("detach runs before the host shutdown", detach < shutdown)
        assertTrue("detach is inside shutdownSessions()", detach > start)
    }

    private fun source(): File = listOf(
        File("src/main/java/me/nagaev/veles/sharing/SharingService.kt"),
        File("app/src/main/java/me/nagaev/veles/sharing/SharingService.kt"),
    ).firstOrNull { it.isFile }
        ?: throw AssertionError("SharingService.kt is not on the test working directory")
}
