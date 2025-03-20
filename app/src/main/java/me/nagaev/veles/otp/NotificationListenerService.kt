package me.nagaev.veles.otp

import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.core.app.NotificationCompat
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.otp.handlers.Message
import me.nagaev.veles.otp.handlers.MessageHandler
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.otp.handlers.UobOtpMessageHandler
import me.nagaev.veles.otp.handlers.UserNotifierOtpMessageHandler

class NotificationListener(
    state: NotificationStatePreferences? = null,
    messageHandler: MessageHandler? = null
): NotificationListenerService() {

    private val state = state ?: NotificationStatePreferences(this)
    private val messageHandler: MessageHandler = messageHandler ?: run {
        val notifier = UserNotifierOtpMessageHandler(this)
        val otpParser = UobOtpMessageHandler(notifier)
        otpParser
    }


    override fun onCreate() {
        Log.d("NotificationListener", "Created")
        super.onCreate()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
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

        super.onListenerConnected()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn?.let {
            val packageName = it.packageName ?: ""
            val extras = it.notification?.extras

            // Extract data from notification extras as needed
            val title = extras?.getCharSequence(NotificationCompat.EXTRA_TITLE).toString()
            val text = extras?.getCharSequence(NotificationCompat.EXTRA_TEXT).toString()

            Log.d("NotificationListener", "Title: $title, Text: $text, Package: $packageName, Timestamp: ${it.postTime}, Key: ${it.key}")

            val mockedMessage = Message(
                key = it.key,
                packageName,
                title = title,
                text = text
            )

            val handlingResult = messageHandler.onMessageReceived(mockedMessage)

            if (handlingResult == MessageHandlingResult.ACCEPTED) {
                cancelNotification(it.key)
            }
        }
    }
}
