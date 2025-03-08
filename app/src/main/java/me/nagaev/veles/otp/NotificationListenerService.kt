package me.nagaev.veles.otp

import android.content.Intent
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import me.nagaev.veles.common.NotificationStatePreferences

class NotificationListener : NotificationListenerService() {

    private val state = NotificationStatePreferences(this)

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

    private val messageHandler: MessageHandler = run {
        val notifier = OtpNotifierImpl(this)
        val otpParser = MessageUobOtpParsingImpl(notifier)
        //val dedup = MessageDeduplicationImpl(otpParser)
        val filter = MessageSourceFiltrationImpl(otpParser)
        filter
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn?.let {
            val packageName = it.packageName ?: ""
            val extras = it.notification?.extras

            // Extract data from notification extras as needed
            val title = extras?.getCharSequence("android.title").toString()
            val text = extras?.getCharSequence("android.text").toString()

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
