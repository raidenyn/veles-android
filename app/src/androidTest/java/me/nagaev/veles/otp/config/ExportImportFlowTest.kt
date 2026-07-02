package me.nagaev.veles.otp.config

import androidx.activity.ComponentActivity
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.Dispatchers
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.config.ui.BankConfigsScreen
import me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ExportImportFlowTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private lateinit var db: BankHandlerDatabase
    private lateinit var repository: BankHandlerRepository
    private lateinit var vm: BankConfigsViewModel

    @Before
    fun setUp() {
        val context = androidx.test.core.app.ApplicationProvider
            .getApplicationContext<android.content.Context>()
        db = Room.inMemoryDatabaseBuilder(context, BankHandlerDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        val field = BankHandlerDatabase::class.java.getDeclaredField("INSTANCE")
        field.isAccessible = true
        field.set(null, db)
        repository = BankHandlerRepository(context)
        vm = BankConfigsViewModel(repository, ioDispatcher = Dispatchers.Main)
        composeRule.setContent {
            BankConfigsScreen(
                state = vm.state.collectAsState().value,
                onAdd = {},
                onEdit = {},
                onRequestDelete = vm::requestDelete,
                onCancelDelete = vm::cancelDelete,
                onConfirmDelete = vm::confirmDelete,
                onExport = vm::onExportRequested,
                onToggleExportItem = vm::toggleExportItem,
                onCancelExportSelection = vm::cancelExportSelection,
                onConfirmExportSelection = vm::confirmExportSelection,
                onImport = {},
                onConfirmImport = vm::confirmImport,
                onCancelImport = vm::cancelImport,
                onDismissMessage = vm::dismissMessage,
            )
        }
    }

    @After
    fun tearDown() {
        db.close()
        val field = BankHandlerDatabase::class.java.getDeclaredField("INSTANCE")
        field.isAccessible = true
        field.set(null, null)
    }

    @Test
    fun `export button opens selection dialog with seeded configs`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "UOB Thailand",
                    otpRegex = "o",
                    moneyRegex = "m",
                    merchantRegex = "mer",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_BUTTON).performClick()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_DIALOG).assertIsDisplayed()
        composeRule.onAllNodesWithText("UOB Thailand").assertCountEquals(2)
    }

    @Test
    fun `confirm export selection produces pending export json`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "UOB Thailand",
                    otpRegex = "o",
                    moneyRegex = "m",
                    merchantRegex = "mer",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_BUTTON).performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_CONFIRM).performClick()
        composeRule.waitForIdle()

        assertNotNull(vm.state.value.pendingExportJson)
        assertTrue(vm.state.value.pendingExportJson!!.contains("UOB Thailand"))
    }

    @Test
    fun `import review dialog lists new and overwrite names`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "old",
                    moneyRegex = "old",
                    merchantRegex = "old",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        val json = me.nagaev.veles.otp.config.io.ConfigSerializer.toJson(
            listOf(
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "new",
                    moneyRegex = "new",
                    merchantRegex = "new",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Brand New Bank",
                    otpRegex = "x",
                    moneyRegex = "x",
                    merchantRegex = "x",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
            ),
        )
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, json)
        composeRule.runOnIdle { vm.onImportUri(context, uri) }
        composeRule.waitUntil(5000) { vm.state.value.importReview != null }
        composeRule.waitForIdle()

        assertEquals(1, vm.state.value.importReview!!.toInsert.size)
        assertEquals("Brand New Bank", vm.state.value.importReview!!.toInsert[0].name)
        assertEquals(1, vm.state.value.importReview!!.toOverwrite.size)
        assertEquals("Existing Bank", vm.state.value.importReview!!.toOverwrite[0].first.name)
    }

    @Test
    fun `confirm import writes new and overwrite rows to the database`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "old",
                    moneyRegex = "old",
                    merchantRegex = "old",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        val json = me.nagaev.veles.otp.config.io.ConfigSerializer.toJson(
            listOf(
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "new-otp",
                    moneyRegex = "new-amt",
                    merchantRegex = "new-mer",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Brand New Bank",
                    otpRegex = "x",
                    moneyRegex = "x",
                    merchantRegex = "x",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
            ),
        )
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, json)
        composeRule.runOnIdle { vm.onImportUri(context, uri) }
        composeRule.waitUntil(5000) { vm.state.value.importReview != null }
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_IMPORT_CONFIRM).performClick()
        composeRule.waitUntil(5000) { vm.state.value.importReview == null }
        composeRule.waitForIdle()

        kotlinx.coroutines.runBlocking {
            val all = repository.getAllSuspend()
            assertEquals(2, all.size)
            val existing = all.first { it.name == "Existing Bank" }
            assertEquals("new-otp", existing.otpRegex)
            assertEquals("new-amt", existing.moneyRegex)
            assertEquals("new-mer", existing.merchantRegex)
            val newBank = all.first { it.name == "Brand New Bank" }
            assertEquals("x", newBank.otpRegex)
        }
    }

    @Test
    fun `cancel import stops everything and writes nothing`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "old",
                    moneyRegex = "old",
                    merchantRegex = "old",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        val json = me.nagaev.veles.otp.config.io.ConfigSerializer.toJson(
            listOf(
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "new",
                    moneyRegex = "new",
                    merchantRegex = "new",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Brand New Bank",
                    otpRegex = "x",
                    moneyRegex = "x",
                    merchantRegex = "x",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
            ),
        )
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, json)
        composeRule.runOnIdle { vm.onImportUri(context, uri) }
        composeRule.waitUntil(5000) { vm.state.value.importReview != null }
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_IMPORT_CANCEL).performClick()
        composeRule.waitForIdle()

        assertNull(vm.state.value.importReview)
        kotlinx.coroutines.runBlocking {
            val all = repository.getAllSuspend()
            assertEquals(1, all.size)
            assertEquals("old", all[0].otpRegex)
        }
    }

    @Test
    fun `import of malformed file shows message and no review dialog`() {
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, "{not json")
        composeRule.runOnIdle { vm.onImportUri(context, uri) }
        composeRule.waitUntil(5000) { vm.state.value.message != null }
        composeRule.waitForIdle()

        assertNull(vm.state.value.importReview)
        assertNotNull(vm.state.value.message)
    }
}
