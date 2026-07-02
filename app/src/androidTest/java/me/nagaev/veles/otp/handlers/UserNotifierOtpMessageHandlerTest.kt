package me.nagaev.veles.otp.handlers

import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.test.core.app.ApplicationProvider
import androidx.test.rule.GrantPermissionRule
import junit.framework.TestCase.assertNotNull
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.math.BigDecimal

class UserNotifierOtpMessageHandlerTest {
    @get:Rule
    val permissionRule: GrantPermissionRule = GrantPermissionRule.grant(android.Manifest.permission.POST_NOTIFICATIONS)

    private lateinit var context: Context
    private lateinit var handler: UserNotifierOtpMessageHandler

    private val defaultMessage =
        OtpMessage(
            id = 1,
            otp = Otp(value = "123456", id = "123"),
            pay = Money(amount = BigDecimal(100), currencyCode = "USD"),
            merchant = "Test Merchant",
        )

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        handler = UserNotifierOtpMessageHandler(context)
    }

    @Test
    fun testNotificationCreation() {
        handler.onOtpMessageReceived(defaultMessage)

        val notificationManager = NotificationManagerCompat.from(context)
        val notification = notificationManager.activeNotifications.find { it.id == defaultMessage.hashCode() }

        assertNotNull(notification)
        assertEquals(
            "OTP: 123456, Pay: 100 USD",
            notification?.notification?.extras?.getString(
                NotificationCompat.EXTRA_TEXT,
            ),
        )
        assertEquals("Test Merchant", notification?.notification?.extras?.getString(NotificationCompat.EXTRA_TITLE))
    }
}
