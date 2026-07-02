package me.nagaev.veles.otp.handlers

interface MessageHandler {
    fun onMessageReceived(message: Message): MessageHandlingResult
}

data class MessageHandlingResult(
    val status: Status,
    val matchedTemplateName: String?,
) {
    enum class Status { ACCEPTED, FILTERED }

    companion object {
        val ACCEPTED = MessageHandlingResult(Status.ACCEPTED, null)
        val FILTERED = MessageHandlingResult(Status.FILTERED, null)
    }
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
