package me.nagaev.veles.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.launch

class PermissionsViewModel(
    private val get: GetState<PermissionsState>,
    private val update: UpdateState<PermissionsState>
): ViewModel() {
    fun request(type: PermissionType) {
        get().permissions[type]?.let {

        }
    }

    init {
        updatePermissions()
    }

    private fun updatePermissions() {
        viewModelScope.launch {

        }
    }

}