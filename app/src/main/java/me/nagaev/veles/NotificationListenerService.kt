package me.nagaev.veles

import android.app.Activity
import android.app.PendingIntent
import android.content.Intent
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import androidx.appcompat.app.AppCompatActivity

class NotificationListener : NotificationListenerService() {

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

        super.onListenerConnected()
    }

    override fun onListenerDisconnected() {
        Log.d("NotificationListener", "ListenerDisconnected")

        super.onListenerConnected()
    }

    val messageHandler: MessageHandler = run {
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
