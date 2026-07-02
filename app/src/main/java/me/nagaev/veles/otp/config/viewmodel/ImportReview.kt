package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.io.BankConfigJson
import me.nagaev.veles.otp.config.io.ConfigImporter

data class ImportReview(
    val toInsert: List<BankConfigJson>,
    val toOverwrite: List<Pair<BankHandlerConfig, BankConfigJson>>,
) {
    val totalConfigs: Int get() = toInsert.size + toOverwrite.size

    companion object {
        fun from(diff: ConfigImporter.Diff): ImportReview = ImportReview(diff.toInsert, diff.toOverwrite)
    }
}
