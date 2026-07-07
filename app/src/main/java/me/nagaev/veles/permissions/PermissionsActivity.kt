package me.nagaev.veles.permissions

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dagger.hilt.android.AndroidEntryPoint
import dagger.hilt.android.lifecycle.withCreationCallback
import me.nagaev.veles.permissions.services.AccessNotificationPermissionProvider
import me.nagaev.veles.permissions.services.ActivityProvider
import me.nagaev.veles.permissions.services.ActivityProviderImpl
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.permissions.services.PermissionsProviderImpl
import me.nagaev.veles.permissions.services.RequestPermissionLauncher
import me.nagaev.veles.permissions.services.SendNotificationPermissionProvider
import me.nagaev.veles.permissions.ui.VelesPermissionsApp
import me.nagaev.veles.permissions.viewmodal.PermissionsViewModel

@AndroidEntryPoint
class PermissionsActivity : ComponentActivity() {
    private val viewModel: PermissionsViewModel by viewModels(
        extrasProducer = {
            defaultViewModelCreationExtras.withCreationCallback<PermissionsViewModel.Factory> { factory ->
                factory.create(
                    permissionsProvider = buildPermissionsProvider(),
                    openSettings = { intent -> startActivity(intent) },
                )
            }
        },
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        setContent {
            val permissionsState by viewModel.uiState.collectAsStateWithLifecycle()
            VelesPermissionsApp(
                permissionsState = permissionsState,
                permissionsActions = viewModel,
            )
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            // Some permissions require switching to focus to Settings.
            // To catch the result of the permission request we need to update the state
            // after use come back to the app
            viewModel.updatePermissionsState()
        }
    }

    private fun buildPermissionsProvider(): PermissionsProvider {
        val activityProvider: ActivityProvider = ActivityProviderImpl(this)
        return PermissionsProviderImpl(
            providers =
            mapOf(
                PermissionType.ACCESS_NOTIFICATIONS to
                    AccessNotificationPermissionProvider(activityProvider),
                PermissionType.SEND_NOTIFICATIONS to
                    SendNotificationPermissionProvider(activityProvider, requestPermissionLauncher),
            ),
        )
    }

    private val requestPermissionLauncher = RequestPermissionLauncher.create(this)
}
