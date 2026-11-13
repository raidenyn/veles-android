package me.nagaev.veles.testing.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.nagaev.veles.common.SharedPreferencesLogConfig
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.testing.TestNotificationSender
import javax.inject.Inject

@HiltViewModel
class TestViewModel @Inject constructor(
    private val preferences: TestInputPreferences,
    private val sender: TestNotificationSender,
    private val logConfig: SharedPreferencesLogConfig,
    private val testResultFlow: TestResultFlow,
) : ViewModel() {
    private val _uiState = MutableStateFlow(
        TestState(
            inputText = preferences.load(),
            logRawContent = logConfig.rawContentEnabled,
        ),
    )
    val uiState: StateFlow<TestState> = _uiState

    init {
        viewModelScope.launch(Dispatchers.Unconfined) {
            testResultFlow.current.collect { result ->
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

    fun onLogRawContentToggled(value: Boolean) {
        logConfig.saveRawContentEnabled(value)
        _uiState.update { it.copy(logRawContent = value) }
    }

    override fun onCleared() {
        super.onCleared()
        testResultFlow.current.value = null
    }
}
