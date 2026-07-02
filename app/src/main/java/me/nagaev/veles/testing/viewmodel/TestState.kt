package me.nagaev.veles.testing.viewmodel

import me.nagaev.veles.common.TestResult

data class TestState(
    val inputText: String = "",
    val lastResult: TestResult? = null,
)
