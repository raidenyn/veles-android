package me.nagaev.veles.otp.config.viewmodel

import android.content.Context
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerializationException
import me.nagaev.veles.common.di.IoDispatcher
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.config.io.ConfigImporter
import me.nagaev.veles.otp.config.io.ConfigSerializer
import java.io.IOException
import javax.inject.Inject

@Suppress("TooManyFunctions")
@HiltViewModel
class BankConfigsViewModel @Inject constructor(
    private val repository: BankHandlerRepository,
    @IoDispatcher private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {

    private val _state = MutableStateFlow(BankConfigsState())
    val state: StateFlow<BankConfigsState> = _state

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch { reload() }
    }

    fun requestDelete(config: BankHandlerConfig) {
        _state.update { it.copy(deleteTarget = config) }
    }

    fun cancelDelete() {
        _state.update { it.copy(deleteTarget = null) }
    }

    fun confirmDelete() {
        val target = _state.value.deleteTarget ?: return
        _state.update { it.copy(deleteTarget = null) }
        viewModelScope.launch {
            repository.delete(target)
            reload()
        }
    }

    fun onExportRequested() {
        val names = _state.value.configs.map { it.name }
        _state.update {
            it.copy(exportSelection = ExportSelection(items = names, checked = names.toSet()))
        }
    }

    fun toggleExportItem(name: String) {
        _state.update { current ->
            val sel = current.exportSelection ?: return@update current
            val next = if (name in sel.checked) sel.checked - name else sel.checked + name
            current.copy(exportSelection = sel.copy(checked = next))
        }
    }

    fun cancelExportSelection() {
        _state.update { it.copy(exportSelection = null) }
    }

    fun confirmExportSelection() {
        val sel = _state.value.exportSelection ?: return
        if (sel.checked.isEmpty()) {
            _state.update { it.copy(message = "Select at least one config") }
            return
        }
        val selected = _state.value.configs.filter { it.name in sel.checked }
        val json = ConfigSerializer.toJson(selected)
        _state.update {
            it.copy(
                exportSelection = null,
                pendingExportJson = json,
                pendingExportCount = selected.size,
            )
        }
    }

    fun writeExportToUri(context: Context, uri: Uri) {
        val json = _state.value.pendingExportJson ?: return
        val count = _state.value.pendingExportCount ?: 0
        viewModelScope.launch {
            val success = try {
                withContext(ioDispatcher) {
                    val out = context.contentResolver.openOutputStream(uri)
                        ?: return@withContext false
                    out.use { it.write(json.toByteArray()) }
                    true
                }
            } catch (e: IOException) {
                false
            }
            _state.update {
                if (success) {
                    it.copy(
                        pendingExportJson = null,
                        pendingExportCount = null,
                        message = "Exported $count configs",
                    )
                } else {
                    it.copy(
                        pendingExportJson = null,
                        pendingExportCount = null,
                        message = "Export failed",
                    )
                }
            }
        }
    }

    fun cancelExport() {
        _state.update {
            it.copy(
                pendingExportJson = null,
                pendingExportCount = null,
            )
        }
    }

    fun onImportUri(context: Context, uri: Uri) {
        viewModelScope.launch {
            val text = withContext(ioDispatcher) {
                context.contentResolver.openInputStream(uri)?.use { it.readBytes().toString(Charsets.UTF_8) }
            } ?: run {
                _state.update { it.copy(message = "Import failed: cannot read file") }
                return@launch
            }
            try {
                val parsed = ConfigSerializer.fromJson(text)
                if (parsed.isEmpty()) {
                    _state.update { it.copy(message = "Nothing to import") }
                    return@launch
                }
                val diff = ConfigImporter.diff(parsed, _state.value.configs)
                _state.update {
                    it.copy(importReview = ImportReview.from(diff))
                }
            } catch (e: SerializationException) {
                _state.update { it.copy(message = "Import failed: invalid file") }
            }
        }
    }

    fun confirmImport() {
        val review = _state.value.importReview ?: return
        viewModelScope.launch {
            val now = System.currentTimeMillis()
            for (incoming in review.toInsert) {
                repository.insert(
                    BankHandlerConfig(
                        id = 0L,
                        name = incoming.name,
                        otpRegex = incoming.regex.otp,
                        moneyRegex = incoming.regex.amount,
                        merchantRegex = incoming.regex.merchant,
                        createdAt = now,
                        updatedAt = now,
                    ),
                )
            }
            for ((existing, incoming) in review.toOverwrite) {
                repository.update(
                    existing.copy(
                        otpRegex = incoming.regex.otp,
                        moneyRegex = incoming.regex.amount,
                        merchantRegex = incoming.regex.merchant,
                        updatedAt = now,
                    ),
                )
            }
            _state.update {
                it.copy(
                    importReview = null,
                    message = "Imported ${review.totalConfigs} configs",
                )
            }
            reload()
        }
    }

    fun cancelImport() {
        _state.update { it.copy(importReview = null) }
    }

    fun dismissMessage() {
        _state.update { it.copy(message = null) }
    }

    private suspend fun reload() {
        _state.update { it.copy(isLoading = true) }
        val configs = repository.getAllSuspend()
        _state.update { it.copy(configs = configs, isLoading = false) }
    }
}
