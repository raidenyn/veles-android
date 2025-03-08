package me.nagaev.veles.permissions.viewmodal

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import me.nagaev.veles.permissions.services.PermissionProvider
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.services.PermissionsProvider
import kotlinx.coroutines.launch
import me.nagaev.veles.common.NotificationStatePreferences

interface PermissionsActions {
    val requestPermission: RequestPermission
    val revokePermission: RevokePermission

    companion object {
        val Mocked: PermissionsActions = object : PermissionsActions {
            override val requestPermission: RequestPermission = {}
            override val revokePermission: RevokePermission = {}
        }
    }
}

typealias RequestPermission = (type: PermissionType) -> Unit

typealias RevokePermission = (type: PermissionType) -> Unit

class PermissionsViewModel(
    private val permissionsProvider: PermissionsProvider,
    private val notificationStatePreferences: NotificationStatePreferences,
): ViewModel(), PermissionsActions {
    private val _uiState = MutableStateFlow(PermissionsState.Init)
    val uiState: StateFlow<PermissionsState> = _uiState

    init {
        updatePermissionsState()
    }

     fun updatePermissionsState() {
         _uiState.value =
            uiState.value.copy(
                permissions = permissionsProvider.providers.entries.associate {
                    it.key to Permission(it.key, it.value.isGranted())
                },
                notificationListenerEnabled = notificationStatePreferences.getConnectionState()
            )
    }

    private fun unsetPermissionState(type: PermissionType) {
        _uiState.value =
            uiState.value.let { it ->
                it.copy(
                    permissions = it.permissions.toMutableMap().also {
                        it[type] = Permission(type, null)
                    }
                )
            }
    }

    private fun execute(type: PermissionType, method: suspend (PermissionProvider) -> Unit) {
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
}