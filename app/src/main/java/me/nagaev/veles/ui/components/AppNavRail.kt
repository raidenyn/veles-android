package me.nagaev.veles.ui.components

import android.content.res.Configuration
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.R
import me.nagaev.veles.ui.Route
import me.nagaev.veles.ui.TOP_LEVEL_ROUTES
import me.nagaev.veles.ui.TopLevelRoute
import me.nagaev.veles.ui.theme.VelesTheme

@Composable
fun AppNavRail(
    currentRoute: Route,
    navigateTo: (destination: Route) -> Unit,
    modifier: Modifier = Modifier,
    destinations: List<TopLevelRoute> = TOP_LEVEL_ROUTES,
) {
    NavigationRail(
        header = {
            Icon(
                painterResource(R.drawable.ic_menu_camera),
                null,
                Modifier.padding(vertical = 12.dp),
                tint = MaterialTheme.colorScheme.primary
            )
        },
        modifier = modifier
    ) {
        Spacer(Modifier.weight(1f))
        LazyColumn {
            items(items = destinations) { destination ->
                val selected = currentRoute == destination.route
                val icon = if (selected) destination.selectedIcon else destination.unselectedIcon

                NavigationRailItem(
                    selected = currentRoute == destination.route,
                    onClick = { navigateTo(destination.route) },
                    icon = { Icon(icon, stringResource(destination.iconTextId)) },
                    label = { Text(stringResource(destination.iconTextId)) },
                    alwaysShowLabel = false
                )
            }
        }
        Spacer(Modifier.weight(1f))
    }
}

@Preview("Drawer contents")
@Preview("Drawer contents (dark)", uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun PreviewAppNavRail() {
    VelesTheme {
        AppNavRail(
            currentRoute = Route.Permissions,
            navigateTo = {},
        )
    }
}