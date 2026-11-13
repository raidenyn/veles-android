package me.nagaev.veles.common

import org.junit.Assert.assertEquals
import org.junit.Test

class VelesLinksTest {
    @Test
    fun guideLinksUseTheProductionCustomDomain() {
        assertEquals("https://veles.nagaev.me/", VelesLinks.SITE)
        assertEquals("https://veles.nagaev.me/#pairing", VelesLinks.PAIRING)
        assertEquals("https://veles.nagaev.me/#adb", VelesLinks.ADB)
    }
}
