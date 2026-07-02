package me.nagaev.veles.otp

import android.app.Notification
import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.handlers.CompositeMessageHandler
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.RegexMessageHandler
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler
import me.nagaev.veles.testing.TestNotificationSender

class NotificationListener(
    state: NotificationStatePreferences? = null,
    messageHandler: MessageHandler? = null,
    private val ownPackageName: String? = null,
) : NotificationListenerService() {
    private val state = state ?: NotificationStatePreferences(this)
    private val injectedHandler: MessageHandler? = messageHandler
    private lateinit var messageHandler: MessageHandler

    override fun onCreate() {
        super.onCreate()
        Log.d("NotificationListener", "Created")
        messageHandler = injectedHandler ?: run {
            val notifier = UserNotifierOtpMessageHandler(this)
            val repository = BankHandlerRepository(this)
            val handlers =
                repository.getAll().map { config ->
                    RegexMessageHandler(
                        name = config.name,
                        otpRegex = config.otpRegex,
                        moneyRegex = config.moneyRegex,
                        merchantRegex = config.merchantRegex,
                        notifier = notifier,
                    )
                }
            CompositeMessageHandler(handlers)
        }
    }

    override fun onStartCommand(
        intent: Intent?,
        flags: Int,
        startId: Int,
    ): Int {
        super.onStartCommand(intent, flags, startId)
        Log.d("NotificationListener", "Started: $startId")
        return START_REDELIVER_INTENT
    }

    override fun onListenerConnected() {
        Log.d("NotificationListener", "ListenerConnected")
        state.saveConnectionState(true)
        super.onListenerConnected()
    }

    override fun onListenerDisconnected() {
        Log.d("NotificationListener", "ListenerDisconnected")
        state.saveConnectionState(false)
        super.onListenerDisconnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn?.let {
            val packageName = it.packageName ?: ""
            val extras = it.notification?.extras
            val title = extras?.getCharSequence(NotificationCompat.EXTRA_TITLE).toString()
            val text = extras?.getCharSequence(NotificationCompat.EXTRA_TEXT).toString()

            Log.d("NotificationListener", "Title: $title, Text: $text, Package: $packageName, Timestamp: ${it.postTime}, Key: ${it.key}")

            val notification = it.notification
            if (RedactionDetector.isRedacted(it)) {
                RedactionStateFlow.current.value = RedactionState.Hidden
            } else if (notification?.visibility == Notification.VISIBILITY_SECRET &&
                RedactionStateFlow.current.value == RedactionState.Hidden
            ) {
                RedactionStateFlow.current.value = RedactionState.Readable
            }

            val message =
                Message(
                    key = it.key,
                    source = packageName,
                    title = title,
                    text = text,
                )

            val handlingResult = messageHandler.onMessageReceived(message)

            val effectiveOwnPackage = ownPackageName ?: getPackageName()
            val channelId = it.notification?.channelId
            if (message.source == effectiveOwnPackage && channelId == TestNotificationSender.CHANNEL_ID) {
                TestResultFlow.current.value = TestResult(
                    handlingResult = handlingResult,
                    receivedText = "",
                    receivedTitle = "",
                    sourcePackage = "",
                    timestamp = System.currentTimeMillis(),
                )
            }

            if (handlingResult == MessageHandlingResult.ACCEPTED) {
                cancelNotification(it.key)
            }
        }
    }
}
