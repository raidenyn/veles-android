package me.nagaev.veles.otp

import android.content.ComponentName
import android.provider.Settings
import me.nagaev.veles.R
import me.nagaev.veles.common.UiText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class NotificationRedactionPathTest {
    private val componentName = ComponentName("me.nagaev.veles", "me.nagaev.veles.otp.NotificationListener")

    @Test
    fun `from oneplus lowercase returns OxygenOS`() {
        assertTrue(NotificationRedactionPath.from("oneplus", componentName) is NotificationRedactionPath.OxygenOS)
    }

    @Test
    fun `from OnePlus mixed case returns OxygenOS`() {
        assertTrue(NotificationRedactionPath.from("OnePlus", componentName) is NotificationRedactionPath.OxygenOS)
    }

    @Test
    fun `from ONEPLUS uppercase returns OxygenOS`() {
        assertTrue(NotificationRedactionPath.from("ONEPLUS", componentName) is NotificationRedactionPath.OxygenOS)
    }

    @Test
    fun `from Google returns StockAndroid`() {
        assertTrue(NotificationRedactionPath.from("Google", componentName) is NotificationRedactionPath.StockAndroid)
    }

    @Test
    fun `from samsung returns StockAndroid`() {
        assertTrue(NotificationRedactionPath.from("samsung", componentName) is NotificationRedactionPath.StockAndroid)
    }

    @Test
    fun `from null returns StockAndroid`() {
        assertTrue(NotificationRedactionPath.from(null, componentName) is NotificationRedactionPath.StockAndroid)
    }

    @Test
    fun `StockAndroid settings intent uses detail action on API 34+`() {
        val path = NotificationRedactionPath.StockAndroid
        assertEquals(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS, path.settingsIntent(componentName).action)
    }

    @Test
    fun `StockAndroid settingsLocation is the stock settings resource`() {
        assertEquals(
            UiText.Res(R.string.sensitive_card_stock_settings_location),
            NotificationRedactionPath.StockAndroid.settingsLocation,
        )
    }

    @Test
    fun `OxygenOS settingsLocation is the OnePlus settings resource`() {
        assertEquals(
            UiText.Res(R.string.sensitive_card_oneplus_settings_location),
            NotificationRedactionPath.OxygenOS.settingsLocation,
        )
    }

    @Test
    fun `OxygenOS settings intent matches StockAndroid intent action`() {
        assertEquals(
            NotificationRedactionPath.StockAndroid.settingsIntent(componentName).action,
            NotificationRedactionPath.OxygenOS.settingsIntent(componentName).action,
        )
    }

    @Test
    fun `StockAndroid settings intent extra is a flattened string`() {
        val path = NotificationRedactionPath.StockAndroid
        val intent = path.settingsIntent(componentName)
        val extra = intent.getStringExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME)
        assertEquals(componentName.flattenToString(), extra)
    }
}
