package me.nagaev.veles.otp.handlers

import android.app.NotificationManager
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import me.nagaev.veles.otp.CopyDataReceiver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.math.BigDecimal

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class UserNotifierOtpMessageHandlerTest {
    private val defaultMessage =
        OtpMessage(
            id = 1,
            otp = Otp(value = "123456", id = "123"),
            pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
            merchant = "Test Merchant",
        )

    @Test
    fun `Valid OTP message handling`() {
        val message = defaultMessage.copy()
        val context = ApplicationProvider.getApplicationContext<Context>()
        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val handler = UserNotifierOtpMessageHandler(context)
        handler.onOtpMessageReceived(message)

        // Verify a notification was posted
        val shadowNotificationManager = shadowOf(notificationManager)
        val notifications = shadowNotificationManager.allNotifications
        assert(notifications.isNotEmpty()) { "Expected at least one notification to be posted" }
    }

    @Test
    fun `Copy PendingIntent is distinct per notification and keeps its own OTP`() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val handler = UserNotifierOtpMessageHandler(context)

        val message1 =
            OtpMessage(
                id = 1,
                otp = Otp(value = "111111", id = "1"),
                pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
                merchant = "Merchant One",
            )
        val message2 =
            OtpMessage(
                id = 2,
                otp = Otp(value = "222222", id = "2"),
                pay = Money(amount = BigDecimal(200), currencyCode = "USD"),
                merchant = "Merchant Two",
            )

        handler.onOtpMessageReceived(message1)
        handler.onOtpMessageReceived(message2)

        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val shadowNotificationManager = shadowOf(notificationManager)

        val notification1 = shadowNotificationManager.getNotification(message1.hashCode())
            ?: error("Expected a notification posted for message1")
        val notification2 = shadowNotificationManager.getNotification(message2.hashCode())
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
