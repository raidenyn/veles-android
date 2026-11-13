package me.nagaev.veles.common

import me.nagaev.veles.otp.handlers.MessageHandlingResult
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class TestResultFlowTest {
    @Test
    fun `initial value is null`() {
        val flow = TestResultFlow()
        assertNull(flow.current.value)
    }

    @Test
    fun `emitting ACCEPTED result updates current value`() {
        val flow = TestResultFlow()
        val result = TestResult(
            handlingResult = MessageHandlingResult.ACCEPTED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 1000L,
        )
        flow.current.value = result
        assertEquals(result, flow.current.value)
    }

    @Test
    fun `emitting FILTERED result updates current value`() {
        val flow = TestResultFlow()
        val result = TestResult(
            handlingResult = MessageHandlingResult.FILTERED,
            receivedText = "text",
            receivedTitle = "title",
            sourcePackage = "pkg",
            timestamp = 2000L,
        )
        flow.current.value = result
        assertEquals(result, flow.current.value)
    }
}
