package me.nagaev.veles.common

import me.nagaev.veles.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class UiTextTest {
    @Test
    fun `formatted resources compare by argument value`() {
        assertEquals(
            UiText.Res(R.string.app_name, listOf("same")),
            UiText.Res(R.string.app_name, listOf("same")),
        )
    }

    @Test
    fun `plural resources include quantity and arguments in equality`() {
        assertEquals(
            UiText.Plural(R.plurals.bank_configs_exported, 2, listOf(2)),
            UiText.Plural(R.plurals.bank_configs_exported, 2, listOf(2)),
        )
        assertNotEquals(
            UiText.Plural(R.plurals.bank_configs_exported, 1, listOf(1)),
            UiText.Plural(R.plurals.bank_configs_exported, 2, listOf(2)),
        )
    }

    @Test
    fun `resource arguments are snapshotted at construction`() {
        val resArgs = mutableListOf<Any>("original")
        val pluralArgs = mutableListOf<Any>(1)
        val res = UiText.Res(R.string.app_name, resArgs)
        val plural = UiText.Plural(R.plurals.bank_configs_exported, 1, pluralArgs)

        resArgs[0] = "mutated"
        pluralArgs[0] = 2

        assertEquals(listOf("original"), res.args)
        assertEquals(UiText.Res(R.string.app_name, listOf("original")), res)
        assertEquals(listOf(1), plural.args)
        assertEquals(UiText.Plural(R.plurals.bank_configs_exported, 1, listOf(1)), plural)
    }
}
