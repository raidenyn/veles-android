package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.viewmodel.BankConfigsState

@Composable
fun BankConfigsScreen(
    state: BankConfigsState,
    onAdd: () -> Unit,
    onEdit: (Long) -> Unit,
    onRequestDelete: (BankHandlerConfig) -> Unit,
    onCancelDelete: () -> Unit,
    onConfirmDelete: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp)
                .statusBarsPadding()
        ) {
            Text(
                text = "Bank Configs",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(vertical = 10.dp)
            )
            TextButton(onClick = onAdd) {
                Text("Add")
            }
            if (state.isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.CenterHorizontally)
                        .padding(top = 32.dp)
                )
            } else {
                LazyColumn {
                    items(state.configs, key = { it.id }) { config ->
                        BankConfigRow(
                            config = config,
                            onEdit = { onEdit(config.id) },
                            onDelete = { onRequestDelete(config) }
                        )
                    }
                }
            }
        }

        if (state.deleteTarget != null) {
            AlertDialog(
                onDismissRequest = onCancelDelete,
                title = { Text("Delete \"${state.deleteTarget.name}\"?") },
                text = { Text("This bank config will be permanently removed.") },
                confirmButton = {
                    TextButton(onClick = onConfirmDelete) { Text("Delete") }
                },
                dismissButton = {
                    TextButton(onClick = onCancelDelete) { Text("Cancel") }
                }
            )
        }
    }
}

@Composable
private fun BankConfigRow(
    config: BankHandlerConfig,
    onEdit: () -> Unit,
    onDelete: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = config.name,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f)
        )
        IconButton(onClick = onEdit) {
            Icon(Icons.Default.Edit, contentDescription = "Edit ${config.name}")
        }
        IconButton(onClick = onDelete) {
            Icon(Icons.Default.Delete, contentDescription = "Delete ${config.name}")
        }
    }
}

@Preview(showBackground = true)
@Composable
fun BankConfigsScreenPreview() {
    val config = BankHandlerConfig(
        id = 1L,
        name = "UOB Thailand",
        otpRegex = """ (\w{4})-(\d{6}) """,
        moneyRegex = """of ([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+) expiring""",
        createdAt = 0L,
        updatedAt = 0L
    )
    BankConfigsScreen(
        state = BankConfigsState(configs = listOf(config)),
        onAdd = {},
        onEdit = {},
        onRequestDelete = {},
        onCancelDelete = {},
        onConfirmDelete = {}
    )
}
