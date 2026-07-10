@file:Suppress("MatchingDeclarationName")

package me.nagaev.veles.common.ui

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

data class BottomNavDestination(
    val route: String,
    val label: String,
    val icon: ImageVector,
)

val bottomNavDestinations =
    listOf(
        BottomNavDestination(route = "permissions", label = "Home", icon = Icons.Filled.Home),
        BottomNavDestination(route = "bank-configs", label = "Templates", icon = Icons.AutoMirrored.Filled.ListAlt),
        BottomNavDestination(route = "test", label = "Test", icon = Icons.Filled.Science),
    )

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
                NavigationBarItem(
                    selected = currentRoute == destination.route,
                    onClick = { onNavigate(destination.route) },
                    icon = { Icon(destination.icon, contentDescription = destination.label) },
                    label = { Text(destination.label) },
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
