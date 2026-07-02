package me.nagaev.veles.otp.config.io

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import me.nagaev.veles.otp.config.BankHandlerConfig

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

    private fun json(name: String, otp: String = "x") =
        BankConfigJson(name, RegexJson(otp, "amt", "mer"))

    @Test
    fun `diff classifies names not present locally as toInsert`() {
        val parsed = listOf(json("New Bank"))
        val diff = ConfigImporter.diff(parsed, listOf(existing))
        assertEquals(1, diff.toInsert.size)
        assertEquals("New Bank", diff.toInsert[0].name)
        assertTrue(diff.toOverwrite.isEmpty())
    }

    @Test
    fun `diff classifies matching names as toOverwrite preserving existing id`() {
        val parsed = listOf(json("UOB Thailand", otp = "new-otp"))
        val diff = ConfigImporter.diff(parsed, listOf(existing))
        assertTrue(diff.toInsert.isEmpty())
        assertEquals(1, diff.toOverwrite.size)
        val (existingRow, incoming) = diff.toOverwrite[0]
        assertEquals(5L, existingRow.id)
        assertEquals("new-otp", incoming.regex.otp)
    }

    @Test
    fun `diff ignores local configs not present in import`() {
        val parsed = listOf(json("Other Bank"))
        val diff = ConfigImporter.diff(parsed, listOf(existing))
        assertEquals(1, diff.toInsert.size)
        assertTrue(diff.toOverwrite.isEmpty())
    }

    @Test
    fun `diff de-duplicates duplicate names in import keeping the last entry`() {
        val parsed = listOf(json("Dup", "first"), json("Dup", "second"))
        val diff = ConfigImporter.diff(parsed, emptyList())
        assertEquals(1, diff.toInsert.size)
        assertEquals("second", diff.toInsert[0].regex.otp)
    }

    @Test
    fun `diff matches first existing row when local has duplicate names`() {
        val existingDup = existing.copy(id = 9L, name = "UOB Thailand")
        val parsed = listOf(json("UOB Thailand", "new"))
        val diff = ConfigImporter.diff(parsed, listOf(existing, existingDup))
        assertEquals(1, diff.toOverwrite.size)
        assertEquals(5L, diff.toOverwrite[0].first.id)
    }
}
