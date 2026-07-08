package me.nagaev.veles.permissions.viewmodal

import io.mockk.mockk
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
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PermissionsViewModelRedactionTest {
    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun viewModel(
        provider: PermissionsProvider = mockk(relaxed = true),
        prefs: NotificationStatePreferences = mockk(relaxed = true),
        path: NotificationRedactionPath = NotificationRedactionPath.StockAndroid,
        componentName: android.content.ComponentName =
            android.content.ComponentName("me.nagaev.veles", "me.nagaev.veles.otp.NotificationListener"),
        redactionStateFlow: RedactionStateFlow = RedactionStateFlow(),
        openSettings: (android.content.Intent) -> Unit = {},
    ): PermissionsViewModel = PermissionsViewModel(prefs, path, componentName, redactionStateFlow, provider, openSettings)

    @Test
    fun `uiState reflects Unknown redaction state initially`() {
        val vm = viewModel()
        assertEquals(RedactionState.Unknown, vm.uiState.value.redactionState)
    }

    @Test
    fun `uiState reflects Hidden redaction state when flow updates`() {
        val flow = RedactionStateFlow()
        val vm = viewModel(redactionStateFlow = flow)
        flow.current.value = RedactionState.Hidden
        assertEquals(RedactionState.Hidden, vm.uiState.value.redactionState)
    }
}
