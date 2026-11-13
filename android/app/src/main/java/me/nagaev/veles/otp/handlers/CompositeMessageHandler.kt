package me.nagaev.veles.otp.handlers

class CompositeMessageHandler(
    private val handlers: List<MessageHandler>,
) : MessageHandler {
    override fun onMessageReceived(message: Message): MessageHandlingResult {
        for (handler in handlers) {
            val result = handler.onMessageReceived(message)
            if (result.status == MessageHandlingResult.Status.ACCEPTED) {
                return result
            }
        }
        return MessageHandlingResult.FILTERED
    }
}
