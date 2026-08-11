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
}
