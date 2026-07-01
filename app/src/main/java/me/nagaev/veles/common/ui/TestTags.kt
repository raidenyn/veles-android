package me.nagaev.veles.common.ui

import me.nagaev.veles.permissions.services.PermissionType

class TestTags {
    companion object {
        const val NOTIFICATION_LISTENER_STATUS = "notification_listener_status"
        const val TEST_INPUT = "test_input"
        const val TEST_SEND_BUTTON = "test_send_button"
        const val TEST_RESULT = "test_result"
        const val REDACTION_STATUS = "redaction_status"
        const val REDACTION_OPEN_SETTINGS = "redaction_open_settings"
        const val REDACTION_TEST_BUTTON = "redaction_test_button"
        val PERMISSION_STATUS = { state: PermissionType -> "permission_status_${state}" }
    }
}