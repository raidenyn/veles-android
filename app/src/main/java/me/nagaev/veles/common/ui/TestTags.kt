package me.nagaev.veles.common.ui

import me.nagaev.veles.permissions.services.PermissionType

class TestTags {
    companion object {
        const val NOTIFICATION_LISTENER_STATUS = "notification_listener_status"
        val PERMISSION_STATUS = {
                state: PermissionType ->
                "permission_status_${state}"
        }
    }
}