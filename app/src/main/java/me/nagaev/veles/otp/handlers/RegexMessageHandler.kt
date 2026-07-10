package me.nagaev.veles.otp.handlers

import java.math.BigDecimal

class RegexMessageHandler(
    private val name: String,
    otpRegex: String,
    moneyRegex: String,
    merchantRegex: String,
    private val notifier: OtpMessageHandler,
) : MessageHandler {
    private val otpPattern = Regex(otpRegex)
    private val moneyPattern = Regex(moneyRegex)
    private val merchantPattern = Regex(merchantRegex)

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        val otp =
            otpPattern.find(message.text)?.let {
                Otp(value = it.groupValues[2], id = it.groupValues[1])
            }
        val money =
            moneyPattern.find(message.text)?.let {
                Money(amount = BigDecimal(it.groupValues[2]), currencyCode = it.groupValues[1])
            }
        val merchant =
            merchantPattern.find(message.text)?.let {
                it.groupValues[1]
            }

        return if (otp != null && money != null && merchant != null) {
            val otpMessage =
                OtpMessage(
                    otp = otp,
                    pay = money,
                    merchant = merchant,
                )
            notifier.onOtpMessageReceived(otpMessage)
            MessageHandlingResult(MessageHandlingResult.Status.ACCEPTED, name, otpMessage)
        } else {
            MessageHandlingResult.FILTERED
        }
    }
}
