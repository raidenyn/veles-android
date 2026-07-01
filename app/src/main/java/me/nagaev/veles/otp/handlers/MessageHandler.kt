package me.nagaev.veles.otp.handlers

interface MessageHandler {
    fun onMessageReceived(message: Message): MessageHandlingResult
}

enum class MessageHandlingResult {
    ACCEPTED,
    FILTERED,
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
    val text: String,
)
