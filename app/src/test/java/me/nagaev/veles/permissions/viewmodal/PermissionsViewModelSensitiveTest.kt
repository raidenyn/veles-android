package me.nagaev.veles.permissions.viewmodal

import android.content.ComponentName
import android.content.Intent
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResult
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.otp.handlers.MessageHandlingResult
import me.nagaev.veles.permissions.services.AssociationOutcome
import me.nagaev.veles.permissions.services.CompanionAssociationService
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.permissions.services.PermissionsProviderImpl
import me.nagaev.veles.permissions.services.SensitiveNotificationPermissionProvider
import me.nagaev.veles.permissions.services.SensitiveNotificationsGrant
import me.nagaev.veles.permissions.services.SensitiveNotificationsStatus
import me.nagaev.veles.testing.TestNotificationSender
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class PermissionsViewModelSensitiveTest {
    private val status = mockk<SensitiveNotificationsStatus>()
    private val sender = mockk<TestNotificationSender>(relaxed = true)
    private val testResultFlow = TestResultFlow()
    private val redactionStateFlow = RedactionStateFlow()

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun viewModel(
        permissionsProvider: PermissionsProvider = mockk(relaxed = true),
        rebindListener: () -> Unit = {},
        notificationStatePreferences: NotificationStatePreferences = mockk(relaxed = true),
    ): PermissionsViewModel = PermissionsViewModel(
        notificationStatePreferences,
        NotificationRedactionPath.StockAndroid,
        ComponentName("me.nagaev.veles", "me.nagaev.veles.otp.NotificationListener"),
        redactionStateFlow,
        status,
        sender,
        testResultFlow,
        permissionsProvider,
        { _: Intent -> },
        rebindListener,
    )

    private fun testResult(text: String) = TestResult(
        handlingResult = MessageHandlingResult.FILTERED,
        receivedText = text,
        receivedTitle = "Veles Test",
        sourcePackage = "me.nagaev.veles",
        timestamp = 1L,
    )

    @Test
    fun `NotApplicable below API 35`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns SensitiveNotificationsGrant.NotApplicable
        assertEquals(SensitiveNotificationsUiState.NotApplicable, viewModel().uiState.value.sensitiveNotifications)
    }

    @Test
    fun `NotGranted when static check says no grant`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns SensitiveNotificationsGrant.NotGranted
        assertEquals(SensitiveNotificationsUiState.NotGranted, viewModel().uiState.value.sensitiveNotifications)
    }

    @Test
    fun `Granted when granted and no redaction observed`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.AppOp)
        assertEquals(SensitiveNotificationsUiState.Granted, viewModel().uiState.value.sensitiveNotifications)
    }

    @Test
    fun `GrantedButRedacted when granted but redaction observed`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        val vm = viewModel()
        redactionStateFlow.current.value = RedactionState.Hidden
        assertEquals(SensitiveNotificationsUiState.GrantedButRedacted, vm.uiState.value.sensitiveNotifications)
    }

    @Test
    fun `verification match sets Readable and Granted, cleans up probe`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        vm.verifySensitiveAccess()
        assertEquals(SensitiveNotificationsUiState.Verifying, vm.uiState.value.sensitiveNotifications)

        testResultFlow.current.value = testResult("Veles check: code 835201")

        assertEquals(RedactionState.Readable, redactionStateFlow.current.value)
        assertEquals(SensitiveNotificationsUiState.Granted, vm.uiState.value.sensitiveNotifications)
        assertNull(testResultFlow.current.value)
        verify { sender.cancelProbe() }
    }

    @Test
    fun `verification mismatch sets Hidden and GrantedButRedacted`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        vm.verifySensitiveAccess()
        testResultFlow.current.value = testResult("Sensitive notification content hidden")

        assertEquals(RedactionState.Hidden, redactionStateFlow.current.value)
        assertEquals(SensitiveNotificationsUiState.GrantedButRedacted, vm.uiState.value.sensitiveNotifications)
    }

    @Test
    fun `verification timeout sets Unknown`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        vm.verifySensitiveAccess()
        advanceTimeBy(6_000)

        assertEquals(SensitiveNotificationsUiState.Unknown, vm.uiState.value.sensitiveNotifications)
        verify { sender.cancelProbe() }
    }

    @Test
    fun `verification mismatch does not stick on Verifying when redaction flow already Hidden`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        redactionStateFlow.current.value = RedactionState.Hidden
        assertEquals(SensitiveNotificationsUiState.GrantedButRedacted, vm.uiState.value.sensitiveNotifications)

        vm.verifySensitiveAccess()
        assertEquals(SensitiveNotificationsUiState.Verifying, vm.uiState.value.sensitiveNotifications)

        testResultFlow.current.value = testResult("Sensitive notification content hidden")

        assertEquals(RedactionState.Hidden, redactionStateFlow.current.value)
        assertEquals(SensitiveNotificationsUiState.GrantedButRedacted, vm.uiState.value.sensitiveNotifications)
    }

    @Test
    fun `verification match does not stick on Verifying when redaction flow already Readable`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        every { status.check() } returns
            SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel()

        redactionStateFlow.current.value = RedactionState.Readable
        assertEquals(SensitiveNotificationsUiState.Granted, vm.uiState.value.sensitiveNotifications)

        vm.verifySensitiveAccess()
        assertEquals(SensitiveNotificationsUiState.Verifying, vm.uiState.value.sensitiveNotifications)

        testResultFlow.current.value = testResult("Veles check: code 835201")

        assertEquals(RedactionState.Readable, redactionStateFlow.current.value)
        assertEquals(SensitiveNotificationsUiState.Granted, vm.uiState.value.sensitiveNotifications)
    }

    @Test
    fun `association waits for role grant before rebinding listener`() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        var grant: SensitiveNotificationsGrant = SensitiveNotificationsGrant.NotGranted
        every { status.check() } answers { grant }
        val association = mockk<CompanionAssociationService>(relaxed = true) {
            every { isSupported() } returns true
            every { hasAssociation() } returns true
        }
        coEvery { association.associate() } returns AssociationOutcome.Associated
        val sensitiveProvider = SensitiveNotificationPermissionProvider(status, association)
        val permissionsProvider = PermissionsProviderImpl(
            mapOf(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS to sensitiveProvider),
        )
        val rebindListener = mockk<() -> Unit>(relaxed = true)
        val connectionState = MutableStateFlow(true)
        val notificationStatePreferences = mockk<NotificationStatePreferences>(relaxed = true) {
            every { getConnectionState() } answers { connectionState.value }
            every { currentConnectionState } returns connectionState
        }
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel(permissionsProvider, rebindListener, notificationStatePreferences)

        vm.requestPermission(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS)
        runCurrent()

        assertEquals(SensitiveNotificationsUiState.ApplyingGrant, vm.uiState.value.sensitiveNotifications)
        verify(exactly = 0) { rebindListener() }

        grant = SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        advanceTimeBy(1_000)
        runCurrent()

        verify { rebindListener() }
        connectionState.value = false
        runCurrent()
        connectionState.value = true
        runCurrent()
        testResultFlow.current.value = testResult("Veles check: code 835201")
        runCurrent()
    }

    @Test
    fun `grant application timeout exposes Force stop fallback`() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        every { status.check() } returns SensitiveNotificationsGrant.NotGranted
        val association = mockk<CompanionAssociationService>(relaxed = true) {
            every { isSupported() } returns true
            every { hasAssociation() } returns true
        }
        coEvery { association.associate() } returns AssociationOutcome.Associated
        val sensitiveProvider = SensitiveNotificationPermissionProvider(status, association)
        val permissionsProvider = PermissionsProviderImpl(
            mapOf(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS to sensitiveProvider),
        )
        val vm = viewModel(permissionsProvider)

        vm.requestPermission(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS)
        runCurrent()
        advanceTimeBy(60_001)
        runCurrent()

        assertEquals(SensitiveNotificationsUiState.Unknown, vm.uiState.value.sensitiveNotifications)
        assertEquals(true, vm.uiState.value.showForceStopButton)

        vm.updatePermissionsState()

        assertEquals(SensitiveNotificationsUiState.Unknown, vm.uiState.value.sensitiveNotifications)
        assertEquals(true, vm.uiState.value.showForceStopButton)
    }

    @Test
    fun `grant application verifies after listener reconnects`() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        var grant: SensitiveNotificationsGrant = SensitiveNotificationsGrant.NotGranted
        every { status.check() } answers { grant }
        val association = mockk<CompanionAssociationService>(relaxed = true) {
            every { isSupported() } returns true
            every { hasAssociation() } returns true
        }
        coEvery { association.associate() } returns AssociationOutcome.Associated
        val sensitiveProvider = SensitiveNotificationPermissionProvider(status, association)
        val permissionsProvider = PermissionsProviderImpl(
            mapOf(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS to sensitiveProvider),
        )
        val connectionState = MutableStateFlow(true)
        val notificationStatePreferences = mockk<NotificationStatePreferences>(relaxed = true) {
            every { getConnectionState() } answers { connectionState.value }
            every { currentConnectionState } returns connectionState
        }
        every { sender.postProbe() } returns "Veles check: code 835201"
        val vm = viewModel(permissionsProvider, {}, notificationStatePreferences)

        vm.requestPermission(PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS)
        runCurrent()
        grant = SensitiveNotificationsGrant.Granted(SensitiveNotificationsGrant.Granted.Via.Role)
        advanceTimeBy(1_000)
        runCurrent()

        connectionState.value = false
        runCurrent()
        connectionState.value = true
        runCurrent()

        verify { sender.postProbe() }
    }
}
