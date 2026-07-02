package me.nagaev.veles.otp.handlers

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Test
import java.math.BigDecimal

@Suppress("MaxLineLength")
class UobThaiRegexMessageHandlerTest {
    private companion object {
        const val HANDLER_NAME = "UOB Thai"
        const val OTP_REGEX = """\((OTP=)(\d{6})\)"""
        const val MONEY_REGEX = """purchase ([A-Z]{3})(\d{1,15}\.\d{1,4})"""
        const val MERCHANT_REGEX = """ at (.+?):"""
    }

    private val defaultMessage =
        Message(
            key = "123456",
            source = "com.uob.th",
            title = "UOB",
            text = "For purchase THB600.00 (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone. If you didn't make it, call 02-285-1573.",
        )

    private fun handler(notifier: OtpMessageHandler) = RegexMessageHandler(HANDLER_NAME, OTP_REGEX, MONEY_REGEX, MERCHANT_REGEX, notifier)

    @Test
    fun `Valid OTP message processing`() {
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(defaultMessage)

        assert(result.status == MessageHandlingResult.Status.ACCEPTED)
        verify {
            otpMessageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = defaultMessage.key.hashCode(),
                    otp = Otp(value = "511066", id = "OTP="),
                    pay = Money(amount = BigDecimal("600.00"), currencyCode = "THB"),
                    merchant = "WWWSFCINEMACITYCOMCORP",
                ),
            )
        }
    }

    @Test
    fun `Message with missing OTP`() {
        val message = defaultMessage.copy(text = "For purchase THB600.00 at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with missing money`() {
        val message = defaultMessage.copy(text = "For purchase (OTP=511066) at WWWSFCINEMACITYCOMCORP: Ref-VjKp. Never share OTP with anyone.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with missing merchant`() {
        val message = defaultMessage.copy(text = "For purchase THB600.00 (OTP=511066). Never share OTP with anyone.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `matched result carries the handler name`() {
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(defaultMessage)

        assert(result.status == MessageHandlingResult.Status.ACCEPTED)
        assert(result.matchedTemplateName == HANDLER_NAME)
    }
}
