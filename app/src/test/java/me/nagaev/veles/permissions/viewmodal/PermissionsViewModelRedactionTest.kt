package me.nagaev.veles.permissions.viewmodal

import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PermissionsViewModelRedactionTest {
    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        RedactionStateFlow.current.value = RedactionState.Unknown
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun viewModel(
        provider: PermissionsProvider = mockk(relaxed = true),
        prefs: NotificationStatePreferences = mockk(relaxed = true),
        sender: TestNotificationSender = mockk(relaxed = true),
        path: NotificationRedactionPath = NotificationRedactionPath.StockAndroid,
        componentName: android.content.ComponentName =
            android.content.ComponentName("me.nagaev.veles", "me.nagaev.veles.otp.NotificationListener"),
        openSettings: (android.content.Intent) -> Unit = {},
    ): PermissionsViewModel = PermissionsViewModel(provider, prefs, sender, path, componentName, openSettings)

    @Test
    fun `uiState reflects Unknown redaction state initially`() {
        val vm = viewModel()
        assertEquals(RedactionState.Unknown, vm.uiState.value.redactionState)
    }

    @Test
    fun `uiState reflects Hidden redaction state when flow updates`() {
        val vm = viewModel()
        RedactionStateFlow.current.value = RedactionState.Hidden
        assertEquals(RedactionState.Hidden, vm.uiState.value.redactionState)
    }

    @Test
    fun `testSensitiveReading calls postSecretProbe`() {
        val sender = mockk<TestNotificationSender>(relaxed = true)
        val vm = viewModel(sender = sender)
        vm.testSensitiveReading()
        verify { sender.postSecretProbe() }
    }
}
