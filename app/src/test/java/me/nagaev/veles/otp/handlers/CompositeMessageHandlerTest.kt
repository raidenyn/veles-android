package me.nagaev.veles.otp.handlers

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Test

class CompositeMessageHandlerTest {
    private val message = Message(key = "key", source = "source", title = "title", text = "text")

    @Test
    fun `first handler matches returns ACCEPTED and does not call second`() {
        val first = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.ACCEPTED
        }
        val second = mockk<MessageHandler>()

        val result = CompositeMessageHandler(listOf(first, second)).onMessageReceived(message)

        assert(result == MessageHandlingResult.ACCEPTED)
        verify(exactly = 0) { second.onMessageReceived(any()) }
    }

    @Test
    fun `first filtered second matches returns ACCEPTED`() {
        val first = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.FILTERED
        }
        val second = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.ACCEPTED
        }

        val result = CompositeMessageHandler(listOf(first, second)).onMessageReceived(message)

        assert(result == MessageHandlingResult.ACCEPTED)
    }

    @Test
    fun `all handlers filtered returns FILTERED`() {
        val first = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.FILTERED
        }
        val second = mockk<MessageHandler> {
            every { onMessageReceived(message) } returns MessageHandlingResult.FILTERED
        }

        val result = CompositeMessageHandler(listOf(first, second)).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
    }

    @Test
    fun `empty handler list returns FILTERED`() {
        val result = CompositeMessageHandler(emptyList()).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
    }
}
