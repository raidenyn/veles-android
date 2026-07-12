package me.nagaev.veles.otp

import android.app.Notification
import android.content.Intent
import android.os.Bundle
import android.service.notification.NotificationListenerService.START_REDELIVER_INTENT
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkObject
import io.mockk.spyk
import io.mockk.unmockkAll
import io.mockk.verify
import me.nagaev.veles.common.LogConfig
import me.nagaev.veles.common.LogSink
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.OtpNotificationBuilder
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class NotificationListenerTest {
    private class RecordingLogSink : LogSink {
        val calls = mutableListOf<Pair<String, String>>()
        override fun d(tag: String, msg: String) {
            calls.add(tag to msg)
        }
    }

    private val testLog = VelesLog(
        RecordingLogSink(),
        object : LogConfig {
            override val rawContentEnabled get() = false
        },
        debugEnabled = true,
    )

    @Before
    fun beforeTest() {
        mockkObject(RedactionDetector)
    }

    @After
    fun afterTest() {
        unmockkAll()
    }

    @Test
    fun `onCreate service initialization`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = "me.nagaev.veles",
            velesLog = testLog,
            testResultFlow = TestResultFlow(),
            redactionStateFlow = RedactionStateFlow(),
        )
        service.onCreate()
    }

    @Test
    fun `onStartCommand returns `() {
        val service = NotificationListener(
            velesLog = testLog,
            testResultFlow = TestResultFlow(),
            redactionStateFlow = RedactionStateFlow(),
        )

        val intent = mockk<Intent>(relaxed = true)
        val result = service.onStartCommand(intent, 0, 0)

        assertEquals(START_REDELIVER_INTENT, result)
    }

    @Test
    fun `onListenerConnected calls saveConnectionState - true`() {
        val state = mockk<NotificationStatePreferences>(relaxed = true)

        val service = NotificationListener(
            state,
            velesLog = testLog,
            testResultFlow = TestResultFlow(),
            redactionStateFlow = RedactionStateFlow(),
        )

        every { state.saveConnectionState(any()) } returns Unit

        service.onListenerConnected()

        verify { state.saveConnectionState(true) }
    }

    @Test
    fun `onListenerDisconnected calls saveConnectionState - false`() {
        val state = mockk<NotificationStatePreferences>(relaxed = true)

        val service = NotificationListener(
            state,
            velesLog = testLog,
            testResultFlow = TestResultFlow(),
            redactionStateFlow = RedactionStateFlow(),
        )

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

        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = "com.external.bank",
            velesLog = testLog,
            testResultFlow = TestResultFlow(),
            redactionStateFlow = RedactionStateFlow(),
        )
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

        val testResultFlow = TestResultFlow()
        val redactionStateFlow = RedactionStateFlow()
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = ownPkg,
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = redactionStateFlow,
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        val testResult = testResultFlow.current.value
        assertEquals(MessageHandlingResult.ACCEPTED, testResult?.handlingResult)
        assertEquals("text", testResult?.receivedText)
        assertEquals("title", testResult?.receivedTitle)
        assertEquals(ownPkg, testResult?.sourcePackage)
    }

    @Test
    fun `secret probe notification round-trips its text through TestResultFlow`() {
        val ownPkg = "me.nagaev.veles"
        val probeText = "Veles check: code 835201"
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "Veles Test"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns probeText
        every { sbn.key } returns "key"
        every { sbn.packageName } returns ownPkg
        every { sbn.notification } returns notification
        notification.visibility = Notification.VISIBILITY_SECRET
        notification.extras = bundle
        every { notification.channelId } returns TestNotificationSender.CHANNEL_ID
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

        val testResultFlow = TestResultFlow()
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = ownPkg,
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = RedactionStateFlow(),
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(probeText, testResultFlow.current.value?.receivedText)
    }

    @Test
    fun `onNotificationPosted writes matched template name and received text to TestResultFlow for self-notifications`() {
        val ownPkg = "me.nagaev.veles"
        val expectedText = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp."
        val matchedResult = MessageHandlingResult(
            MessageHandlingResult.Status.ACCEPTED,
            "UOB Thai",
        )
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "UOB"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns expectedText
        every { sbn.key } returns "key"
        every { sbn.packageName } returns ownPkg
        every { sbn.notification } returns notification
        notification.extras = bundle
        every { notification.channelId } returns TestNotificationSender.CHANNEL_ID
        every { messageHandler.onMessageReceived(any()) } returns matchedResult

        val testResultFlow = TestResultFlow()
        val redactionStateFlow = RedactionStateFlow()
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = ownPkg,
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = redactionStateFlow,
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        val testResult = testResultFlow.current.value
        assertEquals(matchedResult, testResult?.handlingResult)
        assertEquals("UOB Thai", testResult?.handlingResult?.matchedTemplateName)
        assertEquals(expectedText, testResult?.receivedText)
        assertEquals("UOB", testResult?.receivedTitle)
        assertEquals(ownPkg, testResult?.sourcePackage)
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
        every { notification.channelId } returns OtpNotificationBuilder.CHANNEL_ID
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

        val testResultFlow = TestResultFlow()
        val redactionStateFlow = RedactionStateFlow()
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = ownPkg,
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = redactionStateFlow,
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertNull(testResultFlow.current.value)
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

        val testResultFlow = TestResultFlow()
        val redactionStateFlow = RedactionStateFlow()
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = "me.nagaev.veles",
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = redactionStateFlow,
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertNull(testResultFlow.current.value)
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

        val testResultFlow = TestResultFlow()
        val redactionStateFlow = RedactionStateFlow()
        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = "me.nagaev.veles",
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = redactionStateFlow,
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(RedactionState.Hidden, redactionStateFlow.current.value)
    }

    @Test
    fun `onNotificationPosted sets RedactionStateFlow to Readable when previously Hidden and secret notification has digits`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        val testResultFlow = TestResultFlow()
        val redactionStateFlow = RedactionStateFlow()
        redactionStateFlow.current.value = RedactionState.Hidden

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "Your OTP is 123456"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns "com.some.bank"
        every { sbn.notification } returns notification
        notification.visibility = Notification.VISIBILITY_SECRET
        notification.extras = bundle
        every { RedactionDetector.isRedacted(any()) } returns false
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.ACCEPTED

        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = "me.nagaev.veles",
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = redactionStateFlow,
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(RedactionState.Readable, redactionStateFlow.current.value)
    }

    @Test
    fun `onNotificationPosted does not set Readable from a public notification when Hidden`() {
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        val testResultFlow = TestResultFlow()
        val redactionStateFlow = RedactionStateFlow()
        redactionStateFlow.current.value = RedactionState.Hidden

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "title"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "some non-secret text"
        every { sbn.key } returns "key"
        every { sbn.packageName } returns "com.some.bank"
        every { sbn.notification } returns notification
        notification.visibility = Notification.VISIBILITY_PUBLIC
        notification.extras = bundle
        every { RedactionDetector.isRedacted(any()) } returns false
        every { messageHandler.onMessageReceived(any()) } returns MessageHandlingResult.FILTERED

        val service = NotificationListener(
            state,
            messageHandler,
            ownPackageName = "me.nagaev.veles",
            velesLog = testLog,
            testResultFlow = testResultFlow,
            redactionStateFlow = redactionStateFlow,
        )
        service.onCreate()
        service.onNotificationPosted(sbn)

        assertEquals(RedactionState.Hidden, redactionStateFlow.current.value)
    }

    @Test
    fun `onNotificationPosted cancels original notification when handler accepts with non-null template name`() {
        val expectedKey = "key"
        val matchedResult = MessageHandlingResult(
            MessageHandlingResult.Status.ACCEPTED,
            "UOB Thai",
        )
        val messageHandler = mockk<MessageHandler>(relaxed = true)
        val state = mockk<NotificationStatePreferences>(relaxed = true)
        val notification = mockk<Notification>(relaxed = true)
        val sbn = mockk<StatusBarNotification>(relaxed = true)
        val bundle = mockk<Bundle>(relaxed = true)

        every { bundle.getCharSequence(NotificationCompat.EXTRA_TITLE) } returns "UOB"
        every { bundle.getCharSequence(NotificationCompat.EXTRA_TEXT) } returns "text"
        every { sbn.key } returns expectedKey
        every { sbn.packageName } returns "com.external.bank"
        every { sbn.notification } returns notification
        notification.extras = bundle
        every { messageHandler.onMessageReceived(any()) } returns matchedResult

        val service = spyk(
            NotificationListener(
                state,
                messageHandler,
                ownPackageName = "me.nagaev.veles",
                velesLog = testLog,
                testResultFlow = TestResultFlow(),
                redactionStateFlow = RedactionStateFlow(),
            ),
        )
        service.onCreate()
        every { service.cancelNotification(expectedKey) } returns Unit

        service.onNotificationPosted(sbn)

        verify { service.cancelNotification(expectedKey) }
    }
}
