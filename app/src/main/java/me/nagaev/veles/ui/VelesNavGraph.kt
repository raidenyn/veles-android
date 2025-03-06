package me.nagaev.veles.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import me.nagaev.veles.ui.permissions.PermissionsScreen

const val POST_ID = "postId"

@Composable
fun VelesNavGraph(
    isExpandedScreen: Boolean,
    modifier: Modifier = Modifier,
    navController: NavHostController = rememberNavController(),
    openDrawer: () -> Unit = {},
    startDestination: String = VelesDestinations.HOME_ROUTE,
) {
    NavHost(
        navController = navController,
        startDestination = startDestination,
        modifier = modifier
    ) {
        composable(VelesDestinations.HOME_ROUTE) {
            PermissionsScreen(
                isExpandedScreen = isExpandedScreen,
                openDrawer = openDrawer
            )
        }
    }
}
