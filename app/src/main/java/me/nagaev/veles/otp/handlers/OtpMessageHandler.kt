package me.nagaev.veles.otp.handlers

import java.math.BigDecimal

interface OtpMessageHandler {
    fun onOtpMessageReceived(message: OtpMessage)
}

data class OtpMessage(
    val id: Int,
    val otp: Otp,
    val pay: Money,
    val merchant: String,
)

data class Otp(
    val value: String,
    val id: String,
)

data class Money(
    val amount: BigDecimal,
    val currencyCode: String,
)
