package me.nagaev.veles.pairing

import org.junit.After
import org.junit.Assert.assertEquals

/**
 * Every pairing test must finish with no core handle open. A leaked handle lives for
 * 300 s inside the one Gradle test JVM and counts against the core's capacity of
 * eight executions, so the check runs after every test, not only after failures.
 *
 * Subclasses that build a fake core override [core]; the assertion then follows the
 * same seam the test exercised.
 */
abstract class PairingTestBase {
    protected open val core: PairingCore = VelesPairingCore()

    @After
    fun assertsNoOpenCoreHandles() {
        assertEquals("pairing test leaked a core handle", 0, core.openHandles())
    }
}
