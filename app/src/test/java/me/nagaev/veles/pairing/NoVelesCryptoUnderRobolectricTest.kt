package me.nagaev.veles.pairing

import me.nagaev.veles.crypto.VelesCrypto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * A JNI library binds to one class loader per JVM. Robolectric loads app classes in
 * a sandbox loader, so initializing [VelesCrypto] here would make every later
 * real-core test in the same Gradle test JVM fail with "already loaded in another
 * classloader". This guard fails at the source of that mistake instead of leaving
 * the failure to an unrelated class that happens to run later.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class NoVelesCryptoUnderRobolectricTest {
    @Test
    fun robolectric_guard_fails_if_VelesCrypto_initialized() {
        assertFalse(
            "VelesCrypto was initialized under the Robolectric class loader",
            isInitialized(VelesCrypto::class.java),
        )
    }

    @Test
    fun constructing_the_production_seam_does_not_initialize_the_core() {
        val core = VelesPairingCore()

        assertEquals(0, core.openHandles())
        assertFalse(
            "VelesPairingCore must resolve VelesCrypto only when a command runs",
            isInitialized(VelesCrypto::class.java),
        )
    }

    /**
     * [sun.misc.Unsafe.shouldBeInitialized] is the only JDK API that reports class
     * initialization without triggering it; [Class.forName] with `initialize=false`
     * cannot tell the difference. Reached reflectively so no import of an internal
     * JDK class appears in the source.
     */
    private fun isInitialized(type: Class<*>): Boolean {
        val unsafeClass = Class.forName("sun.misc.Unsafe")
        val unsafe = unsafeClass.getDeclaredField("theUnsafe").apply { isAccessible = true }.get(null)
        val shouldBeInitialized = unsafeClass.getMethod("shouldBeInitialized", Class::class.java)
        return !(shouldBeInitialized.invoke(unsafe, type) as Boolean)
    }
}
