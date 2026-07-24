package me.nagaev.veles.otp.config.io

import me.nagaev.veles.otp.config.BankConfigField
import me.nagaev.veles.otp.config.BankHandlerConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigImporterTest {
    private val existing = BankHandlerConfig(
        id = 5L,
        name = "UOB Thailand",
        otpRegex = "old-otp",
        moneyRegex = "old-amount",
        merchantRegex = "old-merchant",
        createdAt = 1000L,
        updatedAt = 1000L,
    )

    private fun json(
        name: String,
        otp: String = """(\w+)-(\d{6})""",
        amount: String = """([A-Z]{3})(\d+)""",
        merchant: String = """at (.+)""",
    ) = BankConfigJson(name, RegexJson(otp, amount, merchant))

    @Test
    fun `analyze classifies names not present locally as toInsert`() {
        val parsed = listOf(json("New Bank"))
        val diff = (ConfigImporter.analyze(parsed, listOf(existing)) as ConfigImporter.Analysis.Valid).diff
        assertEquals(1, diff.toInsert.size)
        assertEquals("New Bank", diff.toInsert[0].name)
        assertTrue(diff.toOverwrite.isEmpty())
    }

    @Test
    fun `analyze classifies matching names as toOverwrite preserving existing id`() {
        val parsed = listOf(json("UOB Thailand", otp = """(\w+)-(\d{6})"""))
        val diff = (ConfigImporter.analyze(parsed, listOf(existing)) as ConfigImporter.Analysis.Valid).diff
        assertTrue(diff.toInsert.isEmpty())
        assertEquals(1, diff.toOverwrite.size)
        val (existingRow, incoming) = diff.toOverwrite[0]
        assertEquals(5L, existingRow.id)
        assertEquals("""(\w+)-(\d{6})""", incoming.regex.otp)
    }

    @Test
    fun `analyze ignores local configs not present in import`() {
        val parsed = listOf(json("Other Bank"))
        val diff = (ConfigImporter.analyze(parsed, listOf(existing)) as ConfigImporter.Analysis.Valid).diff
        assertEquals(1, diff.toInsert.size)
        assertTrue(diff.toOverwrite.isEmpty())
    }

    @Test
    fun `analyze de-duplicates duplicate names in import keeping the last entry`() {
        val parsed = listOf(json("Dup", """(\w+)-(\d{6})-first"""), json("Dup", """(\w+)-(\d{6})-second"""))
        val diff = (ConfigImporter.analyze(parsed, emptyList()) as ConfigImporter.Analysis.Valid).diff
        assertEquals(1, diff.toInsert.size)
        assertEquals("""(\w+)-(\d{6})-second""", diff.toInsert[0].regex.otp)
    }

    @Test
    fun `analyze matches first existing row when local has duplicate names`() {
        val existingDup = existing.copy(id = 9L, name = "UOB Thailand")
        val parsed = listOf(json("UOB Thailand"))
        val diff = (ConfigImporter.analyze(parsed, listOf(existing, existingDup)) as ConfigImporter.Analysis.Valid).diff
        assertEquals(1, diff.toOverwrite.size)
        assertEquals(5L, diff.toOverwrite[0].first.id)
    }

    @Test
    fun `analyze returns every invalid effective entry and field`() {
        val analysis = ConfigImporter.analyze(
            parsed = listOf(
                json("Broken", otp = "[", merchant = "no group"),
                json("", amount = """([A-Z]{3})\d+"""),
            ),
            existing = emptyList(),
        )

        val invalid = analysis as ConfigImporter.Analysis.Invalid
        assertEquals(2, invalid.entries.size)
        assertEquals(
            setOf(BankConfigField.OTP_REGEX, BankConfigField.MERCHANT_REGEX),
            invalid.entries[0].invalidFields,
        )
        assertEquals(
            setOf(BankConfigField.NAME, BankConfigField.MONEY_REGEX),
            invalid.entries[1].invalidFields,
        )
    }

    @Test
    fun `analyze validates only last duplicate value`() {
        val analysis = ConfigImporter.analyze(
            parsed = listOf(json("Dup", otp = "["), json("Dup")),
            existing = emptyList(),
        )

        val valid = analysis as ConfigImporter.Analysis.Valid
        assertEquals(1, valid.diff.toInsert.size)
        assertEquals("""(\w+)-(\d{6})""", valid.diff.toInsert.single().regex.otp)
    }

    @Test
    fun `analyze returns existing diff when every effective entry is valid`() {
        val analysis = ConfigImporter.analyze(
            parsed = listOf(json("New Bank"), json("UOB Thailand")),
            existing = listOf(existing),
        )

        val valid = analysis as ConfigImporter.Analysis.Valid
        assertEquals(1, valid.diff.toInsert.size)
        assertEquals(1, valid.diff.toOverwrite.size)
    }
}
