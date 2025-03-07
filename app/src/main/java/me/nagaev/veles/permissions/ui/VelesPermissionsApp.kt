package me.nagaev.veles.permissions.ui

import androidx.compose.runtime.Composable
import me.nagaev.veles.common.ui.theme.VelesTheme
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import androidx.compose.material3.Surface
import androidx.compose.ui.tooling.preview.Preview
import me.nagaev.veles.permissions.viewmodal.PermissionsState

@Composable
fun VelesApp(
    permissionsState: PermissionsState,
    permissionsActions: PermissionsActions,
) {
    VelesTheme {
        Surface {
            PermissionsScreen(
                uiState = permissionsState,
                actions = permissionsActions,
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
fun VelesAppPreview() {
    VelesApp(
        permissionsState = PermissionsState.Mocked,
        permissionsActions = PermissionsActions.Mocked,
    )
}
