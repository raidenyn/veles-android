package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.viewmodel.BankConfigsState
import me.nagaev.veles.otp.config.viewmodel.ExportSelection
import me.nagaev.veles.otp.config.viewmodel.ImportReview

@Suppress("LongParameterList")
@Composable
fun BankConfigsScreen(
    state: BankConfigsState,
    onAdd: () -> Unit,
    onEdit: (Long) -> Unit,
    onRequestDelete: (BankHandlerConfig) -> Unit,
    onCancelDelete: () -> Unit,
    onConfirmDelete: () -> Unit,
    onExport: () -> Unit,
    onToggleExportItem: (String) -> Unit,
    onCancelExportSelection: () -> Unit,
    onConfirmExportSelection: () -> Unit,
    onImport: () -> Unit,
    onConfirmImport: () -> Unit,
    onCancelImport: () -> Unit,
    onDismissMessage: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp)
                .statusBarsPadding(),
        ) {
            Text(
                text = "Bank Configs",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(vertical = 10.dp),
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TextButton(onClick = onAdd) { Text("Add") }
                TextButton(
                    onClick = onExport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_BUTTON),
                ) { Text("Export") }
                TextButton(
                    onClick = onImport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_BUTTON),
                ) { Text("Import") }
            }
            if (state.isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.CenterHorizontally)
                        .padding(top = 32.dp),
                )
            } else {
                LazyColumn {
                    items(state.configs, key = { it.id }) { config ->
                        BankConfigRow(
                            config = config,
                            onEdit = { onEdit(config.id) },
                            onDelete = { onRequestDelete(config) },
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
                },
            )
        }

        if (state.exportSelection != null) {
            ExportSelectionDialog(
                selection = state.exportSelection,
                onToggle = onToggleExportItem,
                onConfirm = onConfirmExportSelection,
                onDismiss = onCancelExportSelection,
            )
        }

        if (state.importReview != null) {
            ImportReviewDialog(
                review = state.importReview!!,
                onConfirm = onConfirmImport,
                onDismiss = onCancelImport,
            )
        }

        if (state.message != null) {
            AlertDialog(
                onDismissRequest = onDismissMessage,
                confirmButton = {
                    TextButton(onClick = onDismissMessage) { Text("OK") }
                },
                title = { Text("Veles") },
                text = { Text(state.message!!) },
            )
        }
    }
}

@Composable
private fun ExportSelectionDialog(
    selection: ExportSelection,
    onToggle: (String) -> Unit,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_DIALOG),
        title = { Text("Export configs") },
        text = {
            Column {
                selection.items.forEach { name ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(
                            checked = name in selection.checked,
                            onCheckedChange = { onToggle(name) },
                        )
                        Text(name, style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_CONFIRM),
            ) { Text("Export") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
private fun ImportReviewDialog(
    review: ImportReview,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_DIALOG),
        title = { Text("Import ${review.totalConfigs} configs?") },
        text = {
            Column {
                if (review.toInsert.isNotEmpty()) {
                    Text("New:", style = MaterialTheme.typography.titleSmall)
                    review.toInsert.forEach { Text("- ${it.name}") }
                }
                if (review.toOverwrite.isNotEmpty()) {
                    Text("Will replace:", style = MaterialTheme.typography.titleSmall)
                    review.toOverwrite.forEach { (existing, _) ->
                        Text("- ${existing.name}")
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_CONFIRM),
            ) { Text("Import") }
        },
        dismissButton = {
            TextButton(
                onClick = onDismiss,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_CANCEL),
            ) { Text("Cancel") }
        },
    )
}

@Composable
private fun BankConfigRow(
    config: BankHandlerConfig,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = config.name,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f),
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
        updatedAt = 0L,
    )
    BankConfigsScreen(
        state = BankConfigsState(configs = listOf(config)),
        onAdd = {},
        onEdit = {},
        onRequestDelete = {},
        onCancelDelete = {},
        onConfirmDelete = {},
        onExport = {},
        onToggleExportItem = {},
        onCancelExportSelection = {},
        onConfirmExportSelection = {},
        onImport = {},
        onConfirmImport = {},
        onCancelImport = {},
        onDismissMessage = {},
    )
}
