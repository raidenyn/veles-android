package me.nagaev.veles.permissions.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun SwitchWithLoader(
    checked: Boolean?,
    onCheckedChange: ((Boolean) -> Unit)?,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier.padding(10.dp).wrapContentSize(),
    ) {
        if (checked == null) {
            CircularProgressIndicator(
                modifier = Modifier.wrapContentSize(),
            )
        } else {
            Switch(
                modifier = Modifier.wrapContentSize(),
                checked = checked,
                onCheckedChange = onCheckedChange,
            )
        }
    }
}
