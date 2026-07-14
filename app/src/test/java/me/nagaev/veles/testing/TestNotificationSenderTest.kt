package me.nagaev.veles.testing

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.ContextWrapper
import android.content.res.Resources
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.test.core.app.ApplicationProvider
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.slot
import io.mockk.spyk
import io.mockk.verify
import me.nagaev.veles.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TestNotificationSenderTest {
    private val applicationContext = ApplicationProvider.getApplicationContext<Context>()
    private val channelManager = mockk<NotificationManager>(relaxed = true)
    private val notificationManager = mockk<NotificationManagerCompat>()
    private val postedNotification = slot<Notification>()
    private val resources = spyk(applicationContext.resources)
    private val context = object : ContextWrapper(applicationContext) {
        override fun getSystemService(name: String): Any? = if (name == Context.NOTIFICATION_SERVICE) channelManager else super.getSystemService(name)

        override fun getResources(): Resources = this@TestNotificationSenderTest.resources
    }

    @Before
    fun beforeTest() {
        every { resources.getString(R.string.test_notification_title) } returns "Localized test title"
        every { resources.getString(R.string.test_notification_channel_name) } returns "Localized test channel"
        every { resources.getString(R.string.test_notification_channel_description) } returns "Localized test channel description"
        every { resources.getString(R.string.test_notification_probe, *anyVararg()) } answers {
            "Localized test probe ${secondArg<Array<out Any>>().single()}"
        }
        mockkStatic(NotificationManagerCompat::class)
        every { NotificationManagerCompat.from(context) } returns notificationManager
        every { notificationManager.areNotificationsEnabled() } returns true
        every { notificationManager.notify(any(), capture(postedNotification)) } just Runs
    }

    @Test
    fun `Probe uses resource-backed title text and channel metadata`() {
        val probe = TestNotificationSender(context).postProbe()

        assertTrue(probe.startsWith("Localized test probe "))
        assertEquals("Localized test title", postedNotification.captured.extras.getCharSequence(NotificationCompat.EXTRA_TITLE))
        assertEquals(probe, postedNotification.captured.extras.getCharSequence(NotificationCompat.EXTRA_TEXT))
        verify {
            channelManager.createNotificationChannel(
                match {
                    it.id == TestNotificationSender.CHANNEL_ID &&
                        it.name == "Localized test channel" &&
                        it.description == "Localized test channel description"
                },
            )
        }
    }

    @Test
    fun `Channel is resubmitted for every posted test notification`() {
        every {
            channelManager.getNotificationChannel(TestNotificationSender.CHANNEL_ID)
        } returnsMany listOf(
            null,
            NotificationChannel(
                TestNotificationSender.CHANNEL_ID,
                "Existing channel",
                NotificationManager.IMPORTANCE_HIGH,
            ),
        )
        val sender = TestNotificationSender(context)

        sender.post("First")
        sender.post("Second")

        verify(exactly = 2) {
            channelManager.createNotificationChannel(
                match {
                    it.id == TestNotificationSender.CHANNEL_ID
                },
            )
        }
    }
}
