package me.nagaev.veles.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import me.nagaev.veles.ui.permissions.PermissionsScreen
import me.nagaev.veles.viewmodel.PermissionsActions
import me.nagaev.veles.viewmodel.UiState

@Composable
fun VelesNavGraph(
    uiState: UiState,
    permissionsActions: PermissionsActions,
    modifier: Modifier = Modifier,
    navController: NavHostController = rememberNavController(),
    startDestination: Route = Route.Permissions,
) {
    NavHost(
        modifier = modifier,
        navController = navController,
        startDestination = startDestination,
    ) {
        composable<Route.Permissions> {
            PermissionsScreen(
                uiState = uiState.permissions,
                actions = permissionsActions,
            )
        }
    }
}
