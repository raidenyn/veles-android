package me.nagaev.veles.permissions.ui

import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import me.nagaev.veles.common.ui.theme.VelesTheme
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState
import me.nagaev.veles.testing.ui.TestScreen
import me.nagaev.veles.testing.viewmodel.TestViewModel
import me.nagaev.veles.testing.viewmodel.TestViewModelFactory

@Composable
fun VelesPermissionsApp(
    permissionsState: PermissionsState,
    permissionsActions: PermissionsActions,
) {
    VelesTheme {
        Surface {
            val navController = rememberNavController()
            NavHost(navController = navController, startDestination = "permissions") {
                composable("permissions") {
                    PermissionsScreen(
                        state = permissionsState,
                        actions = permissionsActions,
                        onNavigateToTest = { navController.navigate("test") }
                    )
                }
                composable("test") {
                    val context = LocalContext.current
                    val factory = remember { TestViewModelFactory(context) }
                    val testViewModel: TestViewModel = viewModel(factory = factory)
                    val testState by testViewModel.uiState.collectAsStateWithLifecycle()
                    TestScreen(
                        state = testState,
                        onTextChanged = testViewModel::onTextChanged,
                        onSend = testViewModel::send
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun VelesAppPreview() {
    VelesPermissionsApp(
        permissionsState = PermissionsState.Mocked,
        permissionsActions = PermissionsActions.Mocked,
    )
}
