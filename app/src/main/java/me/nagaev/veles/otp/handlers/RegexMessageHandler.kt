package me.nagaev.veles.otp.handlers

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
            otpPattern.find(message.text)?.let { match ->
                val id = match.groupValues.getOrNull(1)?.takeIf { it.isNotEmpty() }
                val value = match.groupValues.getOrNull(2)?.takeIf { it.isNotEmpty() }
                if (id != null && value != null) Otp(value = value, id = id) else null
            }
        val money =
            moneyPattern.find(message.text)?.let { match ->
                val currencyCode = match.groupValues.getOrNull(1)?.takeIf { it.isNotEmpty() }
                val amount =
                    match.groupValues.getOrNull(2)?.takeIf { it.isNotEmpty() }?.replace(",", "")?.toBigDecimalOrNull()
                if (currencyCode != null && amount != null) Money(amount, currencyCode) else null
            }
        val merchant =
            merchantPattern.find(message.text)?.groupValues?.getOrNull(1)?.takeIf { it.isNotEmpty() }

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
