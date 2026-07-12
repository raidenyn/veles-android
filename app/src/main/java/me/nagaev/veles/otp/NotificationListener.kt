package me.nagaev.veles.otp

import android.app.Notification
import android.content.ComponentName
import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationCompat
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.otp.handlers.CompositeMessageHandler
import me.nagaev.veles.otp.handlers.HandlerChainReloader
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.testing.TestNotificationSender

class NotificationListener(
    state: NotificationStatePreferences? = null,
    messageHandler: MessageHandler? = null,
    private val ownPackageName: String? = null,
    velesLog: VelesLog? = null,
    testResultFlow: TestResultFlow? = null,
    redactionStateFlow: RedactionStateFlow? = null,
) : NotificationListenerService() {
    companion object {
        const val ACTION_REBIND = "me.nagaev.veles.action.REBIND"
    }

    private val entryPoint: NotificationListenerEntryPoint by lazy {
        EntryPointAccessors.fromApplication(
            applicationContext,
            NotificationListenerEntryPoint::class.java,
        )
    }
    private val stateOverride: NotificationStatePreferences? = state
    private val state: NotificationStatePreferences by lazy {
        stateOverride ?: entryPoint.notificationStatePreferences()
    }
    private val injectedHandler: MessageHandler? = messageHandler
    private var serviceScope: CoroutineScope? = null
    private var reloader: HandlerChainReloader? = null
    private var pendingRebind = false
    private val logOverride: VelesLog? = velesLog
    private val logger: VelesLog by lazy {
        logOverride ?: entryPoint.velesLog()
    }
    private val testResultFlowOverride: TestResultFlow? = testResultFlow
    private val testResultFlow: TestResultFlow by lazy {
        testResultFlowOverride ?: entryPoint.testResultFlow()
    }
    private val redactionStateFlowOverride: RedactionStateFlow? = redactionStateFlow
    private val redactionStateFlow: RedactionStateFlow by lazy {
        redactionStateFlowOverride ?: entryPoint.redactionStateFlow()
    }

    override fun onCreate() {
        super.onCreate()
        logger.d("NotificationListener", "Created")
        if (injectedHandler == null) {
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
            serviceScope = scope
            val r = HandlerChainReloader(
                entryPoint.bankHandlerRepository().observeAll(),
                entryPoint.userNotifierOtpMessageHandler(),
            )
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
        if (intent?.action == ACTION_REBIND) {
            pendingRebind = true
            requestUnbind()
        }
        logger.d("NotificationListener", "Started: $startId")
        return START_REDELIVER_INTENT
    }

    override fun onListenerConnected() {
        logger.d("NotificationListener", "ListenerConnected")
        state.saveConnectionState(true)
        super.onListenerConnected()
    }

    override fun onListenerDisconnected() {
        logger.d("NotificationListener", "ListenerDisconnected")
        state.saveConnectionState(false)
        if (pendingRebind) {
            pendingRebind = false
            requestRebind(ComponentName(packageName, javaClass.name))
        }
        super.onListenerDisconnected()
    }

    private fun activeHandler(): MessageHandler = injectedHandler ?: reloader?.messageHandler ?: CompositeMessageHandler(emptyList())

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn?.let {
            val packageName = it.packageName ?: ""
            val extras = it.notification?.extras
            val title = extras?.getCharSequence(NotificationCompat.EXTRA_TITLE).toString()
            val text = extras?.getCharSequence(NotificationCompat.EXTRA_TEXT).toString()

            logger.dNotificationLogged(pkg = packageName, title = title, text = text, key = it.key, postTime = it.postTime)

            val notification = it.notification
            if (RedactionDetector.isRedacted(it)) {
                redactionStateFlow.current.value = RedactionState.Hidden
            } else if (notification?.visibility == Notification.VISIBILITY_SECRET &&
                redactionStateFlow.current.value == RedactionState.Hidden
            ) {
                redactionStateFlow.current.value = RedactionState.Readable
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
                testResultFlow.current.value = TestResult(
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
