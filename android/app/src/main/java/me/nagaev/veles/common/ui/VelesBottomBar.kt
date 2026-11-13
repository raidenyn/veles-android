@file:Suppress("MatchingDeclarationName")

package me.nagaev.veles.common.ui

import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Column
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ListAlt
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Science
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import me.nagaev.veles.R

object Routes {
    const val PERMISSIONS = "permissions"
    const val BANK_CONFIGS = "bank-configs"
    const val TEST = "test"
    const val BANK_CONFIG_EDIT = "bank-config-edit?id={id}"
}

data class BottomNavDestination(
    val route: String,
    @StringRes val labelRes: Int,
    val icon: ImageVector,
)

val bottomNavDestinations =
    listOf(
        BottomNavDestination(route = Routes.PERMISSIONS, labelRes = R.string.bottom_nav_home, icon = Icons.Filled.Home),
        BottomNavDestination(
            route = Routes.BANK_CONFIGS,
            labelRes = R.string.bottom_nav_templates,
            icon = Icons.AutoMirrored.Filled.ListAlt,
        ),
        BottomNavDestination(route = Routes.TEST, labelRes = R.string.bottom_nav_test, icon = Icons.Filled.Science),
    )

val topLevelRoutes = bottomNavDestinations.map { it.route }.toSet()

@Composable
fun VelesBottomBar(
    currentRoute: String?,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier) {
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        NavigationBar(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.testTag(TestTags.BOTTOM_NAV_BAR),
        ) {
            bottomNavDestinations.forEach { destination ->
                val label = stringResource(destination.labelRes)
                NavigationBarItem(
                    selected = currentRoute == destination.route,
                    onClick = { onNavigate(destination.route) },
                    icon = { Icon(destination.icon, contentDescription = label) },
                    label = { Text(label) },
                    colors =
                    NavigationBarItemDefaults.colors(
                        selectedIconColor = MaterialTheme.colorScheme.primary,
                        selectedTextColor = MaterialTheme.colorScheme.primary,
                        unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                        indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                    ),
                    modifier = Modifier.testTag(TestTags.BOTTOM_NAV_ITEM(destination.route)),
                )
            }
        }
    }
}
