package me.nagaev.veles.ui.components

import android.content.res.Configuration
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ListAlt
import androidx.compose.material.icons.filled.Home
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
import me.nagaev.veles.ui.VelesDestinations
import me.nagaev.veles.ui.theme.VelesTheme

@Composable
fun AppNavRail(
    currentRoute: String,
    navigateToHome: () -> Unit,
    navigateToInterests: () -> Unit,
    modifier: Modifier = Modifier
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
        NavigationRailItem(
            selected = currentRoute == VelesDestinations.HOME_ROUTE,
            onClick = navigateToHome,
            icon = { Icon(Icons.Filled.Home, stringResource(R.string.menu_home)) },
            label = { Text(stringResource(R.string.menu_home)) },
            alwaysShowLabel = false
        )
        NavigationRailItem(
            selected = currentRoute == VelesDestinations.INTERESTS_ROUTE,
            onClick = navigateToInterests,
            icon = { Icon(Icons.AutoMirrored.Filled.ListAlt, stringResource(R.string.menu_gallery)) },
            label = { Text(stringResource(R.string.menu_gallery)) },
            alwaysShowLabel = false
        )
        Spacer(Modifier.weight(1f))
    }
}

@Preview("Drawer contents")
@Preview("Drawer contents (dark)", uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun PreviewAppNavRail() {
    VelesTheme {
        AppNavRail(
            currentRoute = VelesDestinations.HOME_ROUTE,
            navigateToHome = {},
            navigateToInterests = {},
        )
    }
}