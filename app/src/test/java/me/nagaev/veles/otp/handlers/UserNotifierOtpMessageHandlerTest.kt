package me.nagaev.veles.otp.handlers

import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.test.core.app.ApplicationProvider
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.otp.CopyDataReceiver
import me.nagaev.veles.otp.OtpClipboard
import me.nagaev.veles.settings.SettingsRepository
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.math.BigDecimal

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
@OptIn(ExperimentalCoroutinesApi::class)
class UserNotifierOtpMessageHandlerTest {
    private val defaultMessage =
        OtpMessage(
            otp = Otp(value = "123456", id = "123"),
            pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
            merchant = "Test Merchant",
        )

    private lateinit var context: Context
    private lateinit var notificationManager: NotificationManager
    private lateinit var settingsRepository: SettingsRepository
    private lateinit var otpClipboard: OtpClipboard

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        settingsRepository = mockk(relaxed = true)
        otpClipboard = mockk(relaxed = true)
        // Default: auto-copy disabled so the legacy tests keep their original behavior.
        coEvery { settingsRepository.isAutoCopyEnabled() } returns false
    }

    private fun handler(scope: kotlinx.coroutines.CoroutineScope) =
        UserNotifierOtpMessageHandler(
            context = context,
            settingsRepository = settingsRepository,
            otpClipboard = otpClipboard,
            applicationScope = scope,
        )

    private fun postedActionTitle(): String {
        val notification = shadowOf(notificationManager).getNotification(defaultMessage.hashCode())
            ?: error("Expected a notification posted for message")
        return notification.actions.first().title.toString()
    }

    @Test
    fun `enabled auto-copy writes OTP before copied notification`() = runTest {
        coEvery { settingsRepository.isAutoCopyEnabled() } returns true
        every { otpClipboard.copy("123456") } returns true

        handler(this).onOtpMessageReceived(defaultMessage)
        advanceUntilIdle()

        // The clipboard write must happen before the notification is posted; the
        // posted notification's action title reflects the auto-copy result.
        verify { otpClipboard.copy("123456") }
        assertEquals("Copy 123456 Copied ✓", postedActionTitle())
    }

    @Test
    fun `disabled auto-copy leaves clipboard untouched and posts normal notification`() = runTest {
        coEvery { settingsRepository.isAutoCopyEnabled() } returns false

        handler(this).onOtpMessageReceived(defaultMessage)
        advanceUntilIdle()

        verify(exactly = 0) { otpClipboard.copy(any()) }
        assertEquals("Copy 123456", postedActionTitle())
    }

    @Test
    fun `enabled auto-copy writes even when notifications are disabled`() = runTest {
        coEvery { settingsRepository.isAutoCopyEnabled() } returns true
        every { otpClipboard.copy("123456") } returns true
        shadowOf(notificationManager).setNotificationsEnabled(false)

        handler(this).onOtpMessageReceived(defaultMessage)
        advanceUntilIdle()

        verify { otpClipboard.copy("123456") }
        assertNull(
            "No notification should be posted when notifications are disabled",
            shadowOf(notificationManager).getNotification(defaultMessage.hashCode()),
        )
    }

    @Test
    fun `Valid OTP message handling`() = runTest {
        val message = defaultMessage.copy()
        val h = handler(this)
        h.onOtpMessageReceived(message)
        advanceUntilIdle()

        val notifications = shadowOf(notificationManager).allNotifications
        assert(notifications.isNotEmpty()) { "Expected at least one notification to be posted" }
    }

    @Test
    fun `Notification content text and title reflect the OtpMessage`() = runTest {
        val message = defaultMessage.copy()
        val h = handler(this)
        h.onOtpMessageReceived(message)
        advanceUntilIdle()

        val notification = shadowOf(notificationManager).getNotification(message.hashCode())
            ?: error("Expected a notification posted for message")

        assertEquals(
            "Notification title must be the merchant",
            "Test Merchant",
            notification.extras.get(NotificationCompat.EXTRA_TITLE),
        )
        assertEquals(
            "Notification text must contain OTP, amount, and currency",
            "OTP: 123456, Pay: 100 USD",
            notification.extras.get(NotificationCompat.EXTRA_TEXT),
        )
    }

    @Test
    fun `Copy PendingIntent is distinct per notification and keeps its own OTP`() = runTest {
        val h = handler(this)

        val message1 =
            OtpMessage(
                otp = Otp(value = "111111", id = "1"),
                pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
                merchant = "Merchant One",
            )
        val message2 =
            OtpMessage(
                otp = Otp(value = "222222", id = "2"),
                pay = Money(amount = BigDecimal(200), currencyCode = "USD"),
                merchant = "Merchant Two",
            )

        h.onOtpMessageReceived(message1)
        h.onOtpMessageReceived(message2)
        advanceUntilIdle()

        val notification1 = shadowOf(notificationManager).getNotification(message1.hashCode())
            ?: error("Expected a notification posted for message1")
        val notification2 = shadowOf(notificationManager).getNotification(message2.hashCode())
            ?: error("Expected a notification posted for message2")

        val copyAction1 = notification1.actions.first()
        val copyAction2 = notification2.actions.first()

        val pendingIntent1 = copyAction1.actionIntent
        val pendingIntent2 = copyAction2.actionIntent

        val shadowPendingIntent1 = shadowOf(pendingIntent1)
        val shadowPendingIntent2 = shadowOf(pendingIntent2)

        // The bug: a hard-coded request code 0 made both PendingIntents collapse into
        // one; FLAG_UPDATE_CURRENT then overwrote extras, so tapping "Copy" on the older
        // notification copied the newest OTP. The request code must differ per
        // notification so each carries its own extras.
        assertNotEquals(
            "PendingIntent request codes must differ per notification",
            shadowPendingIntent1.requestCode,
            shadowPendingIntent2.requestCode,
        )
        assertEquals(
            "Older notification's copy intent should carry its own OTP, not the newest one",
            "111111",
            shadowPendingIntent1.savedIntent.getStringExtra(CopyDataReceiver.EXTRA_COPY_TEXT),
        )
        assertEquals(
            "Newer notification's copy intent should carry its own OTP",
            "222222",
            shadowPendingIntent2.savedIntent.getStringExtra(CopyDataReceiver.EXTRA_COPY_TEXT),
        )
    }

    @Test
    fun `Copy PendingIntent request code matches the posted notification id`() = runTest {
        val message = defaultMessage.copy()
        val h = handler(this)
        h.onOtpMessageReceived(message)
        advanceUntilIdle()

        val notification = shadowOf(notificationManager).getNotification(message.hashCode())
            ?: error("Expected a notification posted for message")

        val pendingIntent = notification.actions.first().actionIntent
        val shadowPendingIntent = shadowOf(pendingIntent)

        // Regression guard for the #10 collision: the Copy PendingIntent's request code
        // must be tied to the exact id passed to notify(), so two notifications that are
        // distinct in the tray always have distinct Copy actions, and can never fall back
        // to a value derived from the source notification's (possibly-reused) key.
        assertEquals(
            "Copy PendingIntent request code must match the id passed to notify()",
            message.hashCode(),
            shadowPendingIntent.requestCode,
        )
    }

    @Test
    fun `Copy intent data URI encodes the notification id`() = runTest {
        val message = defaultMessage.copy()
        val h = handler(this)
        h.onOtpMessageReceived(message)
        advanceUntilIdle()

        val notification = shadowOf(notificationManager).getNotification(message.hashCode())
            ?: error("Expected a notification posted for message")

        val pendingIntent = notification.actions.first().actionIntent
        val savedIntent = shadowOf(pendingIntent).savedIntent

        assertEquals(
            "veles://otp/${message.hashCode()}",
            savedIntent.data.toString(),
        )
    }

    @Test
    fun `Copy intent carries the notification id`() = runTest {
        val message = defaultMessage.copy()
        val h = handler(this)
        h.onOtpMessageReceived(message)
        advanceUntilIdle()

        val notification = shadowOf(notificationManager).getNotification(message.hashCode())
            ?: error("Expected a notification posted for message")

        val pendingIntent = notification.actions.first().actionIntent
        val savedIntent = shadowOf(pendingIntent).savedIntent

        assertEquals(
            "Copy intent must carry the notification id used to post it",
            message.hashCode(),
            savedIntent.getIntExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, -1),
        )
    }

    @Test
    fun `Notification channel creation`() {
        // Ensure that the notification channel is created correctly
        // when it doesn't exist.
        // TODO implement test
    }

    @Test
    fun `Notification channel re use`() {
        // Check that the notification channel is not recreated if it already exists.
        // TODO implement test
    }

    @Test
    fun `Notifications disabled`() {
        // Verify that no notification is created if the application
        // notifications are disabled.
        // TODO implement test
    }

    @Test
    fun `Copy intent correctness`() {
        // Ensure that the copy intent is correctly created
        // with the OTP value and associated action.
        // TODO implement test
    }

    @Test
    fun `Pending intent flags`() {
        // Validate that the correct flags (FLAG_UPDATE_CURRENT and FLAG_IMMUTABLE)
        // are set for the copy pending intent.
        // TODO implement test
    }

    @Test
    fun `Notification content correctness`() {
        // Confirm that the notification's title, text, and copy action's
        // text accurately reflect the data in the OtpMessage.
        // TODO implement test
    }

    @Test
    fun `Notification icon`() {
        // Check that the correct small icon (R.drawable.ic_otp_message)
        // is used for the notification.
        // TODO implement test
    }

    @Test
    fun `Empty OTP value`() {
        // Test the behavior when the OtpMessage contains an empty
        // OTP value, ensuring notification content is formed as expected.
        // TODO implement test
    }

    @Test
    fun `Empty merchant`() {
        // Verify that the notification can be created even if the
        // merchant field is empty in the OtpMessage.
        // TODO implement test
    }

    @Test
    fun `Negative pay amount`() {
        // Test the notification generation when the pay amount in the
        // OtpMessage is negative. It should be handled gracefully and
        // included in the notification message.
        // TODO implement test
    }

    @Test
    fun `Zero pay amount`() {
        // Test the notification generation when the pay amount in the
        // OtpMessage is zero.
        // TODO implement test
    }

    @Test
    fun `Empty currency code`() {
        // Verify that the notification is created and displayed correctly
        // even if the currency code field is empty.
        // TODO implement test
    }

    @Test
    fun `Notification ID uniqueness`() {
        // Confirm that each notification created for a different message
        // will have a unique ID. Test to see if message.hashCode() is sufficient
        // TODO implement test
    }

    @Test
    fun `Notification priority`() {
        // Ensure that the notification is set with the correct priority
        // level (NotificationCompat.PRIORITY_HIGH)
        // TODO implement test
    }

    @Test
    fun `Null OtpMessage`() {
        // Test if the method gracefully handles a null OtpMessage.
        // TODO implement test
    }
}