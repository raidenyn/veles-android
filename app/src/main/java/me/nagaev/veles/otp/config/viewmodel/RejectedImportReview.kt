package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankConfigField
import me.nagaev.veles.otp.config.io.ConfigImporter

data class RejectedImportReview(
    val entries: List<Entry>,
) {
    data class Entry(
        val name: String,
        val invalidFields: Set<BankConfigField>,
    )

    companion object {
        fun from(entries: List<ConfigImporter.InvalidEntry>) = RejectedImportReview(
            entries.map { Entry(it.config.name, it.invalidFields) },
        )
    }
}
