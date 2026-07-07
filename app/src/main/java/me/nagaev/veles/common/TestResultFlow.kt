package me.nagaev.veles.common

import kotlinx.coroutines.flow.MutableStateFlow
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import javax.inject.Inject
import javax.inject.Singleton

data class TestResult(
    val handlingResult: MessageHandlingResult,
    val receivedText: String,
    val receivedTitle: String,
    val sourcePackage: String,
    val timestamp: Long,
)

@Singleton
class TestResultFlow @Inject constructor() {
    val current: MutableStateFlow<TestResult?> = MutableStateFlow(null)
}
