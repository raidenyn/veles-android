package me.nagaev.veles

import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Instrumented test, which will execute on an Android device.
 *
 * See [testing documentation](http://d.android.com/tools/testing).
 */
class ExampleInstrumentedTest {
    @Test
    fun useAppContext() {
        // Context of the app under test.
        val appContext = InstrumentationRegistry.getInstrumentation().targetContext
        // The debug build uses applicationIdSuffix ".debug", so the package name
        // is "me.nagaev.veles.debug" in debug and "me.nagaev.veles" in release.
        assertTrue(appContext.packageName.startsWith("me.nagaev.veles"))
    }
}
