package me.nagaev.veles.otp.handlers

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Test
import java.math.BigDecimal

@Suppress("MaxLineLength")
class RegexMessageHandlerTest {
    private companion object {
        const val HANDLER_NAME = "UOB Thailand"
        const val OTP_REGEX = """ (\w{4})-(\d{6}) """
        const val MONEY_REGEX = """of ([A-Z]{3})(\d{1,15}\.\d{1,4}) at"""
        const val MERCHANT_REGEX = """at (.{1,64}) expiring"""
    }

    private val defaultMessage =
        Message(
            key = "123456",
            source = "line",
            title = "OTP Code",
            text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.",
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
                    otp = Otp(value = "079853", id = "HStX"),
                    pay = Money(amount = BigDecimal("319.93"), currencyCode = "THB"),
                    merchant = "AMP*AIS SERVICES",
                ),
            )
        }
    }

    @Test
    fun `Message with missing OTP`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with missing money`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with missing merchant`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Message with empty text`() {
        val message = defaultMessage.copy(text = "")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `OTP with incorrect format`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money with incorrect format`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93. at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Merchant name exceeding max length`() {
        val longMerchantName = "A".repeat(65)
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at $longMerchantName expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Merchant name with special characters`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at AMP*AIS@SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result.status == MessageHandlingResult.Status.ACCEPTED)
        verify(exactly = 1) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money amount with no digits before decimal`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB.1234 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money with extreme amount values`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB999999999999999.9999 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result.status == MessageHandlingResult.Status.ACCEPTED)
        verify {
            otpMessageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = defaultMessage.key.hashCode(),
                    otp = Otp(value = "079853", id = "HStX"),
                    pay = Money(amount = BigDecimal("999999999999999.9999"), currencyCode = "THB"),
                    merchant = "AMP*AIS SERVICES",
                ),
            )
        }
    }

    @Test
    fun `OTP id with edge length`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HSt-079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `OTP value with edge length`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-07985 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `Money with edge currency length`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of TH319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
        val otpMessageHandler = mockk<OtpMessageHandler>()
        every { otpMessageHandler.onOtpMessageReceived(any()) } returns Unit

        val result = handler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.FILTERED)
        verify(exactly = 0) { otpMessageHandler.onOtpMessageReceived(any()) }
    }

    @Test
    fun `OTP format variations`() {
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX:079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")
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
