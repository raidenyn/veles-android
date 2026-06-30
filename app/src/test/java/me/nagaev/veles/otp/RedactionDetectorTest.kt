package me.nagaev.veles.otp

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.service.notification.StatusBarNotification
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class RedactionDetectorTest {

    private lateinit var context: Context
    private val notificationManager: NotificationManager
        get() = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        if (notificationManager.getNotificationChannel("test") == null) {
            notificationManager.createNotificationChannel(
                NotificationChannel("test", "test", NotificationManager.IMPORTANCE_HIGH)
            )
        }
    }

    private fun sbn(visibility: Int, text: CharSequence?): StatusBarNotification {
        val notification = Notification.Builder(context, "test")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("title")
            .setContentText(text)
            .setVisibility(visibility)
            .build()
        return StatusBarNotification(
            "pkg", "pkg", 1, "tag", 0, 0, 0, notification, android.os.Process.myUserHandle(), 0L
        )
    }

    @Test
    fun `secret notification with no digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "Sensitive notification content hidden")))
    }

    @Test
    fun `secret notification with digit run is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "Your OTP is 123456")))
    }

    @Test
    fun `public notification with no digits is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_PUBLIC, "Sensitive notification content hidden")))
    }

    @Test
    fun `private notification with no digits is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_PRIVATE, "no digits here")))
    }

    @Test
    fun `secret notification with blank text is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "")))
    }

    @Test
    fun `secret notification with null text is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, null)))
    }

    @Test
    fun `non-ASCII OTP text with digit run is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "รหัส OTP ของคุณคือ 079853")))
    }

    @Test
    fun `German redaction placeholder with no digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "Vertrauliche Benachrichtigungsinhalte ausgeblendet")))
    }

    @Test
    fun `Japanese redaction placeholder with no digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "通知の機密内容は非表示です")))
    }

    @Test
    fun `notification with exactly 4 digits is not redacted`() {
        assertFalse(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "code 1234")))
    }

    @Test
    fun `notification with 3 digits is redacted`() {
        assertTrue(RedactionDetector.isRedacted(sbn(Notification.VISIBILITY_SECRET, "code 123")))
    }
}