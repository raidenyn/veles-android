package me.nagaev.veles.otp

import android.app.Notification
import android.content.Intent
import android.os.Bundle
import android.service.notification.StatusBarNotification
import android.service.notification.NotificationListenerService.START_REDELIVER_INTENT
import android.util.Log
import androidx.core.app.NotificationCompat
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.verify
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.Before

class NotificationListenerTest {

    @Before
    fun beforeTest() {
        mockkStatic(Log::class)
        every { Log.d(any(), any()) } returns 0
    }

    @Test
    fun `onCreate service initialization`() {
        val service = NotificationListener()
        service.onCreate()
    }

    @Test
    fun `onStartCommand returns `() {
        val service = NotificationListener()

        val intent = mockk<Intent>(relaxed = true)
        val result = service.onStartCommand(intent, 0, 0)

        assertEquals(START_REDELIVER_INTENT, result)
    }

    @Test
    fun `onListenerConnected calls saveConnectionState - true`() {
        val state = mockk<NotificationStatePreferences>(relaxed = true)

        val service = NotificationListener(state)

        every { state.saveConnectionState(any()) } returns Unit

        service.onListenerConnected()

        verify { state.saveConnectionState(true) }
    }

    @Test
    fun `onListenerDisconnected calls saveConnectionState - false`() {
        val state = mockk<NotificationStatePreferences>(relaxed = true)

        val service = NotificationListener(state)

        every { state.saveConnectionState(any()) } returns Unit

        service.onListenerDisconnected()

        verify { state.saveConnectionState(false) }
    }

    @Test
    fun `onNotificationPosted calls messageHandler-onMessageReceived`() {
        val expectedText = "123456"
        val expectedTitle = "title"
        val expectedKey = "key"
        val expectedSource = "source"

        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val statusBarNotification = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns expectedTitle
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns expectedText
        every { statusBarNotification.key } returns expectedKey
        every { statusBarNotification.packageName } returns expectedSource
        every { statusBarNotification.notification } returns notification
        notification.extras = bundle
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

        val service = NotificationListener(state, messageHandler)
        service.onNotificationPosted(statusBarNotification)

        verify {
            messageHandler.onMessageReceived(Message(
                key = expectedKey,
                title = expectedTitle,
                text = expectedText,
                source = expectedSource
            ))
        }
    }
}