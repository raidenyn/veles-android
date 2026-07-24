package me.nagaev.veles.otp.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BankConfigValidatorTest {
    private fun invalidFields(
        name: String = "Bank",
        otp: String = """(\w+)-(\d{6})""",
        amount: String = """([A-Z]{3})(\d+)""",
        merchant: String = """at (.+)""",
    ) = BankConfigValidator.invalidFields(name, otp, amount, merchant)

    @Test
    fun `valid template has no invalid fields`() {
        assertTrue(invalidFields().isEmpty())
    }

    @Test
    fun `blank name and regexes report every field`() {
        assertEquals(
            setOf(
                BankConfigField.NAME,
                BankConfigField.OTP_REGEX,
                BankConfigField.MONEY_REGEX,
                BankConfigField.MERCHANT_REGEX,
            ),
            invalidFields(name = " ", otp = "", amount = " ", merchant = ""),
        )
    }

    @Test
    fun `syntax errors report their regex fields`() {
        assertEquals(
            setOf(
                BankConfigField.OTP_REGEX,
                BankConfigField.MONEY_REGEX,
                BankConfigField.MERCHANT_REGEX,
            ),
            invalidFields(otp = "[", amount = "(", merchant = "*"),
        )
    }

    @Test
    fun `insufficient capture groups report their regex fields`() {
        assertEquals(
            setOf(
                BankConfigField.OTP_REGEX,
                BankConfigField.MONEY_REGEX,
                BankConfigField.MERCHANT_REGEX,
            ),
            invalidFields(
                otp = """(\d{6})""",
                amount = """([A-Z]{3})\d+""",
                merchant = """at .+""",
            ),
        )
    }
}
