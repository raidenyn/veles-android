package me.nagaev.veles.permissions.ui

import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import me.nagaev.veles.common.ui.theme.VelesTheme
import me.nagaev.veles.otp.config.ui.BankConfigEditScreen
import me.nagaev.veles.otp.config.ui.BankConfigsScreen
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModel
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModelFactory
import me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModel
import me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelFactory
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
                        onNavigateToTest = { navController.navigate("test") },
                        onNavigateToBankConfigs = { navController.navigate("bank-configs") },
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
                        onSend = testViewModel::send,
                    )
                }
                composable("bank-configs") {
                    val context = LocalContext.current
                    val factory = remember { BankConfigsViewModelFactory(context) }
                    val vm: BankConfigsViewModel = viewModel(factory = factory)
                    val state by vm.state.collectAsStateWithLifecycle()
                    BankConfigsScreen(
                        state = state,
                        onAdd = { navController.navigate("bank-config-edit") },
                        onEdit = { id -> navController.navigate("bank-config-edit?id=$id") },
                        onRequestDelete = vm::requestDelete,
                        onCancelDelete = vm::cancelDelete,
                        onConfirmDelete = vm::confirmDelete,
                    )
                }
                composable(
                    route = "bank-config-edit?id={id}",
                    arguments = listOf(
                        navArgument("id") {
                            type = NavType.LongType
                            defaultValue = -1L
                        },
                    ),
                ) { backStackEntry ->
                    val rawId = backStackEntry.arguments?.getLong("id") ?: -1L
                    val configId: Long? = if (rawId == -1L) null else rawId
                    val context = LocalContext.current
                    val factory = remember(configId) { BankConfigEditViewModelFactory(context, configId) }
                    val vm: BankConfigEditViewModel = viewModel(factory = factory)
                    val state by vm.state.collectAsStateWithLifecycle()
                    BankConfigEditScreen(
                        state = state,
                        isNew = configId == null,
                        onNameChanged = vm::onNameChanged,
                        onOtpRegexChanged = vm::onOtpRegexChanged,
                        onMoneyRegexChanged = vm::onMoneyRegexChanged,
                        onMerchantRegexChanged = vm::onMerchantRegexChanged,
                        onSave = vm::save,
                        onNavigateBack = { navController.popBackStack() },
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
