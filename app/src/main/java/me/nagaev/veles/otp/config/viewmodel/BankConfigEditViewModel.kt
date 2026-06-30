package me.nagaev.veles.otp.config.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigEditViewModel(
    private val repository: BankHandlerRepository,
    private val configId: Long?
) : ViewModel() {

    private val _state = MutableStateFlow(BankConfigEditState())
    val state: StateFlow<BankConfigEditState> = _state

    init {
        if (configId != null) {
            viewModelScope.launch {
                val config = repository.getAllSuspend().find { it.id == configId }
                if (config != null) {
                    _state.update {
                        it.copy(
                            name = config.name,
                            otpRegex = config.otpRegex,
                            moneyRegex = config.moneyRegex,
                            merchantRegex = config.merchantRegex,
                            originalCreatedAt = config.createdAt
                        )
                    }
                }
            }
        }
    }

    fun onNameChanged(value: String) = _state.update { it.copy(name = value, nameError = null) }
    fun onOtpRegexChanged(value: String) = _state.update { it.copy(otpRegex = value, otpRegexError = null) }
    fun onMoneyRegexChanged(value: String) = _state.update { it.copy(moneyRegex = value, moneyRegexError = null) }
    fun onMerchantRegexChanged(value: String) = _state.update { it.copy(merchantRegex = value, merchantRegexError = null) }

    fun save() {
        val s = _state.value
        val nameError = if (s.name.isBlank()) "Name is required" else null
        val otpRegexError = validateRegex(s.otpRegex)
        val moneyRegexError = validateRegex(s.moneyRegex)
        val merchantRegexError = validateRegex(s.merchantRegex)

        if (nameError != null || otpRegexError != null || moneyRegexError != null || merchantRegexError != null) {
            _state.update {
                it.copy(
                    nameError = nameError,
                    otpRegexError = otpRegexError,
                    moneyRegexError = moneyRegexError,
                    merchantRegexError = merchantRegexError
                )
            }
            return
        }

        _state.update { it.copy(isSaving = true) }
        viewModelScope.launch {
            val now = System.currentTimeMillis()
            if (configId != null) {
                repository.update(
                    BankHandlerConfig(
                        id = configId,
                        name = s.name,
                        otpRegex = s.otpRegex,
                        moneyRegex = s.moneyRegex,
                        merchantRegex = s.merchantRegex,
                        createdAt = s.originalCreatedAt ?: now,
                        updatedAt = now
                    )
                )
            } else {
                repository.insert(
                    BankHandlerConfig(
                        name = s.name,
                        otpRegex = s.otpRegex,
                        moneyRegex = s.moneyRegex,
                        merchantRegex = s.merchantRegex,
                        createdAt = now,
                        updatedAt = now
                    )
                )
            }
            _state.update { it.copy(isSaving = false, savedSuccessfully = true) }
        }
    }

    private fun validateRegex(pattern: String): String? = if (pattern.isBlank()) {
        "Required"
    } else {
        try {
            Regex(pattern)
            null
        } catch (e: Exception) {
            "Invalid regex"
        }
    }
}
