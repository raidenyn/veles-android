package me.nagaev.veles.common.ui

import me.nagaev.veles.permissions.services.PermissionType

object TestTags {
    const val NOTIFICATION_LISTENER_STATUS = "notification_listener_status"
    const val TEST_INPUT = "test_input"
    const val TEST_SEND_BUTTON = "test_send_button"
    const val TEST_RESULT = "test_result"
    const val TEST_RESULT_OTP = "test_result_otp"
    const val TEST_RESULT_REDACTION_HINT = "test_result_redaction_hint"
    const val TEST_RESULT_RECEIVED_TEXT = "test_result_received_text"
    const val TEST_LOG_RAW_CONTENT_SWITCH = "test_log_raw_content_switch"
    const val REDACTION_OPEN_SETTINGS = "redaction_open_settings"
    val PERMISSION_STATUS = { state: PermissionType -> "permission_status_$state" }

    const val BANK_CONFIG_EXPORT_BUTTON = "bank_config_export_button"
    const val BANK_CONFIG_IMPORT_BUTTON = "bank_config_import_button"
    const val BANK_CONFIG_EXPORT_DIALOG = "bank_config_export_dialog"
    const val BANK_CONFIG_IMPORT_DIALOG = "bank_config_import_dialog"
    const val BANK_CONFIG_IMPORT_CONFIRM = "bank_config_import_confirm"
    const val BANK_CONFIG_IMPORT_CANCEL = "bank_config_import_cancel"
    const val BANK_CONFIG_EXPORT_CONFIRM = "bank_config_export_confirm"
    const val BOTTOM_NAV_BAR = "bottom_nav_bar"
    val BOTTOM_NAV_ITEM = { route: String -> "bottom_nav_item_$route" }
    const val BANK_CONFIG_ADD_FAB = "bank_config_add_fab"
}
