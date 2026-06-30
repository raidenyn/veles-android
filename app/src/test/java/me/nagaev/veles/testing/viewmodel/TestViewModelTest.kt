package me.nagaev.veles.testing.viewmodel

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.common.TestInputPreferences
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Before
import org.junit.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

@OptIn(ExperimentalCoroutinesApi::class)
class TestViewModelTest {
    private val testDispatcher = UnconfinedTestDispatcher()
    private lateinit var preferences: TestInputPreferences
    private lateinit var sender: TestNotificationSender
    private lateinit var viewModel: TestViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        TestResultFlow.current.value = null
        preferences = mockk(relaxed = true)
        sender = mockk(relaxed = true)
        viewModel = TestViewModel(preferences, sender)
    }

    @After
    fun tearDown() {
        TestResultFlow.current.value = null
        Dispatchers.resetMain()
    }

    @Test
    fun `initial state has empty text when preferences returns empty string`() {
        assertEquals("", viewModel.uiState.value.inputText)
        assertNull(viewModel.uiState.value.lastResult)
    }

    @Test
    fun `initial state loads saved text from preferences`() {
        every { preferences.load() } returns "saved message"
        val vm = TestViewModel(preferences, sender)
        assertEquals("saved message", vm.uiState.value.inputText)
    }

    @Test
    fun `onTextChanged updates inputText in state`() {
        viewModel.onTextChanged("hello")
        assertEquals("hello", viewModel.uiState.value.inputText)
    }

    @Test
    fun `onTextChanged saves text to preferences`() {
        viewModel.onTextChanged("hello")
        verify { preferences.save("hello") }
    }

    @Test
    fun `send posts notification with current inputText`() {
        viewModel.onTextChanged("test message")
        viewModel.send()
        verify { sender.post("test message") }
    }

    @Test
    fun `TestResultFlow update sets lastResult in state`() {
        assertNull(viewModel.uiState.value.lastResult)
        val result = TestResult(MessageHandlingResult.ACCEPTED, 1000L)
        TestResultFlow.current.value = result
        assertEquals(result, viewModel.uiState.value.lastResult)
    }

    @Test
    fun `TestResultFlow FILTERED result sets lastResult in state`() {
        val result = TestResult(MessageHandlingResult.FILTERED, 2000L)
        TestResultFlow.current.value = result
        assertEquals(result, viewModel.uiState.value.lastResult)
    }

    @Test
    fun `onCleared resets TestResultFlow to null`() {
        TestResultFlow.current.value = TestResult(MessageHandlingResult.ACCEPTED, 1000L)
        TestViewModel::class.java.getDeclaredMethod("onCleared").also { it.isAccessible = true }.invoke(viewModel)
        assertNull(TestResultFlow.current.value)
    }
}
