package me.nagaev.veles

import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
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
        // BuildConfig.APPLICATION_ID includes the applicationIdSuffix for debug
        // ("me.nagaev.veles.debug") and the base id for release ("me.nagaev.veles").
        assertEquals(BuildConfig.APPLICATION_ID, appContext.packageName)
    }
}
