package me.nagaev.veles.otp.config.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigsViewModel(
    private val repository: BankHandlerRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(BankConfigsState())
    val state: StateFlow<BankConfigsState> = _state

    init {
        viewModelScope.launch { reload() }
    }

    fun requestDelete(config: BankHandlerConfig) {
        _state.update { it.copy(deleteTarget = config) }
    }

    fun cancelDelete() {
        _state.update { it.copy(deleteTarget = null) }
    }

    fun confirmDelete() {
        val target = _state.value.deleteTarget ?: return
        _state.update { it.copy(deleteTarget = null) }
        viewModelScope.launch {
            repository.delete(target)
            reload()
        }
    }

    private suspend fun reload() {
        _state.update { it.copy(isLoading = true) }
        val configs = repository.getAllSuspend()
        _state.update { it.copy(configs = configs, isLoading = false) }
    }
}
