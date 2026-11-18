package me.nagaev.veles.sharing

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import me.nagaev.veles.R
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SharingCardTextTest {
    private val ownLine = mapOf(
        SharingAvailability.Disabled to R.string.sharing_disabled,
        SharingAvailability.Starting to R.string.sharing_starting,
        SharingAvailability.Advertising to R.string.sharing_advertising,
        SharingAvailability.Unsupported to R.string.sharing_unsupported,
        SharingAvailability.PermissionRequired to R.string.sharing_permission,
        SharingAvailability.BluetoothOff to R.string.sharing_bluetooth_off,
        SharingAvailability.Failed to R.string.sharing_failed,
    )

    private val context get() = ApplicationProvider.getApplicationContext<Application>()

    @Test fun enabledWithDisabledShowsOnlyTheStoppedLine() {
        assertEquals(listOf(R.string.sharing_stopped), sharingCardLines(true, SharingAvailability.Disabled))
    }

    @Test fun notEnabledAndDisabledShowsOnlyTheDisabledLine() {
        assertEquals(listOf(R.string.sharing_disabled), sharingCardLines(false, SharingAvailability.Disabled))
    }

    @Test fun enabledAndFailingStatesShowTheNotRunningLineThenTheirOwn() {
        listOf(
            SharingAvailability.Unsupported,
            SharingAvailability.PermissionRequired,
            SharingAvailability.BluetoothOff,
            SharingAvailability.Failed,
        ).forEach {
            assertEquals(
                it.name,
                listOf(R.string.sharing_enabled_unavailable, ownLine.getValue(it)),
                sharingCardLines(true, it),
            )
        }
    }

    @Test fun enabledAndRunningShowOnlyTheirOwnLine() {
        assertEquals(listOf(R.string.sharing_starting), sharingCardLines(true, SharingAvailability.Starting))
        assertEquals(listOf(R.string.sharing_advertising), sharingCardLines(true, SharingAvailability.Advertising))
    }

    @Test fun notEnabledShowsOnlyTheOwnLineForEveryAvailability() {
        SharingAvailability.entries.forEach {
            assertEquals(it.name, listOf(ownLine.getValue(it)), sharingCardLines(false, it))
        }
    }

    @Test fun availabilityLinesResolveToTheirExactText() {
        val expected = mapOf(
            R.string.sharing_disabled to "Disabled. This transport does not share OTPs yet.",
            R.string.sharing_starting to "Starting Bluetooth service…",
            R.string.sharing_advertising to "Available over Bluetooth. OTP sharing is not enabled.",
            R.string.sharing_unsupported to "Bluetooth LE peripheral advertising is unavailable on this device.",
            R.string.sharing_permission to "Nearby devices permission is required. Allow it in app settings if denied.",
            R.string.sharing_bluetooth_off to "Turn on Bluetooth, then enable transport again.",
            R.string.sharing_failed to "Bluetooth service stopped. Try enabling transport again.",
            R.string.sharing_enabled_unavailable to
                "Sharing is enabled, but the transport is not running. Enable transport to retry, or stop sharing.",
        )
        expected.forEach { (id, text) -> assertEquals(text, context.getString(id)) }
    }

    @Test fun stoppedLineResolvesToTheExactCopy() {
        assertEquals(
            "Sharing is on, but the Bluetooth service is not running. This can happen after an app update, " +
                "a restart or the system stopping the app. Tap Enable transport to start it again, " +
                "or Stop sharing to turn it off.",
            context.getString(R.string.sharing_stopped),
        )
    }
}
