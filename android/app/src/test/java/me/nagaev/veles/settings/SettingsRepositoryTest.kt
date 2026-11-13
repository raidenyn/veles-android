package me.nagaev.veles.settings

import androidx.datastore.core.DataStore
import androidx.datastore.core.DataStoreFactory
import androidx.datastore.core.handlers.ReplaceFileCorruptionHandler
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.settings.proto.VelesSettings
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class SettingsRepositoryTest {
    @get:Rule
    val tempFolder = TemporaryFolder()

    @Test
    fun `auto-copy defaults to disabled`() = runTest {
        val repository = SettingsRepository(testStore())

        assertFalse(repository.isAutoCopyEnabled())
    }

    @Test
    fun `setting auto-copy persists and is observable`() = runTest {
        val repository = SettingsRepository(testStore())

        repository.setAutoCopyEnabled(true)

        assertTrue(repository.isAutoCopyEnabled())
        assertTrue(repository.autoCopyEnabled.first())
    }

    @Test
    fun `unreadable settings return disabled`() = runTest {
        val repository = SettingsRepository(unreadableStore())

        assertFalse(repository.isAutoCopyEnabled())
    }

    @Test
    fun `corrupt settings are replaced so subsequent updates succeed`() = runTest {
        val repository = SettingsRepository(corruptRecoveringStore())

        assertFalse(repository.isAutoCopyEnabled())

        repository.setAutoCopyEnabled(true)

        assertTrue(repository.isAutoCopyEnabled())
        assertTrue(repository.autoCopyEnabled.first())
    }

    private fun testStore(): DataStore<VelesSettings> = DataStoreFactory.create(
        serializer = SettingsSerializer,
        produceFile = { tempFolder.newFile("settings.test.pb") },
    )

    private fun unreadableStore(): DataStore<VelesSettings> {
        val corruptFile = tempFolder.newFile("settings.corrupt.pb").apply {
            writeBytes(CORRUPT_BYTES)
        }
        return DataStoreFactory.create(
            serializer = SettingsSerializer,
            produceFile = { corruptFile },
        )
    }

    private fun corruptRecoveringStore(): DataStore<VelesSettings> {
        val corruptFile = tempFolder.newFile("settings.recover.pb").apply {
            writeBytes(CORRUPT_BYTES)
        }
        return DataStoreFactory.create(
            serializer = SettingsSerializer,
            produceFile = { corruptFile },
            corruptionHandler = ReplaceFileCorruptionHandler(
                produceNewData = { VelesSettings.getDefaultInstance() },
            ),
        )
    }

    private companion object {
        val CORRUPT_BYTES: ByteArray = ByteArray(8) { 0xFF.toByte() }
    }
}
