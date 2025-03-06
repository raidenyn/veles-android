package me.nagaev.veles.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import kotlinx.serialization.Serializable
import me.nagaev.veles.R

sealed interface Route {
    @Serializable data object Permissions : Route
}

data class TopLevelRoute(
    val route: Route,
    val selectedIcon: ImageVector,
    val unselectedIcon: ImageVector,
    val iconTextId: Int
)

/**
 * Models the navigation actions in the app.
 */
class VelesNavigationActions(private val navController: NavHostController) {
    fun navigateTo(destination: TopLevelRoute) {
        navController.navigate(destination.route) {
            // Pop up to the start destination of the graph to
            // avoid building up a large stack of destinations
            // on the back stack as users select items
            popUpTo(navController.graph.findStartDestination().id) {
                saveState = true
            }
            // Avoid multiple copies of the same destination when
            // reselecting the same item
            launchSingleTop = true
            // Restore state when reselecting a previously selected item
            restoreState = true
        }
    }
}

val TOP_LEVEL_ROUTES = listOf(
    TopLevelRoute(
        route = Route.Permissions,
        selectedIcon = Icons.Filled.Home,
        unselectedIcon = Icons.Filled.Home,
        iconTextId = R.string.menu_home
    ),
)