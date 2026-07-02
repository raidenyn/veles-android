package me.nagaev.veles.common

import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

class RedactionStateFlowTest {
    @Before
    fun reset() {
        RedactionStateFlow.current.value = RedactionState.Unknown
    }

    @Test
    fun `initial state is Unknown`() {
        assertEquals(RedactionState.Unknown, RedactionStateFlow.current.value)
    }

    @Test
    fun `transitions to Hidden`() {
        RedactionStateFlow.current.value = RedactionState.Hidden
        assertEquals(RedactionState.Hidden, RedactionStateFlow.current.value)
    }

    @Test
    fun `transitions to Readable`() {
        RedactionStateFlow.current.value = RedactionState.Readable
        assertEquals(RedactionState.Readable, RedactionStateFlow.current.value)
    }
}
