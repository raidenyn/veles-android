package me.nagaev.veles.permissions.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import me.nagaev.veles.common.ui.Routes
import me.nagaev.veles.common.ui.VelesBottomBar
import me.nagaev.veles.common.ui.theme.VelesTheme
import me.nagaev.veles.common.ui.topLevelRoutes
import me.nagaev.veles.otp.config.ui.BankConfigEditScreen
import me.nagaev.veles.otp.config.ui.BankConfigsScreen
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModel
import me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModel
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState
import me.nagaev.veles.testing.ui.TestScreen
import me.nagaev.veles.testing.viewmodel.TestViewModel

@Composable
fun VelesPermissionsApp(
    permissionsState: PermissionsState,
    permissionsActions: PermissionsActions,
) {
    VelesTheme {
        val navController = rememberNavController()
        val backStackEntry by navController.currentBackStackEntryAsState()
        val currentRoute = backStackEntry?.destination?.route
        Scaffold(
            contentWindowInsets = WindowInsets(0.dp),
            bottomBar = {
                if (currentRoute in topLevelRoutes) {
                    VelesBottomBar(
                        currentRoute = currentRoute,
                        onNavigate = { route ->
                            navController.navigate(route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                    )
                }
            },
        ) { padding ->
            VelesNavHost(
                navController = navController,
                modifier = Modifier.padding(padding),
                permissionsState = permissionsState,
                permissionsActions = permissionsActions,
            )
        }
    }
}

@Composable
private fun VelesNavHost(
    navController: NavHostController,
    modifier: Modifier,
    permissionsState: PermissionsState,
    permissionsActions: PermissionsActions,
) {
    NavHost(
        navController = navController,
        startDestination = Routes.PERMISSIONS,
        modifier = modifier,
    ) {
        composable(Routes.PERMISSIONS) {
            PermissionsScreen(
                state = permissionsState,
                actions = permissionsActions,
            )
        }
        composable(Routes.TEST) {
            val testViewModel: TestViewModel = hiltViewModel()
            val testState by testViewModel.uiState.collectAsStateWithLifecycle()
            TestScreen(
                state = testState,
                onTextChanged = testViewModel::onTextChanged,
                onSend = testViewModel::send,
                logRawContent = testState.logRawContent,
                onLogRawContentToggled = testViewModel::onLogRawContentToggled,
            )
        }
        composable(Routes.BANK_CONFIGS) {
            val context = LocalContext.current
            val vm: BankConfigsViewModel = hiltViewModel()
            val state by vm.state.collectAsStateWithLifecycle()
            val lifecycleOwner = LocalLifecycleOwner.current
            DisposableEffect(lifecycleOwner) {
                val observer = LifecycleEventObserver { _, event ->
                    if (event == Lifecycle.Event.ON_RESUME) {
                        vm.refresh()
                    }
                }
                lifecycleOwner.lifecycle.addObserver(observer)
                onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
            }

            val createDocumentLauncher = rememberLauncherForActivityResult(
                contract = ActivityResultContracts.CreateDocument("application/json"),
            ) { uri ->
                if (uri != null) vm.writeExportToUri(context, uri) else vm.cancelExport()
            }
            val openDocumentLauncher = rememberLauncherForActivityResult(
                contract = ActivityResultContracts.OpenDocument(),
            ) { uri ->
                if (uri != null) vm.onImportUri(context, uri)
            }

            LaunchedEffect(state.pendingExportJson) {
                if (state.pendingExportJson != null) {
                    createDocumentLauncher.launch("veles-bank-configs.json")
                }
            }

            BankConfigsScreen(
                state = state,
                onAdd = { navController.navigate("bank-config-edit") },
                onEdit = { id -> navController.navigate("bank-config-edit?id=$id") },
                onRequestDelete = vm::requestDelete,
                onCancelDelete = vm::cancelDelete,
                onConfirmDelete = vm::confirmDelete,
                onExport = vm::onExportRequested,
                onToggleExportItem = vm::toggleExportItem,
                onCancelExportSelection = vm::cancelExportSelection,
                onConfirmExportSelection = vm::confirmExportSelection,
                onImport = { openDocumentLauncher.launch(arrayOf("application/json", "*/*")) },
                onConfirmImport = vm::confirmImport,
                onCancelImport = vm::cancelImport,
                onDismissMessage = vm::dismissMessage,
            )
        }
        composable(
            route = Routes.BANK_CONFIG_EDIT,
            arguments = listOf(
                navArgument("id") {
                    type = NavType.LongType
                    defaultValue = -1L
                },
            ),
        ) { backStackEntry ->
            val rawId = backStackEntry.arguments?.getLong("id") ?: -1L
            val configId: Long? = if (rawId == -1L) null else rawId
            val vm: BankConfigEditViewModel = hiltViewModel()
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

@Preview(showBackground = true)
@Composable
fun VelesAppPreview() {
    VelesPermissionsApp(
        permissionsState = PermissionsState.Mocked,
        permissionsActions = PermissionsActions.Mocked,
    )
}
