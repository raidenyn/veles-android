package me.nagaev.veles.bluetoothspike

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.time.LocalTime
import java.time.format.DateTimeFormatter

internal data class BluetoothSpikeUiState(
    val supported: Boolean? = null,
    val availabilityDetail: String = "Not checked",
    val serviceRunning: Boolean = false,
    val advertising: Boolean = false,
    val clients: List<BluetoothSpikeClientState> = emptyList(),
    val events: List<BluetoothSpikeEvent> = emptyList(),
    val lastError: String? = null,
)

internal data class BluetoothSpikeClientState(
    val id: String,
    val label: String,
    val subscribed: Boolean,
    val authenticated: Boolean,
)

internal data class BluetoothSpikeEvent(
    val timestamp: String,
    val message: String,
    val error: Boolean,
)

internal fun BluetoothSpikeUiState.eventLogText(): String =
    events.joinToString("\n") { "${it.timestamp}  ${it.message}" }

internal object BluetoothSpikeStateStore {
    private const val MAX_EVENTS = 100
    private val timeFormatter = DateTimeFormatter.ISO_LOCAL_TIME

    private val _state = MutableStateFlow(BluetoothSpikeUiState())
    val state: StateFlow<BluetoothSpikeUiState> = _state.asStateFlow()

    fun reset() {
        _state.value = BluetoothSpikeUiState()
    }

    fun serviceRunning(running: Boolean) {
        _state.update { it.copy(serviceRunning = running) }
    }

    fun availability(supported: Boolean, detail: String) {
        _state.update { it.copy(supported = supported, availabilityDetail = detail) }
    }

    fun advertising(advertising: Boolean) {
        _state.update { it.copy(advertising = advertising) }
    }

    fun client(clientId: String, label: String, subscribed: Boolean, authenticated: Boolean) {
        _state.update { state ->
            val updated = BluetoothSpikeClientState(clientId, label, subscribed, authenticated)
            val others = state.clients.filter { it.id != clientId }
            state.copy(clients = (others + updated).sortedBy { it.id })
        }
    }

    fun removeClient(clientId: String) {
        _state.update { state ->
            state.copy(clients = state.clients.filter { it.id != clientId })
        }
    }

    fun clearClients() {
        _state.update { it.copy(clients = emptyList()) }
    }

    fun event(message: String) {
        appendEvent(BluetoothSpikeEvent(nowTimestamp(), message, error = false))
    }

    fun error(message: String) {
        appendEvent(BluetoothSpikeEvent(nowTimestamp(), message, error = true))
        _state.update { it.copy(lastError = message) }
    }

    private fun appendEvent(event: BluetoothSpikeEvent) {
        _state.update { state ->
            val trimmed = if (state.events.size >= MAX_EVENTS) state.events.drop(1) else state.events
            state.copy(events = trimmed + event)
        }
    }

    private fun nowTimestamp(): String = LocalTime.now().format(timeFormatter)
}
