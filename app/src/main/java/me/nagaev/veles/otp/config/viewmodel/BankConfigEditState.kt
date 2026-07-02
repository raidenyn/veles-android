package me.nagaev.veles.otp.config.viewmodel

data class BankConfigEditState(
    val name: String = "",
    val otpRegex: String = "",
    val moneyRegex: String = "",
    val merchantRegex: String = "",
    val originalCreatedAt: Long? = null,
    val nameError: String? = null,
    val otpRegexError: String? = null,
    val moneyRegexError: String? = null,
    val merchantRegexError: String? = null,
    val isSaving: Boolean = false,
    val savedSuccessfully: Boolean = false,
)
