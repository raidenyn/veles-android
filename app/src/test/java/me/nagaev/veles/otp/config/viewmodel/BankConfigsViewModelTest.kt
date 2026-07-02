package me.nagaev.veles.otp.config.viewmodel

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository
import org.junit.After
import org.junit.Before
import org.junit.Test

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

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        coEvery { repository.getAllSuspend() } returns listOf(config)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
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
}
