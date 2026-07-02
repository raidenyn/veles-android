package me.nagaev.veles.otp.config.io

import me.nagaev.veles.otp.config.BankHandlerConfig

object ConfigImporter {
    data class Diff(
        val toInsert: List<BankConfigJson>,
        val toOverwrite: List<Pair<BankHandlerConfig, BankConfigJson>>,
    )

    fun diff(parsed: List<BankConfigJson>, existing: List<BankHandlerConfig>): Diff {
        val deduped = LinkedHashMap<String, BankConfigJson>()
        for (entry in parsed) {
            deduped[entry.name] = entry
        }
        val toInsert = mutableListOf<BankConfigJson>()
        val toOverwrite = mutableListOf<Pair<BankHandlerConfig, BankConfigJson>>()
        for (incoming in deduped.values) {
            val match = existing.firstOrNull { it.name == incoming.name }
            if (match == null) {
                toInsert.add(incoming)
            } else {
                toOverwrite.add(match to incoming)
            }
        }
        return Diff(toInsert, toOverwrite)
    }
}