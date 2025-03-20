package me.nagaev.veles.permissions

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import me.nagaev.veles.permissions.services.RequestPermissionLauncher
import me.nagaev.veles.permissions.ui.VelesPermissionsApp
import me.nagaev.veles.permissions.viewmodal.PermissionsViewModel
import me.nagaev.veles.permissions.viewmodal.PermissionsViewModelFactory

class PermissionsActivity : ComponentActivity() {
    private val viewModel: PermissionsViewModel by viewModels {
        PermissionsViewModelFactory(this, requestPermissionLauncher)
    }

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

    private val requestPermissionLauncher = RequestPermissionLauncher.create(this)
}
