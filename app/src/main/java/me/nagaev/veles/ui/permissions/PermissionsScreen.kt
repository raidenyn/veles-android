package me.nagaev.veles.ui.permissions

import android.widget.Toast
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import me.nagaev.veles.R

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PermissionsScreen(
    isExpandedScreen: Boolean,
    openDrawer: () -> Unit,
    snackbarHostState: SnackbarHostState = remember { SnackbarHostState() }
) {
    val context = LocalContext.current
    Scaffold(
        snackbarHost = { SnackbarHost(hostState = snackbarHostState) },
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.menu_home),
                        style = MaterialTheme.typography.titleLarge,
                        color = MaterialTheme.colorScheme.primary
                    )
                },
                navigationIcon = {
                    if (!isExpandedScreen) {
                        IconButton(onClick = openDrawer) {
                            Icon(
                                painter = painterResource(R.drawable.ic_menu_camera),
                                contentDescription = stringResource(
                                    R.string.menu_gallery
                                ),
                            )
                        }
                    }
                },
                actions = {
                    IconButton(
                        onClick = {
                            Toast.makeText(
                                context,
                                "Search is not yet implemented in this configuration",
                                Toast.LENGTH_LONG
                            ).show()
                        }
                    ) {
                        Icon(
                            imageVector = Icons.Filled.Search,
                            contentDescription = stringResource(R.string.menu_gallery)
                        )
                    }
                }
            )
        }
    ) { innerPadding ->
        val screenModifier = Modifier.padding(innerPadding)

    }
}