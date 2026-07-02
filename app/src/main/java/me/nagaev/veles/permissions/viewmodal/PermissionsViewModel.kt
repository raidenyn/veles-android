package me.nagaev.veles.permissions.viewmodal

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.PermissionProvider
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import me.nagaev.veles.testing.TestNotificationSender

interface PermissionsActions {
    val requestPermission: RequestPermission
    val revokePermission: RevokePermission
    val openRedactionSettings: () -> Unit
    val testSensitiveReading: () -> Unit

    companion object {
        val Mocked: PermissionsActions =
            object : PermissionsActions {
                override val requestPermission: RequestPermission = {}
                override val revokePermission: RevokePermission = {}
                override val openRedactionSettings: () -> Unit = {}
                override val testSensitiveReading: () -> Unit = {}
            }
    }
}

typealias RequestPermission = (type: PermissionType) -> Unit
typealias RevokePermission = (type: PermissionType) -> Unit

class PermissionsViewModel(
    private val permissionsProvider: PermissionsProvider,
    private val notificationStatePreferences: NotificationStatePreferences,
    private val testNotificationSender: TestNotificationSender,
    private val redactionPath: NotificationRedactionPath,
    private val componentName: android.content.ComponentName,
    private val openSettings: (android.content.Intent) -> Unit,
) : ViewModel(),
    PermissionsActions {
    private val _uiState = MutableStateFlow(PermissionsState.Init)
    val uiState: StateFlow<PermissionsState> = _uiState

    init {
        updatePermissionsState()
        viewModelScope.launch {
            RedactionStateFlow.current.collect { state ->
                _uiState.value =
                    _uiState.value.copy(
                        redactionState = state,
                        redactionSettingsLocation = redactionPath.settingsLocation,
                    )
            }
        }
    }

    fun updatePermissionsState() {
        _uiState.value =
            uiState.value.copy(
                permissions =
                permissionsProvider.providers.entries.associate {
                    it.key to Permission(it.key, it.value.isGranted())
                },
                notificationListenerEnabled = notificationStatePreferences.getConnectionState(),
                redactionSettingsLocation = redactionPath.settingsLocation,
            )
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
        execute(type) { provider -> provider.request() }
    }

    override val revokePermission: RevokePermission = { type ->
        execute(type) { provider -> provider.revoke() }
    }

    override val openRedactionSettings: () -> Unit = {
        openSettings(redactionPath.settingsIntent(componentName))
    }

    override val testSensitiveReading: () -> Unit = {
        testNotificationSender.postSecretProbe()
    }
}
