package me.nagaev.veles.permissions.services

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.provider.Settings
import androidx.test.core.app.ApplicationProvider
import io.mockk.every
import io.mockk.mockk
import me.nagaev.veles.otp.NotificationListener
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AccessNotificationPermissionProviderTest {
    companion object {
        private const val PACKAGE_NAME = "me.nagaev.veles"
    }

    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val activity =
        mockk<Activity> {
            every { packageName } returns PACKAGE_NAME
            every { contentResolver } returns this@AccessNotificationPermissionProviderTest.context.contentResolver
        }
    private val activityProvider =
        mockk<ActivityProvider> {
            every { getActivity() } returns activity
        }
    private val provider = AccessNotificationPermissionProvider(activityProvider)
    private val expectedComponent =
        ComponentName(PACKAGE_NAME, NotificationListener::class.java.name).flattenToString()

    @Test
    fun `null and empty settings are not granted`() {
        assertFalse(isGranted(null))
        assertFalse(isGranted(""))
    }

    @Test
    fun `exact listener component is granted`() {
        assertTrue(isGranted(expectedComponent))
    }

    @Test
    fun `exact listener among multiple entries is granted`() {
        val unrelated = ComponentName("example.other", "example.other.Listener").flattenToString()

        assertTrue(isGranted("not-a-component:$unrelated:$expectedComponent"))
    }

    @Test
    fun `component containing expected text is not granted`() {
        val lookalike =
            ComponentName(
                "prefix.$PACKAGE_NAME",
                NotificationListener::class.java.name,
            ).flattenToString()

        assertFalse(isGranted(lookalike))
    }

    @Test
    fun `malformed and unrelated entries are not granted`() {
        val unrelated = ComponentName("example.other", "example.other.Listener").flattenToString()

        assertFalse(isGranted("not-a-component:$unrelated"))
    }

    private fun isGranted(setting: String?): Boolean {
        Settings.Secure.putString(
            context.contentResolver,
            AccessNotificationPermissionProvider.ENABLED_NOTIFICATION_LISTENERS,
            setting,
        )
        return provider.isGranted()
    }
}
