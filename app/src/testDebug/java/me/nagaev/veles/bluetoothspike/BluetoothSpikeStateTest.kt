package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertEquals

class BluetoothSpikeStateTest {
    @Test
    fun `event log retains newest one hundred entries`() {
        BluetoothSpikeStateStore.reset()
        repeat(105) { index -> BluetoothSpikeStateStore.event("event-$index") }

        val events = BluetoothSpikeStateStore.state.value.events
        assertEquals(100, events.size)
        assertEquals("event-5", events.first().message)
        assertEquals("event-104", events.last().message)
    }

    @Test
    fun `event log text joins timestamp and message one per line`() {
        BluetoothSpikeStateStore.reset()
        BluetoothSpikeStateStore.event("first")
        BluetoothSpikeStateStore.error("second")

        val lines = BluetoothSpikeStateStore.state.value.eventLogText().split("\n")

        assertEquals(2, lines.size)
        val events = BluetoothSpikeStateStore.state.value.events
        assertEquals("${events[0].timestamp}  first", lines[0])
        assertEquals("${events[1].timestamp}  second", lines[1])
    }

    @Test
    fun `event log text is empty when there are no events`() {
        BluetoothSpikeStateStore.reset()

        assertEquals("", BluetoothSpikeStateStore.state.value.eventLogText())
    }
}
