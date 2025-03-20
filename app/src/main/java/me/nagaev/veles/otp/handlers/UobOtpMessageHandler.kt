package me.nagaev.veles.otp.handlers

import java.math.BigDecimal

class UobOtpMessageHandler(
    private val messageHandler: OtpMessageHandler
): MessageHandler {
    private val otpRegex = Regex(""" (\w{4})-(\d{6}) """)
    private val moneyRegex = Regex("""of ([A-Z]{3})(\d{1,15}\.\d{1,4}) at""")
    private val merchantRegex = Regex("""at (.{1,64}) expiring""")

    override fun onMessageReceived(message: Message): MessageHandlingResult {
        val otp = otpRegex.find(message.text)?.let {
            Otp(
                value = it.groupValues[2],
                id = it.groupValues[1]
            )
        }
        val money = moneyRegex.find(message.text)?.let {
            Money(
                amount = BigDecimal(it.groupValues[2]),
                currencyCode = it.groupValues[1]
            )
        }
        val merchant = merchantRegex.find(message.text)?.let {
            it.groupValues[1]
        }

        return if (otp != null && money != null && merchant != null) {
            messageHandler.onOtpMessageReceived(
                OtpMessage(
                    id = message.key.hashCode(),
                    otp = otp,
                    pay = money,
                    merchant = merchant
                )
            )
            MessageHandlingResult.ACCEPTED
        } else {
            MessageHandlingResult.FILTERED
        }
    }
}
