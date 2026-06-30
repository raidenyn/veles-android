package me.nagaev.veles.testing.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.testing.TestNotificationSender

class TestViewModel(
    private val preferences: TestInputPreferences,
    private val sender: TestNotificationSender,
) : ViewModel() {
    private val _uiState = MutableStateFlow(TestState(inputText = preferences.load()))
    val uiState: StateFlow<TestState> = _uiState

    init {
        viewModelScope.launch(Dispatchers.Unconfined) {
            TestResultFlow.current.collect { result ->
                result?.let {
                    _uiState.update { state -> state.copy(lastResult = it) }
                }
            }
        }
    }

    fun onTextChanged(text: String) {
        _uiState.update { it.copy(inputText = text) }
        preferences.save(text)
    }

    fun send() {
        sender.post(_uiState.value.inputText)
    }
}
