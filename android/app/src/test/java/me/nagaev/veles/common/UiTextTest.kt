package me.nagaev.veles.common

import me.nagaev.veles.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
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
        val resArgs = mutableListOf<Any>("original", "second")
        val pluralArgs = mutableListOf<Any>(1, 2)
        val res = UiText.Res(R.string.app_name, resArgs)
        val plural = UiText.Plural(R.plurals.bank_configs_exported, 1, pluralArgs)

        resArgs[0] = "mutated"
        pluralArgs[0] = 2

        val resHashCode = res.hashCode()
        val pluralHashCode = plural.hashCode()

        assertThrows(UnsupportedOperationException::class.java) {
            (res.args as MutableList<Any>)[0] = "changed"
        }
        assertThrows(UnsupportedOperationException::class.java) {
            (plural.args as MutableList<Any>)[0] = 3
        }

        assertEquals(listOf("original", "second"), res.args)
        assertEquals(UiText.Res(R.string.app_name, listOf("original", "second")), res)
        assertEquals(resHashCode, res.hashCode())
        assertEquals(listOf(1, 2), plural.args)
        assertEquals(UiText.Plural(R.plurals.bank_configs_exported, 1, listOf(1, 2)), plural)
        assertEquals(pluralHashCode, plural.hashCode())
    }
}
