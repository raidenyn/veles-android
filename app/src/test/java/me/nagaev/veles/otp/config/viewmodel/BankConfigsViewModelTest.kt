package me.nagaev.veles.otp.config.viewmodel

import android.content.Context
import android.net.Uri
import io.mockk.coEvery
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.mockkStatic
import io.mockk.unmockkStatic
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.config.io.ConfigSerializer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class BankConfigsViewModelTest {

    private val repository = mockk<BankHandlerRepository>()
    private val config = BankHandlerConfig(
        id = 1L,
        name = "Test Bank",
        otpRegex = """\d{6}""",
        moneyRegex = """([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+)""",
        createdAt = 1000L,
        updatedAt = 1000L,
    )

    private val testDispatcher = UnconfinedTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        coEvery { repository.getAllSuspend() } returns listOf(config)
        mockkStatic(Uri::class)
        every { Uri.parse(any()) } returns mockk(relaxed = true)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
        unmockkStatic(Uri::class)
    }

    @Test
    fun `loads configs on init`() {
        val vm = BankConfigsViewModel(repository)
        assert(vm.state.value.configs == listOf(config))
        assert(!vm.state.value.isLoading)
    }

    @Test
    fun `requestDelete sets deleteTarget`() {
        val vm = BankConfigsViewModel(repository)
        vm.requestDelete(config)
        assert(vm.state.value.deleteTarget == config)
    }

    @Test
    fun `cancelDelete clears deleteTarget`() {
        val vm = BankConfigsViewModel(repository)
        vm.requestDelete(config)
        vm.cancelDelete()
        assert(vm.state.value.deleteTarget == null)
    }

    @Test
    fun `confirmDelete deletes config and reloads list`() {
        coEvery { repository.delete(config) } returns Unit
        val vm = BankConfigsViewModel(repository)
        vm.requestDelete(config)
        vm.confirmDelete()
        coVerify { repository.delete(config) }
        coVerify(exactly = 2) { repository.getAllSuspend() }
        assert(vm.state.value.deleteTarget == null)
    }

    @Test
    fun `refresh reloads configs from repository`() {
        val vm = BankConfigsViewModel(repository)
        val updatedConfig = config.copy(name = "Updated Bank")
        coEvery { repository.getAllSuspend() } returns listOf(updatedConfig)

        vm.refresh()

        coVerify(exactly = 2) { repository.getAllSuspend() }
        assert(vm.state.value.configs == listOf(updatedConfig))
    }

    @Test
    fun `onExportRequested opens selection dialog with all names checked`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        val sel = vm.state.value.exportSelection
        assertNotNull(sel)
        assertEquals(setOf("Test Bank"), sel!!.checked)
        assertEquals(listOf("Test Bank"), sel.items)
    }

    @Test
    fun `toggleExportItem toggles checked set`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.toggleExportItem("Test Bank")
        assertTrue(vm.state.value.exportSelection!!.checked.isEmpty())
        vm.toggleExportItem("Test Bank")
        assertEquals(setOf("Test Bank"), vm.state.value.exportSelection!!.checked)
    }

    @Test
    fun `confirmExportSelection with empty selection sets message and stays in dialog`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.toggleExportItem("Test Bank")
        vm.confirmExportSelection()
        assertEquals("Select at least one config", vm.state.value.message)
        assertNotNull(vm.state.value.exportSelection)
        assertNull(vm.state.value.pendingExportJson)
    }

    @Test
    fun `confirmExportSelection serializes checked configs and clears selection`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.confirmExportSelection()
        val json = vm.state.value.pendingExportJson
        assertNotNull(json)
        assertTrue(json!!.contains("Test Bank"))
        assertTrue(json.contains("\"regex\""))
        assertNull(vm.state.value.exportSelection)
    }

    @Test
    fun `writeExportToUri writes pending json and clears it`() {
        val context = mockk<Context>()
        val out = ByteArrayOutputStream()
        every { context.contentResolver.openOutputStream(any()) } returns out
        val vm = BankConfigsViewModel(repository, testDispatcher)
        vm.onExportRequested()
        vm.confirmExportSelection()
        vm.writeExportToUri(context, android.net.Uri.parse("content://x/y"))
        assertTrue(out.toString().contains("Test Bank"))
        assertNull(vm.state.value.pendingExportJson)
    }

    @Test
    fun `cancelExport clears pending export state`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.confirmExportSelection()
        assertNotNull(vm.state.value.pendingExportJson)
        assertNotNull(vm.state.value.pendingExportCount)
        vm.cancelExport()
        assertNull(vm.state.value.pendingExportJson)
        assertNull(vm.state.value.pendingExportCount)
    }

    @Test
    fun `writeExportToUri with null stream sets Export failed message and clears pending state`() {
        val context = mockk<Context>()
        every { context.contentResolver.openOutputStream(any()) } returns null
        val vm = BankConfigsViewModel(repository, testDispatcher)
        vm.onExportRequested()
        vm.confirmExportSelection()
        vm.writeExportToUri(context, android.net.Uri.parse("content://x/y"))
        assertEquals("Export failed", vm.state.value.message)
        assertNull(vm.state.value.pendingExportJson)
        assertNull(vm.state.value.pendingExportCount)
    }

    @Test
    fun `writeExportToUri catches IOException and reports failure`() {
        val context = mockk<Context>()
        every { context.contentResolver.openOutputStream(any()) } throws IOException("denied")
        val vm = BankConfigsViewModel(repository, testDispatcher)
        vm.onExportRequested()
        vm.confirmExportSelection()
        vm.writeExportToUri(context, android.net.Uri.parse("content://x/y"))
        assertEquals("Export failed", vm.state.value.message)
        assertNull(vm.state.value.pendingExportJson)
        assertNull(vm.state.value.pendingExportCount)
    }

    @Test
    fun `onImportUri parses and sets importReview with insert and overwrite split`() {
        val context = mockk<Context>()
        val json = ConfigSerializer.toJson(
            listOf(
                config.copy(name = "New Bank"),
                config,
            ),
        )
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream(json.toByteArray()))
        val vm = BankConfigsViewModel(repository, testDispatcher)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        val review = vm.state.value.importReview
        assertNotNull(review)
        assertEquals(1, review!!.toInsert.size)
        assertEquals("New Bank", review.toInsert[0].name)
        assertEquals(1, review.toOverwrite.size)
        assertEquals("Test Bank", review.toOverwrite[0].first.name)
    }

    @Test
    fun `onImportUri with malformed json sets message and no review`() {
        val context = mockk<Context>()
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream("{bad".toByteArray()))
        val vm = BankConfigsViewModel(repository, testDispatcher)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        assertNull(vm.state.value.importReview)
        assertNotNull(vm.state.value.message)
    }

    @Test
    fun `confirmImport inserts new and updates overwrite rows then clears review`() {
        coJustRun { repository.insert(any()) }
        coEvery { repository.update(any()) } returns Unit
        val context = mockk<Context>()
        val json = ConfigSerializer.toJson(
            listOf(
                config.copy(name = "New Bank"),
                config,
            ),
        )
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream(json.toByteArray()))
        val vm = BankConfigsViewModel(repository, testDispatcher)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        vm.confirmImport()
        coVerify(exactly = 1) { repository.insert(any()) }
        coVerify(exactly = 1) { repository.update(any()) }
        assertNull(vm.state.value.importReview)
    }

    @Test
    fun `cancelImport clears review with no repository writes`() {
        coJustRun { repository.insert(any()) }
        coEvery { repository.update(any()) } returns Unit
        val context = mockk<Context>()
        val json = ConfigSerializer.toJson(
            listOf(
                config.copy(name = "New Bank"),
                config,
            ),
        )
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream(json.toByteArray()))
        val vm = BankConfigsViewModel(repository, testDispatcher)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        vm.cancelImport()
        assertNull(vm.state.value.importReview)
        coVerify(exactly = 0) { repository.insert(any()) }
        coVerify(exactly = 0) { repository.update(any()) }
    }
}
