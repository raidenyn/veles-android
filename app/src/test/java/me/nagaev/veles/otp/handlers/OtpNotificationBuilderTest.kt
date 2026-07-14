package me.nagaev.veles.otp.handlers

import android.app.NotificationManager
import android.app.NotificationChannel
import android.content.Context
import android.content.ContextWrapper
import androidx.core.app.NotificationCompat
import androidx.test.core.app.ApplicationProvider
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import me.nagaev.veles.R
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

    private fun buildNotification(copied: Boolean) = OtpNotificationBuilder(context).build(
        notificationId = testNotificationId,
        merchant = testMerchant,
        otp = testOtp,
        amountText = testAmount,
        currencyCode = testCurrency,
        copied = copied,
    )

    private fun buildNotification(
        builder: OtpNotificationBuilder,
        copied: Boolean,
    ) = builder.build(
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
        assertEquals(context.getString(R.string.otp_notification_copy, testOtp), action.title)
    }

    @Test
    fun `Action label includes Copied checkmark when copied is true`() {
        val notification = buildNotification(copied = true)

        val action = notification.actions.first()
        assertEquals(context.getString(R.string.otp_notification_copied, testOtp), action.title)
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
            context.getString(
                R.string.otp_notification_content,
                testOtp,
                testAmount,
                testCurrency,
            ),
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
    fun `Channel metadata is resubmitted without changing id`() {
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
        assertEquals(
            context.getString(R.string.otp_notification_channel_name),
            channelAfterSecond.name,
        )
        assertEquals(
            context.getString(R.string.otp_notification_channel_description),
            channelAfterSecond.description,
        )
    }

    @Test
    fun `Channel is resubmitted on second build`() {
        val observingManager = mockk<NotificationManager>(relaxed = true)
        every {
            observingManager.getNotificationChannel(OtpNotificationBuilder.CHANNEL_ID)
        } returnsMany listOf(
            null,
            NotificationChannel(
                OtpNotificationBuilder.CHANNEL_ID,
                "Existing channel",
                NotificationManager.IMPORTANCE_HIGH,
            ),
        )
        val observingContext = object : ContextWrapper(context) {
            override fun getSystemService(name: String): Any? =
                if (name == Context.NOTIFICATION_SERVICE) observingManager else super.getSystemService(name)
        }
        val builder = OtpNotificationBuilder(observingContext)

        buildNotification(builder, copied = false)
        buildNotification(builder, copied = false)

        verify(exactly = 2) {
            observingManager.createNotificationChannel(match {
                it.id == OtpNotificationBuilder.CHANNEL_ID
            })
        }
    }
}
