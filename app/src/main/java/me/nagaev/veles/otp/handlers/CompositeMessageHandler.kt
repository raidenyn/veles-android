package me.nagaev.veles.otp.handlers

class CompositeMessageHandler(private val handlers: List<MessageHandler>) : MessageHandler {
    override fun onMessageReceived(message: Message): MessageHandlingResult {
        for (handler in handlers) {
            if (handler.onMessageReceived(message) == MessageHandlingResult.ACCEPTED) {
                return MessageHandlingResult.ACCEPTED
            }
        }
        return MessageHandlingResult.FILTERED
    }
}
