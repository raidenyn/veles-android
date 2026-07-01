package me.nagaev.veles.otp

import android.app.Notification
import android.content.Intent
import android.os.Bundle
import android.service.notification.NotificationListenerService.START_REDELIVER_INTENT
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.mockkStatic
import io.mockk.unmockkAll
import io.mockk.verify
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class NotificationListenerTest {
    @Before
    fun beforeTest() {
        mockkStatic(Log::class)
        every { Log.d(any(), any()) } returns 0
        mockkObject(RedactionDetector)
    }

    @After
    fun afterTest() {
        unmockkAll()
    }

    @Before
    fun resetTestResultFlow() {
        TestResultFlow.current.value = null
    }

    @Before
    fun resetRedactionStateFlow() {
        RedactionStateFlow.current.value = RedactionState.Unknown
    }

    @Test
    fun `onCreate service initialization`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
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

        val service = NotificationListener(state, messageHandler, ownPackageName = "com.external.bank")
        service.onCreate()
        service.onNotificationPosted(statusBarNotification)

        verify {
            messageHandler.onMessageReceived(
                Message(
                    key = expectedKey,
                    title = expectedTitle,
                    text = expectedText,
                    source = expectedSource,
                ),
            )
        }
    }

    @Test
    fun `onNotificationPosted writes ACCEPTED to TestResultFlow for self-notifications`() {
        val ownPkg = "me.nagaev.veles"
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "text"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns ownPkg
        every { sbn.notification } returns notification
        notification.extras = bundle
        every { notification.channelId } returns TestNotificationSender.CHANNEL_ID
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

        val service = NotificationListener(state, messageHandler, ownPackageName = ownPkg)
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(MessageHandlingResult.ACCEPTED, TestResultFlow.current.value?.result)
    }

    @Test
    fun `onNotificationPosted does not write to TestResultFlow for OTP output notifications`() {
        val ownPkg = "me.nagaev.veles"
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "text"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns ownPkg
        every { sbn.notification } returns notification
        notification.extras = bundle
        every { notification.channelId } returns UserNotifierOtpMessageHandler.CHANNEL_ID
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

        val service = NotificationListener(state, messageHandler, ownPackageName = ownPkg)
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertNull(TestResultFlow.current.value)
    }

    @Test
    fun `onNotificationPosted does not write to TestResultFlow for external notifications`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "text"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns "com.some.bank"
        every { sbn.notification } returns notification
        notification.extras = bundle
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

        val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertNull(TestResultFlow.current.value)
    }

    @Test
    fun `onNotificationPosted sets RedactionStateFlow to Hidden when secret notification is redacted`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "Sensitive notification content hidden"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns "com.some.bank"
        every { sbn.notification } returns notification
        notification.extras = bundle
        every { RedactionDetector.isRedacted(any()) } returns true
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

        val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(RedactionState.Hidden, RedactionStateFlow.current.value)
    }

    @Test
    fun `onNotificationPosted sets RedactionStateFlow to Readable when previously Hidden and secret notification has digits`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        RedactionStateFlow.current.value = RedactionState.Hidden

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "Your OTP is 123456"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns "com.some.bank"
        every { sbn.notification } returns notification
        notification.visibility = Notification.VISIBILITY_SECRET
        notification.extras = bundle
        every { RedactionDetector.isRedacted(any()) } returns false
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

        val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(RedactionState.Readable, RedactionStateFlow.current.value)
    }

    @Test
    fun `onNotificationPosted does not set Readable from a public notification when Hidden`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        RedactionStateFlow.current.value = RedactionState.Hidden

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "some non-secret text"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns "com.some.bank"
        every { sbn.notification } returns notification
        notification.visibility = Notification.VISIBILITY_PUBLIC
        notification.extras = bundle
        every { RedactionDetector.isRedacted(any()) } returns false
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

        val service = NotificationListener(state, messageHandler, ownPackageName = "me.nagaev.veles")
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(RedactionState.Hidden, RedactionStateFlow.current.value)
    }
}
