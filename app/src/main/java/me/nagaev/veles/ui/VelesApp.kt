package me.nagaev.veles.ui

import androidx.compose.foundation.layout.Row
import androidx.compose.material3.DrawerState
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.windowsizeclass.WindowWidthSizeClass
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import kotlinx.coroutines.launch
import me.nagaev.veles.ui.components.AppNavRail
import me.nagaev.veles.ui.theme.VelesTheme
import me.nagaev.veles.viewmodel.PermissionsActions
import me.nagaev.veles.viewmodel.UiState
import androidx.compose.material3.Surface

@Composable
fun VelesApp(
    uiState: UiState,
    permissionsActions: PermissionsActions,
    widthSizeClass: WindowWidthSizeClass,
) {
    VelesTheme {
        val navController = rememberNavController()
        val navigationActions = remember(navController) {
            VelesNavigationActions(navController)
        }

        val coroutineScope = rememberCoroutineScope()

        val navBackStackEntry by navController.currentBackStackEntryAsState()
        val currentRoute = navBackStackEntry?.destination

        val isExpandedScreen = widthSizeClass == WindowWidthSizeClass.Expanded
        val sizeAwareDrawerState = rememberSizeAwareDrawerState(isExpandedScreen)

        Surface {
            VelesNavGraph(
                uiState = uiState,
                permissionsActions = permissionsActions,
                navController = navController,
            )
        }
    }
}

/**
 * Determine the drawer state to pass to the modal drawer.
 */
@Composable
private fun rememberSizeAwareDrawerState(isExpandedScreen: Boolean): DrawerState {
    val drawerState = rememberDrawerState(DrawerValue.Closed)

    return if (!isExpandedScreen) {
        // If we want to allow showing the drawer, we use a real, remembered drawer
        // state defined above
        drawerState
    } else {
        // If we don't want to allow the drawer to be shown, we provide a drawer state
        // that is locked closed. This is intentionally not remembered, because we
        // don't want to keep track of any changes and always keep it closed
        DrawerState(DrawerValue.Closed)
    }
}
