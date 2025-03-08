package me.nagaev.veles.otp

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import me.nagaev.veles.R
import java.math.BigDecimal


interface MessageHandler {
    fun onMessageReceived(message: Message): MessageHandlingResult
}

interface OtpMessageHandler {
    fun onOtpMessageReceived(message: OtpMessage)
}

enum class MessageHandlingResult {
    ACCEPTED,
    DUPLICATED,
    FILTERED
}

data class Message(
    /*
     * The unique identifier of the message.
     */
    val key: String,
    /*
     * Source of the message
     */
    val source: String,
    val title: String,
    val text: String
)

class MessageDeduplicationImpl(
    private val messageHandler: MessageHandler
): MessageHandler {
    // TODO: clean old messages to release memory
    private val existingMessages = mutableSetOf<String>()

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        return if (existingMessages.contains(message.key)) {
            MessageHandlingResult.DUPLICATED
        } else {
            existingMessages.add(message.key)
            messageHandler.onMessageReceived(message)
        }
    }
}

class MessageSourceFiltrationImpl(
    private val messageHandler: MessageHandler
): MessageHandler {
    private val allowedSources = setOf(
        "com.google.android.apps.messaging"
    )

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        // TODO("Add real filtration")
        return messageHandler.onMessageReceived(message)
    }
}

data class OtpMessage(
    val id: Int,
    val otp: Otp,
    val pay: Money,
    val merchant: String,
)

data class Otp(
    val value: String,
    val id: String
)

data class Money(
    val amount: BigDecimal,
    val currencyCode: String
)

class MessageUobOtpParsingImpl(
    private val messageHandler: OtpMessageHandler
): MessageHandler {
    private val otpRegex = Regex("""(\w{4})-(\d{6})""")
    private val moneyRegex = Regex("""of (\w{3})(\d{1,15}\.\d{1,4}) at""")
    private val merchantRegex = Regex("""at (.{1,64}) expiring""")

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        val otp = otpRegex.find(message.text)?.let {
            Otp(
                value = it.groupValues[2],
                id = it.groupValues[1]
            )
        }
        val money = moneyRegex.find(message.text)?.let {
            Money(
                amount = BigDecimal(it.groupValues[2]),
                currencyCode = it.groupValues[1]
            )
        }
        val merchant = merchantRegex.find(message.text)?.let {
            it.groupValues[1]
        }

        if (otp != null && money != null && merchant != null) {
            messageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = message.key.hashCode(),
                    otp = otp,
                    pay = money,
                    merchant = merchant
                )
            )
            return MessageHandlingResult.ACCEPTED
        }
        return MessageHandlingResult.FILTERED
    }
}

class OtpNotifierImpl(
    private val context: Context
): OtpMessageHandler {
    companion object {
        const val CHANNEL_ID = "HandyOTPMessageChannel"
    }

    override fun onOtpMessageReceived(message: OtpMessage) {
        val text = "OTP: ${message.otp.value}, Pay: ${message.pay.amount} ${message.pay.currencyCode}"
        val title = message.merchant

        val copyIntent = Intent(context, CopyDataReceiver::class.java).apply {
            action = "Copy"
            putExtra(CopyDataReceiver.EXTRA_COPY_TEXT, message.otp.value)
        }
        val copyPendingIntent: PendingIntent =
            PendingIntent.getBroadcast(context, 0, copyIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_otp_message)
            .setContentTitle(title)
            .setContentText(text)
            .addAction(
                R.drawable.ic_otp_message, "Copy ${message.otp.value}",
                copyPendingIntent)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        with(NotificationManagerCompat.from(context)) {
            if (areNotificationsEnabled()) {
                tryCreateNotificationChannel()
                // notificationId is a unique int for each notification that you must define.
                notify(message.hashCode(), builder.build())
            }
        }
    }

    private fun tryCreateNotificationChannel() {
        // Register the channel with the system.
        val notificationManager: NotificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        if (notificationManager.getNotificationChannel(CHANNEL_ID) != null) {
            return
        }

        val importance = NotificationManager.IMPORTANCE_HIGH
        val channel = NotificationChannel(CHANNEL_ID, "Handy OTP", importance).apply {
            description = "Show handy OTP passwords from banks"
        }

        notificationManager.createNotificationChannel(channel)
    }
}

class CopyDataReceiver: BroadcastReceiver() {
    companion object {
        const val EXTRA_COPY_TEXT = "CopyText"
    }

    override fun onReceive(context: Context?, intent: Intent?) {
        Log.d("CopyDataReceiver", "Context $context")
        context?.apply {
            intent?.getStringExtra(EXTRA_COPY_TEXT)?.let {
                val clipboard: ClipboardManager =
                    getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                val clip = ClipData.newPlainText("OTP", it)
                clipboard.setPrimaryClip(clip)
                Log.d("CopyDataReceiver", "Copied $it")
            }
        }
    }
}