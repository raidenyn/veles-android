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
import androidx.test.platform.app.InstrumentationRegistry
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.services.PermissionType
import me.nagaev.veles.permissions.ui.VelesPermissionsApp
import me.nagaev.veles.permissions.viewmodal.Permission
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState
import me.nagaev.veles.permissions.viewmodal.SensitiveNotificationsUiState
import org.junit.Rule
import org.junit.Test

// Uses createComposeRule() (no Hilt host). The NavHost routes for "test" and
// "bank-configs" call hiltViewModel(), which requires a Hilt Android entry
// point. Navigating beyond the "permissions" route in this test will fail.
// To test those routes, switch to createAndroidHiltComposeRule or a
// @HiltAndroidTest with HiltAndroidRule.
class VelesPermissionsAppTests {
    @get:Rule
    val composeTestRule = createComposeRule()
    private val targetContext = InstrumentationRegistry.getInstrumentation().targetContext

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
            .assertTextContains(
                targetContext.getString(R.string.permissions_listener_enabled),
                substring = true,
            )
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
            .assertTextContains(
                targetContext.getString(R.string.permissions_listener_disabled),
                substring = true,
            )
    }

    @Test
    fun sensitiveCardVisibleWhenNotGranted() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    sensitiveNotifications = SensitiveNotificationsUiState.NotGranted,
                    cdmSupported = true,
                    revealSensitiveFallbacks = true,
                ),
                permissionsActions = permissionsActions,
            )
        }
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertExists()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ENABLE_BUTTON).assertExists()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_PAIRING_GUIDE).assertExists()
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_ADB_GUIDE).assertExists()
    }

    @Test
    fun sensitiveCardAbsentWhenNotApplicable() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState =
                permissionsState.copy(
                    sensitiveNotifications = SensitiveNotificationsUiState.NotApplicable,
                ),
                permissionsActions = permissionsActions,
            )
        }
        composeTestRule.onNodeWithTag(TestTags.SENSITIVE_CARD).assertDoesNotExist()
    }

    @Test
    fun `bottom bar shows three destinations on permissions route`() {
        composeTestRule.setContent {
            VelesPermissionsApp(
                permissionsState = permissionsState,
                permissionsActions = permissionsActions,
            )
        }

        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_BAR).assertExists()
        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_ITEM("permissions")).assertExists()
        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_ITEM("bank-configs")).assertExists()
        composeTestRule.onNodeWithTag(TestTags.BOTTOM_NAV_ITEM("test")).assertExists()
    }
}
