package me.nagaev.veles.viewmodel

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

typealias UpdateState<TState> = (state: TState) -> Unit
typealias GetState<TState> = () -> TState

class UiViewModel {
    private val _uiState = MutableStateFlow(UiState.Init)
    val uiState: StateFlow<UiState> = _uiState

    val permissions = PermissionsViewModel(
        get = { uiState.value.permissions },
        update = { state ->
            _uiState.value = _uiState.value.copy(
                permissions = state
            )
        }
    )
}