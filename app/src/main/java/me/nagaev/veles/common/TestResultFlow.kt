package me.nagaev.veles.common

import kotlinx.coroutines.flow.MutableStateFlow
import me.nagaev.veles.otp.handlers.MessageHandlingResult

data class TestResult(
    val result: MessageHandlingResult,
    val timestamp: Long,
)

object TestResultFlow {
    val current: MutableStateFlow<TestResult?> = MutableStateFlow(null)
}
