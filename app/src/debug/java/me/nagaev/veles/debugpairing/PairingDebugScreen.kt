package me.nagaev.veles.debugpairing

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import me.nagaev.veles.R
import me.nagaev.veles.debugprobe.debugProbeCommands

private const val REFRESH_MILLIS = 1_000L

/**
 * OTP-10 D5 debug screen. It is not launcher-visible; `adb shell am start` or the debug
 * build's activity list opens it. It shows the pairing code on screen only and drives the
 * same debug surface the scripted broadcasts use.
 */
class PairingDebugScreen : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                PairingDebugContent()
            }
        }
    }
}

@Composable
private fun PairingDebugContent() {
    val state by PairingDebugHooks.state.collectAsState()
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val cleared = stringResource(R.string.pairing_debug_cleared)
    val clearFailed = stringResource(R.string.pairing_debug_clear_failed)
    var peerId by remember { mutableStateOf("") }
    var clearStatus by remember { mutableStateOf("") }

    // The window expires lazily; this keeps the StateFlow (and the screen) accurate.
    LaunchedEffect(Unit) {
        while (true) {
            PairingDebugHooks.refresh()
            delay(REFRESH_MILLIS)
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(stringResource(R.string.pairing_debug_title), style = MaterialTheme.typography.headlineSmall)
        if (state.open) {
            // The one permitted readout: on-screen text, never a log or broadcast result.
            Text(stringResource(R.string.pairing_debug_code, state.code.orEmpty()))
        } else {
            Text(stringResource(R.string.pairing_debug_closed))
        }
        Text(stringResource(R.string.pairing_debug_id, state.pairingId.orEmpty()))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    PairingDebugHooks.start()
                    PairingDebugHooks.refresh()
                },
            ) {
                Text(stringResource(R.string.pairing_debug_start))
            }
            Button(onClick = { PairingDebugHooks.cancel() }) {
                Text(stringResource(R.string.pairing_debug_cancel))
            }
        }
        OutlinedTextField(
            value = peerId,
            onValueChange = { peerId = it },
            label = { Text(stringResource(R.string.pairing_debug_peer)) },
            singleLine = true,
        )
        Button(
            onClick = {
                scope.launch {
                    clearStatus = if (clearIncompleteRecord(context, peerId.trim())) cleared else clearFailed
                }
            },
        ) {
            Text(stringResource(R.string.pairing_debug_clear))
        }
        if (clearStatus.isNotEmpty()) Text(clearStatus)
        if (!state.open) Text(stringResource(R.string.pairing_debug_service_off))
    }
}

/**
 * The screen's Clear action reuses the existing debug `REVOKE_PEER` implementation: same
 * open-session refusal, same record deletion. It is a debug control, never the OTP-11
 * revocation protocol.
 */
private suspend fun clearIncompleteRecord(context: Context, peerId: String): Boolean {
    if (peerId.isEmpty()) return false
    return runCatching { debugProbeCommands(context, log = {}).revokePeer(peerId) }.getOrDefault(false)
}
