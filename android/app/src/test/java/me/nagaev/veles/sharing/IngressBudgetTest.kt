package me.nagaev.veles.sharing

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class IngressBudgetTest {
    @Test fun floodCannotExhaustOtherClientsOrOutliveClose() {
        val budget = IngressBudget()
        repeat(16) { assertTrue(budget.acquire("a")) }
        assertFalse(budget.acquire("a"))
        assertTrue(budget.acquire("b"))
        budget.release("a")
        assertTrue(budget.acquire("a"))
        for (id in listOf("c", "d", "e", "f", "g", "h")) assertTrue(budget.acquire(id))
        assertFalse(budget.acquire("ninth"))
        budget.close()
        assertFalse(budget.acquire("a"))
    }
}
