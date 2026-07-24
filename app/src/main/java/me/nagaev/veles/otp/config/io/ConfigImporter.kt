package me.nagaev.veles.otp.config.io

import me.nagaev.veles.otp.config.BankConfigField
import me.nagaev.veles.otp.config.BankConfigValidator
import me.nagaev.veles.otp.config.BankHandlerConfig

object ConfigImporter {
    data class Diff(
        val toInsert: List<BankConfigJson>,
        val toOverwrite: List<Pair<BankHandlerConfig, BankConfigJson>>,
    )

    data class InvalidEntry(
        val config: BankConfigJson,
        val invalidFields: Set<BankConfigField>,
    )

    sealed interface Analysis {
        data class Valid(val diff: Diff) : Analysis
        data class Invalid(val entries: List<InvalidEntry>) : Analysis
    }

    fun analyze(parsed: List<BankConfigJson>, existing: List<BankHandlerConfig>): Analysis {
        val effective = deduplicate(parsed)
        val invalidEntries = effective.mapNotNull { config ->
            val invalidFields = BankConfigValidator.invalidFields(
                name = config.name,
                otpRegex = config.regex.otp,
                moneyRegex = config.regex.amount,
                merchantRegex = config.regex.merchant,
            )
            if (invalidFields.isEmpty()) null else InvalidEntry(config, invalidFields)
        }
        return if (invalidEntries.isEmpty()) {
            Analysis.Valid(classify(effective, existing))
        } else {
            Analysis.Invalid(invalidEntries)
        }
    }

    fun diff(parsed: List<BankConfigJson>, existing: List<BankHandlerConfig>): Diff =
        classify(deduplicate(parsed), existing)

    private fun deduplicate(parsed: List<BankConfigJson>): List<BankConfigJson> {
        val deduped = LinkedHashMap<String, BankConfigJson>()
        for (entry in parsed) {
            deduped[entry.name] = entry
        }
        return deduped.values.toList()
    }

    private fun classify(
        effective: List<BankConfigJson>,
        existing: List<BankHandlerConfig>,
    ): Diff {
        val toInsert = mutableListOf<BankConfigJson>()
        val toOverwrite = mutableListOf<Pair<BankHandlerConfig, BankConfigJson>>()
        for (incoming in effective) {
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
