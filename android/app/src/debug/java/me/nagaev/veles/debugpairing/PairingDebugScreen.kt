package me.nagaev.veles.debugpairing

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import me.nagaev.veles.R
import me.nagaev.veles.debugprobe.DebugProbeEntryPoint
import me.nagaev.veles.debugprobe.PROBE_TAG
import me.nagaev.veles.debugprobe.ProbeCommands
import me.nagaev.veles.debugprobe.debugProbeCommands
import me.nagaev.veles.sharing.SharingState
import me.nagaev.veles.trust.PeerSummary

private const val REFRESH_MILLIS = 1_000L

/**
 * OTP-10 D5 debug screen. It is not launcher-visible; `adb shell am start` or the debug
 * build's activity list opens it. It shows the pairing code on screen only and drives the
 * same debug surface the scripted broadcasts use.
 *
 * OTP-11 D9 adds the peer-management controls: the peer list with Revoke and Mutual remove,
 * the confirmed endpoint-wide Reset, the Clear control (routed through the coordinator) and the
 * one trust status a surface may show. They exist only in this debug source set; a release build
 * links neither the screen nor its strings.
 */
class PairingDebugScreen : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // The code is on screen: keep it out of Recents thumbnails and screenshots.
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        setContent {
            MaterialTheme {
                PairingDebugContent()
            }
        }
    }
}

/** The last peer action, mapped to copy; a `DONE` outcome differs per control. */
private enum class PeerNotice {
    REVOKED,
    MUTUAL_REMOVED,
    MUTUAL_LOCAL_ONLY,
    MUTUAL_NO_RESPONSE,
    RESET_DONE,
    REVOKE_FAILED,
    MUTUAL_FAILED,
    RESET_FAILED,
    CONFIRMATION_REQUIRED,
    UNAVAILABLE,
}

@Composable
private fun PairingDebugContent() {
    val state by PairingDebugHooks.state.collectAsState()
    val trustStatus by SharingState.trustStatus.collectAsState()
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val actions = remember { debugPeerActions() }
    val cleared = stringResource(R.string.pairing_debug_cleared)
    val clearFailed = stringResource(R.string.pairing_debug_clear_failed)
    val connectedLabel = stringResource(R.string.pairing_debug_peer_connected)
    val disconnectedLabel = stringResource(R.string.pairing_debug_peer_disconnected)
    var peerId by remember { mutableStateOf("") }
    var clearStatus by remember { mutableStateOf("") }
    var peers by remember { mutableStateOf<List<PeerSummary>?>(emptyList()) }
    var notice by remember { mutableStateOf<PeerNotice?>(null) }
    var resetArmed by remember { mutableStateOf(false) }

    // The window expires lazily; this keeps the StateFlow (and the peer list) accurate.
    LaunchedEffect(Unit) {
        while (true) {
            PairingDebugHooks.refresh()
            peers = withContext(Dispatchers.IO) { actions.peers() }
            delay(REFRESH_MILLIS)
        }
    }

    fun run(done: PeerNotice, failed: PeerNotice, action: suspend () -> PeerActionOutcome) {
        scope.launch {
            val outcome = withContext(Dispatchers.IO) { action() }
            notice = outcome.toNotice(done, failed)
            peers = withContext(Dispatchers.IO) { actions.peers() }
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
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
        state.fingerprint?.let { fingerprint ->
            Text(stringResource(R.string.pairing_debug_fingerprint, fingerprint))
        }
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

        HorizontalDivider()

        // OTP-11 D6: the one status a surface may show; fail-closed never suggests erasing trust.
        Text(
            stringResource(
                R.string.pairing_debug_trust_status,
                stringResource(trustStatusLabel(trustStatus)),
            ),
        )

        Text(stringResource(R.string.pairing_debug_peers_title), style = MaterialTheme.typography.titleMedium)
        when (val listed = peers) {
            null -> Text(stringResource(R.string.pairing_debug_peers_failed))
            else -> if (listed.isEmpty()) {
                Text(stringResource(R.string.pairing_debug_no_peers))
            } else {
                listed.forEach { peer ->
                    PeerRow(
                        peer = peer,
                        connectedLabel = connectedLabel,
                        disconnectedLabel = disconnectedLabel,
                        onRevoke = {
                            run(PeerNotice.REVOKED, PeerNotice.REVOKE_FAILED) {
                                actions.revoke(peer.installationId)
                            }
                        },
                        onMutualRemove = {
                            run(PeerNotice.MUTUAL_REMOVED, PeerNotice.MUTUAL_FAILED) {
                                actions.removeMutually(peer.installationId)
                            }
                        },
                    )
                }
            }
        }
        notice?.let { Text(noticeText(it)) }

        OutlinedTextField(
            value = peerId,
            onValueChange = { peerId = it },
            label = { Text(stringResource(R.string.pairing_debug_peer)) },
            singleLine = true,
        )
        Button(
            onClick = {
                scope.launch {
                    val logger = EntryPointAccessors.fromApplication(
                        context.applicationContext,
                        DebugProbeEntryPoint::class.java,
                    ).velesLog()
                    val log: (String) -> Unit = { logger.d(PROBE_TAG, it) }
                    val clearedRecord = clearIncompleteRecord(peerId.trim()) {
                        debugProbeCommands(context, log)
                    }
                    clearStatus = if (clearedRecord) cleared else clearFailed
                    peers = withContext(Dispatchers.IO) { actions.peers() }
                }
            },
        ) {
            Text(stringResource(R.string.pairing_debug_clear))
        }
        if (clearStatus.isNotEmpty()) Text(clearStatus)

        HorizontalDivider()

        // OTP-11 D9: destructive and confirmed; the reset is endpoint-wide.
        Text(stringResource(R.string.pairing_debug_reset_warning), style = MaterialTheme.typography.bodySmall)
        Button(onClick = { resetArmed = true }) {
            Text(stringResource(R.string.pairing_debug_reset))
        }
        if (!state.open) Text(stringResource(R.string.pairing_debug_service_off))
    }

    if (resetArmed) {
        AlertDialog(
            onDismissRequest = { resetArmed = false },
            title = { Text(stringResource(R.string.pairing_debug_reset)) },
            text = { Text(stringResource(R.string.pairing_debug_reset_warning)) },
            confirmButton = {
                TextButton(
                    onClick = {
                        resetArmed = false
                        run(PeerNotice.RESET_DONE, PeerNotice.RESET_FAILED) {
                            actions.reset(confirmed = true)
                        }
                    },
                ) {
                    Text(stringResource(R.string.pairing_debug_reset_confirm))
                }
            },
            dismissButton = {
                TextButton(onClick = { resetArmed = false }) {
                    Text(stringResource(R.string.pairing_debug_reset_cancel))
                }
            },
        )
    }
}

@Composable
private fun PeerRow(
    peer: PeerSummary,
    connectedLabel: String,
    disconnectedLabel: String,
    onRevoke: () -> Unit,
    onMutualRemove: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            listOf(
                peer.label,
                peer.state.name,
                if (peer.connected) connectedLabel else disconnectedLabel,
            ).joinToString(" · "),
        )
        Text(stringResource(R.string.pairing_debug_peer_fingerprint, peer.fingerprint))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onRevoke) { Text(stringResource(R.string.pairing_debug_revoke)) }
            Button(onClick = onMutualRemove) { Text(stringResource(R.string.pairing_debug_mutual_remove)) }
        }
    }
}

@Composable
private fun noticeText(notice: PeerNotice): String = when (notice) {
    PeerNotice.REVOKED -> stringResource(R.string.pairing_debug_peer_revoked)
    PeerNotice.MUTUAL_REMOVED -> stringResource(R.string.pairing_debug_mutual_removed)
    PeerNotice.MUTUAL_LOCAL_ONLY -> stringResource(R.string.pairing_debug_mutual_local_only)
    PeerNotice.MUTUAL_NO_RESPONSE -> stringResource(R.string.pairing_debug_mutual_no_response)
    PeerNotice.RESET_DONE -> stringResource(R.string.pairing_debug_reset_done)
    PeerNotice.REVOKE_FAILED -> stringResource(R.string.pairing_debug_revoke_failed)
    PeerNotice.MUTUAL_FAILED -> stringResource(R.string.pairing_debug_mutual_failed)
    PeerNotice.RESET_FAILED -> stringResource(R.string.pairing_debug_reset_failed)
    PeerNotice.CONFIRMATION_REQUIRED -> stringResource(R.string.pairing_debug_confirmation_required)
    PeerNotice.UNAVAILABLE -> stringResource(R.string.pairing_debug_unavailable)
}

/**
 * The outcome copy for each control. `DONE` means a different thing per control, so Revoke,
 * Mutual remove and Reset name their own success and failure lines.
 */
private fun PeerActionOutcome.toNotice(done: PeerNotice, failed: PeerNotice): PeerNotice = when (this) {
    PeerActionOutcome.DONE -> done
    PeerActionOutcome.LOCAL_ONLY -> PeerNotice.MUTUAL_LOCAL_ONLY
    PeerActionOutcome.NO_RESPONSE -> PeerNotice.MUTUAL_NO_RESPONSE
    PeerActionOutcome.CONFIRMATION_REQUIRED -> PeerNotice.CONFIRMATION_REQUIRED
    PeerActionOutcome.FAILED -> failed
    PeerActionOutcome.UNAVAILABLE -> PeerNotice.UNAVAILABLE
}

/**
 * The screen's Clear action reuses the existing debug `REVOKE_PEER` entry point, which now goes
 * through the revocation coordinator (OTP-11 "Clear incomplete record"): the record is deleted
 * and any open session of that peer is terminated instead of the clear being refused. The
 * commands factory is a seam so the unit test exercises this exact screen helper.
 */
internal suspend fun clearIncompleteRecord(
    peerId: String,
    commands: () -> ProbeCommands,
): Boolean {
    if (peerId.isEmpty()) return false
    return withContext(Dispatchers.IO) {
        try {
            commands().revokePeer(peerId)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            false
        }
    }
}
