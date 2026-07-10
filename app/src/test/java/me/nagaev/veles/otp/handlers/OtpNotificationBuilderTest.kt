package me.nagaev.veles.otp.handlers

import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.test.core.app.ApplicationProvider
import me.nagaev.veles.otp.CopyDataReceiver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.math.BigDecimal

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class OtpNotificationBuilderTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val notificationManager =
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private val testMerchant = "Test Merchant"
    private val testOtp = "123456"
    private val testAmount = BigDecimal(100).toPlainString()
    private val testCurrency = "USD"
    private val testNotificationId = 42

    private fun buildNotification(copied: Boolean) =
        OtpNotificationBuilder(context).build(
            notificationId = testNotificationId,
            merchant = testMerchant,
            otp = testOtp,
            amountText = testAmount,
            currencyCode = testCurrency,
            copied = copied,
        )

    @Test
    fun `Action label is Copy OTP when copied is false`() {
        val notification = buildNotification(copied = false)

        val action = notification.actions.first()
        assertEquals("Copy $testOtp", action.title)
    }

    @Test
    fun `Action label includes Copied checkmark when copied is true`() {
        val notification = buildNotification(copied = true)

        val action = notification.actions.first()
        assertEquals("Copy $testOtp Copied ✓", action.title)
    }

    @Test
    fun `PendingIntent request code matches notification id`() {
        val notification = buildNotification(copied = false)

        val pendingIntent = notification.actions.first().actionIntent
        val shadowPendingIntent = shadowOf(pendingIntent)

        assertEquals(
            "Copy PendingIntent request code must match the notification id",
            testNotificationId,
            shadowPendingIntent.requestCode,
        )
    }

    @Test
    fun `PendingIntent dataURI encodes the notification id`() {
        val notification = buildNotification(copied = false)

        val savedIntent = shadowOf(notification.actions.first().actionIntent).savedIntent

        assertEquals(
            "veles://otp/$testNotificationId",
            savedIntent.data.toString(),
        )
    }

    @Test
    fun `Intent extras carry all fields for rebuilding`() {
        val notification = buildNotification(copied = false)

        val savedIntent = shadowOf(notification.actions.first().actionIntent).savedIntent

        assertEquals(testOtp, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_COPY_TEXT))
        assertEquals(
            testNotificationId,
            savedIntent.getIntExtra(CopyDataReceiver.EXTRA_NOTIFICATION_ID, -1),
        )
        assertEquals(testMerchant, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_MERCHANT))
        assertEquals(testAmount, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_AMOUNT_TEXT))
        assertEquals(testCurrency, savedIntent.getStringExtra(CopyDataReceiver.EXTRA_CURRENCY_CODE))
    }

    @Test
    fun `Notification content text and title are correct`() {
        val notification = buildNotification(copied = false)

        assertEquals(testMerchant, notification.extras.get(NotificationCompat.EXTRA_TITLE))
        assertEquals(
            "OTP: $testOtp, Pay: $testAmount $testCurrency",
            notification.extras.get(NotificationCompat.EXTRA_TEXT),
        )
    }

    @Test
    fun `Channel is created on first build`() {
        buildNotification(copied = false)

        val channel = notificationManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID)
        assertNotNull("Notification channel must be created", channel)
    }

    @Test
    fun `Channel is not recreated on second build`() {
        buildNotification(copied = false)
        val channelAfterFirst =
            notificationManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID)

        buildNotification(copied = false)
        val channelAfterSecond =
            notificationManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID)

        assertEquals(
            "Channel id should be unchanged after second build",
            channelAfterFirst.id,
            channelAfterSecond.id,
        )
    }
}
