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
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import me.nagaev.veles.viewmodel.PermissionsViewModel

class MainActivity : ComponentActivity() {
    private val viewModel: PermissionsViewModel by viewModels()

    @OptIn(ExperimentalMaterial3WindowSizeClassApi::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        setContent {
            val widthSizeClass = calculateWindowSizeClass(this).widthSizeClass
            val uiState by viewModel.uiState.collectAsStateWithLifecycle()
            VelesApp(widthSizeClass)
        }
    }
}

@Preview(showBackground = true)
@Composable
fun ReplyAppPreview() {
    VelesApp(
        widthSizeClass = WindowWidthSizeClass.Medium,
    )
}