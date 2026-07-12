package me.nagaev.veles.common

import org.junit.Assert.assertNotNull
import org.junit.Test
import javax.inject.Singleton

class NotificationStatePreferencesTest {
    @Test
    fun `connection state is application scoped`() {
        assertNotNull(NotificationStatePreferences::class.java.getAnnotation(Singleton::class.java))
    }
}
