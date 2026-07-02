package me.nagaev.permissions.ui

import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.filterToOne
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onChildren
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import io.mockk.mockk
import io.mockk.verify
import me.nagaev.veles.common.RedactionState
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.ui.VelesPermissionsApp
import me.nagaev.veles.permissions.viewmodal.Permission
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState
import org.junit.Rule
import org.junit.Test

class VelesPermissionsAppTests {
    @get:Rule
    val composeTestRule = createComposeRule()

    private val permissionsState =
        PermissionsState(
            permissions =
            mapOf(
                PermissionType.SEND_NOTIFICATIONS to
                    Permission(
                        type = PermissionType.SEND_NOTIFICATIONS,
                        granted = false,
                    ),
                PermissionType.ACCESS_NOTIFICATIONS to
                    Permission(
                        type = PermissionType.ACCESS_NOTIFICATIONS,
                        granted = false,
                    ),
            ),
            notificationListenerEnabled = false,
        )
    private val permissionsActions = mockk<PermissionsActions>(relaxed = true)

    @Test
    fun `calls requestPermission on click with on non-granted state`() {
        // Start the app
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState = permissionsState,
                permissionsActions = permissionsActions,
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.PERMISSION_STATUS(PermissionType.SEND_NOTIFICATIONS))
            .performClick()

        verify {
            permissionsActions.requestPermission(
                PermissionType.SEND_NOTIFICATIONS,
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.PERMISSION_STATUS(PermissionType.ACCESS_NOTIFICATIONS))
            .performClick()

        verify {
            permissionsActions.requestPermission(
                PermissionType.ACCESS_NOTIFICATIONS,
            )
        }
    }

    @Test
    fun `calls revokePermission on click with on non-granted state`() {
        // Start the app
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    permissions =
                    mapOf(
                        PermissionType.SEND_NOTIFICATIONS to
                            Permission(
                                type = PermissionType.SEND_NOTIFICATIONS,
                                granted = true,
                            ),
                        PermissionType.ACCESS_NOTIFICATIONS to
                            Permission(
                                type = PermissionType.ACCESS_NOTIFICATIONS,
                                granted = true,
                            ),
                    ),
                ),
                permissionsActions = permissionsActions,
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.PERMISSION_STATUS(PermissionType.SEND_NOTIFICATIONS))
            .performClick()

        verify {
            permissionsActions.revokePermission(
                PermissionType.SEND_NOTIFICATIONS,
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.PERMISSION_STATUS(PermissionType.ACCESS_NOTIFICATIONS))
            .performClick()

        verify {
            permissionsActions.revokePermission(
                PermissionType.ACCESS_NOTIFICATIONS,
            )
        }
    }

    @Test
    fun `reflect granted statuses on checkboxes`() {
        // Start the app
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    permissions =
                    mapOf(
                        PermissionType.SEND_NOTIFICATIONS to
                            Permission(
                                type = PermissionType.SEND_NOTIFICATIONS,
                                granted = true,
                            ),
                        PermissionType.ACCESS_NOTIFICATIONS to
                            Permission(
                                type = PermissionType.ACCESS_NOTIFICATIONS,
                                granted = false,
                            ),
                    ),
                ),
                permissionsActions = permissionsActions,
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.PERMISSION_STATUS(PermissionType.SEND_NOTIFICATIONS))
            .onChildren()
            .filterToOne(hasClickAction())
            .assertIsOn()

        composeTestRule
            .onNodeWithTag(TestTags.PERMISSION_STATUS(PermissionType.ACCESS_NOTIFICATIONS))
            .onChildren()
            .filterToOne(hasClickAction())
            .assertIsOff()
    }

    @Test
    fun `show expected service state - true`() {
        // Start the app
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    notificationListenerEnabled = true,
                ),
                permissionsActions = permissionsActions,
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.NOTIFICATION_LISTENER_STATUS)
            .assertTextContains("enabled", substring = true)
    }

    @Test
    fun `show expected service state - false`() {
        // Start the app
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    notificationListenerEnabled = false,
                ),
                permissionsActions = permissionsActions,
            )
        }

        composeTestRule
            .onNodeWithTag(TestTags.NOTIFICATION_LISTENER_STATUS)
            .assertTextContains("disabled", substring = true)
    }

    @Test
    fun `redaction section shows collapsed readable status`() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    redactionState = RedactionState.Readable,
                    redactionSettingsLocation = "Settings > Sensitive notifications",
                ),
                permissionsActions = permissionsActions,
            )
        }
        composeTestRule
            .onNodeWithTag(TestTags.REDACTION_STATUS)
            .assertTextContains("Readable", substring = true)
    }

    @Test
    fun `redaction section shows hidden status when Hidden`() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    redactionState = RedactionState.Hidden,
                    redactionSettingsLocation = "Settings > Enhanced Notifications",
                ),
                permissionsActions = permissionsActions,
            )
        }
        composeTestRule
            .onNodeWithTag(TestTags.REDACTION_STATUS)
            .assertTextContains("off", substring = true)
        composeTestRule.onNodeWithTag(TestTags.REDACTION_OPEN_SETTINGS).assertExists()
        composeTestRule.onNodeWithTag(TestTags.REDACTION_TEST_BUTTON).assertExists()
    }

    @Test
    fun `redaction section shows unknown status initially`() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    redactionState = RedactionState.Unknown,
                    redactionSettingsLocation = "",
                ),
                permissionsActions = permissionsActions,
            )
        }
        composeTestRule
            .onNodeWithTag(TestTags.REDACTION_STATUS)
            .assertTextContains("Not yet checked", substring = true)
        composeTestRule.onNodeWithTag(TestTags.REDACTION_TEST_BUTTON).assertExists()
    }

    @Test
    fun `clicking test button calls testSensitiveReading`() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    redactionState = RedactionState.Unknown,
                ),
                permissionsActions = permissionsActions,
            )
        }
        composeTestRule.onNodeWithTag(TestTags.REDACTION_TEST_BUTTON).performClick()
        verify { permissionsActions.testSensitiveReading() }
    }

    @Test
    fun `clicking open settings calls openRedactionSettings when Hidden`() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    redactionState = RedactionState.Hidden,
                    redactionSettingsLocation = "Settings > Enhanced Notifications",
                ),
                permissionsActions = permissionsActions,
            )
        }
        composeTestRule.onNodeWithTag(TestTags.REDACTION_OPEN_SETTINGS).performClick()
        verify { permissionsActions.openRedactionSettings() }
    }
}
