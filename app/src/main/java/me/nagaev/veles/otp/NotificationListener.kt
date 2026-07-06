package me.nagaev.veles.otp

import android.app.Notification
import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.handlers.CompositeMessageHandler
import me.nagaev.veles.otp.handlers.HandlerChainReloader
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler
import me.nagaev.veles.testing.TestNotificationSender

class NotificationListener(
    state: NotificationStatePreferences? = null,
    messageHandler: MessageHandler? = null,
    private val ownPackageName: String? = null,
) : NotificationListenerService() {
    private val state = state ?: NotificationStatePreferences(this)
    private val injectedHandler: MessageHandler? = messageHandler
    private var serviceScope: CoroutineScope? = null
    private var reloader: HandlerChainReloader? = null

    override fun onCreate() {
        super.onCreate()
        Log.d("NotificationListener", "Created")
        if (injectedHandler == null) {
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
            serviceScope = scope
            val repository = BankHandlerRepository(this)
            val notifier = UserNotifierOtpMessageHandler(this)
            val r = HandlerChainReloader(repository.observeAll(), notifier)
            r.start(scope)
            reloader = r
        }
    }

    override fun onDestroy() {
        reloader?.stop()
        serviceScope?.cancel()
        serviceScope = null
        reloader = null
        super.onDestroy()
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

    private fun activeHandler(): MessageHandler = injectedHandler ?: reloader?.messageHandler ?: CompositeMessageHandler(emptyList())

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

            val handlingResult = activeHandler().onMessageReceived(message)

            val effectiveOwnPackage = ownPackageName ?: getPackageName()
            val channelId = it.notification?.channelId
            if (message.source == effectiveOwnPackage && channelId == TestNotificationSender.CHANNEL_ID) {
                TestResultFlow.current.value = TestResult(
                    handlingResult = handlingResult,
                    receivedText = message.text,
                    receivedTitle = message.title,
                    sourcePackage = message.source,
                    timestamp = System.currentTimeMillis(),
                )
            }

            if (handlingResult.status == MessageHandlingResult.Status.ACCEPTED) {
                cancelNotification(it.key)
            }
        }
    }
}
