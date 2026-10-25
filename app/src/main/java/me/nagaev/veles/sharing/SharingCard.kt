package me.nagaev.veles.sharing

import android.Manifest
import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags

@Composable
internal fun SharingCard() {
    val context = LocalContext.current
    val state by SharingState.availability.collectAsState()
    val enabled by SharingState.enabled.collectAsState()
    LaunchedEffect(context) { SharingState.load(context) }
    val active = state == SharingAvailability.Starting || state == SharingAvailability.Advertising
    val start = {
        val unavailable = SharingState.prerequisite(context)
        if (unavailable != null) {
            SharingState.update(unavailable)
        } else {
            try {
                // Visible action starts a normal service, which immediately promotes itself.
                // A failed prerequisite can then stop safely without an outstanding fgRequired start.
                context.startService(Intent(context, SharingService::class.java))
            } catch (_: IllegalStateException) {
                SharingState.update(SharingAvailability.Failed)
            } catch (_: SecurityException) {
                SharingState.update(SharingAvailability.PermissionRequired)
            }
        }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { start() }
    Card(Modifier.fillMaxWidth().padding(16.dp).testTag(TestTags.SHARING_CARD)) {
        Column(Modifier.padding(16.dp)) {
            Text(stringResource(R.string.sharing_title))
            if (enabled && !active) Text(stringResource(R.string.sharing_enabled_unavailable))
            Text(
                stringResource(
                    when (state) {
                        SharingAvailability.Disabled -> R.string.sharing_disabled
                        SharingAvailability.Starting -> R.string.sharing_starting
                        SharingAvailability.Advertising -> R.string.sharing_advertising
                        SharingAvailability.Unsupported -> R.string.sharing_unsupported
                        SharingAvailability.PermissionRequired -> R.string.sharing_permission
                        SharingAvailability.BluetoothOff -> R.string.sharing_bluetooth_off
                        SharingAvailability.Failed -> R.string.sharing_failed
                    },
                ),
            )
            Button(onClick = {
                if (active) {
                    context.startService(Intent(context, SharingService::class.java).setAction(SharingService.STOP))
                } else {
                    permission.launch(arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE))
                }
            }) {
                Text(stringResource(if (active) R.string.sharing_stop else R.string.sharing_start))
            }
            if (enabled && !active) {
                TextButton(onClick = { SharingState.setEnabled(context, false) }) {
                    Text(stringResource(R.string.sharing_stop))
                }
            }
        }
    }
}
