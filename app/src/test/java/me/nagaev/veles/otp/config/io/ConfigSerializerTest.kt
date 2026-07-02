package me.nagaev.veles.otp.config.io

import kotlinx.serialization.SerializationException
import me.nagaev.veles.otp.config.BankHandlerConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigSerializerTest {
    private val config = BankHandlerConfig(
        id = 1L,
        name = "UOB Thailand",
        otpRegex = """ (\w{4})-(\d{6}) """,
        moneyRegex = """of ([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+)""",
        createdAt = 1000L,
        updatedAt = 2000L,
    )

    @Test
    fun `toJson produces nested regex shape and omits id and timestamps`() {
        val json = ConfigSerializer.toJson(listOf(config))
        assertTrue(json.contains("\"regex\""))
        assertTrue(json.contains("\"otp\""))
        assertTrue(json.contains("\"amount\""))
        assertTrue(json.contains("\"merchant\""))
        assertTrue(json.contains("\"UOB Thailand\""))
        assertTrue(!json.contains("\"id\""))
        assertTrue(!json.contains("\"createdAt\""))
        assertTrue(!json.contains("\"updatedAt\""))
    }

    @Test
    fun `fromJson parses valid JSON into BankConfigJson list`() {
        val json = """
            [{"name":"UOB Thailand","regex":{"otp":"a","amount":"b","merchant":"c"}}]
        """.trimIndent()
        val parsed = ConfigSerializer.fromJson(json)
        assertEquals(1, parsed.size)
        assertEquals("UOB Thailand", parsed[0].name)
        assertEquals("a", parsed[0].regex.otp)
        assertEquals("b", parsed[0].regex.amount)
        assertEquals("c", parsed[0].regex.merchant)
    }

    @Test
    fun `fromJson handles empty array`() {
        val parsed = ConfigSerializer.fromJson("[]")
        assertTrue(parsed.isEmpty())
    }

    @Test(expected = SerializationException::class)
    fun `fromJson throws on malformed JSON`() {
        ConfigSerializer.fromJson("{ not json")
    }

    @Test(expected = SerializationException::class)
    fun `fromJson throws when a required regex field is missing`() {
        val json = """[{"name":"X","regex":{"otp":"a","amount":"b"}}]"""
        ConfigSerializer.fromJson(json)
    }
}
