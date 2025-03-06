package me.nagaev.veles.ui

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.material3.windowsizeclass.ExperimentalMaterial3WindowSizeClassApi
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.material3.windowsizeclass.WindowWidthSizeClass
import androidx.compose.material3.windowsizeclass.calculateWindowSizeClass
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.tooling.preview.Preview
import me.nagaev.veles.viewmodel.PermissionsActions
import me.nagaev.veles.viewmodel.UiState
import me.nagaev.veles.viewmodel.UiViewModel

class MainActivity : ComponentActivity() {
    private val viewModel: UiViewModel by viewModels()

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
}

@Preview(showBackground = true)
@Composable
fun ReplyAppPreview() {
    VelesApp(
        uiState = UiState.Mocked,
        permissionsActions = PermissionsActions.Mocked,
        widthSizeClass = WindowWidthSizeClass.Medium,
    )
}
