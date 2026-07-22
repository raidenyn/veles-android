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
import me.nagaev.veles.R
import me.nagaev.veles.common.UiText
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class BankConfigEditViewModelTest {

    private val repository = mockk<BankHandlerRepository>()
    private val existingConfig = BankHandlerConfig(
        id = 42L,
        name = "Test Bank",
        otpRegex = """(\w+)-(\d{6})""",
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
        assert(state.otpRegex == """(\w+)-(\d{6})""")
        assert(state.moneyRegex == """([A-Z]{3})(\d+)""")
        assert(state.merchantRegex == """at (.+)""")
        assert(state.originalCreatedAt == 1000L)
    }

    @Test
    fun `save with blank name sets nameError`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onOtpRegexChanged("""(\w+)-(\d{6})""")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")
        vm.save()
        assertEquals(UiText.Res(R.string.bank_config_edit_name_required), vm.state.value.nameError)
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
        assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.otpRegexError)
        assert(!vm.state.value.savedSuccessfully)
    }

    @Test
    fun `save with blank regex fields sets errors`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.save()
        assertEquals(UiText.Res(R.string.bank_config_edit_required), vm.state.value.otpRegexError)
        assertEquals(UiText.Res(R.string.bank_config_edit_required), vm.state.value.moneyRegexError)
        assertEquals(UiText.Res(R.string.bank_config_edit_required), vm.state.value.merchantRegexError)
    }

    @Test
    fun `save valid new config calls insert and sets savedSuccessfully`() {
        coEvery { repository.insert(any()) } returns 1L
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("""(\w+)-(\d{6})""")
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

    @Test
    fun `save rejects OTP regex with fewer than two groups`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("""(\d{6})""")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")

        vm.save()

        assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.otpRegexError)
        coVerify(exactly = 0) { repository.insert(any()) }
    }

    @Test
    fun `save rejects money regex with fewer than two groups`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("""(\w+)-(\d{6})""")
        vm.onMoneyRegexChanged("""([A-Z]{3})\d+""")
        vm.onMerchantRegexChanged("""at (.+)""")

        vm.save()

        assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.moneyRegexError)
        coVerify(exactly = 0) { repository.insert(any()) }
    }

    @Test
    fun `save rejects merchant regex without a group`() {
        val vm = BankConfigEditViewModel(SavedStateHandle(), repository)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("""(\w+)-(\d{6})""")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at .+""")

        vm.save()

        assertEquals(UiText.Res(R.string.bank_config_edit_invalid_regex), vm.state.value.merchantRegexError)
        coVerify(exactly = 0) { repository.insert(any()) }
    }
}
