package me.nagaev.veles.permissions.viewmodal

import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.common.TestResultFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.AssociationOutcome
import me.nagaev.veles.permissions.services.PermissionProvider
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.permissions.services.SensitiveNotificationPermissionProvider
import me.nagaev.veles.permissions.services.SensitiveNotificationsGrant
import me.nagaev.veles.permissions.services.SensitiveNotificationsStatus
import me.nagaev.veles.testing.TestNotificationSender

interface PermissionsActions {
    val requestPermission: RequestPermission
    val revokePermission: RevokePermission
    val openRedactionSettings: () -> Unit
    val openEnhancedNotificationsSettings: () -> Unit
    val verifySensitiveAccess: () -> Unit
    val openAppInfo: () -> Unit

    companion object {
        val Mocked: PermissionsActions =
            object : PermissionsActions {
                override val requestPermission: RequestPermission = {}
                override val revokePermission: RevokePermission = {}
                override val openRedactionSettings: () -> Unit = {}
                override val openEnhancedNotificationsSettings: () -> Unit = {}
                override val verifySensitiveAccess: () -> Unit = {}
                override val openAppInfo: () -> Unit = {}
            }
    }
}

typealias RequestPermission = (type: PermissionType) -> Unit
typealias RevokePermission = (type: PermissionType) -> Unit

@HiltViewModel(assistedFactory = PermissionsViewModel.Factory::class)
@Suppress("LongParameterList")
class PermissionsViewModel @AssistedInject constructor(
    private val notificationStatePreferences: NotificationStatePreferences,
    private val redactionPath: NotificationRedactionPath,
    private val componentName: ComponentName,
    private val redactionStateFlow: RedactionStateFlow,
    private val sensitiveStatus: SensitiveNotificationsStatus,
    private val testNotificationSender: TestNotificationSender,
    private val testResultFlow: TestResultFlow,
    @Assisted private val permissionsProvider: PermissionsProvider,
    @Assisted private val openSettings: (Intent) -> Unit,
    @Assisted private val rebindListener: () -> Unit,
) : ViewModel(),
    PermissionsActions {
    companion object {
        private const val VERIFY_TIMEOUT_MS = 5_000L
        private const val GRANT_POLL_INTERVAL_MS = 1_000L
        private const val GRANT_POLL_TIMEOUT_MS = 60_000L
        private const val REBIND_TIMEOUT_MS = 10_000L
    }

    private val _uiState = MutableStateFlow(PermissionsState.Init)
    val uiState: StateFlow<PermissionsState> = _uiState
    private var isVerifying = false
    private var isApplyingGrant = false
    private var grantApplicationTimedOut = false

    @AssistedFactory
    interface Factory {
        fun create(
            permissionsProvider: PermissionsProvider,
            openSettings: (Intent) -> Unit,
            rebindListener: () -> Unit,
        ): PermissionsViewModel
    }

    init {
        updatePermissionsState()
        viewModelScope.launch {
            redactionStateFlow.current.collect { updatePermissionsState() }
        }
    }

    fun updatePermissionsState() {
        val provider = sensitiveProvider()
        val outcome = provider?.lastOutcome
        val grant = sensitiveStatus.check()
        _uiState.value =
            uiState.value.copy(
                permissions =
                permissionsProvider.providers.entries.associate {
                    it.key to Permission(it.key, it.value.isGranted())
                },
                notificationListenerEnabled = notificationStatePreferences.getConnectionState(),
                sensitiveNotifications =
                when {
                    isVerifying -> SensitiveNotificationsUiState.Verifying
                    isApplyingGrant -> SensitiveNotificationsUiState.ApplyingGrant
                    grantApplicationTimedOut && grant is SensitiveNotificationsGrant.NotGranted ->
                        SensitiveNotificationsUiState.Unknown
                    else -> mergedSensitiveState(grant, redactionStateFlow.current.value)
                },
                cdmSupported = provider?.cdmSupported ?: false,
                showOnePlusAdbPreStep = redactionPath is NotificationRedactionPath.OxygenOS,
                redactionSettingsLocation = redactionPath.settingsLocation,
                revealSensitiveFallbacks =
                outcome is AssociationOutcome.Cancelled ||
                    outcome is AssociationOutcome.Failed ||
                    outcome is AssociationOutcome.Unsupported,
                showForceStopButton =
                grantApplicationTimedOut && grant is SensitiveNotificationsGrant.NotGranted,
            )
    }

    private fun sensitiveProvider(): SensitiveNotificationPermissionProvider? = permissionsProvider.providers[PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS]
        as? SensitiveNotificationPermissionProvider

    private fun mergedSensitiveState(
        grant: SensitiveNotificationsGrant,
        redaction: RedactionState,
    ): SensitiveNotificationsUiState {
        val hasAssociation = sensitiveProvider()?.hasAssociation == true
        return when (grant) {
            SensitiveNotificationsGrant.NotApplicable -> SensitiveNotificationsUiState.NotApplicable
            SensitiveNotificationsGrant.NotGranted ->
                if (hasAssociation) {
                    SensitiveNotificationsUiState.ApplyingGrant
                } else {
                    SensitiveNotificationsUiState.NotGranted
                }
            is SensitiveNotificationsGrant.Granted ->
                if (redaction == RedactionState.Hidden) {
                    SensitiveNotificationsUiState.GrantedButRedacted
                } else {
                    SensitiveNotificationsUiState.Granted
                }
        }
    }

    private fun unsetPermissionState(type: PermissionType) {
        _uiState.value =
            uiState.value.copy(
                permissions =
                uiState.value.permissions.toMutableMap().also {
                    it[type] = Permission(type, null)
                },
            )
    }

    private fun execute(
        type: PermissionType,
        method: suspend (PermissionProvider) -> Unit,
    ) {
        permissionsProvider.providers[type]?.let {
            unsetPermissionState(type)
            viewModelScope.launch {
                method(it)
                updatePermissionsState()
            }
        }
    }

    override val requestPermission: RequestPermission = { type ->
        execute(type) { provider ->
            if (type == PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS) {
                grantApplicationTimedOut = false
                _uiState.value = uiState.value.copy(showForceStopButton = false)
            }
            provider.request()
            if (type == PermissionType.RECEIVE_SENSITIVE_NOTIFICATIONS &&
                (provider as? SensitiveNotificationPermissionProvider)?.lastOutcome is AssociationOutcome.Associated
            ) {
                applyGrantAfterAssociation()
            }
        }
    }

    override val revokePermission: RevokePermission = { type ->
        execute(type) { provider -> provider.revoke() }
    }

    override val openRedactionSettings: () -> Unit = {
        openSettings(redactionPath.settingsIntent(componentName))
    }

    override val openEnhancedNotificationsSettings: () -> Unit = {
        openSettings(Intent(Settings.ACTION_NOTIFICATION_ASSISTANT_SETTINGS))
    }

    override val openAppInfo: () -> Unit = {
        openSettings(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.fromParts("package", componentName.packageName, null)),
        )
    }

    private fun applyGrantAfterAssociation() {
        if (isApplyingGrant) return
        isApplyingGrant = true
        grantApplicationTimedOut = false
        _uiState.value =
            uiState.value.copy(
                sensitiveNotifications = SensitiveNotificationsUiState.ApplyingGrant,
                showForceStopButton = false,
            )
        viewModelScope.launch {
            try {
                val granted =
                    withTimeoutOrNull(GRANT_POLL_TIMEOUT_MS) {
                        while (true) {
                            if (sensitiveStatus.check() is SensitiveNotificationsGrant.Granted) {
                                return@withTimeoutOrNull true
                            }
                            delay(GRANT_POLL_INTERVAL_MS)
                        }
                    } == true
                if (!granted) {
                    grantApplicationTimedOut = true
                    _uiState.value =
                        uiState.value.copy(
                            sensitiveNotifications = SensitiveNotificationsUiState.Unknown,
                            showForceStopButton = true,
                        )
                    return@launch
                }

                val rebound = awaitListenerRebind()
                if (rebound) {
                    verifySensitiveAccess()
                } else {
                    grantApplicationTimedOut = true
                    _uiState.value =
                        uiState.value.copy(
                            sensitiveNotifications = SensitiveNotificationsUiState.Unknown,
                            showForceStopButton = true,
                        )
                }
            } finally {
                isApplyingGrant = false
            }
        }
    }

    private suspend fun awaitListenerRebind(): Boolean = coroutineScope {
        val disconnected =
            async(start = CoroutineStart.UNDISPATCHED) {
                notificationStatePreferences.currentConnectionState
                    .filter { !it }
                    .first()
            }
        rebindListener()
        val didDisconnect = withTimeoutOrNull(REBIND_TIMEOUT_MS) { disconnected.await() } != null
        if (!didDisconnect) return@coroutineScope false
        withTimeoutOrNull(REBIND_TIMEOUT_MS) {
            notificationStatePreferences.currentConnectionState
                .filter { it }
                .first()
            true
        } == true
    }

    override val verifySensitiveAccess: () -> Unit = {
        if (!isVerifying) {
            isVerifying = true
            _uiState.value = uiState.value.copy(sensitiveNotifications = SensitiveNotificationsUiState.Verifying)
            viewModelScope.launch {
                try {
                    testResultFlow.current.value = null
                    val sent = testNotificationSender.postProbe()
                    val received =
                        withTimeoutOrNull(VERIFY_TIMEOUT_MS) {
                            testResultFlow.current.filterNotNull().first()
                        }
                    testNotificationSender.cancelProbe()
                    testResultFlow.current.value = null
                    isVerifying = false
                    when {
                        received == null ->
                            _uiState.value =
                                uiState.value.copy(sensitiveNotifications = SensitiveNotificationsUiState.Unknown)
                        received.receivedText == sent -> {
                            redactionStateFlow.current.value = RedactionState.Readable
                            updatePermissionsState()
                        }
                        else -> {
                            redactionStateFlow.current.value = RedactionState.Hidden
                            updatePermissionsState()
                        }
                    }
                } finally {
                    testNotificationSender.cancelProbe()
                    testResultFlow.current.value = null
                    isVerifying = false
                }
            }
        }
    }
}
