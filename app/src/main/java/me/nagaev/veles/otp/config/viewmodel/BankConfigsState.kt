package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankHandlerConfig

data class BankConfigsState(
    val configs: List<BankHandlerConfig> = emptyList(),
    val isLoading: Boolean = false,
    val deleteTarget: BankHandlerConfig? = null,
    val exportSelection: ExportSelection? = null,
    val pendingExportJson: String? = null,
    val pendingExportCount: Int? = null,
    val importReview: ImportReview? = null,
    val message: String? = null,
)
