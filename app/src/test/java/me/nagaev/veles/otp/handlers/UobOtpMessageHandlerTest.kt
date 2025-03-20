package me.nagaev.veles.otp.handlers

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.Test
import java.math.BigDecimal

class UobOtpMessageHandlerTest {
    private val defaultMessage = Message(
        key = "123456",
        source = "line",
        title = "OTP Code",
        text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time."
    )

    @Test
    fun `Valid OTP message processing`() {
        // Test with a valid OTP message. Verify that the OTP, money, and merchant information are extracted correctly.
        val message = defaultMessage.copy()

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is ACCEPTED and the OTP, money, and merchant information are correct.
        assert(result == MessageHandlingResult.ACCEPTED)

        verify {
            otpMessageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = message.key.hashCode(),
                    otp = Otp(
                        value = "079853",
                        id = "HStX"
                    ),
                    pay = Money(
                        amount = BigDecimal("319.93"),
                        currencyCode = "THB"
                    ),
                    merchant = "AMP*AIS SERVICES"
                )
            )
        }
    }

    @Test
    fun `Message with missing OTP`() {
        // Test with a message that does not contain OTP information. Verify that MessageHandlingResult.FILTERED is returned.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Message with missing money`() {
        // Test with a message that does not contain money information. Verify that MessageHandlingResult.FILTERED is returned.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Message with missing merchant`() {
        // Test with a message that does not contain merchant information. Verify that MessageHandlingResult.FILTERED is returned.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Message with empty text`() {
        // Test with a message that has empty text. Verify that MessageHandlingResult.FILTERED is returned.
        val message = defaultMessage.copy(text = "")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `OTP with incorrect format`() {
        // Test with OTP information that has an incorrect format (e.g., missing separator, incorrect length).
        // Verify that MessageHandlingResult.FILTERED is returned.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Money with incorrect format`() {
        // Test with money information that has an incorrect format (e.g., missing currency code, incorrect decimal places).
        // Verify that MessageHandlingResult.FILTERED is returned.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93. at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Merchant name exceeding max length`() {
        // Test with a merchant name that exceeds the maximum length of 64 characters.
        // Verify that MessageHandlingResult.FILTERED is returned.
        val longMerchantName = "A".repeat(65)
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at $longMerchantName expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Merchant name with special characters`() {
        // Test with a merchant name that contains special characters. Verify that MessageHandlingResult.FILTERED is returned.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB319.93 at AMP*AIS@SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        assert(result == MessageHandlingResult.ACCEPTED)

        verify(exactly = 1) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Money amount with no digits before decimal`() {
        // Test money regex with an amount that starts with a decimal point e.g. .1234. Verify that it correctly extracts this value.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB.1234 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is ACCEPTED and the OTP, money, and merchant information are correct.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Money with extreme amount values`() {
        // Test money regex with very large values to see if BigDecimal parse has any issue.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of THB999999999999999.9999 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is ACCEPTED and the OTP, money, and merchant information are correct.
        assert(result == MessageHandlingResult.ACCEPTED)

        verify {
            otpMessageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = message.key.hashCode(),
                    otp = Otp(
                        value = "079853",
                        id = "HStX"
                    ),
                    pay = Money(
                        amount = BigDecimal("999999999999999.9999"),
                        currencyCode = "THB"
                    ),
                    merchant = "AMP*AIS SERVICES"
                )
            )
        }
    }

    @Test
    fun `OTP id with edge length`() {
        // Test OTP id section with length less than 4 or greater than 4, to ensure that length of id is always 4.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HSt-079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `OTP value with edge length`() {
        // Test OTP value section with length less than 6 or greater than 6, to ensure that length of OTP is always 6.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-07985 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `Money with edge currency length`() {
        // Test money currency code section with length less than 3 or greater than 3, to ensure that length of currency code is always 3.
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX-079853 for your purchase of TH319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is ACCEPTED and the OTP, money, and merchant information are correct.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

    @Test
    fun `OTP format variations`() {
        // Test with OTP messages containing different separator characters to verify that only (-) is valid.
        // Such as (ABCD:123456) (ABCD 123456)
        val message = defaultMessage.copy(text = "Never share OTP with anyone. Use SMS-OTP HStX:079853 for your purchase of THB319.93 at AMP*AIS SERVICES expiring at 02-Mar-2025 9:23PM BKK time.")

        val otpMessageHandler = mockk<OtpMessageHandler>()

        every {
            otpMessageHandler.onOtpMessageReceived(any())
        } returns Unit

        val result = UobOtpMessageHandler(otpMessageHandler).onMessageReceived(message)

        // Verify that the result is FILTERED and no OTP message was sent.
        assert(result == MessageHandlingResult.FILTERED)

        verify(exactly = 0) {
            otpMessageHandler.onOtpMessageReceived(any())
        }
    }

}