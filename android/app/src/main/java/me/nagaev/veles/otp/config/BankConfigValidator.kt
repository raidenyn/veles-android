package me.nagaev.veles.otp.config

import java.util.regex.PatternSyntaxException

enum class BankConfigField {
    NAME,
    OTP_REGEX,
    MONEY_REGEX,
    MERCHANT_REGEX,
}

object BankConfigValidator {
    fun invalidFields(
        name: String,
        otpRegex: String,
        moneyRegex: String,
        merchantRegex: String,
    ): Set<BankConfigField> = buildSet {
        if (name.isBlank()) add(BankConfigField.NAME)
        if (otpRegex.isInvalidRegex(requiredGroupCount = 2)) add(BankConfigField.OTP_REGEX)
        if (moneyRegex.isInvalidRegex(requiredGroupCount = 2)) add(BankConfigField.MONEY_REGEX)
        if (merchantRegex.isInvalidRegex(requiredGroupCount = 1)) add(BankConfigField.MERCHANT_REGEX)
    }

    private fun String.isInvalidRegex(requiredGroupCount: Int): Boolean {
        if (isBlank()) return true
        return try {
            Regex(this).toPattern().matcher("").groupCount() < requiredGroupCount
        } catch (e: PatternSyntaxException) {
            true
        }
    }
}
