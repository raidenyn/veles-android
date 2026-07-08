package me.nagaev.veles.common

import org.junit.Assert.assertEquals
import org.junit.Test

class RedactionStateFlowTest {
    @Test
    fun `initial state is Unknown`() {
        val flow = RedactionStateFlow()
        assertEquals(RedactionState.Unknown, flow.current.value)
    }

    @Test
    fun `transitions to Hidden`() {
        val flow = RedactionStateFlow()
        flow.current.value = RedactionState.Hidden
        assertEquals(RedactionState.Hidden, flow.current.value)
    }

    @Test
    fun `transitions to Readable`() {
        val flow = RedactionStateFlow()
        flow.current.value = RedactionState.Readable
        assertEquals(RedactionState.Readable, flow.current.value)
    }
}
