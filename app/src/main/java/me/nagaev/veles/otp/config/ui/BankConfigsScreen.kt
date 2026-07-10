package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.FileDownload
import androidx.compose.material.icons.filled.FileUpload
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.viewmodel.BankConfigsState
import me.nagaev.veles.otp.config.viewmodel.ExportSelection
import me.nagaev.veles.otp.config.viewmodel.ImportReview

@Suppress("LongParameterList", "LongMethod")
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
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 20.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Bank Templates",
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    onClick = onImport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_BUTTON),
                ) {
                    Icon(
                        imageVector = Icons.Filled.FileDownload,
                        contentDescription = "Import templates",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(
                    onClick = onExport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_BUTTON),
                ) {
                    Icon(
                        imageVector = Icons.Filled.FileUpload,
                        contentDescription = "Export templates",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
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

        FloatingActionButton(
            onClick = onAdd,
            shape = RoundedCornerShape(16.dp),
            containerColor = MaterialTheme.colorScheme.primary,
            contentColor = MaterialTheme.colorScheme.onPrimary,
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(16.dp)
                .testTag(TestTags.BANK_CONFIG_ADD_FAB),
        ) {
            Icon(Icons.Filled.Add, contentDescription = "Add template")
        }

        if (state.deleteTarget != null) {
            AlertDialog(
                onDismissRequest = onCancelDelete,
                shape = RoundedCornerShape(8.dp),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                title = { Text("Delete \"${state.deleteTarget.name}\"?") },
                text = { Text("This template will be permanently removed.") },
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
                shape = RoundedCornerShape(8.dp),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
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
        shape = RoundedCornerShape(8.dp),
        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_DIALOG),
        title = { Text("Export templates") },
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
        shape = RoundedCornerShape(8.dp),
        containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_DIALOG),
        title = { Text("Import ${review.totalConfigs} templates?") },
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

private const val REGEX_PREVIEW_MAX_CHARS = 30

@Composable
private fun BankConfigRow(
    config: BankHandlerConfig,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    Card(
        shape = MaterialTheme.shapes.small,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
        ),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clickable(onClick = onEdit),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .background(
                        color = MaterialTheme.colorScheme.primaryContainer,
                        shape = CircleShape,
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = config.name.take(1).uppercase(),
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = config.name,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = config.otpRegex.take(REGEX_PREVIEW_MAX_CHARS),
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            IconButton(
                onClick = onEdit,
                modifier = Modifier.size(34.dp),
            ) {
                Icon(
                    imageVector = Icons.Filled.Edit,
                    contentDescription = "Edit ${config.name}",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(
                onClick = onDelete,
                modifier = Modifier.size(34.dp),
            ) {
                Icon(
                    imageVector = Icons.Filled.Delete,
                    contentDescription = "Delete ${config.name}",
                    tint = MaterialTheme.colorScheme.error,
                )
            }
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
