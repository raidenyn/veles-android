package me.nagaev.veles.otp.config.viewmodel

import androidx.lifecycle.SavedStateHandle
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
class BankConfigEditViewModelTest {

    private val repository = mockk<BankHandlerRepository>()
    private val existingConfig = BankHandlerConfig(
        id = 42L,
        name = "Test Bank",
        otpRegex = """\d{6}""",
        moneyRegex = """([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+)""",
        createdAt = 1000L,
        updatedAt = 2000L,
    )

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `new config starts with empty fields`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        val state = vm.state.value
        assert(state.name == "")
        assert(state.otpRegex == "")
        assert(state.moneyRegex == "")
        assert(state.merchantRegex == "")
        assert(!state.savedSuccessfully)
    }

    @Test
    fun `existing config loads fields on init`() {
        coEvery { repository.getAllSuspend() } returns listOf(existingConfig)
        val vm = BankConfigEditViewModel(SavedStateHandle(mapOf("id" to 42L)), repository)
        val state = vm.state.value
        assert(state.name == "Test Bank")
        assert(state.otpRegex == """\d{6}""")
        assert(state.moneyRegex == """([A-Z]{3})(\d+)""")
        assert(state.merchantRegex == """at (.+)""")
        assert(state.originalCreatedAt == 1000L)
    }

    @Test
    fun `save with blank name sets nameError`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onOtpRegexChanged("""\d{6}""")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")
        vm.save()
        assert(vm.state.value.nameError != null)
        assert(!vm.state.value.savedSuccessfully)
    }

    @Test
    fun `save with invalid OTP regex sets otpRegexError`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("[invalid")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")
        vm.save()
        assert(vm.state.value.otpRegexError != null)
        assert(!vm.state.value.savedSuccessfully)
    }

    @Test
    fun `save with blank regex fields sets errors`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.save()
        assert(vm.state.value.otpRegexError != null)
        assert(vm.state.value.moneyRegexError != null)
        assert(vm.state.value.merchantRegexError != null)
    }

    @Test
    fun `save valid new config calls insert and sets savedSuccessfully`() {
        coEvery { repository.insert(any()) } returns 1L
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("""\d{6}""")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")
        vm.save()
        coVerify { repository.insert(any()) }
        assert(vm.state.value.savedSuccessfully)
    }

    @Test
    fun `save valid existing config calls update and sets savedSuccessfully`() {
        coEvery { repository.getAllSuspend() } returns listOf(existingConfig)
        coEvery { repository.update(any()) } returns Unit
        val vm = BankConfigEditViewModel(SavedStateHandle(mapOf("id" to 42L)), repository)
        vm.save()
        coVerify { repository.update(any()) }
        assert(vm.state.value.savedSuccessfully)
    }

    @Test
    fun `changing field clears its error`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.save()
        assert(vm.state.value.nameError != null)
        vm.onNameChanged("My Bank")
        assert(vm.state.value.nameError == null)
    }
}
