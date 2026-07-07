package me.nagaev.veles.permissions.viewmodal

import android.content.ComponentName
import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import me.nagaev.veles.common.NotificationStatePreferences
import me.nagaev.veles.common.RedactionStateFlow
import me.nagaev.veles.otp.NotificationRedactionPath
import me.nagaev.veles.permissions.services.PermissionProvider
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider

interface PermissionsActions {
    val requestPermission: RequestPermission
    val revokePermission: RevokePermission
    val openRedactionSettings: () -> Unit

    companion object {
        val Mocked: PermissionsActions =
            object : PermissionsActions {
                override val requestPermission: RequestPermission = {}
                override val revokePermission: RevokePermission = {}
                override val openRedactionSettings: () -> Unit = {}
            }
    }
}

typealias RequestPermission = (type: PermissionType) -> Unit
typealias RevokePermission = (type: PermissionType) -> Unit

@HiltViewModel(assistedFactory = PermissionsViewModel.Factory::class)
class PermissionsViewModel @AssistedInject constructor(
    private val notificationStatePreferences: NotificationStatePreferences,
    private val redactionPath: NotificationRedactionPath,
    private val componentName: ComponentName,
    @Assisted private val permissionsProvider: PermissionsProvider,
    @Assisted private val openSettings: (Intent) -> Unit,
) : ViewModel(),
    PermissionsActions {
    private val _uiState = MutableStateFlow(PermissionsState.Init)
    val uiState: StateFlow<PermissionsState> = _uiState

    @AssistedFactory
    interface Factory {
        fun create(
            permissionsProvider: PermissionsProvider,
            openSettings: (Intent) -> Unit,
        ): PermissionsViewModel
    }

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
}
