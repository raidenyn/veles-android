package me.nagaev.veles.ui

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.material3.windowsizeclass.ExperimentalMaterial3WindowSizeClassApi
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.material3.windowsizeclass.WindowWidthSizeClass
import androidx.compose.material3.windowsizeclass.calculateWindowSizeClass
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.tooling.preview.Preview
import me.nagaev.veles.services.RequestPermissionLauncher
import me.nagaev.veles.viewmodel.PermissionsActions
import me.nagaev.veles.viewmodel.UiState
import me.nagaev.veles.viewmodel.UiViewModel
import me.nagaev.veles.viewmodel.UiViewModelFactory

class MainActivity : ComponentActivity() {
    private val viewModel: UiViewModel by viewModels {
        UiViewModelFactory(this, requestPermissionLauncher)
    }

    @OptIn(ExperimentalMaterial3WindowSizeClassApi::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        setContent {
            val widthSizeClass = calculateWindowSizeClass(this).widthSizeClass
            val uiState by viewModel.uiState.collectAsStateWithLifecycle()
            VelesApp(
                uiState = uiState,
                permissionsActions = viewModel.permissions,
                widthSizeClass = widthSizeClass
            )
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            // Some permissions require switching to focus to Settings.
            // To catch the result of the permission request we need to update the state
            // after use come back to the app
            viewModel.permissions.updatePermissionsState()
        }
    }

    private val requestPermissionLauncher = RequestPermissionLauncher.create(this)
}

@Preview(showBackground = true)
@Composable
fun VelesAppPreview() {
    VelesApp(
        uiState = UiState.Mocked,
        permissionsActions = PermissionsActions.Mocked,
        widthSizeClass = WindowWidthSizeClass.Medium,
    )
}
