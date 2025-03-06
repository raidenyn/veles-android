package me.nagaev.veles.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import me.nagaev.veles.services.PermissionProvider
import me.nagaev.veles.services.PermissionType
import me.nagaev.veles.services.PermissionsProvider
import kotlinx.coroutines.launch

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
    private val viewModelScope: CoroutineScope,
    private val state: GetState<PermissionsState>,
    private val update: UpdateState<PermissionsState>,
    private val permissionsProvider: PermissionsProvider
): PermissionsActions {
    init {
        updatePermissionsState()
    }

    private fun updatePermissionsState() {
        update(
            state().copy(
                permissions = permissionsProvider.providers.entries.associate {
                    it.key to Permission(it.key, it.value.isGranted())
                }
            )
        )
    }

    private fun unsetPermissionState(type: PermissionType) {
        update(
            state().let { it ->
                it.copy(
                    permissions = it.permissions.toMutableMap().also {
                        it[type] = Permission(type, null)
                    }
                )
            }
        )
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
        execute(type, { provider -> provider.request() })
    }

    override val revokePermission: RevokePermission = { type ->
        execute(type, { provider -> provider.revoke() })
    }
}