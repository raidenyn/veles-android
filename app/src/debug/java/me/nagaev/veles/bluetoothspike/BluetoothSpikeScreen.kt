package me.nagaev.veles.bluetoothspike

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.R

@Composable
internal fun BluetoothSpikeScreen(
    state: BluetoothSpikeUiState,
    onStart: () -> Unit,
    onStop: () -> Unit,
    onOpenBluetoothSettings: () -> Unit,
    onPushNow: () -> Unit,
    onScheduleSmokePush: () -> Unit,
    onScheduleLifecyclePush: () -> Unit,
    onCopyEvents: () -> Unit,
) {
    var showLifecycleConfirm by remember { mutableStateOf(false) }

    if (showLifecycleConfirm) {
        AlertDialog(
            onDismissRequest = { showLifecycleConfirm = false },
            title = { Text(stringResource(R.string.bluetooth_spike_schedule_confirm_title)) },
            text = { Text(stringResource(R.string.bluetooth_spike_schedule_confirm_body)) },
            confirmButton = {
                TextButton(onClick = {
                    showLifecycleConfirm = false
                    onScheduleLifecyclePush()
                }) { Text(stringResource(R.string.bluetooth_spike_confirm)) }
            },
            dismissButton = {
                TextButton(onClick = { showLifecycleConfirm = false }) {
                    Text(stringResource(R.string.bluetooth_spike_cancel))
                }
            },
        )
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Column(modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 22.dp)) {
                Text(
                    text = stringResource(R.string.bluetooth_spike_title),
                    fontSize = 28.sp,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = stringResource(R.string.bluetooth_spike_synthetic_warning),
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }

        item {
            SpikeCard(modifier = Modifier.padding(horizontal = 16.dp)) {
                Text("Capability: ${state.availabilityDetail}")
                Text(stringResource(R.string.bluetooth_spike_service_status, state.serviceRunning))
                Text(stringResource(R.string.bluetooth_spike_advertising_status, state.advertising))
                state.lastError?.let { Text("Last error: $it", color = MaterialTheme.colorScheme.error) }
            }
        }

        item {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(
                    onClick = onStart,
                    enabled = !state.serviceRunning,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.bluetooth_spike_start)) }
                Button(
                    onClick = onStop,
                    enabled = state.serviceRunning,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.bluetooth_spike_stop)) }
            }
        }

        item {
            OutlinedButton(
                onClick = onOpenBluetoothSettings,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) { Text(stringResource(R.string.bluetooth_spike_open_settings)) }
        }

        item {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(
                    onClick = onPushNow,
                    enabled = state.serviceRunning,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.bluetooth_spike_push_now)) }
                Button(
                    onClick = onScheduleSmokePush,
                    enabled = state.serviceRunning,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.bluetooth_spike_schedule_smoke)) }
            }
        }

        item {
            Button(
                onClick = { showLifecycleConfirm = true },
                enabled = state.serviceRunning,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp),
            ) { Text(stringResource(R.string.bluetooth_spike_schedule_lifecycle)) }
        }

        item {
            SpikeCard(modifier = Modifier.padding(horizontal = 16.dp)) {
                Text(stringResource(R.string.bluetooth_spike_clients), fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(4.dp))
                if (state.clients.isEmpty()) {
                    Text(stringResource(R.string.bluetooth_spike_no_clients))
                } else {
                    state.clients.forEach { client ->
                        Column(Modifier.padding(vertical = 4.dp)) {
                            Text(client.label, fontWeight = FontWeight.SemiBold)
                            Text(stringResource(R.string.bluetooth_spike_client_address, client.id))
                            Text(stringResource(R.string.bluetooth_spike_client_subscription, client.subscribed))
                            Text(stringResource(R.string.bluetooth_spike_client_authentication, client.authenticated))
                        }
                    }
                }
            }
        }

        item {
            SpikeCard(modifier = Modifier.padding(horizontal = 16.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(stringResource(R.string.bluetooth_spike_events), fontWeight = FontWeight.Bold)
                    TextButton(onClick = onCopyEvents, enabled = state.events.isNotEmpty()) {
                        Text(stringResource(R.string.bluetooth_spike_copy_log))
                    }
                }
                Spacer(Modifier.height(4.dp))
                if (state.events.isEmpty()) {
                    Text(stringResource(R.string.bluetooth_spike_no_events))
                } else {
                    state.events.forEach { event ->
                        Text(
                            text = stringResource(R.string.bluetooth_spike_event_row, event.timestamp, event.message),
                            color = if (event.error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SpikeCard(
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(),
    ) {
        Column(Modifier.padding(16.dp)) { content() }
    }
}
