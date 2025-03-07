package me.nagaev.veles.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import me.nagaev.veles.services.MockedPermissionsProvider
import me.nagaev.veles.services.PermissionsProvider
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import me.nagaev.veles.services.ActivityProvider

typealias UpdateState<TState> = (state: TState) -> Unit
typealias GetState<TState> = () -> TState

class UiViewModel(
    private val permissionsProvider: PermissionsProvider = MockedPermissionsProvider()
): ViewModel() {
    private val _uiState = MutableStateFlow(UiState.Init)
    val uiState: StateFlow<UiState> = _uiState

    val permissions = PermissionsViewModel(
        viewModelScope,
        state = { uiState.value.permissions },
        update = { state ->
            _uiState.value = _uiState.value.copy(
                permissions = state
            )
        },
        permissionsProvider = permissionsProvider
    )
}
