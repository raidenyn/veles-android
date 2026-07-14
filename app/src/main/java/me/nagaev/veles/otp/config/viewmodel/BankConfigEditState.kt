package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.common.UiText

data class BankConfigEditState(
    val name: String = "",
    val otpRegex: String = "",
    val moneyRegex: String = "",
    val merchantRegex: String = "",
    val originalCreatedAt: Long? = null,
    val nameError: UiText? = null,
    val otpRegexError: UiText? = null,
    val moneyRegexError: UiText? = null,
    val merchantRegexError: UiText? = null,
    val isSaving: Boolean = false,
    val savedSuccessfully: Boolean = false,
)
