package me.nagaev.veles.common

import me.nagaev.veles.otp.handlers.MessageHandlingResult
import org.junit.Before
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class TestResultFlowTest {
    @Before
    fun setUp() {
        TestResultFlow.current.value = null
    }

    @Test
    fun `initial value is null`() {
        assertNull(TestResultFlow.current.value)
    }

    @Test
    fun `emitting ACCEPTED result updates current value`() {
        val result = TestResult(
            handlingResult = MessageHandlingResult.ACCEPTED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 1000L,
        )
        TestResultFlow.current.value = result
        assertEquals(result, TestResultFlow.current.value)
    }

    @Test
    fun `emitting FILTERED result updates current value`() {
        val result = TestResult(
            handlingResult = MessageHandlingResult.FILTERED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 2000L,
        )
        TestResultFlow.current.value = result
        assertEquals(result, TestResultFlow.current.value)
    }
}
